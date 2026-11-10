"""test_mode_service — Test mode state management and phase queue.

Provides async functions consumed by TestModeCog, among them:
  - switch_test_mode_off_on:      switch test mode off on a save's connection, deleting every fake
                                  driver
  - count_live_real_drivers:      how many real drivers stand in the way of enabling test mode
  - get_next_pending_phase, get_passed_over_steps: the next step to advance and the steps advance
                                  passes over, each module handing back its own through the
                                  bot's `pending_steps_read` hook, which core orders
  - register_advance, advance_next_step: the step `/test-mode advance` runs for each kind of a
                                  round's job, signed up by its module, and the one call that
                                  picks it, takes its job down and runs it
  - build_review_summary:         format a full season/division/round status string
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, TypedDict

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    CHECK_IN_CALL,
    CHECK_IN_CLEAN_UP,
    CHECK_IN_DEADLINE,
    CHECK_IN_LAST_NOTICE,
)
from leaguebot.core.models.round import ROUND_JOB_PHASES, read_scheduled_at
from leaguebot.core.utils.hooks import AwaitedHook

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
    #: weather's step reads the round's format, 1|2|3 = weather phases of a round of any other
    #: format, 4 = result submission, 5|6|7 = the check-in call, its last notice and its
    #: deadline, 8|9 = the forecast and the check-in cleanups a day after the round.
    phase_number: int
    #: The kind of round job the step is armed under (`ROUND_JOB_KINDS`): `weather_p1` for the
    #: mystery notice (0) as for Phase 1, the key of the step its module signed up
    #: (`register_advance`).
    kind: str
    track_name: str
    division_name: str
    #: The APScheduler job the entry came from; None wherever it was found from database
    #: state instead — result submission always, and any phase the job store has lost.
    job_id: str | None


@dataclass(frozen=True)
class ActiveRound:
    """A round of the active season as core reads it, handed to each module through the
    `pending_steps_read` hook: core's own columns of it alone, its race time read as a moment."""

    round_id: int
    round_number: int
    division_id: int
    division_name: str
    format: str
    track_name: str | None
    scheduled_at: datetime
    status: str


@dataclass(frozen=True)
class PendingStep:
    """A step a module hands back as pending or passed over: its round, its phase number (see
    `PhaseEntry`) and the kind of round job it is armed under, which names the step the module
    signed up for it (`register_advance`)."""

    round_id: int
    phase_number: int
    kind: str


@dataclass(frozen=True)
class ModuleSteps:
    """What one module hands back of the rounds it was handed: its *pending* steps, and those
    advance *passed_over* because the change queue would drop them as not due."""

    pending: list[PendingStep]
    passed_over: list[PendingStep] = field(default_factory=list)


#: The kind of round job each phase number is armed under: `weather_p1` for the mystery notice
#: (0) too, which weather's step tells from Phase 1 by the round's format.
PHASE_KINDS = {phase: kind for kind, phase in ROUND_JOB_PHASES.items()} | {
    0: "weather_p1", 4: "results",
}


#: A step's place among those of its round, by phase number: the notice or Phase 1 (0, 1), the
#: check-in call (5), Phase 2, the last notice (6), Phase 3, the deadline (7) and result
#: submission (4), then the clean-ups (8, 9), which fall a day after the round. A round has the
#: notice or Phase 1, never both. It is the order the modules' steps fall in at their default
#: lead times, which core keeps because no module knows another's.
STEP_ORDER = {0: 0, 1: 0, 5: 1, 2: 2, 6: 3, 3: 4, 7: 5, 4: 6, 8: 7, 9: 8}

#: The clean-ups: offered once nothing else is pending anywhere, and the only steps a round that
#: has ended still has.
CLEAN_UP_PHASES = frozenset({8, 9})


# ---------------------------------------------------------------------------
# The step advance runs for each kind of round job
# ---------------------------------------------------------------------------

class AdvanceStatus(Enum):
    ASKED = "asked"
    NOT_DUE = "not due"
    REFUSED = "refused"


@dataclass(frozen=True)
class AdvanceOutcome:
    """What became of the step `/test-mode advance` ran, as its module words it.

    *reply* goes to the maintainer. *log* is the lines of the success line in the log channel
    below its first, which the command writes. The command makes no reply or line of its own from
    anything here, so a module's step owns every word of its kind's advance.
    """

    status: AdvanceStatus
    reply: str
    log: str = ""

    @classmethod
    def asked(cls, reply: str, log: str) -> AdvanceOutcome:
        """The step is on the change queue: *reply* to the maintainer, *log* under the success
        line."""
        return cls(AdvanceStatus.ASKED, reply, log)

    @classmethod
    def not_due(cls, reply: str) -> AdvanceOutcome:
        """The change queue dropped the step as not due: *reply* alone, nothing logged."""
        return cls(AdvanceStatus.NOT_DUE, reply)

    @classmethod
    def refused(cls, reply: str) -> AdvanceOutcome:
        """The step is refused: *reply* as a refusal, recorded in the log channel."""
        return cls(AdvanceStatus.REFUSED, reply)


class AdvanceStepFailed(Exception):
    """A step failed, its fault the exception's `__cause__`. *step* names it as the failure report
    words it ("Phase 2", "the RSVP deadline"), and *track* the weather phase's track."""

    def __init__(self, step: str, *, track: str | None = None) -> None:
        super().__init__(step)
        self.step = step
        self.track = track


#: A kind's step, handed the entry it was picked for. It raises `AdvanceStepFailed` for a fault,
#: from the fault, to be named.
AdvanceStep = Callable[[PhaseEntry], Awaitable[AdvanceOutcome]]

#: Each round job kind's step, signed up by the builder before the bot connects, as the
#: scheduler's handler of the kind is (`register_timer`).
_ADVANCE_STEPS: dict[str, AdvanceStep] = {}


def register_advance(kind: str, step: AdvanceStep) -> None:
    """Sign up *step* as what `/test-mode advance` runs for a job of *kind*."""
    _ADVANCE_STEPS[kind] = step


async def run_advance_step(
    scheduler_service: SchedulerService, entry: PhaseEntry
) -> AdvanceOutcome:
    """Run the step signed up for the *entry*'s kind and return what became of it.

    The round's job of that kind is taken down first, by round and kind, whether the entry came
    from the job store or was found from database state: left queued, it would fire the step
    again at its own moment. It goes before the step runs, so that a step that raises leaves no
    job to ask it a second time. A kind with no step signed up raises `AdvanceStepFailed`
    naming the kind, as a step that fails does, and takes no job down.
    """
    kind = entry["kind"]
    step = _ADVANCE_STEPS.get(kind)
    if step is None:
        raise AdvanceStepFailed(kind) from LookupError(
            f"no /test-mode advance step is signed up for {kind}"
        )
    scheduler_service.cancel_round(entry["round_id"], only=frozenset({kind}))
    return await step(entry)


@dataclass(frozen=True)
class AdvanceRun:
    """What `advance_next_step` did at one moment.

    *entry* is the step picked, None where nothing is left to advance, and *passed_over* the
    weather steps of raced rounds passed over on the way. *outcome* is what became of the step,
    and *failure* the exception of a step that failed, in place of an outcome: one of the two is
    set where an entry was picked.
    """

    entry: PhaseEntry | None
    passed_over: list[PhaseEntry] = field(default_factory=list)
    outcome: AdvanceOutcome | None = None
    failure: AdvanceStepFailed | None = None


async def advance_next_step(
    db_path: str,
    scheduler_service: SchedulerService,
    *,
    now: datetime,
    announce: Callable[[PhaseEntry, list[PhaseEntry]], Awaitable[None]],
    pending_steps_read: AwaitedHook,
) -> AdvanceRun:
    """Pick the next step and the steps passed over at the one moment *now*, announce them, take
    the step's job down and run its kind's step (`run_advance_step`), the steps read through
    *pending_steps_read*.

    One moment judges both, so that a race time falling between two readings cannot leave a step
    both offered and named, or neither. *announce* is awaited with the entry and the steps passed
    over once a step is picked, before the step runs, so that the command can name them first;
    where nothing is left to advance it is not awaited and the steps passed over are returned
    with no entry. A step that fails is returned as *failure*, not raised; any other fault
    propagates.
    """
    entry = await get_next_pending_phase(
        db_path, scheduler_service, now=now, pending_steps_read=pending_steps_read
    )
    passed_over = await get_passed_over_steps(
        db_path, now=now, pending_steps_read=pending_steps_read
    )
    if entry is None:
        return AdvanceRun(None, passed_over)
    await announce(entry, passed_over)
    try:
        outcome = await run_advance_step(scheduler_service, entry)
    except AdvanceStepFailed as exc:
        return AdvanceRun(entry, passed_over, failure=exc)
    return AdvanceRun(entry, passed_over, outcome=outcome)


# ---------------------------------------------------------------------------
# Toggle
# ---------------------------------------------------------------------------

async def switch_test_mode_off_on(
    db: aiosqlite.Connection, *, profiles_deleted: AwaitedHook
) -> int:
    """Switch test mode off on *db*, committing nothing, deleting every driver it created.

    `/test-mode toggle` does it in its save, with the flag's audit entry. A season's end does it
    inside the save that records its end, just before the season itself is written, so the test
    drivers, the flag and the season's archive land together or not at all. The forecasts posted
    under test mode are not flushed here: that is a job of its own, before the save at a season's
    end and after it at the toggle, and the saved backup's deletion one after it. Every fake driver
    is deleted and their history kept. A server not in test mode is left as it is.

    Returns the count of fake drivers removed.
    """
    from leaguebot.core.services.test_roster_service import clear_all_test_drivers_on

    cursor = await db.execute("SELECT test_mode_active FROM server_configs")
    row = await cursor.fetchone()
    if row is None or not row["test_mode_active"]:
        return 0
    removed = await clear_all_test_drivers_on(db, profiles_deleted=profiles_deleted)
    await db.execute("UPDATE server_configs SET test_mode_active = 0")
    return removed


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
    """Whether a round stored as scheduled at *scheduled_at* has been raced by *now*."""
    return read_scheduled_at(scheduled_at) <= now


#: The statuses of a round that has ended, or whose appeals are being judged.
_ENDED_STATUSES = frozenset({"FINAL", "AWAITING_APPEAL_VERDICTS", "CANCELLED"})


async def _active_rounds(db_path: str) -> list[ActiveRound]:
    """The rounds of the active season as core reads them, by race time and then division: those
    of a cancelled division are left out, and a cancelled round unless it ended so because every
    session was entered as not held, which still has its clean-ups."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT r.id AS round_id, r.round_number, r.division_id, r.format, r.track_name,
                   r.status, r.scheduled_at, d.name AS division_name
            FROM rounds r
            JOIN divisions d ON d.id = r.division_id
            JOIN seasons   s ON s.id = d.season_id
            WHERE s.status    = 'ACTIVE'
              AND d.status   != 'CANCELLED'
              AND (r.status != 'CANCELLED' OR r.not_held = 1)
            ORDER BY r.scheduled_at ASC, d.id ASC
            """,
        )
        return [
            ActiveRound(
                round_id=row["round_id"],
                round_number=row["round_number"],
                division_id=row["division_id"],
                division_name=row["division_name"],
                format=row["format"],
                track_name=row["track_name"],
                scheduled_at=read_scheduled_at(row["scheduled_at"]),
                status=row["status"],
            )
            for row in await cursor.fetchall()
        ]


def _entry(rnd: ActiveRound, step: PendingStep, job_id: str | None = None) -> PhaseEntry:
    """*step* of *rnd* as an entry, with the job it was found from, if any."""
    is_mystery = rnd.format.upper() == "MYSTERY"
    return PhaseEntry(
        round_id=rnd.round_id,
        round_number=rnd.round_number,
        division_id=rnd.division_id,
        phase_number=step.phase_number,
        kind=step.kind,
        track_name=rnd.track_name or ("Mystery" if is_mystery else "Unknown"),
        division_name=rnd.division_name,
        job_id=job_id,
    )


async def get_next_pending_phase(
    db_path: str,
    scheduler_service: SchedulerService,
    *,
    now: datetime,
    pending_steps_read: AwaitedHook,
) -> PhaseEntry | None:
    """Return the next step to advance, from what each module hands back as pending.

    Core reads the active season's rounds and asks each module, through *pending_steps_read*,
    which of its steps of them are still pending as *now* stands; it knows no module's tables.
    A module that is off hands nothing back. A round that has ended (FINAL, its appeals being
    judged, or cancelled because every session was entered as not held) keeps only its clean-ups.
    Nothing is returned where there is no active season, or nothing is pending.

    The scheduler orders what it holds by fire time, and a job is matched to the step its module
    hands back by round and kind: the step is offered with the job's id, and a job its module
    hands back no step for is stale and passed over, but only after every earlier round (and for a
    clean-up, which falls a day after its round, its own) is checked for a step the job store
    cannot see: one its job misfired or was evicted, or never armed for a past-dated round, or
    one that never has a job, such as result submission. Once the job store has nothing, the
    rounds are walked in order for the first step of each, and the clean-ups come last: with
    nothing else pending anywhere, whatever is still standing is what remains to take down.

    A step's place within its round is `STEP_ORDER`.
    """
    rounds = await _active_rounds(db_path)
    if not rounds:
        return None
    answers: list[ModuleSteps] = await pending_steps_read.call(db_path, rounds, now=now)
    steps: dict[int, list[PendingStep]] = {rnd.round_id: [] for rnd in rounds}
    for answer in answers:
        for step in answer.pending:
            if step.round_id in steps:
                steps[step.round_id].append(step)
    for rnd in rounds:
        ended = rnd.status in _ENDED_STATUSES
        steps[rnd.round_id] = sorted(
            (
                step for step in steps[rnd.round_id]
                if not ended or step.phase_number in CLEAN_UP_PHASES
            ),
            key=lambda step: STEP_ORDER[step.phase_number],
        )

    def first(rnd: ActiveRound, *, clean_up: bool) -> PhaseEntry | None:
        """*rnd*'s first step of its kind of step, or None."""
        step = next(
            (
                s for s in steps[rnd.round_id]
                if (s.phase_number in CLEAN_UP_PHASES) == clean_up
            ),
            None,
        )
        return None if step is None else _entry(rnd, step)

    for job in scheduler_service.get_pending_advance_jobs({rnd.round_id for rnd in rounds}):
        phase = job["phase_number"]
        job_kind = PHASE_KINDS.get(phase)
        index = next((i for i, rnd in enumerate(rounds) if rnd.round_id == job["round_id"]), None)
        if index is None:
            continue
        is_cleanup = phase in CLEAN_UP_PHASES
        for rnd in rounds[: index + 1 if is_cleanup else index]:
            entry = first(rnd, clean_up=False)
            if entry is not None:
                return entry
        job_step = next((s for s in steps[job["round_id"]] if s.kind == job_kind), None)
        if job_step is None:
            continue  # its module hands back no such step: stale
        return _entry(rounds[index], job_step, job["job_id"])

    for rnd in rounds:
        entry = first(rnd, clean_up=False)
        if entry is not None:
            return entry
    for rnd in rounds:
        entry = first(rnd, clean_up=True)
        if entry is not None:
            return entry
    return None


async def get_passed_over_steps(
    db_path: str, *, now: datetime, pending_steps_read: AwaitedHook
) -> list[PhaseEntry]:
    """Every step the modules hand back through *pending_steps_read* as passed over as *now*
    stands: those advance does not offer because the change queue would drop them as not due,
    such as a weather step of a round whose race has been run. All of them, wherever they fall
    against the step advance offers, ordered by the round's race time, then its division, then
    the phase. `/test-mode advance` names each in its reply before the step it advances.

    A round that has ended, or whose appeals are being judged, is left out: advance offers
    nothing more for it, so it passes nothing of it over; so are a cancelled round and a round of
    a cancelled division. Nothing is recorded of a step named, so advance names it again on each
    run until the round ends, or the season does where results are off and no round ever ends.
    """
    rounds = [
        rnd for rnd in await _active_rounds(db_path) if rnd.status not in _ENDED_STATUSES
    ]
    if not rounds:
        return []
    answers: list[ModuleSteps] = await pending_steps_read.call(db_path, rounds, now=now)
    place = {rnd.round_id: i for i, rnd in enumerate(rounds)}
    passed_over = sorted(
        (step for answer in answers for step in answer.passed_over if step.round_id in place),
        key=lambda step: (place[step.round_id], step.phase_number),
    )
    return [_entry(rounds[place[step.round_id]], step) for step in passed_over]


async def round_result_status(db_path: str, round_id: int) -> str | None:
    """Return a round's lifecycle state, or None if there is no such round.

    NOT_RUN -> AWAITING_RESULTS -> AWAITING_REPORT_VERDICTS -> AWAITING_APPEAL_VERDICTS -> FINAL,
    with CANCELLED as the other ending. Only FINAL and CANCELLED are terminal — a round awaiting
    appeal verdicts still has its results open to change.

    This replaced `is_round_finalized`, which read `rounds.finalized`: a column nothing has ever
    written, so it returned False for every round however completely it was scored and
    `/test-mode advance` treated a finished round as one still in penalty review.
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
    and its type, never by an ID rebuilt here, which is how every slot once read ⚠️.
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
    now: datetime,
) -> str:
    """Return a formatted multi-line string summarising all rounds and phase status.

    Groups results by division (insertion order), then by round (scheduled_at).
    Covers weather phases (P1-P3) and the forecast cleanup, result submission, and RSVP
    phases (notice / last-notice / deadline) and the check-in cleanup, based on DB state —
    so it reflects actual progress regardless of whether scheduler jobs have fired or been
    evicted. A module's steps are shown only while it is on — nothing of one that is off is
    armed, and advance runs none of it.

    When *scheduler_service* is supplied, each pending phase is annotated:
      ⏳ = job is present in APScheduler (will fire automatically)
      ⚠️  = job is absent (misfired, evicted, or never created — needs /advance)

    A weather step left undone on a round whose race has been run by *now* is marked ⏭️
    (`PASSED_OVER`), scheduler or not: `get_next_pending_phase` passes it over, the change queue
    dropping a forecast for a race already run (A25), so neither ⏳ nor "use /test-mode advance"
    would be true of it. Such a round's clean-up is done once nothing of Phases 1 to 3 stands,
    whether or not Phase 3 ever ran.

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
        # off — the setting cannot change while a season runs, so no job was ever armed.
        last_notice_enabled = bool(att_row["rsvp_last_notice_hours"]) if att_row else False

        # The weather module's steps are shown only while it is on, as the others' are:
        # nothing of it is armed while it is off, and advance runs none of it.
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

        # Rounds with a Phase 1 to 3 forecast still standing, for the forecast cleanup
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
            date_str = read_scheduled_at(row["scheduled_at"]).date().isoformat()

            fmt = str(row["format"]).upper()
            is_mystery = fmt == "MYSTERY"

            parts: list[str] = []
            raced = _race_run(row["scheduled_at"], now)

            def _weather(done: bool, event: str) -> str:
                """A weather step's mark: passed over where undone on a raced round."""
                if raced and not done:
                    return PASSED_OVER
                return _phase_status(done, (rid, event), queued)

            # ── Weather / mystery notice phases ───────────────────────────
            if weather_module_enabled and is_mystery:
                # A mystery round's notice is armed as `weather_p1`: weather's
                # `ask_forecast` reads the round's format as it fires.
                notice = _weather(bool(row["phase1_done"]), "weather_p1")
                parts.append(f"Notice: {notice}")
            elif weather_module_enabled:
                p1 = _weather(bool(row["phase1_done"]), "weather_p1")
                p2 = _weather(bool(row["phase2_done"]), "weather_p2")
                p3 = _weather(bool(row["phase3_done"]), "weather_p3")
                # Done once Phase 3 has run, or the race has (Phase 3 passed over), and nothing
                # of Phases 1 to 3 is left standing.
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
                    # Taken down a day after the round, and its record with it: every
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
