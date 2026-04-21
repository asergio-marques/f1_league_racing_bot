"""season_end_service — automatic season completion and archival.

Two entry points:

check_and_schedule_season_end(server_id, bot)
    Called after every Phase 3 completion.  Checks whether all non-Mystery
    rounds in the active season are done; if so, schedules execute_season_end
    to fire 7 days after the latest round's scheduled_at.

execute_season_end(server_id, season_id, bot)
    Archives the season (status → COMPLETED), writes DriverHistoryEntry
    records for every assigned driver, and announces completion in the log
    channel.  All season data is permanently retained.
    Idempotent: a no-op if no active season is found (handles duplicate calls).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from db.database import get_connection

if TYPE_CHECKING:
    import discord
    from discord.ext.commands import Bot
    from models.season import Season

log = logging.getLogger(__name__)


async def check_and_schedule_season_end(
    server_id: int,
    bot: "Bot",
    *,
    now: datetime | None = None,
) -> None:
    """Schedule season end if all non-Mystery rounds are fully phased.

    If *now* is provided it is used instead of the real wall-clock time (useful
    for testing and for the startup recovery path).  When the computed fire time
    is already in the past (``now >= fire_at``), ``execute_season_end`` is
    called directly instead of scheduling a future job.

    Safe to call multiple times — ``replace_existing=True`` in the scheduler
    means a duplicate call simply refreshes the job's fire time.
    """
    season_svc = bot.season_service  # type: ignore[attr-defined]

    if not await season_svc.all_phases_complete(server_id):
        return  # Some phases are still pending

    season = await season_svc.get_active_season(server_id)
    if season is None:
        return  # Already cleaned up or never activated

    last_at = await season_svc.get_last_scheduled_at(server_id)
    if last_at is None:
        log.warning(
            "check_and_schedule_season_end: no rounds found for server %s", server_id
        )
        return

    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)

    fire_at = last_at + timedelta(days=7)

    season_id_captured = season.id
    effective_now = now if now is not None else datetime.now(tz=timezone.utc)
    if effective_now.tzinfo is None:
        effective_now = effective_now.replace(tzinfo=timezone.utc)

    if effective_now >= fire_at:
        # Due date already passed (e.g. bot was down for >7 days); fire now.
        log.warning(
            "Season end for server %s (season %s) is overdue (fire_at=%s); "
            "executing immediately.",
            server_id,
            season_id_captured,
            fire_at.isoformat(),
        )
        await execute_season_end(server_id, season_id_captured, bot)
        return

    bot.scheduler_service.schedule_season_end(  # type: ignore[attr-defined]
        server_id, fire_at, season_id_captured
    )
    log.info(
        "Season end for server %s (season %s) scheduled at %s",
        server_id,
        season_id_captured,
        fire_at.isoformat(),
    )


async def execute_season_end(server_id: int, season_id: int, bot: "Bot") -> None:
    """Archive the season and announce completion in the log channel.

    All season data is permanently retained (status → COMPLETED).
    Idempotent: returns immediately if no active season is found for the server.
    """
    season_svc = bot.season_service  # type: ignore[attr-defined]

    # Idempotency guard: verify the season still exists and is active
    season = await season_svc.get_active_season(server_id)
    if season is None:
        log.info(
            "execute_season_end: no active season for server %s — already archived.",
            server_id,
        )
        return

    # Cancel any pending season-end scheduler job (no-op if already fired)
    bot.scheduler_service.cancel_season_end(server_id)  # type: ignore[attr-defined]

    # Revoke division, team, and signup roles from all assigned drivers
    guild = bot.get_guild(server_id)  # type: ignore[attr-defined]
    if guild is not None:
        await _revoke_season_roles(server_id, season.id, guild, bot)

    # Write DriverHistoryEntry records for every assigned driver before archiving
    await _write_driver_history_entries(season, bot)

    # Archive: flip status to COMPLETED (all data retained)
    await season_svc.complete_season(season.id)

    # Announce completion
    completion_msg = (
        f"System | Season {season.season_number} complete | Success"
    )
    await bot.output_router.post_log(server_id, completion_msg)  # type: ignore[attr-defined]

    log.info(
        "Season %s for server %s archived (COMPLETED).",
        season_id,
        server_id,
    )


async def _write_driver_history_entries(season: "Season", bot: "Bot") -> None:
    """Write a DriverHistoryEntry for every ASSIGNED driver at season end.

    Sources:
    - season_number, division_name, division_tier: from the season/division rows
    - final_position, final_points: from the most recent driver_standings_snapshots row
    - points_gap_to_winner: derived from final points vs the division winner's final points
    """
    db_path: str = bot.db_path  # type: ignore[attr-defined]

    async with get_connection(db_path) as db:
        # Load all ASSIGNED driver × division pairs for this season
        cursor = await db.execute(
            """
            SELECT dsa.driver_profile_id,
                   d.id   AS division_id,
                   d.name AS division_name,
                   d.tier AS division_tier
            FROM driver_season_assignments dsa
            JOIN divisions d ON d.id = dsa.division_id
            WHERE d.season_id = ?
            """,
            (season.id,),
        )
        assignments = await cursor.fetchall()

        if not assignments:
            log.info(
                "_write_driver_history_entries: no assignments for season %s — skipping.",
                season.id,
            )
            return

        # For each division, determine the winner's final points (max at last round)
        division_ids = list({row["division_id"] for row in assignments})
        division_winner_points: dict[int, int] = {}
        for div_id in division_ids:
            cursor = await db.execute(
                """
                SELECT MAX(dss.total_points)
                FROM driver_standings_snapshots dss
                WHERE dss.division_id = ?
                  AND dss.round_id = (
                      SELECT dss2.round_id
                      FROM driver_standings_snapshots dss2
                      JOIN rounds r ON r.id = dss2.round_id
                      WHERE dss2.division_id = ?
                      ORDER BY r.round_number DESC
                      LIMIT 1
                  )
                """,
                (div_id, div_id),
            )
            row = await cursor.fetchone()
            division_winner_points[div_id] = (row[0] if row and row[0] is not None else 0)

        # Write a history entry for each driver × division
        for asgn in assignments:
            driver_profile_id = asgn["driver_profile_id"]
            div_id = asgn["division_id"]
            div_name = asgn["division_name"]
            div_tier = asgn["division_tier"] or 0

            # Fetch the most recent standings snapshot for this driver × division
            cursor = await db.execute(
                """
                SELECT dss.total_points, dss.standing_position
                FROM driver_standings_snapshots dss
                JOIN rounds r ON r.id = dss.round_id
                JOIN driver_profiles dp ON CAST(dp.discord_user_id AS INTEGER) = dss.driver_user_id
                WHERE dss.division_id = ? AND dp.id = ?
                ORDER BY r.round_number DESC
                LIMIT 1
                """,
                (div_id, driver_profile_id),
            )
            snap = await cursor.fetchone()
            final_points = snap["total_points"] if snap else 0
            final_position = snap["standing_position"] if snap else 0

            winner_points = division_winner_points.get(div_id, 0)
            points_gap = winner_points - final_points

            await db.execute(
                """
                INSERT INTO driver_history_entries
                    (driver_profile_id, season_number, division_name, division_tier,
                     final_position, final_points, points_gap_to_winner)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    driver_profile_id,
                    season.season_number,
                    div_name,
                    div_tier,
                    final_position,
                    final_points,
                    points_gap,
                ),
            )

        await db.commit()
        log.info(
            "_write_driver_history_entries: wrote %d entries for season %s.",
            len(assignments),
            season.id,
        )


async def _revoke_season_roles(
    server_id: int,
    season_id: int,
    guild: "discord.Guild",
    bot: "Bot",
) -> None:
    """Revoke division roles, team roles, and the signup 'signed-up' role from
    every non-test driver assigned in *season_id*.

    Called on both season completion and cancellation.  All failures are logged
    but do not abort the operation.
    """
    import discord

    placement_svc = bot.placement_service  # type: ignore[attr-defined]

    async with get_connection(bot.db_path) as db:  # type: ignore[attr-defined]
        cur = await db.execute(
            """
            SELECT DISTINCT dp.id AS driver_profile_id,
                            CAST(dp.discord_user_id AS INTEGER) AS discord_user_id
            FROM driver_season_assignments dsa
            JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id
            JOIN divisions d ON d.id = dsa.division_id
            WHERE d.season_id = ? AND dp.is_test_driver = 0
            """,
            (season_id,),
        )
        assigned_rows = await cur.fetchall()

        # Fetch the signed-up role once for the whole loop
        cfg_cur = await db.execute(
            "SELECT signed_up_role_id FROM signup_module_config WHERE server_id = ?",
            (server_id,),
        )
        cfg_row = await cfg_cur.fetchone()

    signed_up_role_id: int | None = cfg_row["signed_up_role_id"] if cfg_row else None

    for row in assigned_rows:
        discord_uid: int = row["discord_user_id"]
        driver_profile_id: int = row["driver_profile_id"]

        member = guild.get_member(discord_uid) or None
        if member is None:
            try:
                member = await guild.fetch_member(discord_uid)
            except discord.HTTPException:
                log.warning(
                    "_revoke_season_roles: member %d not found in guild %d — skipping",
                    discord_uid, server_id,
                )
                continue

        await placement_svc.revoke_all_placement_roles(
            server_id, driver_profile_id, season_id, member
        )
        if signed_up_role_id is not None:
            signed_up_role = guild.get_role(signed_up_role_id)
            if signed_up_role is not None and signed_up_role in member.roles:
                await placement_svc._revoke_roles(member, signed_up_role_id)

    log.info(
        "_revoke_season_roles: processed %d driver(s) for season %d",
        len(assigned_rows),
        season_id,
    )
