"""PlacementService — driver placement, role management, and seeded listing."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import discord

from db.database import get_connection
from models.driver_profile import DriverProfile, DriverState
from models.team import TeamRoleConfig

log = logging.getLogger(__name__)


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
        self, server_id: int, team_name: str
    ) -> TeamRoleConfig | None:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, server_id, team_name, role_id, updated_at "
                "FROM team_role_configs WHERE server_id = ? AND team_name = ?",
                (server_id, team_name),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return TeamRoleConfig(
            id=row["id"],
            server_id=row["server_id"],
            team_name=row["team_name"],
            role_id=row["role_id"],
            updated_at=row["updated_at"],
        )

    async def set_team_role_config(
        self, server_id: int, team_name: str, role_id: int,
        actor_id: int = 0, actor_name: str = "system",
    ) -> None:
        """Upsert a team → role mapping and write an audit entry."""
        async with get_connection(self._db_path) as db:
            # Read existing before upsert for audit old_value
            cursor = await db.execute(
                "SELECT role_id FROM team_role_configs WHERE server_id = ? AND team_name = ?",
                (server_id, team_name),
            )
            existing = await cursor.fetchone()
            old_role_id = existing["role_id"] if existing else None

            await db.execute(
                """
                INSERT INTO team_role_configs (server_id, team_name, role_id, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(server_id, team_name) DO UPDATE SET
                    role_id    = excluded.role_id,
                    updated_at = excluded.updated_at
                """,
                (server_id, team_name, role_id),
            )
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, NULL, 'TEAM_ROLE_CONFIG', ?, ?, ?)",
                (
                    server_id,
                    actor_id,
                    actor_name,
                    json.dumps({"team": team_name, "role_id": old_role_id}),
                    json.dumps({"team": team_name, "role_id": role_id}),
                    now,
                ),
            )
            await db.commit()

    async def get_all_team_role_configs(self, server_id: int) -> list[TeamRoleConfig]:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, server_id, team_name, role_id, updated_at "
                "FROM team_role_configs WHERE server_id = ?",
                (server_id,),
            )
            rows = await cursor.fetchall()
        return [
            TeamRoleConfig(
                id=r["id"],
                server_id=r["server_id"],
                team_name=r["team_name"],
                role_id=r["role_id"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    async def delete_team_role_config(
        self, server_id: int, team_name: str,
        actor_id: int = 0, actor_name: str = "system",
    ) -> None:
        """Delete the team -> role mapping if present; silent no-op if absent."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, role_id FROM team_role_configs "
                "WHERE server_id = ? AND team_name = ?",
                (server_id, team_name),
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
                "(server_id, actor_id, actor_name, division_id, change_type, "
                "old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, NULL, 'TEAM_ROLE_CONFIG', ?, ?, ?)",
                (
                    server_id, actor_id, actor_name,
                    json.dumps({"team": team_name, "role_id": row["role_id"]}),
                    json.dumps({"team": team_name, "role_id": None}),
                    now,
                ),
            )
            await db.commit()

    async def rename_team_role_config(
        self, server_id: int, old_name: str, new_name: str,
        actor_id: int = 0, actor_name: str = "system",
    ) -> None:
        """Rename the team_name key in the role mapping; silent no-op if absent."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, role_id FROM team_role_configs "
                "WHERE server_id = ? AND team_name = ?",
                (server_id, old_name),
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
                "(server_id, actor_id, actor_name, division_id, change_type, "
                "old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, NULL, 'TEAM_ROLE_CONFIG', ?, ?, ?)",
                (
                    server_id, actor_id, actor_name,
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
        self, server_id: int, discord_user_id: str, lap_times: dict[str, str]
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
                "WHERE server_id = ? AND discord_user_id = ?",
                (total_ms, server_id, discord_user_id),
            )
            await db.commit()
        return total_ms

    # ------------------------------------------------------------------
    # Seeded unassigned listing (T008)
    # ------------------------------------------------------------------

    async def get_unassigned_drivers_seeded(self, server_id: int) -> list[dict]:
        """Return all Unassigned drivers ordered by seed (total_lap_ms ASC NULLS LAST,
        then earliest approval timestamp)."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT
                    dp.id                   AS profile_id,
                    dp.discord_user_id,
                    sr.server_display_name,
                    sr.platform,
                    sr.availability_slot_ids,
                    sr.driver_type,
                    sr.preferred_teams,
                    sr.preferred_teammate,
                    sr.notes,
                    sr.total_lap_ms,
                    sr.updated_at           AS approved_at
                FROM driver_profiles dp
                LEFT JOIN signup_records sr
                    ON sr.server_id = dp.server_id
                    AND sr.discord_user_id = dp.discord_user_id
                WHERE dp.server_id = ?
                  AND dp.current_state = 'UNASSIGNED'
                ORDER BY
                    sr.total_lap_ms ASC NULLS LAST,
                    sr.updated_at ASC
                """,
                (server_id,),
            )
            rows = await cursor.fetchall()

        results = []
        for i, row in enumerate(rows, start=1):
            total_ms = row["total_lap_ms"]
            results.append({
                "seed": i,
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
    # Assign driver (T010)
    # ------------------------------------------------------------------

    async def assign_driver(
        self,
        server_id: int,
        driver_profile_id: int,
        division_id: int,
        team_name: str,
        season_id: int,
        acting_user_id: int,
        acting_user_name: str,
        guild: discord.Guild,
        discord_user_id: str,
    ) -> dict:
        """Assign a driver to a team seat in a division.

        Returns a summary dict with keys: was_unassigned, team_name, division_name.
        Raises ValueError for all blocking conditions.
        """
        async with get_connection(self._db_path) as db:
            # 1. Fetch profile and validate state
            cursor = await db.execute(
                "SELECT current_state FROM driver_profiles WHERE id = ? AND server_id = ?",
                (driver_profile_id, server_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("Driver profile not found.")
            current_state = DriverState(row["current_state"])
            if current_state not in (DriverState.UNASSIGNED, DriverState.ASSIGNED):
                raise ValueError(
                    f"Driver must be Unassigned or Assigned to be placed "
                    f"(current state: {current_state.value})."
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

            seat_id: int | None = None
            if not is_reserve:
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
                if seat_row is None:
                    raise ValueError(
                        f"**{team_name}** in this division has no available seats."
                    )
                seat_id = seat_row["id"]
            else:
                # For Reserve, pick the first seat (unlimited; driver_profile_id may be set)
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
                if seat_row is None:
                    # Reserve has unlimited seats; create a new one
                    cursor2 = await db.execute(
                        "SELECT MAX(ts.seat_number) FROM team_seats ts "
                        "JOIN team_instances ti ON ti.id = ts.team_instance_id "
                        "WHERE ti.division_id = ? AND ti.name = ?",
                        (division_id, team_name),
                    )
                    max_row = await cursor2.fetchone()
                    next_seat = (max_row[0] or 0) + 1
                    cursor3 = await db.execute(
                        "SELECT id FROM team_instances WHERE division_id = ? AND name = ?",
                        (division_id, team_name),
                    )
                    ti_id_row = await cursor3.fetchone()
                    ti_id = ti_id_row["id"]
                    cursor4 = await db.execute(
                        "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                        "VALUES (?, ?, NULL)",
                        (ti_id, next_seat),
                    )
                    seat_id = cursor4.lastrowid
                else:
                    seat_id = seat_row["id"]

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
            await db.execute(
                "INSERT INTO driver_season_assignments "
                "(driver_profile_id, season_id, division_id, team_seat_id, "
                " current_position, current_points, points_gap_to_first) "
                "VALUES (?, ?, ?, ?, 0, 0, 0)",
                (driver_profile_id, season_id, division_id, seat_id),
            )
            if was_unassigned:
                await db.execute(
                    "UPDATE driver_profiles SET current_state = ? WHERE id = ?",
                    (DriverState.ASSIGNED.value, driver_profile_id),
                )
            # Audit log
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, ?, 'DRIVER_ASSIGN', ?, ?, ?)",
                (
                    server_id,
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

        if member is not None:
            role_ids_to_grant = [div_role_id]
            team_cfg = await self.get_team_role_config(server_id, team_name)
            if team_cfg is not None:
                role_ids_to_grant.append(team_cfg.role_id)
            await self._grant_roles(member, *role_ids_to_grant)

        return {"was_unassigned": was_unassigned, "team_name": team_name, "division_name": div_name}

    # ------------------------------------------------------------------
    # Unassign driver (T012)
    # ------------------------------------------------------------------

    async def unassign_driver(
        self,
        server_id: int,
        driver_profile_id: int,
        division_id: int,
        season_id: int,
        acting_user_id: int,
        acting_user_name: str,
        guild: discord.Guild,
        discord_user_id: str,
    ) -> dict:
        """Remove a driver's assignment from one division.

        Returns a summary dict: division_name, has_remaining_assignments.
        Raises ValueError for blocking conditions.
        """
        async with get_connection(self._db_path) as db:
            # 1. Validate driver state
            cursor = await db.execute(
                "SELECT current_state FROM driver_profiles WHERE id = ? AND server_id = ?",
                (driver_profile_id, server_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("Driver profile not found.")
            current_state = DriverState(row["current_state"])
            if current_state != DriverState.ASSIGNED:
                raise ValueError(
                    f"Driver must be in Assigned state to be unassigned "
                    f"(current state: {current_state.value})."
                )

            # 2. Find the assignment row for this division
            cursor = await db.execute(
                "SELECT id, team_seat_id FROM driver_season_assignments "
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
                team_cfg = await self.get_team_role_config(server_id, team_name)
                if team_cfg is not None:
                    # Check other assignments that share this role
                    cursor = await db.execute(
                        """
                        SELECT COUNT(*) FROM driver_season_assignments dsa
                        JOIN team_seats ts ON ts.id = dsa.team_seat_id
                        JOIN team_instances ti ON ti.id = ts.team_instance_id
                        JOIN team_role_configs trc
                            ON trc.server_id = ? AND trc.team_name = ti.name
                        WHERE dsa.driver_profile_id = ?
                          AND dsa.season_id = ?
                          AND dsa.division_id != ?
                          AND trc.role_id = ?
                        """,
                        (server_id, driver_profile_id, season_id, division_id, team_cfg.role_id),
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
                await db.execute(
                    "UPDATE driver_profiles SET current_state = ? WHERE id = ?",
                    (DriverState.UNASSIGNED.value, driver_profile_id),
                )

            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, ?, 'DRIVER_UNASSIGN', ?, ?, ?)",
                (
                    server_id,
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

        if member is not None:
            roles_to_revoke = [div_role_id]
            if team_role_id_to_revoke is not None:
                roles_to_revoke.append(team_role_id_to_revoke)
            await self._revoke_roles(member, *roles_to_revoke)

        return {"division_name": div_name, "has_remaining_assignments": has_remaining}

    # ------------------------------------------------------------------
    # Revoke all placement roles (T014)
    # ------------------------------------------------------------------

    async def revoke_all_placement_roles(
        self,
        server_id: int,
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
                    ON trc.server_id = ? AND trc.team_name = ti.name
                WHERE dsa.driver_profile_id = ? AND dsa.season_id = ?
                """,
                (server_id, driver_profile_id, season_id),
            )
            team_role_rows = await cursor.fetchall()

        all_role_ids = {r["mention_role_id"] for r in div_role_rows} | {
            r["role_id"] for r in team_role_rows
        }
        if all_role_ids:
            await self._revoke_roles(member, *all_role_ids)

    # ------------------------------------------------------------------
    # Sack driver (T015)
    # ------------------------------------------------------------------

    async def sack_driver(
        self,
        server_id: int,
        driver_profile_id: int,
        season_id: int | None,
        acting_user_id: int,
        acting_user_name: str,
        guild: discord.Guild,
        discord_user_id: str,
    ) -> None:
        """Sack a driver: revoke all roles, clear all assignments, transition to
        Not Signed Up. Applies former_driver rules for record retention.

        Raises ValueError for blocking conditions.
        """
        async with get_connection(self._db_path) as db:
            # Validate state
            cursor = await db.execute(
                "SELECT current_state, former_driver FROM driver_profiles "
                "WHERE id = ? AND server_id = ?",
                (driver_profile_id, server_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("Driver profile not found.")
            current_state = DriverState(row["current_state"])
            former_driver = bool(row["former_driver"])
            if current_state not in (DriverState.UNASSIGNED, DriverState.ASSIGNED):
                raise ValueError(
                    f"Driver must be Unassigned or Assigned to be sacked "
                    f"(current state: {current_state.value})."
                )

            now = datetime.now(timezone.utc).isoformat()

            # Fetch current division assignments for the audit log
            if season_id is not None:
                cursor = await db.execute(
                    "SELECT division_id FROM driver_season_assignments "
                    "WHERE driver_profile_id = ? AND season_id = ?",
                    (driver_profile_id, season_id),
                )
                asgn_rows = await cursor.fetchall()
                division_ids = [r["division_id"] for r in asgn_rows]
            else:
                division_ids = []

        # Revoke all roles before DB mutation (needs guild lookup)
        member = guild.get_member(int(discord_user_id))
        if member is None:
            try:
                member = await guild.fetch_member(int(discord_user_id))
            except discord.HTTPException:
                member = None

        if member is not None:
            if season_id is not None:
                await self.revoke_all_placement_roles(server_id, driver_profile_id, season_id, member)
            # Revoke the signed-up role granted at approval
            async with get_connection(self._db_path) as db:
                cur = await db.execute(
                    "SELECT signed_up_role_id FROM signup_module_config WHERE server_id = ?",
                    (server_id,),
                )
                cfg_row = await cur.fetchone()
            if cfg_row and cfg_row["signed_up_role_id"]:
                signed_up_role = guild.get_role(cfg_row["signed_up_role_id"])
                if signed_up_role is not None and signed_up_role in member.roles:
                    await self._revoke_roles(member, signed_up_role.id)

        async with get_connection(self._db_path) as db:
            # Free all occupied seats
            await db.execute(
                "UPDATE team_seats SET driver_profile_id = NULL "
                "WHERE driver_profile_id = ?",
                (driver_profile_id,),
            )
            # Delete all season assignments
            if season_id is not None:
                await db.execute(
                    "DELETE FROM driver_season_assignments "
                    "WHERE driver_profile_id = ? AND season_id = ?",
                    (driver_profile_id, season_id),
                )
            # Transition to NOT_SIGNED_UP per constitution rules
            if former_driver:
                # Retain profile row; null signup record fields
                await db.execute(
                    "UPDATE driver_profiles SET current_state = ? WHERE id = ?",
                    (DriverState.NOT_SIGNED_UP.value, driver_profile_id),
                )
                await db.execute(
                    """
                    UPDATE signup_records
                    SET discord_username = NULL, server_display_name = NULL,
                        nationality = NULL, platform = NULL, platform_id = NULL,
                        availability_slot_ids = NULL, driver_type = NULL,
                        preferred_teams = NULL, preferred_teammate = NULL,
                        lap_times_json = NULL, notes = NULL, total_lap_ms = NULL,
                        updated_at = datetime('now')
                    WHERE server_id = ? AND discord_user_id = ?
                    """,
                    (server_id, discord_user_id),
                )
            else:
                # Delete profile atomically
                await db.execute(
                    "DELETE FROM driver_profiles WHERE id = ?", (driver_profile_id,)
                )

            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, NULL, 'DRIVER_SACK', ?, ?, ?)",
                (
                    server_id,
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

    # ------------------------------------------------------------------
    # Division resolution helper (used by cogs)
    # ------------------------------------------------------------------

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
