"""What a restart does to a round whose submission channel was left open (#439, slice 2).

Restart recovery decides here, per round, and the entry point only reads the rows and calls it, so
the entry point gains no rule. A review is put back by asking the change queue, as the bot, for
the change that does it (``results.review.open``, ``results.appeals.open``,
``results.review.close_stale``): recovery posts nothing and deletes nothing of a review itself.

**A round whose review, or whose submission, a change is carrying out is left alone**, a stopped
one included: a paste's turn, the cancelled sessions' lines or the closing of the channel they
leave, a resubmission or its swap (`SUBMISSION_KINDS`). Recovery over any of them would delete the
sessions of a finished round as a first paste cut short, or end a resubmission whose swap is queued. Nothing
overtakes a stopped job, so the change finishes it, and a second prompt posted beside it would
replace the one a queued approval was pressed on and refuse that approval at its check. A round with
no change in hand is put back by the stage it was left at:

- its report stage (the review flag is up): ``results.review.open`` with ``publish = not
  results_posted``, so interim results already posted are not posted again, and the old prompt's id
  for the change to delete once the new prompt stands;
- its appeals stage: ``results.appeals.open`` with the old prompt's id;
- a FINAL round with its channel still open, which only a run from before this change can leave:
  ``results.review.close_stale``, in place of a dead review posted again at every restart;
- a round that ended cancelled, every session being entered as not held, with its channel still
  open (the bot stopped, or the closing was discarded, before it was closed): only
  ``results.submission.close_channel``, as a restart, deleting the channel. Its sessions stand and
  its notes were posted, so it is never taken for a first paste cut short, whose results recovery
  deletes, nor for a review, whatever the flag a resubmission left up;
- a round whose last paste finished but whose review never opened, because its opening was
  discarded: ``results.review.open`` again. **The queue's own record is the durable mark that the
  last paste finished** (:func:`~leaguebot.core.services.change_queue.ever_asked`): the paste asks
  for the review as its last act, and the request is kept in whatever state it ends. Without the
  mark such a round, results all in and review flag still down, is indistinguishable from a first
  paste cut short, whose results recovery deletes.

Anything else is a first paste cut short and is not a review: :func:`recover_review` says so by
returning ``False`` and the entry point deals with it, outside the queue (A5 of the plan).

A resubmission a restart cut short is ended by the review's opening itself. Its collection lived
in memory; recovery asks ``results.review.open`` returning from a restart, whose own jobs clear the
flag and the drafts, take the Cancel button down and tell the channel (and complete, posting
nothing, where the channel is gone).
The "penalties already applied" warning is gone with the column it read: the queue carries what was
staged, so a penalty applied and unannounced cannot happen.

Nothing here raises: this runs inside start-up, and one round unrecoverable must not stop the rest.
"""

from __future__ import annotations

import logging
from typing import Any

import aiosqlite
import discord

from leaguebot.core.models.change import ChangeOrigin
from leaguebot.core.models.round import RoundStatus
from leaguebot.core.services.change_queue import ChangeQueue, ever_asked, unfinished
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.log_lines import name_of_member
from leaguebot.results.services import (
    resubmission_changes,
    review_open_change,
    submission_changes,
    submission_turn_change,
)
from leaguebot.results.services.review_changes import round_in_hand

log = logging.getLogger(__name__)

_BOT = ChangeOrigin.BOT

#: Every kind of change that carries out part of a round's submission: a paste's turn, the
#: cancelled sessions' lines and the closing of a channel they leave, a resubmission and its swap,
#: and the opening of the submission and its clearing at a restart (the row of an opening whose
#: prompt is still to be posted already stands open). A payload names its round, or only the
#: channel it was typed in.
SUBMISSION_KINDS: tuple[str, ...] = (
    submission_changes.KIND,
    submission_changes.RESTART_KIND,
    submission_turn_change.KIND,
    submission_turn_change.CONFIG_KIND,
    submission_turn_change.RESUBMISSION_KIND,
    submission_turn_change.RESUBMISSION_CONFIG_KIND,
    submission_turn_change.SWAP_KIND,
    submission_changes.ALL_CANCELLED_KIND,
    submission_changes.CLOSE_KIND,
    resubmission_changes.KIND,
)


async def _submission_in_hand(db_path: str, round_id: int, channel_id: int) -> bool:
    """Whether a change of a submission kind naming *round_id* or its *channel_id* is queued,
    running or stopped."""
    return any(
        payload.get("round_id") == round_id or payload.get("channel_id") == channel_id
        for payload in await unfinished(db_path, SUBMISSION_KINDS)
    )

__all__ = ["recover_review"]

async def recover_review(
    bot: LeagueBot,
    row: aiosqlite.Row,
    guild: discord.Guild | None,
    *,
    queue: ChangeQueue,
) -> bool:
    """Put round ``row["round_id"]``'s review back, or leave it to the queue; give ``False`` where
    the round is no review but a first paste cut short.

    *queue* is the change queue the review is put back through, handed by whoever calls the
    recovery and never looked up on the bot (architecture.md, "Services stay attached to the bot,
    and are built in one place").

    *row* is the round's open submission channel row with its round's ``status``, ``division_id``,
    ``round_number`` and the division's ``division_name``. *guild* is the league's server, or
    ``None`` where it is not in the cache, in which case a round that is a review is left as it is
    for the next restart to find.
    """
    round_id = int(row["round_id"])
    try:
        if await round_in_hand(bot.db_path, round_id) or await _submission_in_hand(
            bot.db_path, round_id, int(row["channel_id"])
        ):
            log.info("Recovery: round %s's review is in hand on the queue, left to it", round_id)
            return True
        stage = await _stage(bot.db_path, row)
        if stage is None:
            return False
        await _put_back(bot, row, guild, stage, queue)
    except Exception:
        log.exception("Recovery: failed to restore the review of round %s", round_id)
    return True


async def _stage(db_path: str, row: aiosqlite.Row) -> str | None:
    """The stage a round with no change in hand was left at: ``"final"``, ``"cancelled"``,
    ``"appeals"``, ``"report"`` or ``"opening"``, or ``None`` where it is a first paste cut short.

    A round that ended cancelled is told before its review flag is read: a resubmission leaves the
    flag at 1, and the round is no review but one whose channel is left to close."""
    status = row["status"] or ""
    if status == RoundStatus.FINAL.value:
        return "final"
    if status == RoundStatus.CANCELLED.value:
        return "cancelled"
    if row["in_penalty_review"]:
        return "appeals" if status == RoundStatus.AWAITING_APPEAL_VERDICTS.value else "report"
    round_id = int(row["round_id"])
    if any(
        payload.get("round_id") == round_id
        for payload in await ever_asked(db_path, [review_open_change.KIND])
    ):
        return "opening"
    return None


async def _restart_payload(bot: LeagueBot, row: aiosqlite.Row, round_label: str) -> dict[str, Any]:
    """What the review's opening needs to end a resubmission a restart cut short: the round, the
    announcement whose Cancel button comes down, and who started it (named now, as a save may
    not wait on Discord) with the round's name, for the lapse line."""
    return {
        "round_id": int(row["round_id"]), "channel_id": int(row["channel_id"]),
        "label": "Provisional Results", "publish": False, "returning": "restart",
        "while_resubmitting": True,
        "round_number": int(row["round_number"]), "division_name": row["division_name"],
        "cancel_message_id": row["resubmit_prompt_message_id"],
        "started_by_named": await name_of_member(bot, row["resubmit_started_by"]),
        "round_label": round_label,
    }


async def _put_back(
    bot: LeagueBot,
    row: aiosqlite.Row,
    guild: discord.Guild | None,
    stage: str,
    queue: ChangeQueue,
) -> None:
    round_id = int(row["round_id"])
    channel_id = int(row["channel_id"])
    resubmitting = bool(row["resubmitting"])
    round_label = f"round {row['round_number']} ({row['division_name']})"

    if guild is None:
        log.warning(
            "Recovery: the league's server is not in the cache, cannot restore the review of "
            "round %s", round_id,
        )
        return
    if stage == "cancelled":
        # Its sessions stand and its notes were posted: only the channel is left, whether it
        # stands or has been deleted since (the closing then completes at once).
        await queue.ask(
            submission_changes.CLOSE_KIND,
            {"round_id": round_id, "channel_id": channel_id, "round_number": int(row["round_number"]),
             "restart": True},
            origin=_BOT, what=f"closing the submission channel of {round_label}",
        )
        log.info("Recovery: asked the queue to close the channel of round %s, ended cancelled", round_id)
        return
    channel = as_text_channel(guild.get_channel(channel_id))
    if channel is None:
        log.warning(
            "Recovery: channel %s not found, cannot restore the review of round %s",
            channel_id, round_id,
        )
        if resubmitting and stage == "report":
            # The round must not stay stuck as resubmitting with its channel gone: the review's
            # opening ends the resubmission and records its lapse, posting nothing.
            await queue.ask(
                review_open_change.KIND,
                {**await _restart_payload(bot, row, round_label), "channel_gone": True},
                origin=_BOT, what=f"ending the resubmission of {round_label}",
            )
        return

    if stage == "final":
        await queue.ask(
            review_open_change.CLOSE_STALE_KIND,
            {
                "round_id": round_id, "round_number": int(row["round_number"]),
                "division_name": row["division_name"],
            },
            origin=_BOT, what=f"closing the stale review of {round_label}",
        )
    elif stage == "appeals":
        payload: dict[str, Any] = {
            "round_id": round_id, "division_id": int(row["division_id"]),
            "round_number": int(row["round_number"]), "division_name": row["division_name"],
        }
        if row["appeals_prompt_message_id"] is not None:
            payload["old_prompt_id"] = int(row["appeals_prompt_message_id"])
        await queue.ask(
            review_open_change.APPEALS_OPEN_KIND, payload, origin=_BOT,
            what=f"posting the appeals review of {round_label} again",
        )
    else:
        payload = {
            "round_id": round_id, "label": "Provisional Results",
            "publish": not row["results_posted"],
            "round_number": int(row["round_number"]), "division_name": row["division_name"],
        }
        if resubmitting:
            # The collection lived in memory and is gone: the opening ends the resubmission in
            # its own jobs, publishing nothing, the round's results being posted already.
            payload.update(await _restart_payload(bot, row, round_label), publish=False)
        if row["prompt_message_id"] is not None:
            payload["old_prompt_id"] = int(row["prompt_message_id"])
        await queue.ask(
            review_open_change.KIND, payload, origin=_BOT,
            what=f"opening the penalty review of {round_label} again",
        )
    log.info("Recovery: asked the queue to put back the %s of round %s", stage, round_id)
