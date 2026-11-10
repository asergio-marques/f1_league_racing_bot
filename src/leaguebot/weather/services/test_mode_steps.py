"""Weather's steps for `/test-mode advance`: what of each round it still has to post or take down,
and what advance passes over, read from weather's own state alone.

Core's `pending_steps_read` hook asks each module which of the rounds it hands over have steps
still pending (architecture.md, "How modules and core fit together"). Weather answers from its own
switch, its own columns on `rounds` (`phase1_done` to `phase3_done`), the forecasts it keeps and
the forecast changes still in hand on the change queue; core hands it the rounds as it reads them,
their format, race time and status, and orders what comes back.

Weather's steps of a round are a mystery round's notice (0) until it is posted, or each of Phases 1
to 3 (1, 2, 3) not done, and the forecast clean-up (8) while a forecast of Phases 1 to 3 stands. A
step of a round whose race has been run is passed over: the change queue drops a forecast for a
race already run as not due, so advance does not offer it, though it names it. The clean-up is
never passed over, and never a step already asked of the change queue and not yet run: the advance
that asked for it cancelled the scheduler's job, so the database alone would offer it again. While
weather is off nothing of it is armed and nothing is handed back.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import aiosqlite

from leaguebot.core.db.database import get_connection, tuple_rows_in
from leaguebot.core.models.change import FORECAST, FORECAST_CLEAN_UP
from leaguebot.core.services.change_queue import unfinished
from leaguebot.core.services.test_mode_service import (
    PHASE_KINDS,
    ActiveRound,
    ModuleSteps,
    PendingStep,
)
from leaguebot.weather.services.draw_guard import weather_enabled_on

#: The step that takes the forecasts of Phases 1 to 3 down, a day after the round.
CLEAN_UP_PHASE = 8


async def phases_done(
    db: aiosqlite.Connection, round_ids: Sequence[int]
) -> dict[int, tuple[bool, bool, bool]]:
    """Whether each of Phases 1 to 3 is done, by round id, for the rounds *round_ids*."""
    rows = await tuple_rows_in(
        db,
        "SELECT id, phase1_done, phase2_done, phase3_done FROM rounds WHERE id IN ({marks})",
        round_ids,
    )
    return {int(r[0]): (bool(r[1]), bool(r[2]), bool(r[3])) for r in rows}


async def _standing_forecasts(db: aiosqlite.Connection) -> set[int]:
    """The rounds with a forecast of Phase 1 to 3 still standing."""
    cursor = await db.execute(
        "SELECT DISTINCT round_id FROM forecast_messages WHERE phase_number IN (1, 2, 3)"
    )
    return {int(row["round_id"]) for row in await cursor.fetchall()}


def _owed(rnd: ActiveRound, done: tuple[bool, bool, bool]) -> list[int]:
    """The weather steps of *rnd* not done, in order: a mystery round's notice (0) until it is
    posted, or each of Phases 1 to 3 not done."""
    if rnd.format.upper() == "MYSTERY":
        return [] if done[0] else [0]
    return [phase for phase in (1, 2, 3) if not done[phase - 1]]


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
        done = await phases_done(db, [r.round_id for r in rounds])
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
