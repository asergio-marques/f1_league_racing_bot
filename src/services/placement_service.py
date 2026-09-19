"""PlacementService — driver placement, role management, and seeded listing."""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import datetime, timezone

import discord

from db.database import get_connection
from models.driver_profile import DriverProfile, DriverState
from services.driver_service import DRIVERS_SIGNUP_OF_DP_SQL, write_transition
from models.signup_module import AvailabilitySlot
from models.team import TeamRoleConfig

log = logging.getLogger(__name__)

#: The driver states a signup is unsettled in: approved and unplaced, or still being judged.
#: The unassigned listing reports every one of them (issue #220).
_UNSETTLED_SQL = ", ".join(
    f"'{state}'"
    for state in (
        "UNASSIGNED",
        "PENDING_ADMIN_APPROVAL",
        "AWAITING_CORRECTION_PARAMETER",
        "PENDING_DRIVER_CORRECTION",
    )
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_ms(total_ms: int) -> str:
    """Format milliseconds as M:ss.mmm (e.g. 83456 → '1:23.456')."""
    minutes, remainder = divmod(total_ms, 60_000)
    seconds, ms = divmod(remainder, 1000)
    return f"{minutes}:{seconds:02d}.{ms:03d}"


def _parse_lap_time_ms(time_str: str) -> int | None:
    """Parse 'M:ss.mmm' or 'M:ss.ms' into milliseconds. Returns None on failure."""
    try:
        minutes_part, rest = time_str.strip().split(":", 1)
        if "." in rest:
            secs_part, ms_part = rest.split(".", 1)
        else:
            secs_part, ms_part = rest, "0"
        ms_part = ms_part.ljust(3, "0")[:3]
        return int(minutes_part) * 60_000 + int(secs_part) * 1000 + int(ms_part)
    except (ValueError, AttributeError):
        return None


def _compute_total_lap_ms(lap_times: dict[str, str]) -> int | None:
    """Sum all lap times in a lap_times dict. Returns None if empty or unparseable."""
    if not lap_times:
        return None
    total = 0
    for time_str in lap_times.values():
        ms = _parse_lap_time_ms(time_str)
        if ms is None:
            return None
        total += ms
    return total if total > 0 else None


class PlacementService:
    def __init__(self, db_path: str, bot=None) -> None:
        self._db_path = db_path
        #: The bot, where one is available. Needed only by the lineup **image** path, which
        #: reads the module toggles and the render service through it. None in every unit
        #: test that exercises placement alone, and the textual lineup is unaffected by its
        #: absence — which is the point: without a bot this service behaves exactly as it
        #: did before 038.
        self._bot = bot

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

    async def set_team_role_config(
        self, team_name: str, role_id: int,
        actor_id: int = 0, actor_name: str = "system",
    ) -> None:
        """Upsert a team → role mapping and write an audit entry."""
        async with get_connection(self._db_path) as db:
            # Read existing before upsert for audit old_value
            cursor = await db.execute(
                "SELECT role_id FROM team_role_configs WHERE team_name = ?",
                (team_name,),
            )
            existing = await cursor.fetchone()
            old_role_id = existing["role_id"] if existing else None

            await db.execute(
                """
                INSERT INTO team_role_configs (team_name, role_id, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(team_name) DO UPDATE SET
                    role_id    = excluded.role_id,
                    updated_at = excluded.updated_at
                """,
                (team_name, role_id),
            )
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, 'TEAM_ROLE_CONFIG', ?, ?, ?)",
                (
                    actor_id,
                    actor_name,
                    json.dumps({"team": team_name, "role_id": old_role_id}),
                    json.dumps({"team": team_name, "role_id": role_id}),
                    now,
                ),
            )
            await db.commit()

    async def get_all_team_role_configs(self) -> list[TeamRoleConfig]:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, team_name, role_id, updated_at "
                "FROM team_role_configs",
            )
            rows = await cursor.fetchall()
        return [
            TeamRoleConfig(
                id=r["id"],
                team_name=r["team_name"],
                role_id=r["role_id"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    async def swap_team_role(
        self,
        team_name: str,
        old_role_id: int | None,
        new_role_id: int | None,
        guild: discord.Guild | None,
    ) -> int:
        """Move every driver seated in *team_name* from its old role to its new one.

        The team's role is never fixed (issue #220): a league repoints it when the role is
        deleted or replaced, and the drivers already seated in the team — in any division of
        the season being raced, their placements confirmed — follow it. The old role is taken
        from each where one was mapped and no other team still maps to it; the new one is
        granted where one is given. A driver created by test mode holds no roles and is left
        alone. Returns how many drivers were reached.
        """
        from services.season_lifecycle_service import uncommitted_seat_excluded

        if guild is None or old_role_id == new_role_id:
            return 0
        async with get_connection(self._db_path) as db:
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
            user_ids = [row["discord_user_id"] for row in await cursor.fetchall()]
            still_mapped = False
            if old_role_id is not None:
                cursor = await db.execute(
                    "SELECT 1 FROM team_role_configs WHERE role_id = ? "
                    "AND team_name != ? LIMIT 1",
                    (old_role_id, team_name),
                )
                still_mapped = await cursor.fetchone() is not None

        reached = 0
        for user_id in user_ids:
            member = guild.get_member(int(user_id))
            if member is None:
                try:
                    member = await guild.fetch_member(int(user_id))
                except discord.HTTPException:
                    continue
            if old_role_id is not None and not still_mapped:
                await self._revoke_roles(member, old_role_id)
            if new_role_id is not None:
                await self._grant_roles(member, new_role_id)
            reached += 1
        return reached

    async def delete_team_role_config(
        self, team_name: str,
        actor_id: int = 0, actor_name: str = "system",
    ) -> None:
        """Delete the team -> role mapping if present; silent no-op if absent."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, role_id FROM team_role_configs "
                "WHERE team_name = ?",
                (team_name,),
            )
            row = await cursor.fetchone()
            if row is None:
                return
            await db.execute(
                "DELETE FROM team_role_configs WHERE id = ?", (row["id"],)
            )
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, "
                "old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, 'TEAM_ROLE_CONFIG', ?, ?, ?)",
                (
                    actor_id, actor_name,
                    json.dumps({"team": team_name, "role_id": row["role_id"]}),
                    json.dumps({"team": team_name, "role_id": None}),
                    now,
                ),
            )
            await db.commit()

    async def rename_team_role_config(
        self, old_name: str, new_name: str,
        actor_id: int = 0, actor_name: str = "system",
    ) -> None:
        """Rename the team_name key in the role mapping; silent no-op if absent."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, role_id FROM team_role_configs "
                "WHERE team_name = ?",
                (old_name,),
            )
            row = await cursor.fetchone()
            if row is None:
                return
            await db.execute(
                "UPDATE team_role_configs "
                "SET team_name = ?, updated_at = datetime('now') WHERE id = ?",
                (new_name, row["id"]),
            )
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, "
                "old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, 'TEAM_ROLE_CONFIG', ?, ?, ?)",
                (
                    actor_id, actor_name,
                    json.dumps({"team": old_name, "role_id": row["role_id"]}),
                    json.dumps({"team": new_name, "role_id": row["role_id"]}),
                    now,
                ),
            )
            await db.commit()

    # ------------------------------------------------------------------
    # total_lap_ms computation (called at approval)
    # ------------------------------------------------------------------

    async def store_total_lap_ms(
        self, discord_user_id: str, lap_times: dict[str, str]
    ) -> int | None:
        """Compute and persist total_lap_ms on the driver's SignupRecord.

        Returns the computed value (or None if no times). Called within the
        same logical operation as signup approval — uses its own connection
        since the caller may already be inside a different context.
        """
        total_ms = _compute_total_lap_ms(lap_times)
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE signup_records SET total_lap_ms = ? "
                "WHERE id = (SELECT MAX(id) FROM signup_records "
                "            WHERE discord_user_id = ?)",
                (total_ms, discord_user_id),
            )
            await db.commit()
        return total_ms

    # ------------------------------------------------------------------
    # Confirming placements mid-season (issue #220)
    # ------------------------------------------------------------------

    async def uncommitted_placements(self, season_id: int) -> list[dict]:
        """Every placement of *season_id* not yet committed, ordered by division and team.

        Each row: driver_profile_id, discord_user_id, is_test_driver, test_display_name,
        division_id, division_name, division_role_id, team_name.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT dsa.driver_profile_id, dp.discord_user_id, dp.is_test_driver,
                       dp.test_display_name, d.id AS division_id, d.name AS division_name,
                       d.mention_role_id AS division_role_id, ti.name AS team_name
                FROM driver_season_assignments dsa
                JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id
                JOIN divisions d ON d.id = dsa.division_id
                LEFT JOIN team_seats ts ON ts.id = dsa.team_seat_id
                LEFT JOIN team_instances ti ON ti.id = ts.team_instance_id
                WHERE dsa.season_id = ? AND dsa.committed = 0
                ORDER BY d.tier, ti.is_reserve, ti.name, dp.discord_user_id
                """,
                (season_id,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def commit_mid_season_placements(
        self, season_id: int, guild: discord.Guild | None
    ) -> list[dict]:
        """Commit the season's uncommitted placements, granting their roles and posting lineups.

        Each driver committed is granted their division's role and their team's role; the
        lineup of each division holding such a driver is posted once, however many of them
        it holds. Returns the placements committed, as ``uncommitted_placements`` reads them.
        """
        placements = await self.uncommitted_placements(season_id)
        if not placements:
            return []
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE driver_season_assignments SET committed = 1 "
                "WHERE season_id = ? AND committed = 0",
                (season_id,),
            )
            await db.commit()

        if guild is not None:
            for placement in placements:
                if placement["is_test_driver"]:
                    continue
                member = guild.get_member(int(placement["discord_user_id"]))
                if member is None:
                    try:
                        member = await guild.fetch_member(int(placement["discord_user_id"]))
                    except discord.HTTPException:
                        continue
                role_ids = [placement["division_role_id"]]
                if placement["team_name"]:
                    team_cfg = await self.get_team_role_config(placement["team_name"])
                    if team_cfg is not None:
                        role_ids.append(team_cfg.role_id)
                await self._grant_roles(member, *role_ids)
            for division_id in dict.fromkeys(p["division_id"] for p in placements):
                await self._refresh_lineup_post(guild, division_id)
        return placements

    # ------------------------------------------------------------------
    # Seeded unassigned listing (T008)
    # ------------------------------------------------------------------

    async def get_unassigned_drivers_seeded(self) -> list[dict]:
        """Return every unsettled signup: Unassigned drivers in seed order, then the rest.

        Unassigned drivers are seeded by total_lap_ms ASC NULLS LAST, then by the moment their
        signup was submitted — not approved, and not last corrected. A driver still awaiting
        approval or correction follows them, holding no seed (``seed`` is None) — they are
        listed because confirming placements waits on them too (issue #220).
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT
                    dp.id                   AS profile_id,
                    dp.discord_user_id,
                    dp.current_state,
                    sr.server_display_name,
                    sr.platform,
                    sr.availability_slot_ids,
                    sr.driver_type,
                    sr.preferred_teams,
                    sr.preferred_teammate,
                    sr.notes,
                    sr.total_lap_ms,
                    sr.created_at           AS submitted_at
                FROM driver_profiles dp
                -- The driver's signup: records are kept, never overwritten (#220), and an
                -- approved one outranks a later one that was not (#243).
                LEFT JOIN signup_records sr ON sr.id = {drivers_signup}
                WHERE dp.current_state IN ({unsettled})
                ORDER BY
                    dp.current_state = 'UNASSIGNED' DESC,
                    sr.total_lap_ms ASC NULLS LAST,
                    -- A tie goes to whoever sent their signup in first: the moment the record
                    -- was made, which a correction never moves.
                    sr.created_at ASC,
                    sr.id ASC
                """.format(unsettled=_UNSETTLED_SQL, drivers_signup=DRIVERS_SIGNUP_OF_DP_SQL)
            )
            rows = await cursor.fetchall()

        results = []
        for i, row in enumerate(rows, start=1):
            total_ms = row["total_lap_ms"]
            results.append({
                "seed": i if row["current_state"] == "UNASSIGNED" else None,
                "state": row["current_state"],
                "discord_user_id": row["discord_user_id"],
                "server_display_name": row["server_display_name"] or row["discord_user_id"],
                "platform": row["platform"] or "—",
                "availability_slot_ids": json.loads(row["availability_slot_ids"] or "[]"),
                "driver_type": row["driver_type"] or "—",
                "preferred_teams": json.loads(row["preferred_teams"] or "[]"),
                "preferred_teammate": row["preferred_teammate"],
                "notes": row["notes"],
                "total_lap_ms": total_ms,
                "total_lap_fmt": _fmt_ms(total_ms) if total_ms is not None else "—",
            })
        return results

    # ------------------------------------------------------------------
    # Export unassigned drivers (T017)
    # ------------------------------------------------------------------

    async def get_unassigned_drivers_for_export(
        self, slots: list[AvailabilitySlot]
    ) -> list[dict]:
        """Return every unsettled signup, as the seeded listing orders it, enriched for CSV.

        Each row dict contains:
          seed, display_name, discord_user_id, driver_type, total_lap_fmt,
          slot_presence (dict {slot_sequence_id: bool}),
          preferred_team_1, preferred_team_2, preferred_team_3,
          platform, platform_id

        ``slot_presence`` is **keyed** by the display ordinal, because that is the CSV
        column order, but each answer is **matched** on the slot's durable ID. Matching
        on the ordinal is the defect in issue #126: it is a chronological position
        recomputed on every read, so a slot added or removed since the driver signed up
        would put their X under somebody else's time.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT
                    dp.discord_user_id,
                    dp.current_state,
                    sr.server_display_name,
                    sr.discord_username,
                    sr.platform,
                    sr.platform_id,
                    sr.availability_slot_ids,
                    sr.driver_type,
                    sr.preferred_teams,
                    sr.total_lap_ms,
                    sr.created_at           AS submitted_at
                FROM driver_profiles dp
                -- The driver's signup: records are kept, never overwritten (#220), and an
                -- approved one outranks a later one that was not (#243).
                LEFT JOIN signup_records sr ON sr.id = {drivers_signup}
                WHERE dp.current_state IN ({unsettled})
                ORDER BY
                    dp.current_state = 'UNASSIGNED' DESC,
                    sr.total_lap_ms ASC NULLS LAST,
                    -- A tie goes to whoever sent their signup in first: the moment the record
                    -- was made, which a correction never moves.
                    sr.created_at ASC,
                    sr.id ASC
                """.format(unsettled=_UNSETTLED_SQL, drivers_signup=DRIVERS_SIGNUP_OF_DP_SQL)
            )
            rows = await cursor.fetchall()

        slots_ordered = sorted(slots, key=lambda s: s.slot_sequence_id)
        results = []
        for i, row in enumerate(rows, start=1):
            total_ms = row["total_lap_ms"]
            slot_ids_raw: list[str] = json.loads(row["availability_slot_ids"] or "[]")
            slot_presence = {s.slot_sequence_id: (s.slot_id in slot_ids_raw) for s in slots_ordered}

            preferred_teams_raw: list[str] = json.loads(row["preferred_teams"] or "[]")
            preferred_team_1 = preferred_teams_raw[0] if len(preferred_teams_raw) > 0 else ""
            preferred_team_2 = preferred_teams_raw[1] if len(preferred_teams_raw) > 1 else ""
            preferred_team_3 = preferred_teams_raw[2] if len(preferred_teams_raw) > 2 else ""

            display_name = row["server_display_name"] or row["discord_username"] or row["discord_user_id"]
            results.append({
                "seed": i if row["current_state"] == "UNASSIGNED" else None,
                "state": row["current_state"],
                "display_name": display_name,
                "discord_user_id": row["discord_user_id"],
                "driver_type": row["driver_type"] or "",
                "total_lap_fmt": _fmt_ms(total_ms) if total_ms is not None else "",
                "slot_presence": slot_presence,
                "preferred_team_1": preferred_team_1,
                "preferred_team_2": preferred_team_2,
                "preferred_team_3": preferred_team_3,
                "platform": row["platform"] or "",
                "platform_id": row["platform_id"] or "",
            })
        return results

    # ------------------------------------------------------------------
    # Image template capacity (036 / Constitution XIV.12)
    # ------------------------------------------------------------------

    async def _is_reserve_team(self, division_id: int, team_name: str) -> bool | None:
        """Whether *team_name* is the division's reserve team, or None where there is no such team.

        The capacity guards below measure two different populations — the reserve block and
        the classification — and a driver joins exactly one of them. Neither guard can tell
        which without this, and both run before ``assign_driver`` opens its own transaction,
        where the same flag is read again from the row it needs in hand anyway.
        """
        async with get_connection(self._db_path) as db:
            row = await (
                await db.execute(
                    "SELECT is_reserve FROM team_instances "
                    "WHERE division_id = ? AND name = ?",
                    (division_id, team_name),
                )
            ).fetchone()
        return None if row is None else bool(row["is_reserve"])

    async def _guard_reserve_capacity(
        self, division_id: int, team_name: str, *, adding: int = 1
    ) -> None:
        """Refuse a reserve placement that would outgrow the lineup template (FR/R8).

        The reserve block is the one lineup collection whose slots the template fixes, so
        it is the one to which overflow applies. XIV.12 requires overflow to be rejected
        at the earliest moment it can be detected, with the change unapplied — which is
        this command, not the render.

        **It bounds reserve placements and nothing else.** A driver going into an ordinary
        race team never joins the reserve block, so a full block says nothing about them.
        Until #140 this guard was not told which team was being filled and counted the
        division's reserves on every assignment, which left a division carrying as many
        reserves as its template drew slots for unable to seat anybody at all — the refusal
        blaming the reserve block for a placement that was never part of it.

        *adding* is how many reserves the change seats — one for a placement, more for a test
        roster seated whole (``guard_roster_capacity``).

        Never raises for its own reasons: a fault in this check must not block a
        placement, only a genuine over-capacity may.
        """
        bot = self._bot
        if bot is None:
            return
        try:
            from models.image_catalogues import reserve_capacity_problem
            from services.image_lineup_post import lineup_enabled
            from utils.svg_document import load_svg

            # Not a reserve placement, or no such team — `assign_driver` reports a team
            # that does not exist in its own words, and this guard stays quiet.
            if not await self._is_reserve_team(division_id, team_name):
                return

            if not await lineup_enabled(bot):
                return

            reports = await bot.image_validity_service.template_reports()
            report = reports.get("lineup_template")
            if report is None or not report.valid:
                return

            async with get_connection(self._db_path) as db:
                row = await (
                    await db.execute(
                        "SELECT COUNT(*) AS seated FROM driver_season_assignments dsa "
                        "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
                        "JOIN team_instances ti ON ti.id = ts.team_instance_id "
                        "WHERE dsa.division_id = ? AND ti.is_reserve = 1",
                        (division_id,),
                    )
                ).fetchone()
            seated = (row["seated"] if row else 0) or 0
            problem = reserve_capacity_problem(load_svg(report.resolved_path), seated + adding)
        except Exception as exc:  # noqa: BLE001
            log.error("reserve capacity guard could not run: %s", exc)
            return

        if problem is not None:
            raise ValueError(
                f"{problem}. The driver was **not** assigned. Enlarge the template, or "
                f"turn the `lineup` image aspect off with `/images config toggle`."
            )

    async def _guard_sheet_capacity(self, division_id: int, *, adding: int = 1) -> None:
        """Refuse a placement that would outgrow the attendance sheet template (FR-042).

        The sheet draws every driver of the division, and its rows are counted from the file
        rather than declared as a number — so this reads the configured template exactly as
        the reserve guard does, and for the same reason: XIV.12 rejects overflow at the
        earliest moment it can be detected, with the change unapplied. That moment is this
        command. Discovering it at a posting means the league has already lost its sheet.

        **It counts reserves, and that is deliberate — do not make it match the standings
        guard.** The two look alike and measure different things. A reserve holds no row in a
        classification ever, so ``_guard_standings_capacity`` excludes them; but the sheet
        draws a reserve the moment one is allocated to a round, and
        ``attendance_service`` selects on ``ti.is_reserve = 1 AND dra.assigned_team_id IS NOT
        NULL`` to do it. In the worst case every reserve on the books is allocated and takes a
        row, so counting them all is the correct ceiling rather than an over-count — and the
        team being filled is likewise immaterial here, a reserve placement being exactly the
        one that might later want a row. Pinned by
        ``test_placement_sheet_capacity_guard.py``.

        *adding* is how many drivers the change seats, of either kind — one for a placement,
        more for a test roster seated whole (``guard_roster_capacity``).

        Never raises for its own reasons: a fault in this check must not block a placement,
        only a genuine over-capacity may.
        """
        bot = self._bot
        if bot is None:
            return
        try:
            from models.image_catalogues import row_capacity_problem
            from services.image_attendance_post import attendance_enabled
            from utils.svg_document import load_svg

            if not await attendance_enabled(bot):
                return

            reports = await bot.image_validity_service.template_reports()
            report = reports.get("attendance_template")
            if report is None or not report.valid or report.resolved_path is None:
                return

            async with get_connection(self._db_path) as db:
                row = await (
                    await db.execute(
                        "SELECT COUNT(*) AS seated FROM driver_season_assignments "
                        "WHERE division_id = ?",
                        (division_id,),
                    )
                ).fetchone()
            seated = (row["seated"] if row else 0) or 0
            problem = row_capacity_problem(
                "attendance_template", load_svg(report.resolved_path), seated + adding
            )
        except Exception as exc:  # noqa: BLE001
            log.error("attendance sheet capacity guard could not run: %s", exc)
            return

        if problem is not None:
            raise ValueError(
                f"{problem}. The driver was **not** assigned. Enlarge the template, or "
                f"the sheet would silently drop a driver."
            )

    async def _guard_standings_capacity(
        self, division_id: int, team_name: str, *, adding: int = 1
    ) -> None:
        """Refuse a placement that would outgrow the driver standings template (FR-044).

        The standings draw every driver of the division's classification, and their rows are
        counted from the file rather than declared as a number — so this reads the configured
        template exactly as the reserve and sheet guards do, and for the same reason: XIV.12
        rejects overflow at the earliest moment it can be detected, with the change unapplied.

        **A reserve is not an entry of a classification.** They stand in for an absent driver
        and add no car to the grid, and ``standings_service`` filters ``ti.is_reserve = 0`` out
        of every classification it builds. So the count below excludes them, and a placement
        *into* the reserve team is not measured at all — it grows no classification. Until #140
        this guard counted every assignment of the division and added one whatever team was
        being filled, which refused an ordinary placement one seat early for each reserve on
        the books, and refused a reserve outright once the classified drivers filled the rows.

        The **constructors** ceiling is not checked here. Seating a driver adds no team, so no
        driver assignment can breach it; it is checked at ``/season placements-review``, which is where a
        division's team count is settled.

        *adding* is how many classified drivers the change seats — one for a placement, more
        for a test roster seated whole (``guard_roster_capacity``).

        Never raises for its own reasons: a fault in this check must not block a placement,
        only a genuine over-capacity may.
        """
        bot = self._bot
        if bot is None:
            return
        try:
            from models.image_catalogues import row_capacity_problem
            from services.image_standings_post import standings_enabled
            from services.image_standings_service import DRIVERS_TEMPLATE_KEY
            from utils.svg_document import load_svg

            # A reserve placement adds no entry to the classification, and no such team —
            # `assign_driver` reports a team that does not exist in its own words.
            if await self._is_reserve_team(division_id, team_name) is not False:
                return

            if not await standings_enabled(bot, DRIVERS_TEMPLATE_KEY):
                return

            reports = await bot.image_validity_service.template_reports()
            report = reports.get(DRIVERS_TEMPLATE_KEY)
            if report is None or not report.valid or report.resolved_path is None:
                return

            async with get_connection(self._db_path) as db:
                row = await (
                    await db.execute(
                        "SELECT COUNT(*) AS seated FROM driver_season_assignments dsa "
                        "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
                        "JOIN team_instances ti ON ti.id = ts.team_instance_id "
                        "WHERE dsa.division_id = ? AND ti.is_reserve = 0",
                        (division_id,),
                    )
                ).fetchone()
            seated = (row["seated"] if row else 0) or 0
            problem = row_capacity_problem(
                DRIVERS_TEMPLATE_KEY, load_svg(report.resolved_path), seated + adding
            )
        except Exception as exc:  # noqa: BLE001
            log.error("standings capacity guard could not run: %s", exc)
            return

        if problem is not None:
            raise ValueError(
                f"{problem}. The driver was **not** assigned. Enlarge the template, or "
                f"the standings would silently drop a driver."
            )

    async def _guard_test_mode(self, driver_profile_id: int) -> None:
        """Refuse to seat a *real* driver while the server is in test mode.

        Test mode and a real league may not share a server, so a real driver never enters
        a division while the flag is on: their signup is refused at the button, and their
        placement here. A fake driver is untouched by this — the roster commands seat them
        by another path, but attendance autoreserve moves them through this very call.

        Unlike the capacity guards above, this is a genuine precondition rather than a
        best-effort check, so it does **not** swallow its own errors: a fault reading the
        flag must not be the thing that lets a real driver through.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT (SELECT test_mode_active FROM server_configs) "
                "           AS test_mode_active, "
                "       dp.is_test_driver "
                "FROM driver_profiles dp WHERE dp.id = ?",
                (driver_profile_id,),
            )
            row = await cursor.fetchone()

        if row is None:
            # No such profile: assign_driver reports that itself, in its own words.
            return
        if row["is_test_driver"]:
            return
        if row["test_mode_active"]:
            raise ValueError(
                "Test mode is active — a real driver cannot be assigned to a team. "
                "Turn it off with `/test-mode toggle` first."
            )

    async def _guard_image_capacity(
        self, division_id: int, season_id: int, team_name: str
    ) -> None:
        """Refuse a placement that would outgrow a configured image template.

        Reads the declared capacities from the catalogue module. While no image type is
        specified every catalogue is empty, ``declared_capacities()`` is empty, and this
        returns immediately — the guard activates **by data**, the moment the first image
        type declares a capacity, with no further code change.

        Never raises for its own reasons: a fault in this check must not block a
        placement, only a genuine over-capacity may.
        """
        from models.image_catalogues import declared_capacities
        from services.module_service import ModuleService

        # The reserve block is guarded separately: it counts reserve drivers, not every
        # seated driver, and its capacity comes from the template rather than from here.
        # It needs the team, because only a placement into the reserve team joins it.
        await self._guard_reserve_capacity(division_id, team_name)

        # The attendance sheet's rows are likewise counted from the template rather than
        # declared as a number, so they are invisible to ``declared_capacities()`` below.
        await self._guard_sheet_capacity(division_id)

        # And so are the driver standings' — both standings catalogues declare
        # ``capacity=None`` and derive their rows from the file. It needs the team too,
        # because a reserve joins no classification.
        await self._guard_standings_capacity(division_id, team_name)

        capacities = declared_capacities()
        if not capacities:
            return

        try:
            if not await ModuleService(self._db_path).is_images_enabled():
                return

            smallest = min(capacities.values())

            async with get_connection(self._db_path) as db:
                cursor = await db.execute(
                    "SELECT COUNT(*) AS seated FROM driver_season_assignments "
                    "WHERE season_id = ? AND division_id = ?",
                    (season_id, division_id),
                )
                row = await cursor.fetchone()
            seated = (row["seated"] if row else 0) or 0
        except Exception as exc:  # noqa: BLE001
            log.error("image capacity guard could not run: %s", exc)
            return

        if seated + 1 <= smallest:
            return

        template_key = min(capacities, key=lambda key: capacities[key])
        from models.image_constants import TEMPLATE_LABELS

        label = TEMPLATE_LABELS.get(template_key, template_key)
        raise ValueError(
            f"This would seat {seated + 1} drivers in the division, but the "
            f"**{label}** image template provides only {smallest} rows. "
            f"Enlarge that template, or disable the images module, before adding "
            f"another driver."
        )

    async def guard_roster_capacity(
        self, division_id: int, added: Mapping[str, int]
    ) -> None:
        """Refuse a test roster that a real placement into the same teams would be refused for.

        *added* maps each team of *division_id* to the number of drivers the change seats in
        it. Test mode seats its fake drivers without passing through ``assign_driver``, so
        without this a roster larger than the league's templates could hold was accepted, and
        the rehearsal passed a configuration a real season would refuse (#150).

        **The count is per division, summed across its teams.** A bulk roster seats a whole
        division at once, and two teams that each fit on their own can together outgrow the
        standings. The three template guards run once each, measuring the reserves, every
        driver, and the classified drivers respectively. A team the division does not hold is
        skipped: the roster service refuses it in its own words.

        The declared-capacity branch of ``_guard_image_capacity`` is not run: every catalogue
        declares ``capacity=None`` today, so it bounds no placement, real or test.

        Raises ValueError naming the template, as a real placement's refusal does.
        """
        reserve_team: str | None = None
        classified_team: str | None = None
        reserves = classified = 0
        for team_name, count in added.items():
            is_reserve = await self._is_reserve_team(division_id, team_name)
            if is_reserve is None or count <= 0:
                continue
            if is_reserve:
                reserve_team, reserves = team_name, reserves + count
            else:
                classified_team, classified = team_name, classified + count

        if reserve_team is not None:
            await self._guard_reserve_capacity(division_id, reserve_team, adding=reserves)
        if reserves + classified:
            await self._guard_sheet_capacity(division_id, adding=reserves + classified)
        if classified_team is not None:
            await self._guard_standings_capacity(
                division_id, classified_team, adding=classified
            )

    # ------------------------------------------------------------------
    # Assign driver (T010)
    # ------------------------------------------------------------------

    async def _free_seat(self, db, division_id: int, team_name: str) -> tuple[int, bool]:
        """The first free seat of *team_name* in *division_id*, and whether it is Reserve.

        A race team's seats are finite and fill in number order; the reserve team makes
        another seat rather than refusing. Raises ValueError for a team the division does
        not hold, or a race team with no seat free. Shared by placing a driver and moving
        one, so the two cannot disagree about where a driver may sit.
        """
        cursor = await db.execute(
            """
            SELECT ti.is_reserve FROM team_instances ti
            WHERE ti.division_id = ? AND ti.name = ?
            """,
            (division_id, team_name),
        )
        ti_row = await cursor.fetchone()
        if ti_row is None:
            raise ValueError(f"Team **{team_name}** not found in this division.")
        is_reserve = bool(ti_row["is_reserve"])

        cursor = await db.execute(
            """
            SELECT ts.id FROM team_seats ts
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            WHERE ti.division_id = ? AND ti.name = ? AND ts.driver_profile_id IS NULL
            ORDER BY ts.seat_number ASC
            LIMIT 1
            """,
            (division_id, team_name),
        )
        seat_row = await cursor.fetchone()
        if seat_row is not None:
            return seat_row["id"], is_reserve
        if not is_reserve:
            raise ValueError(f"**{team_name}** in this division has no available seats.")

        # Reserve has unlimited seats; create a new one
        cursor = await db.execute(
            "SELECT MAX(ts.seat_number) FROM team_seats ts "
            "JOIN team_instances ti ON ti.id = ts.team_instance_id "
            "WHERE ti.division_id = ? AND ti.name = ?",
            (division_id, team_name),
        )
        max_row = await cursor.fetchone()
        next_seat = (max_row[0] or 0) + 1
        cursor = await db.execute(
            "SELECT id FROM team_instances WHERE division_id = ? AND name = ?",
            (division_id, team_name),
        )
        ti_id = (await cursor.fetchone())["id"]
        cursor = await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
            "VALUES (?, ?, NULL)",
            (ti_id, next_seat),
        )
        return cursor.lastrowid, is_reserve

    async def move_driver(
        self,
        driver_profile_id: int,
        season_id: int,
        from_division_id: int,
        to_division_id: int,
        team_name: str,
        acting_user_id: int,
        acting_user_name: str,
        guild: discord.Guild | None,
        discord_user_id: str,
    ) -> dict:
        """Move a committed driver from their seat in one division to a team of the same or another.

        One change (issue #220): the old seat freed and the new one taken in a single
        transaction, the roles of the seat left revoked where no other seat of the driver maps
        to them, the roles of the seat taken granted, and the lineup of each division touched
        posted once. A driver moved to another division leaves the points they scored in the
        division they left, those being counted per division.

        Refused where the driver holds no committed placement in the division left, where
        they already hold a seat in a different division moved into, where the team is the
        one they already sit in, and where the team has no seat free.

        Returns a summary dict: from_division, to_division, from_team, to_team.
        """
        await self._guard_image_capacity(to_division_id, season_id, team_name)

        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT current_state, is_test_driver FROM driver_profiles WHERE id = ?",
                (driver_profile_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("Driver profile not found.")
            is_test_driver = bool(row["is_test_driver"])

            cursor = await db.execute(
                """
                SELECT dsa.id, dsa.team_seat_id, dsa.committed, ti.name AS team_name
                FROM driver_season_assignments dsa
                LEFT JOIN team_seats ts ON ts.id = dsa.team_seat_id
                LEFT JOIN team_instances ti ON ti.id = ts.team_instance_id
                WHERE dsa.driver_profile_id = ? AND dsa.season_id = ? AND dsa.division_id = ?
                """,
                (driver_profile_id, season_id, from_division_id),
            )
            source = await cursor.fetchone()
            if source is None:
                raise ValueError("Driver holds no seat in the division they are moved from.")
            if not source["committed"]:
                raise ValueError(
                    "That placement is not yet confirmed. Change it with `/driver unassign` "
                    "and `/driver assign`."
                )
            if from_division_id == to_division_id and source["team_name"] == team_name:
                raise ValueError(f"Driver already sits in **{team_name}** in this division.")
            if from_division_id != to_division_id:
                cursor = await db.execute(
                    "SELECT 1 FROM driver_season_assignments "
                    "WHERE driver_profile_id = ? AND season_id = ? AND division_id = ?",
                    (driver_profile_id, season_id, to_division_id),
                )
                if await cursor.fetchone() is not None:
                    raise ValueError(
                        "Driver already holds a seat in the division they would move into."
                    )

            new_seat_id, _ = await self._free_seat(db, to_division_id, team_name)

            cursor = await db.execute(
                "SELECT id, name, mention_role_id FROM divisions WHERE id IN (?, ?)",
                (from_division_id, to_division_id),
            )
            divisions = {r["id"]: r for r in await cursor.fetchall()}

            await db.execute(
                "UPDATE team_seats SET driver_profile_id = NULL WHERE id = ?",
                (source["team_seat_id"],),
            )
            await db.execute(
                "UPDATE team_seats SET driver_profile_id = ? WHERE id = ?",
                (driver_profile_id, new_seat_id),
            )
            if from_division_id == to_division_id:
                await db.execute(
                    "UPDATE driver_season_assignments SET team_seat_id = ? WHERE id = ?",
                    (new_seat_id, source["id"]),
                )
            else:
                # A placement in the division left goes with the seat; the points already
                # scored there stay in that division's results, which is where they count.
                await db.execute(
                    "UPDATE driver_season_assignments "
                    "SET division_id = ?, team_seat_id = ?, current_position = 0, "
                    "    current_points = 0, points_gap_to_first = 0 "
                    "WHERE id = ?",
                    (to_division_id, new_seat_id, source["id"]),
                )
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, 'DRIVER_MOVE', ?, ?, ?)",
                (
                    acting_user_id, acting_user_name, to_division_id,
                    json.dumps({"division": divisions[from_division_id]["name"],
                                "team": source["team_name"]}),
                    json.dumps({"division": divisions[to_division_id]["name"],
                                "team": team_name}),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()

            # Which team roles the driver still holds a seat for, now the move is written.
            cursor = await db.execute(
                """
                SELECT DISTINCT ti.name FROM driver_season_assignments dsa
                JOIN team_seats ts ON ts.id = dsa.team_seat_id
                JOIN team_instances ti ON ti.id = ts.team_instance_id
                WHERE dsa.driver_profile_id = ? AND dsa.season_id = ?
                """,
                (driver_profile_id, season_id),
            )
            teams_held = {r["name"] for r in await cursor.fetchall()}

        if guild is not None and not is_test_driver:
            member = guild.get_member(int(discord_user_id))
            if member is None:
                try:
                    member = await guild.fetch_member(int(discord_user_id))
                except discord.HTTPException:
                    member = None
            if member is not None:
                old_cfg = (
                    await self.get_team_role_config(source["team_name"])
                    if source["team_name"] else None
                )
                new_cfg = await self.get_team_role_config(team_name)
                held_role_ids = set()
                for held in teams_held:
                    cfg = await self.get_team_role_config(held)
                    if cfg is not None:
                        held_role_ids.add(cfg.role_id)
                revoke: list[int] = []
                if from_division_id != to_division_id:
                    revoke.append(divisions[from_division_id]["mention_role_id"])
                if old_cfg is not None and old_cfg.role_id not in held_role_ids:
                    revoke.append(old_cfg.role_id)
                if revoke:
                    await self._revoke_roles(member, *revoke)
                grant = [divisions[to_division_id]["mention_role_id"]]
                if new_cfg is not None:
                    grant.append(new_cfg.role_id)
                await self._grant_roles(member, *grant)

        if guild is not None:
            for division_id in dict.fromkeys((from_division_id, to_division_id)):
                await self._refresh_lineup_post(guild, division_id)

        return {
            "from_division": divisions[from_division_id]["name"],
            "to_division": divisions[to_division_id]["name"],
            "from_team": source["team_name"],
            "to_team": team_name,
        }

    async def assign_driver(
        self,
        driver_profile_id: int,
        division_id: int,
        team_name: str,
        season_id: int,
        acting_user_id: int,
        acting_user_name: str,
        guild: discord.Guild,
        discord_user_id: str,
        season_state: str = "ACTIVE",
        *,
        committed: bool | None = None,
        uncommitted_only: bool = False,
    ) -> dict:
        """Assign a driver to a team seat in a division.

        *committed* says whether the placement is committed (issue #220). `/driver assign`
        places uncommitted drivers only, so it passes False: the placement stands outside the
        championship — no role granted, no lineup posted — until placements are confirmed.
        Left None, the placement takes the default migration 057 gives its season: committed
        where the season's placements are confirmed. *uncommitted_only* refuses a driver who
        already holds a committed placement in the season, which is how Ongoing, placements
        keeps the assign command to the drivers of the window just closed.

        *season_state* is no longer read: whether roles are granted follows the placement's
        being committed, not the season's status.

        Returns a summary dict with keys: was_unassigned, team_name, division_name, committed.
        Raises ValueError for all blocking conditions.
        """
        # A command that would carry a division past what its configured templates can
        # draw is refused here, with the change not applied (Constitution XIV.12,
        # FR-028). This is the single choke point through which a driver enters a
        # division, so guarding it covers the signup wizard, manual placement and bulk
        # import alike. Inert while every catalogue is empty.
        await self._guard_image_capacity(division_id, season_id, team_name)

        # A real driver may not be seated while the server is in test mode: the same
        # choke point keeps the manual command, the signup path and attendance's
        # autoreserve alike from mixing a real roster into a test one.
        await self._guard_test_mode(driver_profile_id)

        async with get_connection(self._db_path) as db:
            # 1. Fetch profile and validate state
            cursor = await db.execute(
                "SELECT current_state, is_test_driver FROM driver_profiles WHERE id = ?",
                (driver_profile_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("Driver profile not found.")
            current_state = DriverState(row["current_state"])
            is_test_driver: bool = bool(row["is_test_driver"])
            if current_state not in (DriverState.UNASSIGNED, DriverState.ASSIGNED):
                raise ValueError(
                    f"Driver must be Unassigned or Assigned to be placed "
                    f"(current state: {current_state.value})."
                )

            if uncommitted_only:
                cursor = await db.execute(
                    "SELECT 1 FROM driver_season_assignments "
                    "WHERE driver_profile_id = ? AND season_id = ? AND committed = 1 LIMIT 1",
                    (driver_profile_id, season_id),
                )
                if await cursor.fetchone() is not None:
                    raise ValueError(
                        "Driver already holds a confirmed placement this season. Move them "
                        "with `/driver move`, or release them from a division with "
                        "`/driver release`."
                    )

            # 2. Check no duplicate division assignment
            cursor = await db.execute(
                "SELECT id FROM driver_season_assignments "
                "WHERE driver_profile_id = ? AND season_id = ? AND division_id = ?",
                (driver_profile_id, season_id, division_id),
            )
            if await cursor.fetchone() is not None:
                cursor = await db.execute(
                    "SELECT name FROM divisions WHERE id = ?", (division_id,)
                )
                div_row = await cursor.fetchone()
                div_name = div_row["name"] if div_row else str(division_id)
                raise ValueError(
                    f"Driver is already assigned to a team in **{div_name}**."
                )

            # 3. Find a free seat in this team/division (Reserve = always free)
            seat_id, is_reserve = await self._free_seat(db, division_id, team_name)

            # 4. Fetch division name and role
            cursor = await db.execute(
                "SELECT name, mention_role_id FROM divisions WHERE id = ?", (division_id,)
            )
            div_info = await cursor.fetchone()
            div_name = div_info["name"]
            div_role_id = div_info["mention_role_id"]

            # 5. Atomically occupy seat + create assignment + transition state
            was_unassigned = current_state == DriverState.UNASSIGNED
            now = datetime.now(timezone.utc).isoformat()

            await db.execute(
                "UPDATE team_seats SET driver_profile_id = ? WHERE id = ?",
                (driver_profile_id, seat_id),
            )
            cursor = await db.execute(
                "INSERT INTO driver_season_assignments "
                "(driver_profile_id, season_id, division_id, team_seat_id, "
                " current_position, current_points, points_gap_to_first, committed) "
                "VALUES (?, ?, ?, ?, 0, 0, 0, ?)",
                (
                    driver_profile_id, season_id, division_id, seat_id,
                    None if committed is None else int(committed),
                ),
            )
            assignment_id = cursor.lastrowid
            cursor = await db.execute(
                "SELECT committed FROM driver_season_assignments WHERE id = ?",
                (assignment_id,),
            )
            is_committed = bool((await cursor.fetchone())["committed"])
            if was_unassigned:
                await write_transition(
                    db, driver_profile_id, DriverState.UNASSIGNED, DriverState.ASSIGNED
                )
            # Audit log
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, 'DRIVER_ASSIGN', ?, ?, ?)",
                (
                    acting_user_id,
                    acting_user_name,
                    division_id,
                    json.dumps({"state": current_state.value}),
                    json.dumps({
                        "team": team_name,
                        "division": div_name,
                        "seat_id": seat_id,
                        "new_state": DriverState.ASSIGNED.value,
                    }),
                    now,
                ),
            )
            await db.commit()

        # 6. Grant Discord roles (fail-soft)
        member = guild.get_member(int(discord_user_id))
        if member is None:
            try:
                member = await guild.fetch_member(int(discord_user_id))
            except discord.HTTPException:
                member = None

        # An uncommitted placement is outside the championship: it grants no role and posts
        # no lineup. Both follow when placements are confirmed.
        if member is not None and not is_test_driver and is_committed:
            role_ids_to_grant = [div_role_id]
            team_cfg = await self.get_team_role_config(team_name)
            if team_cfg is not None:
                role_ids_to_grant.append(team_cfg.role_id)
            await self._grant_roles(member, *role_ids_to_grant)

        if guild is not None and is_committed:
            await self._refresh_lineup_post(guild, division_id)
        return {
            "was_unassigned": was_unassigned,
            "team_name": team_name,
            "division_name": div_name,
            "committed": is_committed,
        }

    # ------------------------------------------------------------------
    # Unassign driver (T012)
    # ------------------------------------------------------------------

    async def release_driver(
        self,
        driver_profile_id: int,
        division_id: int,
        season_id: int,
        acting_user_id: int,
        acting_user_name: str,
        guild: discord.Guild,
        discord_user_id: str,
    ) -> dict:
        """Release a committed driver from one division, keeping every other seat they hold.

        Issue #220. The division's role is revoked, the team's role only where no other seat
        maps to it, and the division's lineup is posted again — the removal a committed
        placement undergoes. Refused for an uncommitted placement, which is unassigned instead,
        and for a driver's only seat, which is sacked or moved instead.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT committed FROM driver_season_assignments "
                "WHERE driver_profile_id = ? AND season_id = ? AND division_id = ?",
                (driver_profile_id, season_id, division_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("Driver holds no seat in that division.")
            if not row["committed"]:
                raise ValueError(
                    "That placement is not yet confirmed. Remove it with `/driver unassign`."
                )
            # Only a confirmed seat counts: a driver left holding nothing but an unconfirmed
            # placement has been taken out of the championship, which moving them does instead.
            cursor = await db.execute(
                "SELECT COUNT(*) AS n FROM driver_season_assignments "
                "WHERE driver_profile_id = ? AND season_id = ? AND division_id != ? "
                "AND committed = 1",
                (driver_profile_id, season_id, division_id),
            )
            if (await cursor.fetchone())["n"] == 0:
                raise ValueError(
                    "That is the driver's only seat. Sack them with `/driver sack`, or move "
                    "them with `/driver move`."
                )
        return await self.unassign_driver(
            driver_profile_id=driver_profile_id,
            division_id=division_id,
            season_id=season_id,
            acting_user_id=acting_user_id,
            acting_user_name=acting_user_name,
            guild=guild,
            discord_user_id=discord_user_id,
        )

    async def unassign_driver(
        self,
        driver_profile_id: int,
        division_id: int,
        season_id: int,
        acting_user_id: int,
        acting_user_name: str,
        guild: discord.Guild,
        discord_user_id: str,
        season_state: str = "ACTIVE",
        *,
        uncommitted_only: bool = False,
    ) -> dict:
        """Remove a driver's assignment from one division.

        Roles are revoked and the lineup posted again only where the placement was committed;
        an uncommitted one held neither (issue #220). *uncommitted_only* refuses a committed
        placement, which `/driver move` and `/driver release` change instead. *season_state*
        is no longer read.

        Returns a summary dict: division_name, has_remaining_assignments.
        Raises ValueError for blocking conditions.
        """
        async with get_connection(self._db_path) as db:
            # 1. Validate driver state
            cursor = await db.execute(
                "SELECT current_state, is_test_driver FROM driver_profiles WHERE id = ?",
                (driver_profile_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("Driver profile not found.")
            current_state = DriverState(row["current_state"])
            is_test_driver: bool = bool(row["is_test_driver"])
            if current_state != DriverState.ASSIGNED:
                raise ValueError(
                    f"Driver must be in Assigned state to be unassigned "
                    f"(current state: {current_state.value})."
                )

            # 2. Find the assignment row for this division
            cursor = await db.execute(
                "SELECT id, team_seat_id, committed FROM driver_season_assignments "
                "WHERE driver_profile_id = ? AND season_id = ? AND division_id = ?",
                (driver_profile_id, season_id, division_id),
            )
            asgn_row = await cursor.fetchone()
            if asgn_row is None:
                cursor = await db.execute(
                    "SELECT name FROM divisions WHERE id = ?", (division_id,)
                )
                div_row = await cursor.fetchone()
                div_name = div_row["name"] if div_row else str(division_id)
                raise ValueError(
                    f"Driver is not assigned to any team in **{div_name}**."
                )
            asgn_id = asgn_row["id"]
            seat_id = asgn_row["team_seat_id"]
            was_committed = bool(asgn_row["committed"])
            if uncommitted_only and was_committed:
                raise ValueError(
                    "That placement has been confirmed. Move the driver with `/driver move`, "
                    "or release them from the division with `/driver release`."
                )

            # 3. Fetch team name for this seat (needed for role revocation)
            team_name: str | None = None
            if seat_id is not None:
                cursor = await db.execute(
                    "SELECT ti.name FROM team_instances ti "
                    "JOIN team_seats ts ON ts.team_instance_id = ti.id "
                    "WHERE ts.id = ?",
                    (seat_id,),
                )
                team_row = await cursor.fetchone()
                team_name = team_row["name"] if team_row else None

            # 4. Fetch division name and role
            cursor = await db.execute(
                "SELECT name, mention_role_id FROM divisions WHERE id = ?", (division_id,)
            )
            div_info = await cursor.fetchone()
            div_name = div_info["name"]
            div_role_id = div_info["mention_role_id"]

            # 5. Count remaining assignments after this removal
            cursor = await db.execute(
                "SELECT COUNT(*) FROM driver_season_assignments "
                "WHERE driver_profile_id = ? AND season_id = ? AND division_id != ?",
                (driver_profile_id, season_id, division_id),
            )
            remaining_count = (await cursor.fetchone())[0]
            has_remaining = remaining_count > 0

            # 6. Determine if team role should be revoked
            # Revoke only if the driver holds no other seat in any team mapped to that role
            team_role_id_to_revoke: int | None = None
            if team_name is not None:
                team_cfg = await self.get_team_role_config(team_name)
                if team_cfg is not None:
                    # Check other assignments that share this role
                    cursor = await db.execute(
                        """
                        SELECT COUNT(*) FROM driver_season_assignments dsa
                        JOIN team_seats ts ON ts.id = dsa.team_seat_id
                        JOIN team_instances ti ON ti.id = ts.team_instance_id
                        JOIN team_role_configs trc
                            ON trc.team_name = ti.name
                        WHERE dsa.driver_profile_id = ?
                          AND dsa.season_id = ?
                          AND dsa.division_id != ?
                          AND trc.role_id = ?
                        """,
                        (driver_profile_id, season_id, division_id, team_cfg.role_id),
                    )
                    other_same_role = (await cursor.fetchone())[0]
                    if other_same_role == 0:
                        team_role_id_to_revoke = team_cfg.role_id

            now = datetime.now(timezone.utc).isoformat()

            # 7. Atomically: free seat, delete assignment, update state if needed
            if seat_id is not None:
                await db.execute(
                    "UPDATE team_seats SET driver_profile_id = NULL WHERE id = ?",
                    (seat_id,),
                )
            await db.execute(
                "DELETE FROM driver_season_assignments WHERE id = ?", (asgn_id,)
            )
            if not has_remaining:
                await write_transition(
                    db, driver_profile_id, DriverState.ASSIGNED, DriverState.UNASSIGNED
                )

            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, 'DRIVER_UNASSIGN', ?, ?, ?)",
                (
                    acting_user_id,
                    acting_user_name,
                    division_id,
                    json.dumps({"team": team_name, "seat_id": seat_id}),
                    json.dumps({
                        "new_state": DriverState.UNASSIGNED.value if not has_remaining else DriverState.ASSIGNED.value,
                        "has_remaining": has_remaining,
                    }),
                    now,
                ),
            )
            await db.commit()

        # 8. Revoke Discord roles (fail-soft)
        member = guild.get_member(int(discord_user_id))
        if member is None:
            try:
                member = await guild.fetch_member(int(discord_user_id))
            except discord.HTTPException:
                member = None

        if member is not None and not is_test_driver and was_committed:
            roles_to_revoke = [div_role_id]
            if team_role_id_to_revoke is not None:
                roles_to_revoke.append(team_role_id_to_revoke)
            await self._revoke_roles(member, *roles_to_revoke)

        if guild is not None and was_committed:
            await self._refresh_lineup_post(guild, division_id)
        return {"division_name": div_name, "has_remaining_assignments": has_remaining, "team_name": team_name}

    # ------------------------------------------------------------------
    # Revoke all placement roles (T014)
    # ------------------------------------------------------------------

    async def revoke_all_placement_roles(
        self,
        driver_profile_id: int,
        season_id: int | None,
        member: discord.Member,
    ) -> None:
        """Revoke all division and team roles for a driver across all active assignments.

        Reusable by future ban management commands (FR-029).
        """
        if season_id is None:
            return

        async with get_connection(self._db_path) as db:
            # Division role IDs
            cursor = await db.execute(
                """
                SELECT DISTINCT d.mention_role_id
                FROM driver_season_assignments dsa
                JOIN divisions d ON d.id = dsa.division_id
                WHERE dsa.driver_profile_id = ? AND dsa.season_id = ?
                """,
                (driver_profile_id, season_id),
            )
            div_role_rows = await cursor.fetchall()

            # Team role IDs (via team_role_configs keyed on team name)
            cursor = await db.execute(
                """
                SELECT DISTINCT trc.role_id
                FROM driver_season_assignments dsa
                JOIN team_seats ts ON ts.id = dsa.team_seat_id
                JOIN team_instances ti ON ti.id = ts.team_instance_id
                JOIN team_role_configs trc
                    ON trc.team_name = ti.name
                WHERE dsa.driver_profile_id = ? AND dsa.season_id = ?
                """,
                (driver_profile_id, season_id),
            )
            team_role_rows = await cursor.fetchall()

        all_role_ids = {r["mention_role_id"] for r in div_role_rows} | {
            r["role_id"] for r in team_role_rows
        }
        if all_role_ids:
            await self._revoke_roles(member, *all_role_ids)

    # ------------------------------------------------------------------
    # A driver's roles follow their current account (issue #243)
    # ------------------------------------------------------------------

    async def driver_role_ids(self, driver_profile_id: int) -> set[int]:
        """The roles the driver's standing entitles them to, read from state, not from Discord.

        The signed-up role while they are Unassigned or Assigned, and the division and team
        roles of every confirmed placement in the live season — exactly what approval and
        confirming placements grant. A test-mode driver holds no roles. Read from the league's
        own record so that it answers even when the account holding the roles has left.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT current_state, is_test_driver FROM driver_profiles WHERE id = ?",
                (driver_profile_id,),
            )
            profile = await cursor.fetchone()
            if profile is None or profile["is_test_driver"]:
                return set()
            roles: set[int] = set()
            if profile["current_state"] in (
                DriverState.UNASSIGNED.value, DriverState.ASSIGNED.value
            ):
                cursor = await db.execute(
                    "SELECT signed_up_role_id FROM signup_module_config",
                )
                row = await cursor.fetchone()
                if row is not None and row["signed_up_role_id"]:
                    roles.add(int(row["signed_up_role_id"]))
            cursor = await db.execute(
                """
                SELECT d.mention_role_id AS division_role, trc.role_id AS team_role
                FROM driver_season_assignments dsa
                JOIN seasons s ON s.id = dsa.season_id
                JOIN divisions d ON d.id = dsa.division_id
                LEFT JOIN team_seats ts ON ts.id = dsa.team_seat_id
                LEFT JOIN team_instances ti ON ti.id = ts.team_instance_id
                LEFT JOIN team_role_configs trc
                    ON trc.team_name = ti.name
                WHERE dsa.driver_profile_id = ? AND dsa.committed = 1 AND s.status IN ('SETUP', 'ACTIVE')
                """,
                (driver_profile_id,),
            )
            for row in await cursor.fetchall():
                for role_id in (row["division_role"], row["team_role"]):
                    if role_id:
                        roles.add(int(role_id))
        return roles

    async def move_driver_roles(
        self,
        guild: discord.Guild,
        driver_profile_id: int,
        from_account: str,
        to_account: str,
    ) -> list[str]:
        """Give the driver's roles to *to_account* and take them from *from_account*.

        Called once a reassign has made *to_account* current. The roles are those the
        driver's standing entitles them to (`driver_role_ids`), so a replaced account that
        has already left the server costs nothing: its removal is skipped, and the new
        account is granted from the league's record. Returns what could not be done, for the
        command to report; the account change itself stands either way.
        """
        role_ids = await self.driver_role_ids(driver_profile_id)
        if not role_ids:
            return []
        problems: list[str] = []
        roles = []
        for role_id in sorted(role_ids):
            role = guild.get_role(role_id)
            if role is None:
                problems.append(f"role {role_id} no longer exists")
            else:
                roles.append(role)

        async def _member(account: str):
            member = guild.get_member(int(account))
            if member is None:
                try:
                    member = await guild.fetch_member(int(account))
                except discord.HTTPException:
                    member = None
            return member

        new_member = await _member(to_account)
        if new_member is None:
            problems.append(f"<@{to_account}> is not in the server to receive the roles")
        elif roles:
            try:
                await new_member.add_roles(*roles, reason="Driver's current account changed")
            except discord.HTTPException as exc:
                problems.append(f"the roles could not be given to <@{to_account}>: {exc}")
        old_member = await _member(from_account)
        if old_member is not None and roles:
            held = [r for r in roles if r in getattr(old_member, "roles", [])]
            if held:
                try:
                    await old_member.remove_roles(*held, reason="Driver's current account changed")
                except discord.HTTPException as exc:
                    problems.append(f"the roles could not be taken from <@{from_account}>: {exc}")
        return problems

    # ------------------------------------------------------------------
    # Sack driver (T015)
    # ------------------------------------------------------------------

    async def sack_driver(
        self,
        driver_profile_id: int,
        season_id: int,
        acting_user_id: int,
        acting_user_name: str,
        guild: discord.Guild,
        discord_user_id: str,
    ) -> None:
        """Sack a committed driver: revoke every role, free every seat, return to Not Signed Up.

        Issue #220. Sacking is for a driver whose placement is confirmed — an uncommitted one
        is unassigned or rejected instead — and is available only while the season is
        ongoing, which the command checks. The profile is **never deleted here**: a driver
        without the former-driver flag who reaches Not Signed Up is pending deletion, and is
        deleted by the driver pass that ends the season. Their attendance, results and
        standings rows are therefore kept, and so is every signup they made.

        The season's placements are removed, so the driver holds no seat and gains no history
        entry for the season. Raises ValueError for blocking conditions.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT current_state, former_driver, is_test_driver FROM driver_profiles "
                "WHERE id = ?",
                (driver_profile_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("Driver profile not found.")
            current_state = DriverState(row["current_state"])
            former_driver = bool(row["former_driver"])
            is_test_driver: bool = bool(row["is_test_driver"])
            if current_state not in (DriverState.UNASSIGNED, DriverState.ASSIGNED):
                raise ValueError(
                    f"Driver must be Unassigned or Assigned to be sacked "
                    f"(current state: {current_state.value})."
                )

            cursor = await db.execute(
                "SELECT division_id, committed FROM driver_season_assignments "
                "WHERE driver_profile_id = ? AND season_id = ?",
                (driver_profile_id, season_id),
            )
            asgn_rows = await cursor.fetchall()
            if not any(r["committed"] for r in asgn_rows):
                raise ValueError(
                    "Only a driver whose placement is confirmed can be sacked. Remove an "
                    "unconfirmed placement with `/driver unassign`, and turn down an "
                    "Unassigned driver with `/driver reject`."
                )
            division_ids = [r["division_id"] for r in asgn_rows]
            now = datetime.now(timezone.utc).isoformat()

        # Revoke all roles before DB mutation: the placements are what they are computed from.
        member = guild.get_member(int(discord_user_id))
        if member is None:
            try:
                member = await guild.fetch_member(int(discord_user_id))
            except discord.HTTPException:
                member = None

        if member is not None and not is_test_driver:
            await self.revoke_all_placement_roles(driver_profile_id, season_id, member)
            # Revoke the signed-up role granted at approval
            async with get_connection(self._db_path) as db:
                cur = await db.execute(
                    "SELECT signed_up_role_id FROM signup_module_config",
                )
                cfg_row = await cur.fetchone()
            if cfg_row and cfg_row["signed_up_role_id"]:
                signed_up_role = guild.get_role(cfg_row["signed_up_role_id"])
                if signed_up_role is not None and signed_up_role in member.roles:
                    await self._revoke_roles(member, signed_up_role.id)

        async with get_connection(self._db_path) as db:
            # The seats of this season alone: a former driver's seats in a completed season
            # are the archive, which a sack never changes.
            await db.execute(
                "UPDATE team_seats SET driver_profile_id = NULL "
                "WHERE driver_profile_id = ? AND team_instance_id IN ("
                "    SELECT ti.id FROM team_instances ti "
                "    JOIN divisions d ON d.id = ti.division_id WHERE d.season_id = ?)",
                (driver_profile_id, season_id),
            )
            await db.execute(
                "DELETE FROM driver_season_assignments "
                "WHERE driver_profile_id = ? AND season_id = ?",
                (driver_profile_id, season_id),
            )
            await write_transition(
                db, driver_profile_id, current_state, DriverState.NOT_SIGNED_UP
            )
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, 'DRIVER_SACK', ?, ?, ?)",
                (
                    acting_user_id,
                    acting_user_name,
                    json.dumps({"state": current_state.value, "divisions": division_ids}),
                    json.dumps({
                        "new_state": DriverState.NOT_SIGNED_UP.value,
                        "former_driver": former_driver,
                    }),
                    now,
                ),
            )
            await db.commit()

        if guild is not None:
            for _div_id in division_ids:
                await self._refresh_lineup_post(guild, _div_id)

    # ------------------------------------------------------------------
    # Division resolution helper (used by cogs)
    # ------------------------------------------------------------------

    async def _refresh_lineup_post(
        self, guild: discord.Guild, division_id: int, *, bot=None
    ) -> None:
        """Post the division's lineup: as a graphic where configured, else as the embed.

        **The image path is a guard clause in front of an untouched body.** Where the
        images module is enabled, the `lineup` aspect is on and a valid template is
        configured, ``image_lineup_post.try_post`` produces the PNG *before* deleting the
        message it replaces (FR-025) and this method returns.

        Where it is not — the module off, the aspect off, no template — everything below
        runs exactly as it did before 038, **delete-then-build order included**
        (FR-025a, SC-007). That order was specified in specs/028-season-signup-flow/ and
        is deliberately not reopened by this feature: the lineup image is an alternative
        output beside the text, not a reform of it.
        """
        owner = bot if bot is not None else getattr(self, "_bot", None)
        if owner is not None:
            try:
                from services.image_lineup_post import try_post

                outcome = await try_post(owner, guild, division_id)
                if outcome.applicable:
                    return
            except Exception as exc:  # noqa: BLE001 — never block a placement on this
                log.error("_refresh_lineup_post: image path failed: %s", exc)

        async with get_connection(self._db_path) as db:
            cur = await db.execute(
                "SELECT d.name AS div_name, d.lineup_channel_id, d.lineup_message_id "
                "FROM divisions d JOIN seasons s ON s.id = d.season_id WHERE d.id = ?",
                (division_id,),
            )
            div_row = await cur.fetchone()

        if div_row is None or div_row["lineup_channel_id"] is None:
            return

        lineup_channel_id: int = div_row["lineup_channel_id"]
        lineup_message_id: int | None = div_row["lineup_message_id"]
        div_name: str = div_row["div_name"] or str(division_id)

        # Resolve the channel
        channel = guild.get_channel(lineup_channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(lineup_channel_id)
            except (discord.NotFound, discord.HTTPException):
                log.error(
                    "_refresh_lineup_post: lineup channel %s not found for division %s",
                    lineup_channel_id, division_id,
                )
                return
        if not isinstance(channel, discord.TextChannel):
            return

        # Delete old message if present
        if lineup_message_id is not None:
            try:
                old_msg = await channel.fetch_message(lineup_message_id)
                await old_msg.delete()
            except (discord.NotFound, discord.Forbidden):
                pass  # Already gone or no permission — safe to continue

        # Fetch teams with assigned drivers for this division
        async with get_connection(self._db_path) as db:
            cur = await db.execute(
                """
                SELECT ti.name AS team_name, ti.is_reserve,
                       dp.discord_user_id,
                       dp.is_test_driver, dp.test_display_name
                FROM driver_season_assignments dsa
                JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id
                JOIN team_seats ts ON ts.id = dsa.team_seat_id
                JOIN team_instances ti ON ti.id = ts.team_instance_id
                JOIN seasons s ON s.id = dsa.season_id
                WHERE dsa.division_id = ? AND dp.current_state = 'ASSIGNED'
                  -- A placement not yet confirmed mid-season is not posted (issue #220).
                  AND (dsa.committed = 1 OR s.status != 'ACTIVE')
                ORDER BY ti.is_reserve ASC, ti.name ASC
                """,
                (division_id,),
            )
            assign_rows = await cur.fetchall()

        # Group by team, preserving regular-vs-reserve split
        regular: dict[str, list[str]] = {}
        reserve: dict[str, list[str]] = {}
        for row in assign_rows:
            uid = int(row["discord_user_id"])
            mention = f"<@{uid}>"
            if row["is_test_driver"] and row["test_display_name"]:
                mention = f"<@{uid}> ({row['test_display_name']})"
            target = reserve if row["is_reserve"] else regular
            target.setdefault(row["team_name"], []).append(mention)

        # Build embed description: regular teams first, then reserve separated by ---
        parts: list[str] = [
            f"**{t}**: {', '.join(labels)}" for t, labels in regular.items()
        ]
        if reserve:
            if parts:
                parts.append("---")
            parts.extend(
                f"**{t}**: {', '.join(labels)}" for t, labels in reserve.items()
            )

        description = "\n".join(parts) if parts else "*(no drivers assigned)*"
        embed = discord.Embed(
            title=f"\U0001f4cb {div_name} Lineup",
            description=description,
            color=discord.Color.blurple(),
        )

        # Post and persist new message ID
        try:
            new_msg = await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.error("_refresh_lineup_post: failed to post embed: %s", exc)
            return

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE divisions SET lineup_message_id = ? WHERE id = ?",
                (new_msg.id, division_id),
            )
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, 'SIGNUP_LINEUP_POSTED', '', ?, ?)",
                (0, "system", division_id,
                 json.dumps({"channel_id": lineup_channel_id, "division": div_name}), now),
            )
            await db.commit()

    async def resolve_division(
        self, season_id: int, division_input: str
    ) -> tuple[int, str] | None:
        """Resolve a division by tier number or name. Returns (division_id, name) or None."""
        async with get_connection(self._db_path) as db:
            # Try as integer tier first
            try:
                tier = int(division_input)
                cursor = await db.execute(
                    "SELECT id, name FROM divisions WHERE season_id = ? AND tier = ?",
                    (season_id, tier),
                )
            except ValueError:
                cursor = await db.execute(
                    "SELECT id, name FROM divisions WHERE season_id = ? AND name = ?",
                    (season_id, division_input),
                )
            row = await cursor.fetchone()
        if row is None:
            return None
        return row["id"], row["name"]
