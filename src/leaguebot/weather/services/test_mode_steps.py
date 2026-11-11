"""Weather's steps for `/test-mode advance`: what of each round it still has to post or take down,
and what advance passes over, and how the review marks them, read from weather's own state alone.

Core's `pending_steps_read` hook asks each module which of the rounds it hands over have steps
still pending (architecture.md, "How modules and core fit together"). Weather answers from its own
switch, its own columns on `rounds` (`phase1_done` to `phase3_done`), the forecasts it keeps and
the forecast changes still in hand on the change queue; core hands it the rounds as it reads them,
their format, race time and status, and orders what comes back. Weather reads a round's format
itself, from the round it is handed, to tell a mystery round's notice from Phase 1.

Weather's steps of a round are a mystery round's notice (0) until it is posted, or each of Phases 1
to 3 (1, 2, 3) not done, and the forecast clean-up (8) while a forecast of Phases 1 to 3 stands. A
step of a round whose race has been run is passed over: the change queue drops a forecast for a
race already run as not due, so advance does not offer it, though it names it. The clean-up is
never passed over, and never a step already asked of the change queue and not yet run: the advance
that asked for it cancelled the scheduler's job, so the database alone would offer it again. While
weather is off nothing of it is armed and nothing is handed back.

The review's marks of a round come through core's `review_marks_read` hook: Phases 1 to 3 and the
clean-up, or a mystery round's notice alone, each done (✅), pending with its job queued or with no
scheduler to ask (⏳), pending with no job (⚠️), or, where the round's race has been run, passed
over (⏭️). The clean-up is done once nothing of Phases 1 to 3 stands and Phase 3 has run or the
race has.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import FORECAST, FORECAST_CLEAN_UP
from leaguebot.core.services.queue.queue_records import unfinished
from leaguebot.core.services.test_mode.test_mode_service import (
    PASSED_OVER,
    PHASE_KINDS,
    ActiveRound,
    ModuleSteps,
    PendingStep,
    ReviewMarks,
    phase_mark,
)
from leaguebot.weather.services.draw_guard import weather_enabled_on
from leaguebot.weather.services.round_phases import phases_drawn_on

#: The step that takes the forecasts of Phases 1 to 3 down, a day after the round.
CLEAN_UP_PHASE = 8


async def _standing_forecasts(db: aiosqlite.Connection) -> set[int]:
    """The rounds with a forecast of Phase 1 to 3 still standing."""
    cursor = await db.execute(
        "SELECT DISTINCT round_id FROM forecast_messages WHERE phase_number IN (1, 2, 3)"
    )
    return {int(row["round_id"]) for row in await cursor.fetchall()}


def _owed(rnd: ActiveRound, done: dict[int, bool]) -> list[int]:
    """The weather steps of *rnd* not done, in order: a mystery round's notice (0) until it is
    posted, or each of Phases 1 to 3 not done."""
    if rnd.format.upper() == "MYSTERY":
        return [] if done[1] else [0]
    return [phase for phase in (1, 2, 3) if not done[phase]]


async def pending_steps(
    db_path: str, rounds: Sequence[ActiveRound], *, now: datetime
) -> ModuleSteps:
    """Weather's pending steps of *rounds* as *now* stands, and those advance passes over.

    A step of a raced round (`scheduled_at` by *now*) is passed over, not pending; the clean-up is
    pending for any round while a forecast stands that no clean-up already in hand will take down.
    Nothing is handed back while weather is off.
    """
    async with get_connection(db_path) as db:
        if not await weather_enabled_on(db):
            return ModuleSteps(pending=[], passed_over=[])
        done = await phases_drawn_on(db, [r.round_id for r in rounds])
        standing = await _standing_forecasts(db)
    forecasts_in_hand = {
        (int(p["round_id"]), int(p["phase"])) for p in await unfinished(db_path, (FORECAST,))
    }
    clean_ups_in_hand = {
        int(p["round_id"]) for p in await unfinished(db_path, (FORECAST_CLEAN_UP,))
    }

    pending: list[PendingStep] = []
    passed_over: list[PendingStep] = []
    for rnd in rounds:
        raced = rnd.scheduled_at <= now
        for phase in _owed(rnd, done[rnd.round_id]):
            if (rnd.round_id, phase) in forecasts_in_hand:
                continue
            (passed_over if raced else pending).append(
                PendingStep(rnd.round_id, phase, PHASE_KINDS[phase])
            )
        if rnd.round_id in standing and rnd.round_id not in clean_ups_in_hand:
            pending.append(PendingStep(rnd.round_id, CLEAN_UP_PHASE, PHASE_KINDS[CLEAN_UP_PHASE]))
    return ModuleSteps(pending=pending, passed_over=passed_over)


async def review_marks(
    db_path: str,
    rounds: Sequence[ActiveRound],
    *,
    now: datetime,
    queued: set[tuple[int, str]] | None,
) -> ReviewMarks:
    """Weather's marks of *rounds* for `/test-mode review`, as *now* stands; *queued* is the jobs
    the scheduler holds as (round, event type), None where there is no scheduler. Nothing is
    handed back while weather is off."""
    async with get_connection(db_path) as db:
        if not await weather_enabled_on(db):
            return {}
        done = await phases_drawn_on(db, [r.round_id for r in rounds])
        standing = await _standing_forecasts(db)

    marks: ReviewMarks = {}
    for rnd in rounds:
        rid = rnd.round_id
        raced = rnd.scheduled_at <= now
        drawn = done[rid]
        p1, p2, p3 = drawn[1], drawn[2], drawn[3]

        def mark(is_done: bool, event: str) -> str:
            """A step's mark: passed over where undone on a raced round."""
            if raced and not is_done:
                return PASSED_OVER
            return phase_mark(is_done, (rid, event), queued)

        if rnd.format.upper() == "MYSTERY":
            # A mystery round's notice is armed as `weather_p1`: weather's `ask_forecast` reads
            # the round's format as it fires.
            marks[rid] = [("Notice", mark(p1, "weather_p1"))]
        else:
            marks[rid] = [
                ("P1", mark(p1, "weather_p1")),
                ("P2", mark(p2, "weather_p2")),
                ("P3", mark(p3, "weather_p3")),
                (
                    "Cleanup",
                    phase_mark((p3 or raced) and rid not in standing, (rid, "cleanup"), queued),
                ),
            ]
    return marks
