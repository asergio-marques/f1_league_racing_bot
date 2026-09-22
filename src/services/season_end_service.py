"""season_end_service — season completion and archival.

One entry point:

execute_season_end(season_id, bot)
    Archives the season (status → COMPLETED), writes DriverHistoryEntry
    records for every assigned driver, posts each division's final standings
    and attendance sheet, and announces completion in the log channel.  All
    season data is permanently retained.
    Idempotent: a no-op if no active season is found (handles duplicate calls).

A season ends when a league manager runs `/season complete`, and in no other way. There was once
a second entry point, `check_and_schedule_season_end`, which armed a timer to end a season seven
days after its last round. Nothing ever called it — `_recover_season_end_jobs` was a documented
no-op and `/season complete` calls `execute_season_end` directly — so it sat unreachable while
reading as live code, and it misled the README into describing automatic completion until
2026-08-17. It was deleted with issue #154, whose lifecycle settles the question the other way:
a season is completed explicitly, once every division is finished or cancelled.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from db.database import get_connection
from utils.league_server import league_guild

if TYPE_CHECKING:
    import discord
    from discord.ext.commands import Bot
    from models.season import Season

log = logging.getLogger(__name__)


async def execute_season_end(season_id: int, bot: "Bot") -> None:
    """Archive the season and announce completion in the log channel.

    All season data is permanently retained (status → COMPLETED).
    Idempotent: returns immediately if no active season is found for the server.
    """
    season_svc = bot.season_service  # type: ignore[attr-defined]

    # Idempotency guard: verify the season still exists and is active
    season = await season_svc.get_confirmed_season()
    if season is None:
        log.info(
            "execute_season_end: no active season — already archived.",
        )
        return

    # Cancel any pending season-end scheduler job (no-op if already fired)
    bot.scheduler_service.cancel_season_end()  # type: ignore[attr-defined]

    guild = await league_guild(bot)  # type: ignore[attr-defined]

    # The season's end, in the order the core specification sets (issue #220).
    # 1. The final classification, per division. The season's last word: each division's
    #    standings and attendance record posted once more, headed `Final Classification`, as
    #    graphics with no text above them. Posted while the season is still active, because
    #    everything downstream of here reads it as the live one. A failure never blocks the
    #    archival — the season completes either way, and a picture is not what the completion
    #    is for (XIV.7).
    if guild is not None:
        from services import season_classification_service as classification

        try:
            problems = await classification.post_final_classifications(
                bot, guild, bot.db_path, season.id
            )
        except Exception:  # noqa: BLE001 — never fail an archival on a picture
            log.exception("execute_season_end: the final classifications failed")
            problems = []

        if problems:
            log.error(
                "execute_season_end: final classification problems - %s",
                "; ".join(problems),
            )
            try:
                await bot.output_router.post_log(  # type: ignore[attr-defined]
                    "\n".join(
                        ["System | Season complete | Final classification", *(
                            f"    - {line}" for line in problems
                        )]
                    ),
                )
            except Exception:  # noqa: BLE001
                log.exception(
                    "execute_season_end: could not post the final classification report"
                )

    # 2. History entries, for every driver holding a committed placement.
    await _write_driver_history_entries(season, bot)

    # 3. The division and team roles, and the driver role, of the season's drivers.
    if guild is not None:
        await _revoke_season_roles(season.id, guild, bot)

    # 4-6. The signup window, the driver pass and test mode, shared with cancelling. The
    # saved test-mode backup goes too: the season it belonged to has been run to its end.
    await end_of_season_pass(bot, guild, discard_backup=True)

    # 7. Archive: flip status to COMPLETED (all data retained)
    await season_svc.complete_season(season.id)

    # Announce completion
    completion_msg = (
        f"System | Season {season.season_number} complete | Success"
    )
    await bot.output_router.post_log(completion_msg)  # type: ignore[attr-defined]

    log.info(
        "Season %s archived (COMPLETED).",
        season_id,
    )


async def end_of_season_pass(
    bot: "Bot", guild, *, discard_backup: bool = False
) -> dict:
    """The driver pass, the signup window and test mode: what every end of a season does (#220).

    Shared by completing, cancelling and aborting a season, in that order within each:

    - the signup window closed, where one stands open — first, so that nobody begins a signup
      the driver pass has already gone by;
    - the driver pass, returning the season's drivers to Not Signed Up and deleting those
      pending deletion;
    - test mode switched off, deleting every driver it created and keeping their history.

    Each step is fail-soft against the next: a window that cannot be closed does not keep a
    server in test mode. Returns what the driver pass reported.

    *discard_backup* deletes the saved test-mode backup with it, which **completing** a season
    passes and cancelling or aborting one does not (decided 2026-09-17): a season run to its end
    leaves a state nothing could restore, where an abandoned one leaves the state a maintainer
    goes back to.
    """
    from services.season_lifecycle_service import run_driver_pass
    from services.test_mode_service import switch_test_mode_off

    try:
        signup_cfg = await bot.signup_module_service.get_config()  # type: ignore[attr-defined]
        if signup_cfg is not None and signup_cfg.signups_open:
            from cogs.module_cog import execute_forced_close

            await execute_forced_close(bot, audit_action="SIGNUP_SEASON_END_CLOSE")
    except Exception:  # noqa: BLE001
        log.exception("end_of_season_pass: could not close the signup window")

    result = await run_driver_pass(bot.db_path, bot=bot, guild=guild)

    try:
        await switch_test_mode_off(bot, discard_backup=discard_backup)
    except Exception:  # noqa: BLE001
        log.exception("end_of_season_pass: could not switch test mode off")

    return result


async def _write_driver_history_entries(
    season: "Season", bot: "Bot", *, force_cancelled: bool = False
) -> None:
    """Write a DriverHistoryEntry for every division each driver took part in during the season.

    A driver took part in a division once a placement of theirs in it was committed, and
    stays part of it whatever becomes of the placement afterwards: a driver moved, released
    or sacked mid-season holds an entry for every division they raced in, as the season's
    history lists them (issue #220). Read from ``driver_division_memberships``, which records
    each committed placement as it is committed and is never cleared by a placement changing.

    Sources:
    - season_number, division_name, division_tier: from the season/division rows
    - final_position, final_points: from the most recent driver_standings_snapshots row
    - points_gap_to_winner: derived from final points vs the division winner's final points
    - cancelled: whether the driver's division was cancelled

    **Idempotent, and deliberately so.** Ending a season is a run of separate writes with no
    transaction around them, and the season's own row is flipped last — so a process that dies
    part-way leaves the season still ACTIVE with its history already written, and the retry the
    league is told to run would append a second set. `INSERT OR IGNORE` against the unique index
    from migration 053 is what stands in for the atomicity the sequence does not have. Do not
    relax it to a plain INSERT on the grounds that the guard above already returns early: that
    guard reads the season *before* this runs, which is precisely the window that fails.

    *force_cancelled* marks every entry cancelled without consulting the divisions. `/season
    cancel` needs it because it writes history **before** cascading — writing afterwards would
    read the right statuses but put the rows beyond reach of a retry, since the command refuses
    once the season is no longer active, and a failure between the two would lose them for good.
    """
    db_path: str = bot.db_path  # type: ignore[attr-defined]

    async with get_connection(db_path) as db:
        # Every driver × division a committed placement was ever held in this season.
        cursor = await db.execute(
            """
            SELECT m.driver_profile_id,
                   dp.discord_user_id,
                   d.id     AS division_id,
                   d.name   AS division_name,
                   d.tier   AS division_tier,
                   d.status AS division_status
            FROM driver_division_memberships m
            JOIN divisions d ON d.id = m.division_id
            JOIN driver_profiles dp ON dp.id = m.driver_profile_id
            WHERE m.season_id = ?
            ORDER BY m.id
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
            # Cancellation reaches a driver only through their division: cancelling a
            # season cancels each of its divisions first, so the division's status is
            # the whole answer however the cancellation was ordered.
            cancelled = 1 if (force_cancelled or asgn["division_status"] == "CANCELLED") else 0

            # Fetch the most recent standings snapshot for this driver × division, under
            # whichever of their accounts it stands (issue #243): a driver who changed
            # account after the last round finished it under the old one.
            cursor = await db.execute(
                """
                SELECT dss.total_points, dss.standing_position
                FROM driver_standings_snapshots dss
                JOIN rounds r ON r.id = dss.round_id
                JOIN driver_accounts da
                  ON CAST(da.discord_user_id AS INTEGER) = dss.driver_user_id
                WHERE dss.division_id = ? AND da.driver_profile_id = ?
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
                INSERT OR IGNORE INTO driver_history_entries
                    (discord_user_id, driver_profile_id, season_number,
                     division_name, division_tier, final_position, final_points,
                     points_gap_to_winner, cancelled)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asgn["discord_user_id"],
                    driver_profile_id,
                    season.season_number,
                    div_name,
                    div_tier,
                    final_position,
                    final_points,
                    points_gap,
                    cancelled,
                ),
            )

        await db.commit()
        log.info(
            "_write_driver_history_entries: wrote %d entries for season %s.",
            len(assignments),
            season.id,
        )


async def _revoke_season_roles(
    season_id: int,
    guild: "discord.Guild",
    bot: "Bot",
) -> None:
    """Revoke division roles, team roles, and the league's driver role from
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

        # Fetch the driver role once for the whole loop
        cfg_cur = await db.execute("SELECT driver_role_id FROM server_configs")
        cfg_row = await cfg_cur.fetchone()

    driver_role_id: int | None = cfg_row["driver_role_id"] if cfg_row else None

    for row in assigned_rows:
        discord_uid: int = row["discord_user_id"]
        driver_profile_id: int = row["driver_profile_id"]

        member = guild.get_member(discord_uid) or None
        if member is None:
            try:
                member = await guild.fetch_member(discord_uid)
            except discord.HTTPException:
                log.warning(
                    "_revoke_season_roles: member %d not found — skipping",
                    discord_uid,
                )
                continue

        await placement_svc.revoke_all_placement_roles(
            driver_profile_id, season_id, member
        )
        if driver_role_id is not None:
            driver_role = guild.get_role(driver_role_id)
            if driver_role is not None and driver_role in member.roles:
                await placement_svc._revoke_roles(member, driver_role_id)

    log.info(
        "_revoke_season_roles: processed %d driver(s) for season %d",
        len(assigned_rows),
        season_id,
    )
