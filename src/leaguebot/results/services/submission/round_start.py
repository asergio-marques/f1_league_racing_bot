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
from leaguebot.core.services.queue.change_queue import ChangeQueue
from leaguebot.core.services.queue.queue_records import unfinished
from leaguebot.core.services.seasons.season_end_changes import test_mode_on
from leaguebot.core.services.seasons.season_service import round_in_division
from leaguebot.results.services.submission.submission_changes import KIND, ask_submission_open

__all__ = ["ask_round_start"]


async def ask_round_start(
    queue: ChangeQueue, db_path: str, round_id: int, *, came_up_at: datetime,
) -> None:
    """Ask the queue, as the bot, to open *round_id*'s results submission, where the round is
    still due to be opened.

    The round is read again. It is due while it is still to be run (not cancelled, not opened,
    not held) and no opening of it is in hand on the queue, a stopped one included, so that the
    late job and the catch-up both firing, in either order, ask for it once.

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
    if found.scheduled_at < came_up_at:
        async with get_connection(db_path) as db:
            if await test_mode_on(db):
                return
    await ask_submission_open(queue, db_path, round_id)
