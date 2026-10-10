"""season_end_service — season completion and archival.

One entry point:

execute_season_end(season_id, bot, *, actor)
    Archives the season (status → COMPLETED), writes DriverHistoryEntry
    records for every assigned driver, posts each division's final standings
    and attendance sheet.  The caller records the outcome.  All
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

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import StepKind, StepResult
from leaguebot.core.services.change_queue import Step, StepContext
from leaguebot.core.services.season_lifecycle_service import (
    SeasonEndHooks,
    close_signup_steps,
    close_signup_window,
    require_guild,
    window_closed_jobs,
)
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.member_names import member_named

if TYPE_CHECKING:
    import aiosqlite
    import discord
    from leaguebot.core.services.placement_service import PlacementService
    from leaguebot.core.utils.league_bot import LeagueBot
    from leaguebot.core.models.season import Season

log = logging.getLogger(__name__)

#: The jobs every end of a season shares, as the stop notice, the tests and ``StepView`` know them.
REVOKE_ROLES = "revoke_roles"
CLOSE_WINDOW = "close_window"
FLUSH_FORECASTS = "flush_forecasts"
DISCARD_PORTRAITS = "discard_portraits"
DISCARD_BACKUP = "discard_backup"
FORGET_SETUP = "forget_setup"

#: The cause the window's close is recorded under, where a season's end closes it.
SEASON_END_CAUSE = "season end"


async def execute_season_end(
    season_id: int, bot: "LeagueBot", *, actor: "discord.User | discord.Member"
) -> None:
    """Archive the season. The caller records the outcome.

    All season data is permanently retained (status → COMPLETED).
    Idempotent: returns immediately if no active season is found for the server.

    **This writes no success line** (#482): `/season complete`, the one caller, writes the
    command's own, naming the member, as soon as this returns. *actor* is the member who
    completes the season, named in the one line this does write, the report of a final
    classification that had problems, as every line names the member.
    """
    season_svc = bot.season_service

    # Idempotency guard: verify the season still exists and is active
    season = await season_svc.get_confirmed_season()
    if season is None:
        log.info(
            "execute_season_end: no active season — already archived.",
        )
        return

    # Cancel any pending season-end scheduler job (no-op if already fired)
    bot.scheduler_service.cancel_season_end()

    guild = await league_guild(bot)

    # The season's end, in the order the core specification sets (issue #220).
    # 1. The final classification, per division. The season's last word: each division's
    #    standings and attendance record posted once more, headed `Final Classification`, as
    #    graphics with no text above them. Posted while the season is still active, because
    #    everything downstream of here reads it as the live one. A failure never blocks the
    #    archival — the season completes either way, and a picture is not what the completion
    #    is for (XIV.7).
    if guild is not None:
        from leaguebot.core.services import season_classification_service as classification

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
                await bot.output_router.post_log(
                    "\n".join(
                        [
                            f"{member_named(getattr(actor, 'display_name', None), actor.id)}"
                            " | /season complete | Final classification",
                            *(
                                f"    - {line}" for line in problems
                            ),
                        ]
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

    log.info(
        "Season %s archived (COMPLETED).",
        season_id,
    )


async def end_of_season_pass(
    bot: "LeagueBot", guild, *, discard_backup: bool = False
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
    from leaguebot.core.services.season_lifecycle_service import run_driver_pass
    from leaguebot.core.services.test_mode_service import switch_test_mode_off

    try:
        signup_cfg = await bot.signup_module_service.get_config()
        if signup_cfg is not None and signup_cfg.signups_open:
            from leaguebot.core.cogs.module_cog import close_signups_unattended

            await close_signups_unattended(bot, cause="season end")
    except Exception:  # noqa: BLE001
        log.exception("end_of_season_pass: could not close the signup window")

    result = await run_driver_pass(bot.db_path, bot=bot, guild=guild)

    try:
        await switch_test_mode_off(bot, discard_backup=discard_backup)
    except Exception:  # noqa: BLE001
        log.exception("end_of_season_pass: could not switch test mode off")

    return result


async def write_driver_history_entries_on(
    db: "aiosqlite.Connection",
    season_id: int,
    season_number: int,
    *,
    force_cancelled: bool = False,
) -> None:
    """Write a DriverHistoryEntry for every division each driver took part in during the season.

    A driver took part in a division once a placement of theirs in it was committed, and
    stays part of it whatever becomes of the placement afterwards: a driver moved, released
    or sacked mid-season holds an entry for every division they raced in, as the season's
    history lists them (issue #220). Read from ``driver_division_memberships``, which records
    each committed placement as it is committed and is never cleared by a placement changing.

    Commits nothing: the caller's connection carries the write and the caller commits it.

    Sources:
    - season_number, division_name, division_tier: from the season/division rows
    - final_position, final_points: from the most recent driver_standings_snapshots row
    - points_gap_to_winner: derived from final points vs the division winner's final points
    - cancelled: whether the driver's division was cancelled

    **Idempotent, and deliberately so.** Ending a season is a run of separate writes with no
    transaction around them, and the season's own row is flipped last — so a process that dies
    part-way leaves the season still ACTIVE with its history already written, and the retry the
    league is told to run would append a second set. `INSERT OR IGNORE` against the unique index
    `idx_driver_history_unique` is what stands in for the atomicity the sequence does not have.
    Do not relax it to a plain INSERT on the grounds that the guard above already returns early:
    that guard reads the season *before* this runs, which is precisely the window that fails.

    *force_cancelled* marks every entry cancelled without consulting the divisions. `/season
    cancel` needs it because it writes history **before** cascading — writing afterwards would
    read the right statuses but put the rows beyond reach of a retry, since the command refuses
    once the season is no longer active, and a failure between the two would lose them for good.
    """
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
        (season_id,),
    )
    assignments = list(await cursor.fetchall())

    if not assignments:
        log.info(
            "write_driver_history_entries_on: no assignments for season %s — skipping.",
            season_id,
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
                season_number,
                div_name,
                div_tier,
                final_position,
                final_points,
                points_gap,
                cancelled,
            ),
        )

    log.info(
        "write_driver_history_entries_on: wrote %d entries for season %s.",
        len(assignments),
        season_id,
    )


async def _write_driver_history_entries(
    season: "Season", bot: "LeagueBot", *, force_cancelled: bool = False
) -> None:
    """Write the season's history entries and commit them."""
    async with get_connection(bot.db_path) as db:
        await write_driver_history_entries_on(
            db, season.id, season.season_number, force_cancelled=force_cancelled
        )
        await db.commit()


async def season_role_targets_on(db: aiosqlite.Connection, season_id: int) -> list[dict]:
    """Every real driver of *season_id* with the roles a season's end takes back, on *db*.

    Each entry is ``{"user_id": int, "role_ids": list[int]}``, in user-id order: the division
    role and the team role of each of the driver's placements in the season, and the league's
    driver role where one is set. Test drivers are skipped: no Discord member stands behind
    them. Which of these roles a member holds is Discord's to say, when
    :meth:`PlacementService.revoke_roles` takes them, so the driver role is named for every
    driver here and left alone for one who never held it.

    Read in the save that records the season's end and committing nothing: the roles are
    fixed there, and each driver's are taken back by a job of its own afterwards.
    """
    cursor = await db.execute(
        """
        SELECT DISTINCT dp.id AS driver_profile_id,
                        CAST(dp.discord_user_id AS INTEGER) AS user_id
        FROM driver_season_assignments dsa
        JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id
        JOIN divisions d ON d.id = dsa.division_id
        WHERE d.season_id = ? AND dp.is_test_driver = 0
        ORDER BY user_id
        """,
        (season_id,),
    )
    drivers = [dict(row) for row in await cursor.fetchall()]

    cursor = await db.execute("SELECT driver_role_id FROM server_configs")
    config = await cursor.fetchone()
    driver_role_id = config["driver_role_id"] if config else None

    targets: list[dict] = []
    for driver in drivers:
        cursor = await db.execute(
            """
            SELECT d.mention_role_id AS role_id
            FROM driver_season_assignments dsa
            JOIN divisions d ON d.id = dsa.division_id
            WHERE dsa.driver_profile_id = ? AND d.season_id = ?
            UNION
            SELECT trc.role_id
            FROM driver_season_assignments dsa
            JOIN divisions d ON d.id = dsa.division_id
            JOIN team_seats ts ON ts.id = dsa.team_seat_id
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            JOIN team_role_configs trc ON trc.team_name = ti.name
            WHERE dsa.driver_profile_id = ? AND d.season_id = ?
            """,
            (driver["driver_profile_id"], season_id) * 2,
        )
        role_ids = {row["role_id"] for row in await cursor.fetchall()}
        if driver_role_id is not None:
            role_ids.add(driver_role_id)
        targets.append(
            {"user_id": driver["user_id"], "role_ids": sorted(r for r in role_ids if r is not None)}
        )
    return targets


async def _revoke_season_roles(
    season_id: int,
    guild: "discord.Guild",
    bot: "LeagueBot",
) -> None:
    """Revoke division roles, team roles, and the league's driver role from
    every non-test driver assigned in *season_id*.

    Called on both season completion and cancellation.  All failures are logged
    but do not abort the operation.
    """
    import discord

    placement_svc = bot.placement_service

    async with get_connection(bot.db_path) as db:
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
        assigned_rows = list(await cur.fetchall())

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


def season_end_steps(placement: "PlacementService", hooks: SeasonEndHooks) -> dict[str, Step]:
    """The jobs the completion, the cancellation and the abort share (#439), each an ACT.

    - `revoke_roles`: one driver's division, team and driver roles taken back, from the targets
      the save read (`season_role_targets_on`). A member who left or a role gone is passed over.
    - `close_window`: the signup close timer cancelled and the window closed, where it stands open;
      it plans a `signup_notice` and a `close_signup` for each driver the close returned.
    - `flush_forecasts`: the forecasts posted under test mode deleted.
    - `discard_portraits`: the portraits of the drivers the driver pass deleted removed.
    - `discard_backup`: the saved test-mode state deleted (a completion's alone).
    - `forget_setup`: the setup held in memory let go of (an abort's alone).
    - the three jobs of one driver's Discord side (`close_signup_steps`).

    Every one raises where it fails, for the queue to stop on.
    """

    async def revoke_roles(ctx: StepContext) -> StepResult:
        guild = await require_guild(ctx.bot)
        each = ctx.step_payload
        revoked = await placement.revoke_roles(
            guild, int(each["user_id"]), *[int(r) for r in each["role_ids"]],
            reason=str(each.get("reason", "Season ended")),
        )
        return StepResult(result={"revoked": revoked})

    async def describe_revoke(ctx: StepContext) -> str:
        number = ctx.payload.get("season_number")
        of = f" of season {number}" if number else ""
        return f"taking back <@{ctx.step_payload['user_id']}>'s roles{of}"

    async def window_due(_ctx: StepContext) -> bool:
        return await hooks.window_open()

    async def close_window(ctx: StepContext) -> StepResult:
        returned = await close_signup_window(
            ctx.bot, hooks, str(ctx.step_payload.get("cause", SEASON_END_CAUSE))
        )
        return StepResult(result={"returned": list(returned)}, then=window_closed_jobs(returned))

    async def describe_window(ctx: StepContext) -> str:
        number = ctx.payload.get("season_number")
        return (
            f"closing the signup window as season {number} ends"
            if number
            else "closing the signup window as the season ends"
        )

    async def flush_forecasts(_ctx: StepContext) -> StepResult:
        await hooks.flush_forecasts()
        return StepResult()

    async def describe_flush(_ctx: StepContext) -> str:
        return "clearing the forecasts posted under test mode"

    async def discard_portraits(ctx: StepContext) -> StepResult:
        removed = await hooks.discard_portraits([str(a) for a in ctx.step_payload["accounts"]])
        # Not "discarded": that is the key a league admin's Discard leaves on a job's result.
        return StepResult(result={"removed": removed if isinstance(removed, int) else 0})

    async def describe_portraits(_ctx: StepContext) -> str:
        return "discarding the portraits of the drivers deleted"

    async def discard_backup(_ctx: StepContext) -> StepResult:
        hooks.discard_backup()
        return StepResult()

    async def describe_backup(_ctx: StepContext) -> str:
        return "deleting the saved test-mode state"

    async def forget_setup(_ctx: StepContext) -> StepResult:
        hooks.forget_setup()
        return StepResult()

    async def describe_setup(_ctx: StepContext) -> str:
        return "letting go of the setup the bot holds in memory"

    return {
        REVOKE_ROLES: Step(REVOKE_ROLES, StepKind.ACT, revoke_roles, describe=describe_revoke),
        CLOSE_WINDOW: Step(
            CLOSE_WINDOW, StepKind.ACT, close_window, still_due=window_due, describe=describe_window
        ),
        FLUSH_FORECASTS: Step(
            FLUSH_FORECASTS, StepKind.ACT, flush_forecasts, describe=describe_flush
        ),
        DISCARD_PORTRAITS: Step(
            DISCARD_PORTRAITS, StepKind.ACT, discard_portraits, describe=describe_portraits
        ),
        DISCARD_BACKUP: Step(
            DISCARD_BACKUP, StepKind.ACT, discard_backup, describe=describe_backup
        ),
        FORGET_SETUP: Step(FORGET_SETUP, StepKind.ACT, forget_setup, describe=describe_setup),
        **close_signup_steps(placement, hooks),
    }
