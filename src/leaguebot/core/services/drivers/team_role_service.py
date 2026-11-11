"""TeamRoleService — a team's Discord role and the role helpers that give and take it.

The bot's own part of the roles a driver holds. It owns the `team_role_configs` table (each team's
shorthand mapped to the Discord role the league made for it) and the helpers that give and take
Discord roles: the ones that **log and carry on** (`_grant_roles`, `_revoke_roles`, what a
placement's own write calls once it is committed) and the ones that **raise** where Discord will
not (`grant_roles`, `revoke_roles`, `grant_to_every_driver`, what a job on the change queue calls,
the queue stopping where Discord does). It posts nothing. The seats are `PlacementService`'s, which is handed this service by the
builder, as is every change that gives or takes a role; it reads them only to name who is seated
(`seated_user_ids_on`). It is handed no bot, since it grants through the guild or member it is
given.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import StepFailedOnDiscord
from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.models.refusal import Refused
from leaguebot.core.models.team import TeamRoleConfig

log = logging.getLogger(__name__)


@dataclass
class RoleGrantOutcome:
    """What granting one role to every driver did.

    *granted* counts the drivers given it. *not_granted* is the Discord id of each driver
    Discord would not give it to; a driver no longer in the server is in neither, there being
    nobody to give it to.
    """

    granted: int = 0
    not_granted: list[str] = field(default_factory=list)


class TeamRoleService:
    """See the module docstring. Built by the builder over the bot's database."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Internal role helpers
    # ------------------------------------------------------------------

    async def _grant_roles(self, member: discord.Member, *role_ids: int) -> None:
        """Grant Discord roles to member. Logs failures but does not raise."""
        for role_id in role_ids:
            role = member.guild.get_role(role_id)
            if role is None:
                log.warning("_grant_roles: role %s not found in guild %s", role_id, member.guild.id)
                continue
            try:
                await member.add_roles(role, reason="Driver placement")
            except discord.HTTPException as exc:
                log.warning("_grant_roles: failed to add role %s to %s: %s", role_id, member.id, exc)

    async def grant_roles(
        self,
        guild: discord.Guild,
        user_id: int,
        *role_ids: int,
        reason: str = "Driver placement",
    ) -> bool:
        """Grant *role_ids* to the member *user_id*, **raising** where it could not be done.

        What a job on the change queue calls: a driver's roles are theirs to give and the queue
        stops where Discord will not, where `_grant_roles` logs and carries on. A member Discord
        reports absent (`NotFound`) has left the server and is passed over: nothing is given,
        nothing raises, and the result is False. Anything else stops the job with
        `StepFailedOnDiscord`, raised from the Discord fault where there is one: the member could
        not be fetched for another reason, a role is no longer on the server (checked before any is
        given, so no driver is left with half), or Discord refuses the grant. *reason* is what
        Discord's audit log is given for the grant. Granting is idempotent, so a retry gives again
        what is already held.
        """
        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            return False
        except discord.HTTPException as exc:
            raise StepFailedOnDiscord(
                f"member {user_id} could not be fetched to be given their roles: {exc}"
            ) from exc
        roles = []
        for role_id in role_ids:
            role = guild.get_role(role_id)
            if role is None:
                raise StepFailedOnDiscord("a role is no longer on the server")
            roles.append(role)
        try:
            await member.add_roles(*roles, reason=reason)
        except discord.HTTPException as exc:
            raise StepFailedOnDiscord(
                f"member {user_id} could not be given their roles: {exc}"
            ) from exc
        return True

    async def revoke_roles(
        self, guild: discord.Guild, user_id: int, *role_ids: int, reason: str
    ) -> bool:
        """Take *role_ids* from the member *user_id*, **raising** where it could not be done.

        What a job on the change queue calls when a season's end takes a driver's roles back:
        the queue stops where Discord will not, where `_revoke_roles` logs and carries on. The
        mirror of `grant_roles`. A member Discord reports absent (`NotFound`) has left
        the server, and their roles went with them: nothing is taken, nothing raises, and the
        result is False. A member who cannot be fetched for any other reason, or a removal
        Discord refuses, stops the job with `StepFailedOnDiscord`, raised from the fault.

        A role no longer on the server is passed over, and only the roles the member holds are
        asked of Discord: a driver without the driver role is not asked to lose it. Taking is
        idempotent, so a retry takes again what is still held.
        """
        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            return False
        except discord.HTTPException as exc:
            raise StepFailedOnDiscord(
                f"member {user_id} could not be fetched to have their roles taken back: {exc}"
            ) from exc
        held = list(member.roles)
        roles = [
            role
            for role in (guild.get_role(role_id) for role_id in role_ids)
            if role is not None and role in held
        ]
        if roles:
            try:
                await member.remove_roles(*roles, reason=reason)
            except discord.HTTPException as exc:
                raise StepFailedOnDiscord(
                    f"member {user_id} could not have their roles taken back: {exc}"
                ) from exc
        return True

    async def grant_to_every_driver(
        self, guild: discord.Guild, role_id: int
    ) -> RoleGrantOutcome:
        """Grant *role_id* to every driver of the league, and say what came of it.

        Called when a league role deleted from the server is replaced while the season's
        configuration is fixed. Nobody holds a deleted role, so every driver who held it lost it
        with the role; this gives them its replacement. **Every driver** is whoever the driver
        role belongs to — a real driver, Unassigned or Assigned — which is the set
        `driver_role_ids` grants it on.

        Each driver is granted on their own and a failure is recorded, not raised: the role is
        already stored by the time this runs, and one member Discord refuses must not cost the
        rest theirs. A driver no longer in the server is passed over.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT discord_user_id FROM driver_profiles "
                "WHERE is_test_driver = 0 AND current_state IN (?, ?) "
                "ORDER BY discord_user_id",
                (DriverState.UNASSIGNED.value, DriverState.ASSIGNED.value),
            )
            user_ids = [row["discord_user_id"] for row in await cursor.fetchall()]

        outcome = RoleGrantOutcome()
        role = guild.get_role(role_id)
        if role is None:
            outcome.not_granted = list(user_ids)
            return outcome
        for user_id in user_ids:
            try:
                member = guild.get_member(int(user_id))
                if member is None:
                    try:
                        member = await guild.fetch_member(int(user_id))
                    except discord.NotFound:
                        continue
                await member.add_roles(role, reason="League role replaced")
            except Exception:  # noqa: BLE001 — the role is stored; the rest still get it
                log.exception("grant_to_every_driver: could not grant %s to %s", role_id, user_id)
                outcome.not_granted.append(str(user_id))
            else:
                outcome.granted += 1
        return outcome

    async def _revoke_roles(self, member: discord.Member, *role_ids: int) -> None:
        """Revoke Discord roles from member. Logs failures but does not raise."""
        for role_id in role_ids:
            role = member.guild.get_role(role_id)
            if role is None:
                log.warning("_revoke_roles: role %s not found in guild %s", role_id, member.guild.id)
                continue
            try:
                await member.remove_roles(role, reason="Driver placement")
            except discord.HTTPException as exc:
                log.warning("_revoke_roles: failed to remove role %s from %s: %s", role_id, member.id, exc)

    # ------------------------------------------------------------------
    # team_role_configs DB layer
    # ------------------------------------------------------------------

    async def get_team_role_config(
        self, team_name: str
    ) -> TeamRoleConfig | None:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, team_name, role_id, updated_at "
                "FROM team_role_configs WHERE team_name = ?",
                (team_name,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return TeamRoleConfig(
            id=row["id"],
            team_name=row["team_name"],
            role_id=row["role_id"],
            updated_at=row["updated_at"],
        )

    async def team_holding_role(
        self, role_id: int, *, other_than: str | None = None
    ) -> str | None:
        """The team of the server's list mapped to *role_id*, other than *other_than*, or None."""
        async with get_connection(self._db_path) as db:
            return await self._team_holding_role(db, role_id, other_than)

    @staticmethod
    async def _team_holding_role(db, role_id: int, other_than: str | None) -> str | None:
        cursor = await db.execute(
            "SELECT team_name FROM team_role_configs WHERE role_id = ? AND team_name != ? "
            "LIMIT 1",
            (role_id, other_than or ""),
        )
        row = await cursor.fetchone()
        return row["team_name"] if row else None

    async def set_team_role_config_on(
        self, db: aiosqlite.Connection, team_name: str, role_id: int, *, now: datetime,
    ) -> int | None:
        """Upsert a team -> role mapping on *db*, a connection the caller holds, committing
        nothing and writing no audit entry: the change that calls it records the mapping. Returns
        the role the team held before, or None. *now* is when the mapping is stamped updated.

        **A role belongs to one team only.** A submission names a team by its role, and a result
        records the team the role resolves to, so a role two teams held would name either. Raises
        ``Refused`` naming the team that holds it, and writes nothing; re-setting a team's own role
        is allowed. The schema's unique index on ``role_id`` is the backstop.
        """
        holder = await self._team_holding_role(db, role_id, team_name)
        if holder is not None:
            raise Refused(
                f'<@&{role_id}> is already the role of "{holder}". A role belongs to '
                "one team only."
            )
        cursor = await db.execute(
            "SELECT role_id FROM team_role_configs WHERE team_name = ?", (team_name,),
        )
        existing = await cursor.fetchone()
        await db.execute(
            "INSERT INTO team_role_configs (team_name, role_id, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(team_name) DO UPDATE SET "
            "role_id = excluded.role_id, updated_at = excluded.updated_at",
            (team_name, role_id, now.isoformat()),
        )
        return existing["role_id"] if existing else None

    async def delete_team_role_config_on(
        self, db: aiosqlite.Connection, team_name: str,
    ) -> int | None:
        """Delete the team -> role mapping on *db*, a connection the caller holds, committing
        nothing and writing no audit entry. Returns the role it held, or None where it had none:
        a silent no-op."""
        cursor = await db.execute(
            "SELECT id, role_id FROM team_role_configs WHERE team_name = ?", (team_name,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        await db.execute("DELETE FROM team_role_configs WHERE id = ?", (row["id"],))
        return row["role_id"]

    async def rename_team_role_config_on(
        self, db: aiosqlite.Connection, old_name: str, new_name: str, *, now: datetime,
    ) -> int | None:
        """Rename the team_name key in the role mapping on *db*, a connection the caller holds,
        committing nothing and writing no audit entry. Returns the role it holds, or None where
        the team had none: a silent no-op. *now* is when the mapping is stamped updated."""
        cursor = await db.execute(
            "SELECT id, role_id FROM team_role_configs WHERE team_name = ?", (old_name,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        await db.execute(
            "UPDATE team_role_configs SET team_name = ?, updated_at = ? WHERE id = ?",
            (new_name, now.isoformat(), row["id"]),
        )
        return row["role_id"]

    @staticmethod
    async def team_role_on(db: aiosqlite.Connection, team_name: str) -> int | None:
        """The role the server's list maps the team *team_name* to, read on *db*, or None."""
        row = await (
            await db.execute(
                "SELECT role_id FROM team_role_configs WHERE team_name = ?", (team_name,)
            )
        ).fetchone()
        return int(row["role_id"]) if row else None

    @staticmethod
    async def seated_user_ids_on(db: aiosqlite.Connection, team_name: str) -> list[int]:
        """The accounts of the drivers seated in the team *team_name*, read on *db*: those in any
        division of the season being raced whose placement is confirmed, in the order of their ids.

        The drivers already seated in a team follow its role when a league repoints it. A driver
        created by test mode holds no roles and is left out.
        """
        from leaguebot.core.services.seasons.season_lifecycle_service import uncommitted_seat_excluded

        cursor = await db.execute(
            f"""
            SELECT DISTINCT dp.discord_user_id
            FROM team_seats ts
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            JOIN divisions d ON d.id = ti.division_id
            JOIN seasons s ON s.id = d.season_id
            JOIN driver_profiles dp ON dp.id = ts.driver_profile_id
            WHERE s.status = 'ACTIVE' AND ti.name = ?
              AND dp.is_test_driver = 0
              AND {uncommitted_seat_excluded("ts")}
            ORDER BY dp.discord_user_id
            """,
            (team_name,),
        )
        return [int(row["discord_user_id"]) for row in await cursor.fetchall()]
