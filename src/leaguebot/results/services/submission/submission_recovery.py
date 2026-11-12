"""What a restart does to the submission channels a previous bot process left open.

It is results' own step of the start-up sweep, `recover_orphaned_submission_channels`, which reads
the rows of the open channels and decides, round by round, what each is: a review, put back or left
to the change queue by :func:`~leaguebot.results.services.submission.review_recovery.recover_review`,
or a first paste cut short, which is asked of the queue as `results.submission.restart`. The wiring
only names this function as a step (architecture.md, "A module's wiring"), so it holds neither the
read nor the rule.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from leaguebot.core.db.database import get_connection
from leaguebot.core.services.queue.change_queue import ChangeQueue
from leaguebot.core.services.seasons.season_service import (
    round_in_division,
    rounds_still_to_run_by,
)
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import league_guild
from leaguebot.results.models.recovery_failed import RecoveryFailed
from leaguebot.results.services.submission.review_recovery import recover_review
from leaguebot.results.services.submission.round_start import ask_round_start
from leaguebot.results.services.submission.submission_changes import ask_submission_restart

log = logging.getLogger(__name__)

__all__ = ["recover_missed_round_starts", "recover_orphaned_submission_channels"]


async def recover_orphaned_submission_channels(bot: LeagueBot, *, queue: ChangeQueue) -> None:
    """Clear any submission channels left open by a previous bot process, through the queue.

    When the bot restarts, the paste a submission was waiting for is lost: the row remains
    closed=0 and the Discord channel still stands, but nothing is listening.

    A mid-submission orphan (in_penalty_review=0) is asked of the queue as
    `results.submission.restart`: it clears the round's sessions (so the round is not mistaken
    for complete by get_next_pending_phase) and the submission row, deletes the channel, tells the
    log channel to re-submit and opens the submission afresh. A paste whose turn is in hand on the
    queue is left to it. This runs before the queue starts.

    Reviews are not decided here: `review_recovery.recover_review` leaves a round whose review a
    change on the queue is carrying out to it, and otherwise asks the queue to put the review
    back. A resubmission a restart cut short is ended by the review's opening, its collection
    being lost though the round's results are not.

    A round that cannot be recovered stops none of the others. Once every round has been gone
    through, a failure for any is raised as one `RecoveryFailed` naming each round and its error,
    which the start-up sweep reports to the log channel in one line.

    The change queue is handed in by the wiring's step, not looked up on the bot.
    """
    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            """
            SELECT round_id, channel_id, in_penalty_review, results_posted, prompt_message_id,
                   appeals_prompt_message_id, resubmitting, resubmit_prompt_message_id,
                   resubmit_started_by
            FROM round_submission_channels
            WHERE closed = 0
            """
        )
        channels = await cursor.fetchall()

    # Each open channel with the round and the division core says it belongs to; a channel whose
    # round is gone has nothing to recover.
    orphans: list[dict[str, Any]] = []
    for channel in channels:
        found = await round_in_division(bot.db_path, int(channel["round_id"]))
        if found is not None:
            orphans.append({
                **{key: channel[key] for key in channel.keys()},
                "division_id": found.division_id, "status": found.status,
                "round_number": found.round_number, "division_name": found.division_name,
            })

    failures: dict[int, BaseException] = {}
    for row in orphans:
        round_id: int = row["round_id"]
        try:
            guild = await league_guild(bot)

            # A round that is a review is put back, or left to the change carrying it out, by the
            # results service. What is left is a first paste cut short, which is no review.
            if await recover_review(
                bot, row, guild, queue=queue,
            ):
                continue

            # A first paste cut short is cleared, its channel deleted and its submission opened
            # afresh by the change queue's `results.submission.restart`, before the queue starts.
            # A round with a change of its submission in hand is left to it above.
            log.info("Recovery: asking the queue to clear and reopen round %s's submission",
                     round_id)
            await ask_submission_restart(
                queue, bot.db_path, round_id, int(row["channel_id"]),
            )
        except Exception as error:
            log.exception("Recovery: could not recover round %s's submission channel", round_id)
            failures[round_id] = error
    if failures:
        raise RecoveryFailed(failures)


async def recover_missed_round_starts(
    bot: LeagueBot, *, queue: ChangeQueue, came_up_at: Callable[[], datetime],
) -> None:
    """Open the results submission of every round whose start passed while the bot was down.

    The core specification's "When the bot stops" has a restart recover what a stop interrupted,
    and the results specification has a restart reopen a submission left unopened as the queue
    does on reaching it. A round's `results` job is armed with no lateness limit, so the job store
    keeps one whose moment passed and fires it at start-up; this is the step that covers a job the
    store lost, and asks for the round the same way, through `ask_round_start`, so that a round
    reached by both is asked for once.

    Each round of the live season still to be run whose start is at or before the queue's clock is
    handed to `ask_round_start`, which asks nothing for one that is no longer due: opened,
    cancelled or in hand on the queue. It runs after the two recoveries above, a round whose
    submission one of them cleared and asked open again being then in hand on the queue.

    **Under test mode it asks nothing** for a start before the bot came up, `/test-mode advance`
    opening each round in its turn. *came_up_at* gives that moment, the one the wiring hands the
    `results` job's handler too, so a late job and this step judge a start by one value.

    A round that cannot be asked for stops none of the others. Once every round has been gone
    through, a failure for any is raised as one `RecoveryFailed` naming each round and its error,
    which the start-up sweep reports to the log channel in one line.
    """
    failures: dict[int, BaseException] = {}
    for rnd in await rounds_still_to_run_by(bot.db_path, queue.now()):
        try:
            await ask_round_start(queue, bot.db_path, rnd.id, came_up_at=came_up_at)
        except Exception as error:
            log.exception("Recovery: could not open round %s's results submission", rnd.id)
            failures[rnd.id] = error
    if failures:
        raise RecoveryFailed(failures)
