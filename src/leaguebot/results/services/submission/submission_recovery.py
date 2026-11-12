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
from typing import Any

from leaguebot.core.db.database import get_connection
from leaguebot.core.services.seasons.season_service import round_in_division
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import league_guild
from leaguebot.results.models.recovery_failed import RecoveryFailed
from leaguebot.results.services.submission.review_recovery import recover_review
from leaguebot.results.services.submission.submission_changes import ask_submission_restart

log = logging.getLogger(__name__)

__all__ = ["recover_orphaned_submission_channels"]


async def recover_orphaned_submission_channels(bot: LeagueBot) -> None:
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
                bot, row, guild, queue=bot.change_queue,
            ):
                continue

            # A first paste cut short is cleared, its channel deleted and its submission opened
            # afresh by the change queue's `results.submission.restart`, before the queue starts.
            # A round with a change of its submission in hand is left to it above.
            log.info("Recovery: asking the queue to clear and reopen round %s's submission",
                     round_id)
            await ask_submission_restart(
                bot.change_queue, bot.db_path, round_id, int(row["channel_id"]),
            )
        except Exception as error:
            log.exception("Recovery: could not recover round %s's submission channel", round_id)
            failures[round_id] = error
    if failures:
        raise RecoveryFailed(failures)
