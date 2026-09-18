"""Moving a season through its lifecycle as the events that drive it happen (issue #220).

``SeasonService.set_stage`` enforces which transitions are legal. This module decides which
transition an event calls for — a signup window opening or closing, and later placements
being confirmed and a season ending — so that every path reaching the event (a command, a
timer, a restart) moves the season the same way.

It reads the database directly rather than through a bot, so the signup window's forced
close, which runs from a command, a scheduled job and the startup sweep alike, can call it
with nothing but a path.
"""
from __future__ import annotations

import json
import logging

from db.database import get_connection
from models.season import ONGOING_STAGES, InvalidStageTransition, SeasonStage, status_of_stage

log = logging.getLogger(__name__)

#: A signup is unsettled while its driver stands in one of these states: approved and not
#: yet placed, or still being judged. Closing a mid-season window with any of them standing
#: leaves placements to make.
UNSETTLED_STATES: tuple[str, ...] = (
    "UNASSIGNED",
    "PENDING_ADMIN_APPROVAL",
    "AWAITING_CORRECTION_PARAMETER",
    "PENDING_DRIVER_CORRECTION",
)

#: The stages a signup window may be opened from, and the stage opening it moves to.
WINDOW_OPENS_FROM: dict[SeasonStage, SeasonStage] = {
    SeasonStage.WAITING: SeasonStage.SIGNUPS,
    SeasonStage.ONGOING: SeasonStage.ONGOING_SIGNUPS,
}


def uncommitted_seat_excluded(seat_alias: str = "ts") -> str:
    """SQL predicate: the seat *seat_alias* is not held by an uncommitted driver mid-season.

    A driver placed in Ongoing, placements stands outside the championship until placements
    are confirmed (issue #220): no check-in, no attendance, no results, no standings, no
    lineup. Every reader of the championship that walks the seats adds this predicate.

    Written as the absence of an *uncommitted* placement in a confirmed season, rather than
    the presence of a committed one, so a seat whose occupant holds no placement row reads as
    it always did. In a season still in Placements every placement is uncommitted and nothing
    is being raced, so the predicate leaves those seats alone.
    """
    return (
        "NOT EXISTS (SELECT 1 FROM driver_season_assignments uc "
        "JOIN seasons ucs ON ucs.id = uc.season_id "
        f"WHERE uc.team_seat_id = {seat_alias}.id "
        f"AND uc.driver_profile_id = {seat_alias}.driver_profile_id "
        "AND uc.committed = 0 AND ucs.status = 'ACTIVE')"
    )


async def live_season_stage(db_path: str) -> tuple[int, SeasonStage] | None:
    """The server's active season and its stage, or None where it holds none."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id, stage FROM seasons "
            "WHERE status IN ('SETUP', 'ACTIVE') "
            "ORDER BY id DESC LIMIT 1",
        )
        row = await cursor.fetchone()
    if row is None or row["stage"] is None:
        return None
    return int(row["id"]), SeasonStage(row["stage"])


async def count_unsettled_signups(db_path: str) -> int:
    """How many drivers of *server_id* hold a signup not yet placed or turned down."""
    placeholders = ",".join("?" for _ in UNSETTLED_STATES)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT COUNT(*) AS n FROM driver_profiles "
            f"WHERE current_state IN ({placeholders})",
            (*UNSETTLED_STATES,),
        )
        row = await cursor.fetchone()
    return int(row["n"]) if row is not None else 0


async def _move(db_path: str, season_id: int, current: SeasonStage, target: SeasonStage) -> None:
    """Write the transition, conditioned on the stage that was read."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "UPDATE seasons SET status = ?, stage = ? WHERE id = ? AND stage = ?",
            (status_of_stage(target).value, target.value, season_id, current.value),
        )
        await db.commit()
    if cursor.rowcount == 0:
        raise InvalidStageTransition(
            f"season {season_id} left {current.value} before it could move to {target.value}"
        )


async def advance_on_window_open(db_path: str) -> SeasonStage | None:
    """Move the active season on for a signup window just opened.

    Waiting becomes Signups; Ongoing becomes Ongoing, signups open. Returns the new stage,
    or None where the season stood in neither and nothing was moved.
    """
    found = await live_season_stage(db_path)
    if found is None:
        return None
    season_id, stage = found
    target = WINDOW_OPENS_FROM.get(stage)
    if target is None:
        return None
    await _move(db_path, season_id, stage, target)
    return target


async def advance_on_window_close(db_path: str) -> SeasonStage | None:
    """Move the active season on for a signup window just closed.

    Signups becomes Placements. Ongoing, signups open becomes Ongoing, placements where any
    signup remains unsettled, and Ongoing where none does. Returns the new stage, or None
    where the season stood in neither and nothing was moved — a window force-closed by
    disabling the module, for one, belongs to no stage.
    """
    found = await live_season_stage(db_path)
    if found is None:
        return None
    season_id, stage = found
    if stage is SeasonStage.SIGNUPS:
        target = SeasonStage.PLACEMENTS
    elif stage is SeasonStage.ONGOING_SIGNUPS:
        unsettled = await count_unsettled_signups(db_path)
        target = SeasonStage.ONGOING_PLACEMENTS if unsettled else SeasonStage.ONGOING
    else:
        return None
    await _move(db_path, season_id, stage, target)
    if target is SeasonStage.ONGOING:
        await advance_to_pending_completion(db_path, season_id)
    return target


async def turn_down_pending_placements(bot, server_id: int, season_id: int, guild) -> list[int]:
    """Turn down every placement of *season_id* still pending, as the reject command would.

    Pending are the unsettled signups — Unassigned, awaiting approval or mid-correction — and
    every placement not yet committed. Each such placement is discarded, and each such driver
    returns to Not Signed Up: a signup in review has its channel closed, an approved driver
    loses the signed-up role. A driver without the former-driver flag is thereby pending
    deletion. Returns the profile ids turned down.
    """
    from models.driver_profile import DriverState
    from services.driver_service import write_transition

    db_path = bot.db_path
    placeholders = ",".join("?" for _ in UNSETTLED_STATES)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT id, discord_user_id, current_state, is_test_driver FROM driver_profiles "
            f"WHERE ("
            f"  current_state IN ({placeholders}) "
            f"  OR (current_state = 'ASSIGNED' AND id IN ("
            f"      SELECT driver_profile_id FROM driver_season_assignments "
            f"      WHERE season_id = ? AND committed = 0) "
            f"    AND id NOT IN ("
            f"      SELECT driver_profile_id FROM driver_season_assignments "
            f"      WHERE season_id = ? AND committed = 1))"
            f") ORDER BY id",
            (*UNSETTLED_STATES, season_id, season_id),
        )
        drivers = [dict(r) for r in await cursor.fetchall()]
        cursor = await db.execute(
            "SELECT signed_up_role_id FROM signup_module_config",
        )
        cfg_row = await cursor.fetchone()

    await _close_driver_signups(
        server_id, drivers, cfg_row["signed_up_role_id"] if cfg_row else None,
        bot=bot, guild=guild,
        notice="🔒 Every division of this season is done, so its signups are closed. "
        "This channel will be automatically deleted in 24 hours.",
        reason="Season's divisions done; signup turned down",
    )

    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE team_seats SET driver_profile_id = NULL WHERE id IN ("
            "  SELECT team_seat_id FROM driver_season_assignments "
            "  WHERE season_id = ? AND committed = 0 AND team_seat_id IS NOT NULL)",
            (season_id,),
        )
        await db.execute(
            "DELETE FROM driver_season_assignments WHERE season_id = ? AND committed = 0",
            (season_id,),
        )
        for driver in drivers:
            await write_transition(
                db, driver["id"], DriverState(driver["current_state"]), DriverState.NOT_SIGNED_UP
            )
        if drivers:
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, 0, 'system', NULL, 'PENDING_PLACEMENTS_TURNED_DOWN', ?, ?, datetime('now'))",
                (
                    server_id,
                    json.dumps({d["id"]: d["current_state"] for d in drivers}, sort_keys=True),
                    json.dumps({"state": "NOT_SIGNED_UP"}),
                ),
            )
        await db.commit()
    return [d["id"] for d in drivers]


async def wind_down_ongoing(bot, server_id: int) -> bool:
    """Take a season whose every division is done out of the ongoing stages (issue #220).

    A season in Ongoing, signups open or Ongoing, placements has no round left to place a
    driver into once every division is finished or cancelled. Its signup window is closed,
    every pending placement is turned down, and it moves straight to Pending completion. A
    season in plain Ongoing moves too. Called from everywhere a division can finish; a no-op
    for any other season. Returns True where the season was moved.
    """
    db_path = bot.db_path
    found = await live_season_stage(db_path)
    if found is None:
        return False
    season_id, stage = found
    if stage not in ONGOING_STAGES:
        return False
    _, done = await _stage_and_whether_done(db_path, season_id)
    if not done:
        return False

    guild = bot.get_guild(server_id)
    if stage is not SeasonStage.ONGOING:
        try:
            signup_cfg = await bot.signup_module_service.get_config()
            if signup_cfg is not None and signup_cfg.signups_open:
                from cogs.module_cog import execute_forced_close

                try:
                    bot.scheduler_service.cancel_signup_close_timer()
                except Exception:  # noqa: BLE001 — a timer already gone is the aim
                    pass
                await execute_forced_close(
                    server_id, bot, audit_action="SIGNUP_DIVISIONS_DONE_CLOSE"
                )
        except Exception:  # noqa: BLE001 — the window's close must not hold the season
            log.exception("wind_down_ongoing: could not close the signup window of %s", server_id)
        turned_down = await turn_down_pending_placements(bot, server_id, season_id, guild)
        current_stage, _ = await _stage_and_whether_done(db_path, season_id)
        if current_stage in (SeasonStage.ONGOING_SIGNUPS.value, SeasonStage.ONGOING_PLACEMENTS.value):
            await _move(db_path, season_id, SeasonStage(current_stage), SeasonStage.ONGOING)
        if turned_down:
            try:
                await bot.output_router.post_log(
                    server_id,
                    "System | Every division is done | Signups closed\n"
                    f"  pending placements turned down: {len(turned_down)}",
                )
            except Exception:  # noqa: BLE001
                log.exception("wind_down_ongoing: could not post the log line")
    await advance_to_pending_completion(db_path, season_id)
    final_stage, _ = await _stage_and_whether_done(db_path, season_id)
    return final_stage == SeasonStage.PENDING_COMPLETION.value


async def signup_configuration_fixed(db_path: str) -> int | None:
    """The number of the season holding the signup module fixed, or None where it is free.

    The signup module is enabled, disabled and configured only while the server holds no
    active season, or while its season stands in Configuration. From the confirmation of
    that configuration to the season's end, it is fixed: its time slots, its questions and
    its roles are what that season's signups were made under.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT season_number, stage FROM seasons "
            "WHERE status IN ('SETUP', 'ACTIVE') "
            "ORDER BY id DESC LIMIT 1",
        )
        row = await cursor.fetchone()
    if row is None or row["stage"] == SeasonStage.CONFIGURATION.value:
        return None
    return int(row["season_number"])


async def modules_frozen_for_completion(db_path: str) -> bool:
    """True while the server's season stands in Pending completion.

    Nothing but amending a final round's results, approving an amendment of the season's
    points and completing the season may be done then, so no module may be disabled.
    """
    found = await live_season_stage(db_path)
    return found is not None and found[1] is SeasonStage.PENDING_COMPLETION


async def _stage_and_whether_done(db_path: str, season_id: int) -> tuple[str | None, bool]:
    """The season's stage, and whether every one of its divisions is finished or cancelled.

    A season with no division is not done: it has had nothing to race.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT stage, "
            "  (SELECT COUNT(*) FROM divisions d WHERE d.season_id = s.id) AS divisions, "
            "  (SELECT COUNT(*) FROM divisions d WHERE d.season_id = s.id "
            "     AND d.status NOT IN ('FINISHED', 'CANCELLED')) AS outstanding "
            "FROM seasons s WHERE s.id = ?",
            (season_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None, False
    return row["stage"], row["divisions"] > 0 and row["outstanding"] == 0


async def advance_to_pending_completion(db_path: str, season_id: int) -> bool:
    """Move a season in Ongoing to Pending completion once every division is done (issue #220).

    A division is done when it is finished or cancelled. Only a season in plain Ongoing is
    moved here, this needing nothing but the database. A season with a signup window open or
    placements still to confirm has signups to close and placements to turn down first, which
    need Discord: :func:`wind_down_ongoing` does that, from wherever a division can finish.
    Returns True where this call moved it.
    """
    stage, done = await _stage_and_whether_done(db_path, season_id)
    if stage != SeasonStage.ONGOING.value or not done:
        return False
    try:
        await _move(db_path, season_id, SeasonStage.ONGOING, SeasonStage.PENDING_COMPLETION)
    except InvalidStageTransition:
        return False
    log.info("season %s is pending completion", season_id)
    return True


#: The states the driver pass returns to Not Signed Up (issue #220). Season Banned and League
#: Banned are left untouched: bans are to be specified on their own.
DRIVER_PASS_STATES: tuple[str, ...] = (
    "UNASSIGNED",
    "ASSIGNED",
    "PENDING_SIGNUP_COMPLETION",
    "PENDING_ADMIN_APPROVAL",
    "AWAITING_CORRECTION_PARAMETER",
    "PENDING_DRIVER_CORRECTION",
)

#: The states of a driver whose signup is still in progress or in review.
_SIGNUP_IN_PROGRESS: frozenset[str] = frozenset({
    "PENDING_SIGNUP_COMPLETION",
    "PENDING_ADMIN_APPROVAL",
    "AWAITING_CORRECTION_PARAMETER",
    "PENDING_DRIVER_CORRECTION",
})


async def _close_driver_signups(
    server_id: int,
    drivers: list[dict],
    signed_up_role_id: int | None,
    *,
    bot,
    guild,
    notice: str,
    reason: str,
) -> None:
    """The Discord side of returning *drivers* to Not Signed Up: their signups and their role.

    A signup still in progress or in review has its channel told *notice* and set to be
    deleted, and its inactivity timeout cancelled; an approved real driver loses the signed-up
    role. Each driver is a row with ``discord_user_id``, ``current_state`` and
    ``is_test_driver``. Nothing here is worth the caller's work: every failure is logged.
    """
    for driver in drivers:
        uid = driver["discord_user_id"]
        if driver["current_state"] in _SIGNUP_IN_PROGRESS and bot is not None:
            try:
                if guild is not None:
                    await bot.wizard_service._trigger_channel_hold(server_id, uid, guild, notice)
                # The channel's own deletion job stays armed, and reads the wizard record
                # when it fires; only the inactivity timeout is cancelled.
                try:
                    bot.scheduler_service._scheduler.remove_job(
                        f"wizard_inactivity_{server_id}_{uid}"
                    )
                except Exception:  # noqa: BLE001 — a job already gone is the aim
                    pass
            except Exception:  # noqa: BLE001 — a signup channel is never worth the pass
                log.exception("closing signups: could not close the signup of %s", uid)
        if (
            guild is not None
            and signed_up_role_id
            and not driver["is_test_driver"]
            and driver["current_state"] in ("UNASSIGNED", "ASSIGNED")
        ):
            member = guild.get_member(int(uid))
            role = guild.get_role(signed_up_role_id)
            if member is not None and role is not None and role in member.roles:
                try:
                    await member.remove_roles(role, reason=reason)
                except Exception:  # noqa: BLE001 — a role is never worth the pass
                    log.warning("closing signups: could not revoke the signed-up role of %s", uid)


async def delete_driver_profiles(db, profile_ids: list[int], *, keep_history: bool) -> None:
    """Delete *profile_ids* and everything that holds them, within the caller's transaction.

    Every reference to a profile without a cascade has to go, or let go, first: seats are
    vacated, placements and attendance rows deleted, and result and standings rows let go of
    the profile while naming the driver by user id still. A driver's signups are keyed by the
    Discord account and are never touched.

    *keep_history* keeps the driver's history entries, which name them by identifier and let
    go of the profile themselves (migration 057) — how test mode keeps its drivers' history.
    Otherwise the entries are deleted with the driver, as the driver pass deletes a real driver
    who never raced: the archive keeps no placement and no history of them.
    """
    if not profile_ids:
        return
    placeholders = ",".join("?" for _ in profile_ids)
    ids = list(profile_ids)
    await db.execute(
        f"UPDATE team_seats SET driver_profile_id = NULL WHERE driver_profile_id IN ({placeholders})",
        ids,
    )
    await db.execute(
        f"DELETE FROM driver_season_assignments WHERE driver_profile_id IN ({placeholders})", ids
    )
    if not keep_history:
        await db.execute(
            f"DELETE FROM driver_history_entries WHERE driver_profile_id IN ({placeholders})", ids
        )
    await db.execute(
        f"DELETE FROM driver_round_attendance WHERE driver_profile_id IN ({placeholders})", ids
    )
    for table in ("race_session_results", "qualifying_session_results", "driver_standings_snapshots"):
        await db.execute(
            f"UPDATE {table} SET driver_profile_id = NULL WHERE driver_profile_id IN ({placeholders})",
            ids,
        )
    await db.execute(f"DELETE FROM driver_profiles WHERE id IN ({placeholders})", ids)


async def run_driver_pass(db_path: str, server_id: int, *, bot=None, guild=None) -> dict:
    """The driver pass that ends a season: completion, cancellation and abort alike (#220).

    1. Every driver Unassigned, Assigned, mid-signup or in review returns to Not Signed Up. A
       signup still in progress or in review is cancelled: its inactivity timeout is cancelled
       and, where a guild is to hand, its channel is told and set to be deleted.
    2. The signed-up role is revoked from every such real driver, where a guild is to hand.
    3. Every real driver at Not Signed Up without the former-driver flag — pending deletion —
       is deleted, with their placements and history entries. Their signups remain.

    A former driver is kept. A driver created by test mode is not deleted here: switching test
    mode off does that, and keeps their history. Returns ``{"reset": n, "deleted": m}``.
    """
    placeholders = ",".join("?" for _ in DRIVER_PASS_STATES)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT id, discord_user_id, current_state, is_test_driver FROM driver_profiles "
            f"WHERE current_state IN ({placeholders})",
            (*DRIVER_PASS_STATES,),
        )
        to_reset = [dict(r) for r in await cursor.fetchall()]
        cursor = await db.execute(
            "SELECT signed_up_role_id FROM signup_module_config",
        )
        cfg_row = await cursor.fetchone()
    signed_up_role_id = cfg_row["signed_up_role_id"] if cfg_row else None

    await _close_driver_signups(
        server_id, to_reset, signed_up_role_id, bot=bot, guild=guild,
        notice="🔒 This season has ended. This channel will be automatically deleted in 24 hours.",
        reason="Season ended",
    )

    from models.driver_profile import DriverState
    from services.driver_service import write_transition

    async with get_connection(db_path) as db:
        # Through the transition table, as every change of a driver's state is.
        for driver in to_reset:
            await write_transition(
                db, driver["id"], DriverState(driver["current_state"]),
                DriverState.NOT_SIGNED_UP,
            )
        cursor = await db.execute(
            "SELECT id FROM driver_profiles WHERE is_test_driver = 0 "
            "AND former_driver = 0 AND current_state = 'NOT_SIGNED_UP'",
        )
        pending_deletion = [r["id"] for r in await cursor.fetchall()]
        await delete_driver_profiles(db, pending_deletion, keep_history=False)
        await db.execute(
            "INSERT INTO audit_entries "
            "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
            "VALUES (?, 0, 'system', NULL, 'DRIVER_PASS', ?, ?, datetime('now'))",
            (
                server_id,
                json.dumps({d["id"]: d["current_state"] for d in to_reset}, sort_keys=True),
                json.dumps({"state": "NOT_SIGNED_UP", "deleted": sorted(pending_deletion)}),
            ),
        )
        await db.commit()

    return {"reset": len(to_reset), "deleted": len(pending_deletion)}

