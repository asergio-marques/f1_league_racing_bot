"""PlacementService — driver placement, role management, and seeded listing."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import discord

from db.database import get_connection
from models.driver_profile import DriverProfile, DriverState
from models.signup_module import AvailabilitySlot
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
    # Export unassigned drivers (T017)
    # ------------------------------------------------------------------

    async def get_unassigned_drivers_for_export(
        self, server_id: int, slots: list[AvailabilitySlot]
    ) -> list[dict]:
        """Return all Unassigned drivers seeded, each row enriched for CSV export.

        Each row dict contains:
          seed, display_name, discord_user_id, driver_type, total_lap_fmt,
          slot_presence (dict {slot_sequence_id: bool}),
          preferred_team_1, preferred_team_2, preferred_team_3,
          platform, platform_id
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT
                    dp.discord_user_id,
                    sr.server_display_name,
                    sr.discord_username,
                    sr.platform,
                    sr.platform_id,
                    sr.availability_slot_ids,
                    sr.driver_type,
                    sr.preferred_teams,
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

        slots_ordered = sorted(slots, key=lambda s: s.slot_sequence_id)
        results = []
        for i, row in enumerate(rows, start=1):
            total_ms = row["total_lap_ms"]
            slot_ids_raw: list[int] = json.loads(row["availability_slot_ids"] or "[]")
            slot_presence = {s.slot_sequence_id: (s.slot_sequence_id in slot_ids_raw) for s in slots_ordered}

            preferred_teams_raw: list[str] = json.loads(row["preferred_teams"] or "[]")
            preferred_team_1 = preferred_teams_raw[0] if len(preferred_teams_raw) > 0 else ""
            preferred_team_2 = preferred_teams_raw[1] if len(preferred_teams_raw) > 1 else ""
            preferred_team_3 = preferred_teams_raw[2] if len(preferred_teams_raw) > 2 else ""

            display_name = row["server_display_name"] or row["discord_username"] or row["discord_user_id"]
            results.append({
                "seed": i,
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

    async def _guard_reserve_capacity(self, server_id: int, division_id: int) -> None:
        """Refuse a reserve placement that would outgrow the lineup template (FR/R8).

        The reserve block is the one lineup collection whose slots the template fixes, so
        it is the one to which overflow applies. XIV.12 requires overflow to be rejected
        at the earliest moment it can be detected, with the change unapplied — which is
        this command, not the render.

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

            if not await lineup_enabled(bot, server_id):
                return

            reports = await bot.image_validity_service.template_reports(server_id)
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
            problem = reserve_capacity_problem(load_svg(report.resolved_path), seated + 1)
        except Exception as exc:  # noqa: BLE001
            log.error("reserve capacity guard could not run: %s", exc)
            return

        if problem is not None:
            raise ValueError(
                f"{problem}. The driver was **not** assigned. Enlarge the template, or "
                f"turn the `lineup` image aspect off with `/images config toggle`."
            )

    async def _guard_sheet_capacity(self, server_id: int, division_id: int) -> None:
        """Refuse a placement that would outgrow the attendance sheet template (FR-042).

        The sheet draws every driver of the division, and its rows are counted from the file
        rather than declared as a number — so this reads the configured template exactly as
        the reserve guard does, and for the same reason: XIV.12 rejects overflow at the
        earliest moment it can be detected, with the change unapplied. That moment is this
        command. Discovering it at a posting means the league has already lost its sheet.

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

            if not await attendance_enabled(bot, server_id):
                return

            reports = await bot.image_validity_service.template_reports(server_id)
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
                "attendance_template", load_svg(report.resolved_path), seated + 1
            )
        except Exception as exc:  # noqa: BLE001
            log.error("attendance sheet capacity guard could not run: %s", exc)
            return

        if problem is not None:
            raise ValueError(
                f"{problem}. The driver was **not** assigned. Enlarge the template, or "
                f"the sheet would silently drop a driver."
            )

    async def _guard_standings_capacity(self, server_id: int, division_id: int) -> None:
        """Refuse a placement that would outgrow the driver standings template (FR-044).

        The standings draw every driver of the division's classification, and their rows are
        counted from the file rather than declared as a number — so this reads the configured
        template exactly as the reserve and sheet guards do, and for the same reason: XIV.12
        rejects overflow at the earliest moment it can be detected, with the change unapplied.

        The **constructors** ceiling is not checked here. Seating a driver adds no team, so no
        driver assignment can breach it; it is checked at ``/season review``, which is where a
        division's team count is settled.

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

            if not await standings_enabled(bot, server_id, DRIVERS_TEMPLATE_KEY):
                return

            reports = await bot.image_validity_service.template_reports(server_id)
            report = reports.get(DRIVERS_TEMPLATE_KEY)
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
                DRIVERS_TEMPLATE_KEY, load_svg(report.resolved_path), seated + 1
            )
        except Exception as exc:  # noqa: BLE001
            log.error("standings capacity guard could not run: %s", exc)
            return

        if problem is not None:
            raise ValueError(
                f"{problem}. The driver was **not** assigned. Enlarge the template, or "
                f"the standings would silently drop a driver."
            )

    async def _guard_image_capacity(
        self, server_id: int, division_id: int, season_id: int
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
        await self._guard_reserve_capacity(server_id, division_id)

        # The attendance sheet's rows are likewise counted from the template rather than
        # declared as a number, so they are invisible to ``declared_capacities()`` below.
        await self._guard_sheet_capacity(server_id, division_id)

        # And so are the driver standings' — both standings catalogues declare
        # ``capacity=None`` and derive their rows from the file.
        await self._guard_standings_capacity(server_id, division_id)

        capacities = declared_capacities()
        if not capacities:
            return

        try:
            if not await ModuleService(self._db_path).is_images_enabled(server_id):
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
        season_state: str = "ACTIVE",
    ) -> dict:
        """Assign a driver to a team seat in a division.

        Returns a summary dict with keys: was_unassigned, team_name, division_name.
        Raises ValueError for all blocking conditions.
        """
        # A command that would carry a division past what its configured templates can
        # draw is refused here, with the change not applied (Constitution XIV.12,
        # FR-028). This is the single choke point through which a driver enters a
        # division, so guarding it covers the signup wizard, manual placement and bulk
        # import alike. Inert while every catalogue is empty.
        await self._guard_image_capacity(server_id, division_id, season_id)

        async with get_connection(self._db_path) as db:
            # 1. Fetch profile and validate state
            cursor = await db.execute(
                "SELECT current_state, is_test_driver FROM driver_profiles WHERE id = ? AND server_id = ?",
                (driver_profile_id, server_id),
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

        if member is not None and not is_test_driver:
            if season_state == "ACTIVE":
                role_ids_to_grant = [div_role_id]
                team_cfg = await self.get_team_role_config(server_id, team_name)
                if team_cfg is not None:
                    role_ids_to_grant.append(team_cfg.role_id)
                await self._grant_roles(member, *role_ids_to_grant)

        if guild is not None:
            await self._refresh_lineup_post(guild, division_id)
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
        season_state: str = "ACTIVE",
    ) -> dict:
        """Remove a driver's assignment from one division.

        Returns a summary dict: division_name, has_remaining_assignments.
        Raises ValueError for blocking conditions.
        """
        async with get_connection(self._db_path) as db:
            # 1. Validate driver state
            cursor = await db.execute(
                "SELECT current_state, is_test_driver FROM driver_profiles WHERE id = ? AND server_id = ?",
                (driver_profile_id, server_id),
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

        if member is not None and not is_test_driver:
            if season_state == "ACTIVE":
                roles_to_revoke = [div_role_id]
                if team_role_id_to_revoke is not None:
                    roles_to_revoke.append(team_role_id_to_revoke)
                await self._revoke_roles(member, *roles_to_revoke)

        if guild is not None:
            await self._refresh_lineup_post(guild, division_id)
        return {"division_name": div_name, "has_remaining_assignments": has_remaining, "team_name": team_name}

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
                "SELECT current_state, former_driver, is_test_driver FROM driver_profiles "
                "WHERE id = ? AND server_id = ?",
                (driver_profile_id, server_id),
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

        if member is not None and not is_test_driver:
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
                # Delete attendance history rows (driver_round_attendance has a NOT NULL
                # FK to driver_profiles with no ON DELETE CASCADE — must clean up first).
                await db.execute(
                    "DELETE FROM driver_round_attendance WHERE driver_profile_id = ?",
                    (driver_profile_id,),
                )
                # NULL-out soft FK references in historical result/standings rows so the
                # profile row itself can be removed without violating FK constraints.
                await db.execute(
                    "UPDATE race_session_results SET driver_profile_id = NULL "
                    "WHERE driver_profile_id = ?",
                    (driver_profile_id,),
                )
                await db.execute(
                    "UPDATE qualifying_session_results SET driver_profile_id = NULL "
                    "WHERE driver_profile_id = ?",
                    (driver_profile_id,),
                )
                await db.execute(
                    "UPDATE driver_standings_snapshots SET driver_profile_id = NULL "
                    "WHERE driver_profile_id = ?",
                    (driver_profile_id,),
                )
                await db.execute(
                    "UPDATE signup_records SET driver_profile_id = NULL "
                    "WHERE driver_profile_id = ?",
                    (driver_profile_id,),
                )
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

        # Refresh lineup post for each division the driver was removed from
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
                "SELECT s.server_id, d.name AS div_name, d.lineup_channel_id, d.lineup_message_id "
                "FROM divisions d JOIN seasons s ON s.id = d.season_id WHERE d.id = ?",
                (division_id,),
            )
            div_row = await cur.fetchone()

        if div_row is None or div_row["lineup_channel_id"] is None:
            return

        lineup_channel_id: int = div_row["lineup_channel_id"]
        lineup_message_id: int | None = div_row["lineup_message_id"]
        div_name: str = div_row["div_name"] or str(division_id)
        server_id: int = div_row["server_id"]

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
                WHERE dsa.division_id = ? AND dp.current_state = 'ASSIGNED'
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
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, ?, 'SIGNUP_LINEUP_POSTED', '', ?, ?)",
                (server_id, 0, "system", division_id,
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
