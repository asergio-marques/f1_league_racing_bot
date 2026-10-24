"""test_mode_service — Test mode state management and phase queue.

Provides four async functions consumed by TestModeCog:
  - toggle_test_mode:             flip the test_mode_active flag in server_configs
  - toggle_test_mode_nationality: flip whether mock drivers carry a nationality
  - count_live_real_drivers:      how many real drivers stand in the way of enabling test mode
  - get_next_pending_phase:       find the earliest un-executed phase across all rounds
  - build_review_summary:         format a full season/division/round status string
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, TypedDict

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    CHECK_IN_CALL,
    CHECK_IN_CLEAN_UP,
    CHECK_IN_DEADLINE,
    CHECK_IN_LAST_NOTICE,
    FORECAST,
    FORECAST_CLEAN_UP,
)

if TYPE_CHECKING:
    from leaguebot.core.services.scheduler_service import SchedulerService

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Return-type hints
# ---------------------------------------------------------------------------

class PhaseEntry(TypedDict):
    round_id: int
    round_number: int
    division_id: int
    #: 0 = mystery notice — from the job store as well, where it is armed as `weather_p1` and
    #: `get_next_pending_phase` reads the round's format (#426), 1|2|3 = weather phases of a
    #: round of any other format, 4 = result submission, 5|6|7 = the check-in
    #: call, its last notice and its deadline, 8|9 = the forecast and the check-in cleanups a
    #: day after the round (#425).
    phase_number: int
    track_name: str
    division_name: str
    #: The APScheduler job the entry came from; None wherever it was found from database
    #: state instead — result submission always, and any phase the job store has lost.
    job_id: str | None


# ---------------------------------------------------------------------------
# Toggle
# ---------------------------------------------------------------------------

async def toggle_test_mode(db_path: str) -> bool:
    """Flip test_mode_active and return the NEW value.

    Uses a single atomic UPDATE so no read-modify-write race can occur.
    Returns False if the server has no config row (bot not initialised).
    """
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE server_configs "
            "SET test_mode_active = 1 - test_mode_active "
            "",
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT test_mode_active FROM server_configs",
        )
        row = await cursor.fetchone()

    if row is None:
        log.error("toggle_test_mode: no server_config row")
        return False
    return bool(row["test_mode_active"])


async def switch_test_mode_off_on(db: aiosqlite.Connection) -> int:
    """Switch test mode off on *db*, committing nothing, deleting every driver it created.

    A season's end does it inside the save that records its end, just before the season itself
    is written, so the test drivers, the flag and the season's archive land together or not at
    all (decided 2026-10-09). The forecasts posted under test mode are not flushed here: that
    is a job of its own before the save, and the saved backup's deletion one after it. Every
    fake driver is deleted and their history kept. A server not in test mode is left as it is.

    Returns the count of fake drivers removed.
    """
    from leaguebot.core.services.test_roster_service import clear_all_test_drivers_on

    cursor = await db.execute("SELECT test_mode_active FROM server_configs")
    row = await cursor.fetchone()
    if row is None or not row["test_mode_active"]:
        return 0
    removed = await clear_all_test_drivers_on(db)
    await db.execute("UPDATE server_configs SET test_mode_active = 0")
    return removed


async def toggle_test_mode_nationality(db_path: str) -> bool:
    """Flip test_mode_nationality_required and return the NEW value.

    The test-mode counterpart of the signup nationality switch: while test mode is active
    it stands in for it, so the graphics of a server under test may be seen with flags and
    without them without the real signup setting being touched.

    Same atomic UPDATE as toggle_test_mode, for the same reason. Returns False if the
    server has no config row (bot not initialised).
    """
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE server_configs "
            "SET test_mode_nationality_required = 1 - test_mode_nationality_required "
            "",
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT test_mode_nationality_required FROM server_configs",
        )
        row = await cursor.fetchone()

    if row is None:
        log.error(
            "toggle_test_mode_nationality: no server_config row"
        )
        return False
    return bool(row["test_mode_nationality_required"])


async def count_live_real_drivers(db_path: str) -> int:
    """Return how many *live* real drivers this server holds.

    A live real driver is a driver_profiles row with is_test_driver = 0 whose state is
    anything but NOT_SIGNED_UP: someone mid-signup, unassigned or assigned. A row
    sitting at NOT_SIGNED_UP is a retained former driver — the profile of someone who has
    left, kept only for their history — and is not in the league, so it does not count.

    Test mode and a real league may not share a server: toggling test mode off deletes
    every fake driver on it, unscoped and unconfirmed, and a mixed roster corrupts the
    standings, the attendance sheets and the lineup graphics alike. This count is what
    /test-mode toggle refuses on.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM driver_profiles "
            "WHERE is_test_driver = 0 AND current_state != 'NOT_SIGNED_UP'",
        )
        row = await cursor.fetchone()

    return int(row["n"]) if row is not None else 0


# ---------------------------------------------------------------------------
# Phase advancement queue
# ---------------------------------------------------------------------------

def _weather_steps_undone(row: Any, in_hand: set[tuple[int, int]]) -> list[int]:
    """The weather steps of *row*'s round not done and not already in hand on the change queue
    (*in_hand*, as (round, phase)), in order: a mystery round's notice (0) until it is posted, or
    each of Phases 1 to 3 not done.

    The one judgement of a weather step still owed: `get_next_pending_phase` offers from it for a
    round still to be raced, and `get_passed_over_steps` names from it what advance passes over
    for a raced one, so that the two lists cannot drift apart.
    """
    if str(row["format"]).upper() == "MYSTERY":
        owed = [] if row["phase1_done"] else [0]
    else:
        owed = [n for n in (1, 2, 3) if not row[f"phase{n}_done"]]
    return [n for n in owed if (row["round_id"], n) not in in_hand]


def _weather_entry(row: Any, phase: int) -> PhaseEntry:
    """*row*'s weather step *phase* as an entry found from database state, with no job."""
    is_mystery = str(row["format"]).upper() == "MYSTERY"
    return PhaseEntry(
        round_id=row["round_id"],
        round_number=row["round_number"],
        division_id=row["division_id"],
        phase_number=phase,
        track_name=row["track_name"] or ("Mystery" if is_mystery else "Unknown"),
        division_name=row["division_name"],
        job_id=None,
    )


#: The scheduler's event type of each check-in step, by the change that asks for it.
_CHECK_IN_EVENTS = {
    CHECK_IN_CALL: "rsvp_notice",
    CHECK_IN_LAST_NOTICE: "rsvp_last_notice",
    CHECK_IN_DEADLINE: "rsvp_deadline",
    CHECK_IN_CLEAN_UP: "rsvp_cleanup",
}


async def _check_ins_in_hand(db_path: str) -> set[tuple[int, str]]:
    """Each check-in step asked of the change queue and not yet run, as (round, scheduler event):
    asked by an earlier advance, whose scheduler job it cancelled, so that the database alone
    would offer the same step again."""
    from leaguebot.core.services.change_queue import unfinished

    return {
        (int(payload["round_id"]), _CHECK_IN_EVENTS[kind])
        for kind in _CHECK_IN_EVENTS
        for payload in await unfinished(db_path, (kind,))
    }


def _deadline_has_run(rsvp: Any) -> bool:
    """Whether a round's RSVP deadline has run: it is marked (`deadline_run_at`, set in the save
    that records the distribution, before the announcement is posted) or its announcement stands.
    The mark is what the deadline's own job reads, so a deadline whose announcement was refused
    is not run again by advance; the announcement alone covers a row written before the mark."""
    return bool(rsvp["deadline_run_at"] or rsvp["distribution_msg_id"])


def _race_run(scheduled_at: str, now: datetime) -> bool:
    """Whether a round scheduled at *scheduled_at* (naive means UTC) has been raced by *now*."""
    moment = datetime.fromisoformat(scheduled_at)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment <= now


async def get_next_pending_phase(
    db_path: str,
    scheduler_service: Any = None,
    *,
    now: datetime | None = None,
) -> PhaseEntry | None:
    """Return the earliest pending action entry based on the APScheduler job store.

    Uses the scheduler as the single source of truth for what is actually pending,
    so only events that the system has genuinely scheduled (i.e. the relevant module
    was enabled when the season was approved or the module was later enabled) are
    returned.  This means, for example, that weather phases are never advanced when
    the weather module is disabled — because no phase jobs would have been created.

    Resolution order (matches APScheduler fire-time ordering):
      1. next_run_time ASC  — earliest scheduled fire time first
      2. round_id ASC       — tie-break for same-fire-time jobs
      3. phase_number ASC   — e.g. phase 1 before phase 2 on same round

    Result submission never comes from the scheduler. ``get_pending_advance_jobs``
    excludes results jobs, for every round format, so that a past-dated job which
    already auto-fired can neither block the wizard nor open it twice. A round is found
    due for submission from database state instead — no ACTIVE session results, and
    standing at NOT_RUN or AWAITING_RESULTS — whatever its format, mystery included: in
    the check of earlier rounds made before each scheduler job, and in the fallback once
    the job store is exhausted.

    A round that ended cancelled because every session was entered as not held (`rounds.not_held`)
    is read for its clean-ups alone, a day after its start as any round's: it offers no other step.
    A round called off is not read at all.

    A weather step of a round whose race has been run by *now* (the present, where not given) is
    passed over, since the change queue drops a forecast for a race already run as not due
    (A25). Rehearsing weather in test mode needs rounds dated in the future (owner, 2026-10-12).

    A weather step whose forecast or clean-up is already in hand on the change queue (asked by
    an earlier advance, and not yet run) is passed over, so a second advance before the queue
    has run the first offers the next step and not the same one. The scheduler's own job was
    cancelled by that first advance, so the database would otherwise offer the step again. A
    check-in step (call, last notice, deadline or clean-up) in hand is passed over alike.

    If there is no ACTIVE season for this server, returns None.
    """
    from leaguebot.core.services.change_queue import unfinished

    # (round, phase) of each forecast asked of the queue and not yet run; the round alone for a
    # clean-up (phase 8).
    forecasts_in_hand: set[tuple[int, int]] = {
        (int(p["round_id"]), int(p["phase"]))
        for p in await unfinished(db_path, (FORECAST,))
    }
    clean_ups_in_hand: set[int] = {
        int(p["round_id"]) for p in await unfinished(db_path, (FORECAST_CLEAN_UP,))
    }
    check_ins_in_hand = await _check_ins_in_hand(db_path)
    # Rounds whose results submission an earlier advance asked of the queue and which it has not
    # opened yet: the database alone would offer the round start again.
    submissions_in_hand: set[int] = {
        int(p["round_id"]) for p in await unfinished(db_path, ("results.submission.open",))
    }

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT
                r.id           AS round_id,
                r.round_number,
                r.division_id,
                r.format,
                r.track_name,
                r.phase1_done,
                r.phase2_done,
                r.phase3_done,
                r.checkin_cleared,
                r.status,
                r.scheduled_at,
                d.name         AS division_name
            FROM rounds r
            JOIN divisions d ON d.id  = r.division_id
            JOIN seasons   s ON s.id  = d.season_id
            WHERE s.status    = 'ACTIVE'
              AND d.status   != 'CANCELLED'
              AND (r.status != 'CANCELLED' OR r.not_held = 1)
            ORDER BY r.scheduled_at ASC, d.id ASC
            """,
        )
        rows = list(await cursor.fetchall())
        if not rows:
            return None

        round_ids: set[int] = {r["round_id"] for r in rows}
        round_info = {r["round_id"]: r for r in rows}
        # Rounds whose race has been run: the change queue drops their forecasts as not due (A25),
        # so none of their weather steps is offered.
        moment = now if now is not None else datetime.now(timezone.utc)
        raced: set[int] = {
            r["round_id"] for r in rows if _race_run(r["scheduled_at"], moment)
        }

        # Rounds that already have at least one ACTIVE session_result (for mystery fallback)
        results_cursor = await db.execute(
            """
            SELECT DISTINCT sr.round_id
            FROM session_results sr
            JOIN rounds r ON r.id = sr.round_id
            JOIN divisions d ON d.id = r.division_id
            JOIN seasons s ON s.id = d.season_id
            WHERE sr.status = 'ACTIVE'
            """,
        )
        rounds_with_results: set[int] = {r["round_id"] for r in await results_cursor.fetchall()}

    # ── DB-based path (no scheduler) ────────────────────────────────────────
    # Used by unit tests and any caller that does not have a scheduler_service.
    # Implements the original spec ordering: scheduled_at ASC, division_id ASC;
    # Mystery rounds with phase1_done=0 return phase_number=0 (notice pending);
    # Mystery rounds with phase1_done=1 are skipped; Normal rounds return the
    # first incomplete phase (1, 2, or 3) based on the phase flag columns.
    if scheduler_service is None:
        for row in rows:
            if row["round_id"] in raced or row["status"] == "CANCELLED":
                continue  # its race has been run, or it ended not held: no forecast is due
            # The first weather step still owed and not in hand on the queue: a mystery round's
            # notice, or the first of Phases 1 to 3.
            undone = _weather_steps_undone(row, forecasts_in_hand)
            if not undone:
                continue  # all weather phases done (a noticed mystery round), or in hand
            return _weather_entry(row, undone[0])
        return None

    # ── Primary: scheduler job store ────────────────────────────────────────
    pending_jobs = scheduler_service.get_pending_advance_jobs(round_ids)

    # Fetch module flags, RSVP embed state, and finalized status.
    # These are used by _first_pending_for_row which drives both the
    # earlier-round priority check and the all-misfired DB fallback.
    async with get_connection(db_path) as db:
        rmc_cursor = await db.execute(
            "SELECT module_enabled FROM results_module_config",
        )
        rmc_row = await rmc_cursor.fetchone()
        results_module_enabled = bool(rmc_row[0]) if rmc_row else False

        wm_cursor = await db.execute(
            "SELECT weather_module_enabled FROM server_configs",
        )
        wm_row = await wm_cursor.fetchone()
        weather_module_enabled = bool(wm_row[0]) if wm_row else False

        att_cursor = await db.execute(
            "SELECT module_enabled, rsvp_last_notice_hours FROM attendance_config"
        )
        att_row = await att_cursor.fetchone()
        attendance_module_enabled = bool(att_row["module_enabled"]) if att_row else False
        # A last notice set to 0 is never sent, and no job is armed for it (#426).
        last_notice_enabled = bool(att_row["rsvp_last_notice_hours"]) if att_row else False

        # RSVP embed state: keyed by round_id (one row per round since each
        # round belongs to exactly one division).
        if attendance_module_enabled:
            rsvp_cursor = await db.execute(
                """
                SELECT rem.round_id, rem.last_notice_msg_id, rem.distribution_msg_id,
                       rem.deadline_run_at
                FROM rsvp_embed_messages rem
                JOIN rounds r ON r.id = rem.round_id
                JOIN divisions d ON d.id = r.division_id
                JOIN seasons s ON s.id = d.season_id
                """,
            )
            rsvp_state: dict[int, Any] = {
                r["round_id"]: r for r in await rsvp_cursor.fetchall()
            }
        else:
            rsvp_state = {}

        # Rounds with a Phase 1 to 3 forecast still standing, for the forecast cleanup (8).
        if weather_module_enabled:
            forecast_cursor = await db.execute(
                "SELECT DISTINCT round_id FROM forecast_messages WHERE phase_number IN (1, 2, 3)",
            )
            standing_forecasts: set[int] = {
                r["round_id"] for r in await forecast_cursor.fetchall()
            }
        else:
            standing_forecasts = set()

    def _make_entry(row: Any, phase: int, job_id: str | None = None) -> PhaseEntry:
        is_mystery = str(row["format"]).upper() == "MYSTERY"
        return PhaseEntry(
            round_id=row["round_id"],
            round_number=row["round_number"],
            division_id=row["division_id"],
            phase_number=phase,
            track_name=row["track_name"] or ("Mystery" if is_mystery else "Unknown"),
            division_name=row["division_name"],
            job_id=job_id,
        )

    def _cleanup_pending(row: Any, phase: int | None = None) -> "PhaseEntry | None":
        """Return *row*'s cleanup still to run — *phase* alone where given — or None.

        Kept apart from `_first_pending_for_row` for two reasons (#425). A cleanup falls a day
        after its round, so offering one while checking the rounds before a scheduler job would
        pull it ahead of a later round's earlier work — the first of a double-header's cleanup
        before the second's deadline. And a finished round still has its cleanups to run, where
        `_first_pending_for_row` offers nothing for one, which would discard its cleanup job as
        stale.

        Pending means there is still something to take down: a forecast of Phase 1 to 3 (8), or a
        check-in call recorded as standing (9). Once taken down there is nothing, and a cleanup
        with nothing to take down is not offered either.
        """
        rid = row["round_id"]
        if phase in (None, 8) and rid in standing_forecasts and rid not in clean_ups_in_hand:
            return _make_entry(row, 8)
        if (
            phase in (None, 9) and attendance_module_enabled and rid in rsvp_state
            and (rid, "rsvp_cleanup") not in check_ins_in_hand
        ):
            return _make_entry(row, 9)
        return None

    def _first_pending_for_row(row: Any) -> "PhaseEntry | None":
        """Return the first pending phase for *row* detected from DB state only.

        Canonical order mirrors APScheduler fire-time ordering with defaults:
          normal:  P1(1) → RSVP-notice(5) → P2(2) → RSVP-last(6) → P3(3) → RSVP-deadline(7) → results(4)
          mystery: notice(0) → RSVP-notice(5) → RSVP-last(6) → RSVP-deadline(7) → results(4)
        Each only while its module is on, and the last notice only where it is not set to 0.

        Used for two purposes:
          1. Priority check: detect misfired/absent work in earlier rounds before
             returning the first scheduler job.
          2. Empty-jobs fallback: iterate all rounds when the scheduler has nothing
             (all jobs auto-fired or evicted by misfire_grace_time).
        """
        rid = row["round_id"]
        is_mystery = str(row["format"]).upper() == "MYSTERY"
        # The weather steps still owed, by the rule `get_passed_over_steps` shares: none while
        # the module is off, nor for a round whose race has been run.
        weather_due = (
            set(_weather_steps_undone(row, forecasts_in_hand))
            if weather_module_enabled and rid not in raced
            else set()
        )

        # Skip rounds that are fully done or already in results processing.
        # A round that has ended, or whose appeals are being judged, offers no more phases.
        # A round that ended cancelled because every session was entered as not held is in the
        # query for its clean-ups alone, and offers nothing else.
        if row["status"] in ("FINAL", "AWAITING_APPEAL_VERDICTS", "CANCELLED"):
            return None

        def _make(phase: int) -> PhaseEntry:
            return PhaseEntry(
                round_id=rid,
                round_number=row["round_number"],
                division_id=row["division_id"],
                phase_number=phase,
                track_name=row["track_name"] or ("Mystery" if is_mystery else "Unknown"),
                division_name=row["division_name"],
                job_id=None,
            )

        # ── Phase 0 / Phase 1: mystery notice or weather P1 ──────────────
        # The notice is a weather posting like Phase 1, and a disabled module produces
        # nothing: a live season arms no job for either while weather is off (#426).
        if (0 if is_mystery else 1) in weather_due:
            return _make(0 if is_mystery else 1)

        # ── Phase 5: RSVP notice ──────────────────────────────────────────
        # A round whose check-in has been taken down after it has no call recorded either, and
        # is not owed one (#425).
        if (
            attendance_module_enabled and rid not in rsvp_state and not row["checkin_cleared"]
            and (rid, "rsvp_notice") not in check_ins_in_hand
        ):
            return _make(5)

        # ── Phase 2: weather P2 (normal rounds only) ──────────────────────
        if 2 in weather_due:
            return _make(2)

        # ── Phase 6: RSVP last-notice ─────────────────────────────────────
        # Not where the league has set it to 0: a live season sends none (#426).
        if attendance_module_enabled and last_notice_enabled:
            rsvp = rsvp_state.get(rid)
            if (
                rsvp is not None and not rsvp["last_notice_msg_id"]
                and (rid, "rsvp_last_notice") not in check_ins_in_hand
            ):
                return _make(6)

        # ── Phase 3: weather P3 (normal rounds only) ──────────────────────
        if 3 in weather_due:
            return _make(3)

        # ── Phase 7: RSVP deadline ────────────────────────────────────────
        if attendance_module_enabled:
            rsvp = rsvp_state.get(rid)
            if (
                rsvp is not None and not _deadline_has_run(rsvp)
                and (rid, "rsvp_deadline") not in check_ins_in_hand
            ):
                return _make(7)

        # ── Phase 4: result submission ────────────────────────────────────
        if results_module_enabled:
            if (
                rid not in rounds_with_results and rid not in submissions_in_hand
                and row["status"] in ("NOT_RUN", "AWAITING_RESULTS")
            ):
                return _make(4)

        return None

    if pending_jobs:
        for job in pending_jobs:
            rnd = round_info[job["round_id"]]
            phase = job["phase_number"]
            # A mystery round's notice is armed as `weather_p1`, with `weather_p2` and
            # `weather_p3` beside it, and the job store knows nothing of formats: a live season's
            # `_weather_phase_job` reads the format as the job fires, and this is where advance
            # reads it, once. `weather_p1` is the notice (0) — and stale once the notice is up,
            # since advancing it would post the notice a second time. The other two do nothing
            # when they fire, so there is nothing to advance for them (#426).
            if phase in (1, 2, 3) and str(rnd["format"]).upper() == "MYSTERY":
                if phase != 1 or rnd["phase1_done"]:
                    continue
                phase = 0
            if (job["round_id"], phase) in forecasts_in_hand:
                continue  # asked of the change queue by an earlier advance, not yet run
            if phase in (0, 1, 2, 3) and job["round_id"] in raced:
                continue  # its race has been run: the change queue would drop the forecast
            is_cleanup = phase in (8, 9)
            # Before returning this scheduler job, check all earlier rounds (by
            # scheduled_at) for any pending work that the scheduler cannot see —
            # misfired/evicted phase jobs, result submission (excluded from
            # pending_jobs), or RSVP phases that were never created for past-dated
            # rounds.  Ensures chronological advance order is preserved even when
            # the job store is incomplete.
            #
            # A cleanup checks its own round as well: it falls a day after the round, so the
            # round's own result submission, which never comes from the job store, is due first.
            first_job_idx = next(
                (i for i, r in enumerate(rows) if r["round_id"] == job["round_id"]),
                len(rows),
            )
            for row in rows[: first_job_idx + 1 if is_cleanup else first_job_idx]:
                entry = _first_pending_for_row(row)
                if entry is not None:
                    return entry
            # Validate this job against DB state before trusting it.  A scheduler
            # job can become stale when phases were already completed (e.g. run via
            # a previous advance invocation) but APScheduler still holds the job
            # because cancel_job was never called or the job fired-and-was-missed.
            # A cleanup is judged by its own work alone — see `_cleanup_pending`.
            if is_cleanup:
                if _cleanup_pending(rnd, phase) is None:
                    continue
            elif _first_pending_for_row(rnd) is None:
                # Round is fully done — stale scheduler job; try the next one.
                continue
            return _make_entry(rnd, phase, job["job_id"])

    # ── DB fallback: all scheduler jobs have misfired or been evicted ─────────
    # Walk rounds in scheduled_at order and return the first pending phase
    # detected from DB state alone.  Covers the common test-mode scenario where
    # all rounds are past-dated and every job auto-fired or was evicted by
    # APScheduler's misfire_grace_time, leaving the job store empty.
    for row in rows:
        entry = _first_pending_for_row(row)
        if entry is not None:
            return entry

    # The cleanups come last: each falls a day after its round, so with nothing else pending
    # anywhere, whatever is still standing is what remains to take down.
    for row in rows:
        entry = _cleanup_pending(row)
        if entry is not None:
            return entry

    return None


async def get_passed_over_steps(
    db_path: str, *, now: datetime | None = None
) -> list[PhaseEntry]:
    """Every weather step `get_next_pending_phase` passes over as *now* stands (the present, where
    not given): each Phase 1 to 3, or mystery round notice, still owed (`_weather_steps_undone`)
    for a round whose race has been run, the change queue dropping a forecast for a race already
    run (A25). All of them, wherever they fall against the step advance offers, ordered by the
    round's race time, then its division, then the phase. `/test-mode advance` names each in its
    reply before the step it advances (owner, 2026-10-12, "Advance names what it skipped").

    Only while the weather module is on, as nothing of it is armed while it is off, and never a
    step already in hand on the change queue. A round that has ended, or whose appeals are being
    judged, is left out: advance offers nothing more for it, so it passes nothing of it over; so
    are a cancelled round and a round of a cancelled division. Nothing is recorded of a step
    named, so advance names it again on each run until the round ends, or the season does where
    results are off and no round ever ends.
    """
    from leaguebot.core.services.change_queue import unfinished

    in_hand: set[tuple[int, int]] = {
        (int(p["round_id"]), int(p["phase"])) for p in await unfinished(db_path, (FORECAST,))
    }
    moment = now if now is not None else datetime.now(timezone.utc)
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT weather_module_enabled FROM server_configs")
        found = await cursor.fetchone()
        if found is None or not found[0]:
            return []
        cursor = await db.execute(
            """
            SELECT r.id AS round_id, r.round_number, r.division_id, r.format, r.track_name,
                   r.phase1_done, r.phase2_done, r.phase3_done, r.scheduled_at,
                   d.name AS division_name
            FROM rounds r
            JOIN divisions d ON d.id = r.division_id
            JOIN seasons   s ON s.id = d.season_id
            WHERE s.status = 'ACTIVE'
              AND d.status != 'CANCELLED'
              AND r.status NOT IN ('CANCELLED', 'FINAL', 'AWAITING_APPEAL_VERDICTS')
            ORDER BY r.scheduled_at ASC, d.id ASC
            """,
        )
        rows = list(await cursor.fetchall())
    return [
        _weather_entry(row, phase)
        for row in rows
        if _race_run(row["scheduled_at"], moment)
        for phase in _weather_steps_undone(row, in_hand)
    ]


async def standing_forecast_phases(db_path: str, round_id: int) -> list[int]:
    """The phases, of 1 to 3, whose forecast of *round_id* is still recorded: what the forecast
    clean-up deletes, by which `/test-mode advance` names it."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT DISTINCT phase_number FROM forecast_messages "
            "WHERE round_id = ? AND phase_number IN (1, 2, 3) ORDER BY phase_number",
            (round_id,),
        )
        return [int(row["phase_number"]) for row in await cursor.fetchall()]


async def round_result_status(db_path: str, round_id: int) -> str | None:
    """Return a round's lifecycle state, or None if there is no such round.

    NOT_RUN -> AWAITING_RESULTS -> AWAITING_REPORT_VERDICTS -> AWAITING_APPEAL_VERDICTS -> FINAL,
    with CANCELLED as the other ending. Only FINAL and CANCELLED are terminal — a round awaiting
    appeal verdicts still has its results open to change.

    This replaced `is_round_finalized`, which read `rounds.finalized`: a column nothing has ever
    written, so it returned False for every round however completely it was scored and
    `/test-mode advance` treated a finished round as one still in penalty review (issue #154).
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT status FROM rounds WHERE id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()
    return row["status"] if row else None


# ---------------------------------------------------------------------------
# Review summary
# ---------------------------------------------------------------------------

def _phase_status(
    done: bool, event: tuple[int, str], queued: set[tuple[int, str]] | None
) -> str:
    """Return a status emoji for a single phase slot.

    ✅ — phase complete (DB flag set)
    ⏳ — pending; scheduler job is present (will fire automatically)
    ⚠️  — pending; scheduler job is absent (misfired, never created, or
          already auto-fired — must use /test-mode advance)

    *event* is the ``(round_id, event_type)`` the slot's job is armed under, and *queued* the
    scheduler's own answer from ``get_queued_events_for_rounds`` — a job is found by its round
    and its type, never by an ID rebuilt here, which is how every slot once read ⚠️ (#426).
    When *queued* is None (no scheduler available) pending phases show ⏳.
    """
    if done:
        return "✅"
    if queued is None:
        return "⏳"
    return "⏳" if event in queued else "⚠️"


#: The mark of a raced round's weather step that was never done: advance passes it over.
PASSED_OVER = "⏭️"


async def build_review_summary(
    db_path: str,
    scheduler_service: Any = None,
    *,
    now: datetime | None = None,
) -> str:
    """Return a formatted multi-line string summarising all rounds and phase status.

    Groups results by division (insertion order), then by round (scheduled_at).
    Covers weather phases (P1-P3) and the forecast cleanup, result submission, and RSVP
    phases (notice / last-notice / deadline) and the check-in cleanup, based on DB state —
    so it reflects actual progress regardless of whether scheduler jobs have fired or been
    evicted. A module's steps are shown only while it is on — nothing of one that is off is
    armed, and advance runs none of it (#426).

    When *scheduler_service* is supplied, each pending phase is annotated:
      ⏳ = job is present in APScheduler (will fire automatically)
      ⚠️  = job is absent (misfired, evicted, or never created — needs /advance)

    A weather step left undone on a round whose race has been run by *now* (the present, where
    not given) is marked ⏭️ (`PASSED_OVER`), scheduler or not: `get_next_pending_phase` passes it
    over, the change queue dropping a forecast for a race already run (A25), so neither ⏳ nor
    "use /test-mode advance" would be true of it. Such a round's clean-up is done once nothing
    of Phases 1 to 3 stands, whether or not Phase 3 ever ran.

    Returns an informative message string if no active season exists.
    """
    async with get_connection(db_path) as db:
        # Season header
        season_cursor = await db.execute(
            "SELECT start_date FROM seasons WHERE status = 'ACTIVE'",
        )
        season_row = await season_cursor.fetchone()

        if season_row is None:
            return "ℹ️ No active season found. Configure a season first."

        season_name = f"Season starting {season_row['start_date']}"

        # Module-enabled flags
        rmc_cursor = await db.execute(
            "SELECT module_enabled FROM results_module_config",
        )
        rmc_row = await rmc_cursor.fetchone()
        results_module_enabled = bool(rmc_row[0]) if rmc_row else False

        att_cursor = await db.execute(
            "SELECT module_enabled, rsvp_last_notice_hours FROM attendance_config"
        )
        att_row = await att_cursor.fetchone()
        attendance_module_enabled = bool(att_row["module_enabled"]) if att_row else False
        # A last notice set to 0 is never sent, and is left out like a step of a module that is
        # off — the setting cannot change while a season runs, so no job was ever armed (#426).
        last_notice_enabled = bool(att_row["rsvp_last_notice_hours"]) if att_row else False

        # The weather module's steps are shown only while it is on, as the others' are:
        # nothing of it is armed while it is off, and advance runs none of it (#426).
        wm_cursor = await db.execute("SELECT weather_module_enabled FROM server_configs")
        wm_row = await wm_cursor.fetchone()
        weather_module_enabled = bool(wm_row[0]) if wm_row else False

        # All non-cancelled rounds for the active season
        cursor = await db.execute(
            """
            SELECT
                r.id          AS round_id,
                r.round_number,
                r.format,
                r.track_name,
                r.scheduled_at,
                r.phase1_done,
                r.phase2_done,
                r.phase3_done,
                r.checkin_cleared,
                r.status,
                d.name        AS division_name,
                d.id          AS division_id
            FROM rounds r
            JOIN divisions d ON d.id  = r.division_id
            JOIN seasons   s ON s.id  = d.season_id
            WHERE s.status    = 'ACTIVE'
              AND d.status   != 'CANCELLED'
              AND r.status   != 'CANCELLED'
            ORDER BY d.id ASC, r.scheduled_at ASC
            """,
        )
        rows = await cursor.fetchall()

        # Rounds that have at least one ACTIVE session_result
        sr_cursor = await db.execute(
            """
            SELECT DISTINCT sr.round_id
            FROM session_results sr
            JOIN rounds r ON r.id = sr.round_id
            JOIN divisions d ON d.id = r.division_id
            JOIN seasons s ON s.id = d.season_id
            WHERE sr.status = 'ACTIVE'
            """,
        )
        rounds_with_results: set[int] = {r["round_id"] for r in await sr_cursor.fetchall()}

        # Rounds with a Phase 1 to 3 forecast still standing, for the forecast cleanup (#425)
        forecast_cursor = await db.execute(
            "SELECT DISTINCT round_id FROM forecast_messages WHERE phase_number IN (1, 2, 3)",
        )
        standing_forecasts: set[int] = {r["round_id"] for r in await forecast_cursor.fetchall()}

        # RSVP embed message rows: keyed by (round_id, division_id)
        rsvp_cursor = await db.execute(
            """
            SELECT rem.round_id, rem.division_id,
                   rem.message_id, rem.last_notice_msg_id, rem.distribution_msg_id,
                   rem.deadline_run_at
            FROM rsvp_embed_messages rem
            JOIN rounds r ON r.id = rem.round_id
            JOIN divisions d ON d.id = r.division_id
            JOIN seasons s ON s.id = d.season_id
            """,
        )
        rsvp_rows = {
            (r["round_id"], r["division_id"]): r
            for r in await rsvp_cursor.fetchall()
        }

    if not rows:
        return f"**Season: {season_name} — ACTIVE**\n\nNo rounds have been configured yet."

    moment = now if now is not None else datetime.now(timezone.utc)

    # The jobs the scheduler holds, as (round_id, event_type) — when one is available.
    round_ids: set[int] = {r["round_id"] for r in rows}
    queued: set[tuple[int, str]] | None = (
        scheduler_service.get_queued_events_for_rounds(round_ids)
        if scheduler_service is not None
        else None
    )
    if queued is not None:
        # A check-in step in hand on the change queue is pending, though its scheduler job was
        # cancelled by the advance that asked for it.
        queued |= await _check_ins_in_hand(db_path)

    # Group by division
    divisions: dict[str, list] = {}
    for row in rows:
        div_name = row["division_name"]
        if div_name not in divisions:
            divisions[div_name] = []
        divisions[div_name].append(row)

    lines: list[str] = [f"**Season: {season_name} — ACTIVE**\n"]

    for div_name, div_rounds in divisions.items():
        lines.append(f"**{div_name}**")
        for row in div_rounds:
            rid = row["round_id"]
            rnum = row["round_number"]
            track = row["track_name"] or "TBA"
            date_str = str(row["scheduled_at"])[:10]

            fmt = str(row["format"]).upper()
            is_mystery = fmt == "MYSTERY"

            parts: list[str] = []
            raced = _race_run(row["scheduled_at"], moment)

            def _weather(done: bool, event: str) -> str:
                """A weather step's mark: passed over where undone on a raced round."""
                if raced and not done:
                    return PASSED_OVER
                return _phase_status(done, (rid, event), queued)

            # ── Weather / mystery notice phases ───────────────────────────
            if weather_module_enabled and is_mystery:
                # A mystery round's notice is armed as `weather_p1`: `_weather_phase_job` reads
                # the round's format as it fires.
                notice = _weather(bool(row["phase1_done"]), "weather_p1")
                parts.append(f"Notice: {notice}")
            elif weather_module_enabled:
                p1 = _weather(bool(row["phase1_done"]), "weather_p1")
                p2 = _weather(bool(row["phase2_done"]), "weather_p2")
                p3 = _weather(bool(row["phase3_done"]), "weather_p3")
                # Done once Phase 3 has run, or the race has (Phase 3 passed over), and nothing
                # of Phases 1 to 3 is left standing (#425).
                cleanup = _phase_status(
                    (bool(row["phase3_done"]) or raced) and rid not in standing_forecasts,
                    (rid, "cleanup"),
                    queued,
                )
                parts.append(f"P1: {p1}  P2: {p2}  P3: {p3}  Cleanup: {cleanup}")

            # ── Result submission ─────────────────────────────────────────
            if results_module_enabled:
                if row["status"] == "FINAL":
                    res = "✅ finalized"
                elif rid in rounds_with_results:
                    res = "⏸️ pending review"
                else:
                    res = _phase_status(False, (rid, "results"), queued)
                parts.append(f"Results: {res}")

            # ── RSVP / attendance phases ──────────────────────────────────
            if attendance_module_enabled:
                check_in: list[tuple[str, str]]
                if row["checkin_cleared"]:
                    # Taken down a day after the round, and its record with it (#425): every
                    # step before the cleanup had run by then.
                    check_in = [
                        ("RSVP", "✅"), ("Last", "✅"), ("Deadline", "✅"), ("Cleared", "✅")
                    ]
                else:
                    rsvp = rsvp_rows.get((rid, row["division_id"]))
                    check_in = [
                        ("RSVP", "✅" if rsvp is not None
                         else _phase_status(False, (rid, "rsvp_notice"), queued)),
                        ("Last", "✅" if (rsvp and rsvp["last_notice_msg_id"])
                         else _phase_status(False, (rid, "rsvp_last_notice"), queued)),
                        ("Deadline", "✅" if (rsvp and _deadline_has_run(rsvp))
                         else _phase_status(False, (rid, "rsvp_deadline"), queued)),
                        ("Cleared", _phase_status(False, (rid, "rsvp_cleanup"), queued)),
                    ]
                if not last_notice_enabled:
                    check_in = [(name, mark) for name, mark in check_in if name != "Last"]
                parts.append("  ".join(f"{name}: {mark}" for name, mark in check_in))

            line = f"  Round {rnum} · {track:<15} · {date_str}  " + "  |  ".join(parts)
            lines.append(line)

        lines.append("")  # blank line between divisions

    if queued is not None:
        lines.append(
            "*Legend: ✅ done  ⏳ pending (job scheduled)  ⚠️ pending (no job — use /test-mode "
            f"advance)  {PASSED_OVER} passed over (its race has been run)*"
        )

    return "\n".join(lines).rstrip()
