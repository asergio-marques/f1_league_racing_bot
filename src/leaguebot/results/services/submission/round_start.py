"""What a round's start asks of the change queue, on time or caught up.

A round's results submission is opened by its `results` job at the round's start, and by results'
start-up catch-up where the start passed while the bot was down. Both come here, through
`ask_round_start`, so a job run on time and one caught up take one path (architecture.md, "Timed
work and restarts"): the job is armed with no lateness limit and its handler, this one, decides
what becomes of one fired late.
"""

from __future__ import annotations

from datetime import datetime

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.round import RoundStatus
from leaguebot.core.services.cancellations.cancellation_changes import (
    DIVISION_CANCEL,
    ROUND_CANCEL,
    ROUND_REARM,
)
from leaguebot.core.services.queue.change_queue import ChangeQueue
from leaguebot.core.services.queue.queue_records import in_hand, unfinished
from leaguebot.core.services.rounds.round_amend_change import ROUND_AMEND
from leaguebot.core.services.seasons.season_end_changes import SEASON_CANCEL, test_mode_on
from leaguebot.core.services.seasons.season_service import RoundInDivision, round_in_division
from leaguebot.results.services.submission.submission_changes import KIND, ask_submission_open

__all__ = ["ask_round_start"]


async def ask_round_start(
    queue: ChangeQueue, db_path: str, round_id: int, *, came_up_at: datetime,
) -> None:
    """Ask the queue, as the bot, to open *round_id*'s results submission, where the round is
    still due to be opened.

    The round is read again. It is due while it is still to be run (not cancelled, not opened,
    not held), no opening of it is in hand on the queue, a stopped one included, so that the late
    job and the catch-up both firing, in either order, ask for it once, and no change that takes
    the round's timed work down and arms it again is in hand either (`_held_by_a_change`): that
    change's arming catches up a moment that passed meanwhile, and an opening asked ahead of it
    would run after it had moved the round.

    **Under test mode a start that passed before the bot came up is left to `/test-mode advance`.**
    *came_up_at* is the moment the bot came up, which the wiring hands in (the catch-up, which runs
    in the start-up sweep, hands in the queue's clock as it runs). A
    round whose start is before it, under test mode, is asked nothing here and nothing is recorded
    as skipped, so the advance opens it in its turn, as it does for a round with weather off,
    whose job is not armed at all. A start at or after it fires while the bot is up and opens on
    time, test mode or not. Outside test mode a late start opens at once.
    """
    found = await round_in_division(db_path, round_id)
    if found is None or found.status != RoundStatus.NOT_RUN.value:
        return
    if any(p.get("round_id") == round_id for p in await unfinished(db_path, (KIND,))):
        return
    if await _held_by_a_change(db_path, found):
        return
    if found.scheduled_at < came_up_at:
        async with get_connection(db_path) as db:
            if await test_mode_on(db):
                return
    await ask_submission_open(queue, db_path, round_id)


async def _held_by_a_change(db_path: str, found: RoundInDivision) -> bool:
    """Whether a change that holds the round is in hand on the queue, queued, running or stopped on
    a failure: an amendment of it, its cancellation or the arming of it again after a refused one,
    a cancellation of its division, or a cancellation of a season.

    Each of these removes the round's timed work and arms it again, or ends the round, and is left
    alone by anything else that would put the round back or undo it (core specification, "How a
    change is carried out"). A season's cancellation is judged whatever season it names: only the
    live season's rounds have a start to be asked for.
    """
    for payload, _job in await in_hand(db_path, (ROUND_AMEND, ROUND_REARM, ROUND_CANCEL)):
        if payload.get("round_id") == found.round_id:
            return True
    for payload, _job in await in_hand(db_path, (DIVISION_CANCEL,)):
        if payload.get("division_id") == found.division_id:
            return True
    return bool(await in_hand(db_path, (SEASON_CANCEL,)))
