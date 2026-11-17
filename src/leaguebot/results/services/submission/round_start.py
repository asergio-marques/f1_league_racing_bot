"""What a round's start asks of the change queue, on time or caught up.

A round's results submission is opened by its `results` job at the round's start, and by results'
start-up catch-up where the start passed while the bot was down. Both come here, through
`ask_round_start`, so a job run on time and one caught up take one path (architecture.md, "Timed
work and restarts"): the job is armed with no lateness limit and its handler, this one, decides
what becomes of one fired late.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from leaguebot.framework.db.database import get_connection
from leaguebot.core.models.amendment import RoundArming
from leaguebot.core.models.round import RoundLead, RoundStatus
from leaguebot.framework.services.queue.change_queue import ChangeQueue
from leaguebot.framework.services.queue.queue_records import unfinished
from leaguebot.core.services.seasons.season_end_changes import test_mode_on
from leaguebot.core.services.seasons.season_service import round_in_division
from leaguebot.results.services.submission.submission_changes import KIND, ask_submission_open

__all__ = ["ROUND_START_KIND", "ask_round_start", "round_armed", "submission_lead", "submission_leads"]

#: The kind a round's submission is stored under, the kind of the timer `ask_round_start` answers.
ROUND_START_KIND = "results"


def submission_lead() -> RoundLead:
    """The lead of a round's results submission: its timer, at the round's own moment.

    It is the round's one way off Not run, so results answers it for every round, whatever its
    switch: its handler moves a round on where results is off. It is marked to be run at once where
    an amendment finds the round's moment passed while it was still Not run, and to be left to
    `/test-mode advance` where core says so (`results_left_to_advance`). It is armed whatever the
    hour, and quoted by no date check.
    """
    return RoundLead(
        kind=ROUND_START_KIND, offset=timedelta(0), label="Result submission",
        run_at_once=True, left_to_advance=True,
    )


async def submission_leads() -> tuple[RoundLead, ...]:
    """What results answers a round's leads read (`round_leads_read`): its submission."""
    return (submission_lead(),)


async def round_armed(*_args: Any, **_context: Any) -> RoundArming:
    """What results owes the round armed again: its submission, as the round stands, whatever its
    switch and whether or not the modules arm anything else. Subscribed to core's `round_armed`."""
    return RoundArming(jobs=(submission_lead(),))


async def ask_round_start(
    queue: ChangeQueue, db_path: str, round_id: int, *, came_up_at: Callable[[], datetime],
) -> None:
    """Ask the queue, as the bot, to open *round_id*'s results submission, where the round is
    still due to be opened.

    The round is read again. It is due while it is still to be run (not cancelled, not opened,
    not held) and no opening of it is in hand on the queue, a stopped one included, so that the
    late job and the catch-up both firing, in either order, ask for it once. The ask is the round's
    start's (`at_its_start`), so the opening change judges again, when it comes up to run, that the
    round is still due: its start has passed by the queue's clock and it is still to be run. A round
    an amendment or a cancellation in hand ahead of the opening has since moved ahead or ended is
    then dropped at that check, and a change refused leaves the opening to run, so nothing here
    holds the round back for such a change.

    **Under test mode a start that passed before the bot came up is left to `/test-mode advance`.**
    *came_up_at* gives the moment the bot came up, recorded once by the entry point's `build` on the
    bot (`LeagueBot.came_up_at`), which the wiring hands in both here and to the catch-up, so that
    the two judge "before the bot came up" by one value. A round whose start is before it, under
    test mode, is asked nothing here and nothing is recorded as skipped, so the advance opens it in
    its turn, as it does for a round with weather off, whose job is not armed at all. A start at or
    after it fires while the bot is up and opens on time, test mode or not. Outside test mode a
    late start opens at once.
    """
    found = await round_in_division(db_path, round_id)
    if found is None or found.status != RoundStatus.NOT_RUN.value:
        return
    if any(p.get("round_id") == round_id for p in await unfinished(db_path, (KIND,))):
        return
    if found.scheduled_at < came_up_at():
        async with get_connection(db_path) as db:
            if await test_mode_on(db):
                return
    await ask_submission_open(queue, db_path, round_id, at_its_start=True)
