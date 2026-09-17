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

import logging

from db.database import get_connection
from models.season import InvalidStageTransition, SeasonStage, status_of_stage

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


async def live_season_stage(db_path: str, server_id: int) -> tuple[int, SeasonStage] | None:
    """The server's active season and its stage, or None where it holds none."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id, stage FROM seasons "
            "WHERE server_id = ? AND status IN ('SETUP', 'ACTIVE') "
            "ORDER BY id DESC LIMIT 1",
            (server_id,),
        )
        row = await cursor.fetchone()
    if row is None or row["stage"] is None:
        return None
    return int(row["id"]), SeasonStage(row["stage"])


async def count_unsettled_signups(db_path: str, server_id: int) -> int:
    """How many drivers of *server_id* hold a signup not yet placed or turned down."""
    placeholders = ",".join("?" for _ in UNSETTLED_STATES)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT COUNT(*) AS n FROM driver_profiles "
            f"WHERE server_id = ? AND current_state IN ({placeholders})",
            (server_id, *UNSETTLED_STATES),
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


async def advance_on_window_open(db_path: str, server_id: int) -> SeasonStage | None:
    """Move the active season on for a signup window just opened.

    Waiting becomes Signups; Ongoing becomes Ongoing, signups open. Returns the new stage,
    or None where the season stood in neither and nothing was moved.
    """
    found = await live_season_stage(db_path, server_id)
    if found is None:
        return None
    season_id, stage = found
    target = WINDOW_OPENS_FROM.get(stage)
    if target is None:
        return None
    await _move(db_path, season_id, stage, target)
    return target


async def advance_on_window_close(db_path: str, server_id: int) -> SeasonStage | None:
    """Move the active season on for a signup window just closed.

    Signups becomes Placements. Ongoing, signups open becomes Ongoing, placements where any
    signup remains unsettled, and Ongoing where none does. Returns the new stage, or None
    where the season stood in neither and nothing was moved — a window force-closed by
    disabling the module, for one, belongs to no stage.
    """
    found = await live_season_stage(db_path, server_id)
    if found is None:
        return None
    season_id, stage = found
    if stage is SeasonStage.SIGNUPS:
        target = SeasonStage.PLACEMENTS
    elif stage is SeasonStage.ONGOING_SIGNUPS:
        unsettled = await count_unsettled_signups(db_path, server_id)
        target = SeasonStage.ONGOING_PLACEMENTS if unsettled else SeasonStage.ONGOING
    else:
        return None
    await _move(db_path, season_id, stage, target)
    if target is SeasonStage.ONGOING:
        await advance_to_pending_completion(db_path, season_id)
    return target


async def signup_configuration_fixed(db_path: str, server_id: int) -> int | None:
    """The number of the season holding the signup module fixed, or None where it is free.

    The signup module is enabled, disabled and configured only while the server holds no
    active season, or while its season stands in Configuration. From the confirmation of
    that configuration to the season's end, it is fixed: its time slots, its questions and
    its roles are what that season's signups were made under.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT season_number, stage FROM seasons "
            "WHERE server_id = ? AND status IN ('SETUP', 'ACTIVE') "
            "ORDER BY id DESC LIMIT 1",
            (server_id,),
        )
        row = await cursor.fetchone()
    if row is None or row["stage"] == SeasonStage.CONFIGURATION.value:
        return None
    return int(row["season_number"])


async def modules_frozen_for_completion(db_path: str, server_id: int) -> bool:
    """True while the server's season stands in Pending completion.

    Nothing but amending a final round's results, approving an amendment of the season's
    points and completing the season may be done then, so no module may be disabled.
    """
    found = await live_season_stage(db_path, server_id)
    return found is not None and found[1] is SeasonStage.PENDING_COMPLETION


async def advance_to_pending_completion(db_path: str, season_id: int) -> bool:
    """Move a season in Ongoing to Pending completion once every division is done (issue #220).

    A division is done when it is finished or cancelled. Only a season in plain Ongoing moves:
    one with a signup window open, or placements still to confirm, waits until it has returned
    to Ongoing — and every path returning it there calls this again. Returns True where this
    call moved it.
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
    if row is None or row["stage"] != SeasonStage.ONGOING.value:
        return False
    if row["divisions"] == 0 or row["outstanding"] != 0:
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
    mode off does that, and keeps their history. Season Banned and League Banned drivers are
    left untouched. Returns ``{"reset": n, "deleted": m}``.
    """
    placeholders = ",".join("?" for _ in DRIVER_PASS_STATES)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT id, discord_user_id, current_state, is_test_driver FROM driver_profiles "
            f"WHERE server_id = ? AND current_state IN ({placeholders})",
            (server_id, *DRIVER_PASS_STATES),
        )
        to_reset = [dict(r) for r in await cursor.fetchall()]
        cursor = await db.execute(
            "SELECT signed_up_role_id FROM signup_module_config WHERE server_id = ?",
            (server_id,),
        )
        cfg_row = await cursor.fetchone()
    signed_up_role_id = cfg_row["signed_up_role_id"] if cfg_row else None

    for driver in to_reset:
        uid = driver["discord_user_id"]
        if driver["current_state"] in _SIGNUP_IN_PROGRESS and bot is not None:
            try:
                if guild is not None:
                    await bot.wizard_service._trigger_channel_hold(
                        server_id, uid, guild,
                        "🔒 This season has ended. This channel will be automatically "
                        "deleted in 24 hours.",
                    )
                # The channel's own deletion job stays armed, and reads the wizard record
                # when it fires; only the inactivity timeout is cancelled.
                try:
                    bot.scheduler_service._scheduler.remove_job(
                        f"wizard_inactivity_{server_id}_{uid}"
                    )
                except Exception:  # noqa: BLE001 — a job already gone is the aim
                    pass
            except Exception:  # noqa: BLE001 — a signup channel is never worth the pass
                log.exception("driver pass: could not close the signup of %s", uid)
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
                    await member.remove_roles(role, reason="Season ended")
                except Exception:  # noqa: BLE001 — a role is never worth the pass
                    log.warning("driver pass: could not revoke the signed-up role of %s", uid)

    async with get_connection(db_path) as db:
        if to_reset:
            reset_ids = [d["id"] for d in to_reset]
            id_placeholders = ",".join("?" for _ in reset_ids)
            await db.execute(
                f"UPDATE driver_profiles SET current_state = 'NOT_SIGNED_UP' "
                f"WHERE id IN ({id_placeholders})",
                reset_ids,
            )
        cursor = await db.execute(
            "SELECT id FROM driver_profiles WHERE server_id = ? AND is_test_driver = 0 "
            "AND former_driver = 0 AND current_state = 'NOT_SIGNED_UP'",
            (server_id,),
        )
        pending_deletion = [r["id"] for r in await cursor.fetchall()]
        await delete_driver_profiles(db, pending_deletion, keep_history=False)
        await db.commit()

    return {"reset": len(to_reset), "deleted": len(pending_deletion)}

