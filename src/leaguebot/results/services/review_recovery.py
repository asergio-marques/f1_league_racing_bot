"""What a restart does to a round whose submission channel was left open (#439, slice 2).

Restart recovery decides here, per round, and the entry point only reads the rows and calls it, so
the entry point gains no rule. A review is put back by asking the change queue, as the bot, for
the change that does it (``results.review.open``, ``results.appeals.open``,
``results.review.close_stale``): recovery posts nothing and deletes nothing of a review itself.

**A round whose review a change is carrying out is left alone**, a stopped one included. Nothing
overtakes a stopped job, so the change finishes it, and a second prompt posted beside it would
replace the one a queued approval was pressed on and refuse that approval at its check. A round with
no change in hand is put back by the stage it was left at:

- its report stage (the review flag is up): ``results.review.open`` with ``publish = not
  results_posted``, so interim results already posted are not posted again, and the old prompt's id
  for the change to delete once the new prompt stands;
- its appeals stage: ``results.appeals.open`` with the old prompt's id;
- a FINAL round with its channel still open, which only a run from before this change can leave:
  ``results.review.close_stale``, in place of a dead review posted again at every restart;
- a round whose last paste finished but whose review never opened, because its opening was
  discarded: ``results.review.open`` again. **The queue's own record is the durable mark that the
  last paste finished** (:func:`~leaguebot.core.services.change_queue.ever_asked`): the paste asks
  for the review as its last act, and the request is kept in whatever state it ends. Without the
  mark such a round, results all in and review flag still down, is indistinguishable from a first
  paste cut short, whose results recovery deletes.

Anything else is a first paste cut short and is not a review: :func:`recover_review` says so by
returning ``False`` and the entry point deals with it, outside the queue (A5 of the plan).

A resubmission a restart cut short is not carried by the change. Its collection lived in memory;
recovery clears it through the *abandon* callable it is handed, then asks for the review plainly.
The "penalties already applied" warning is gone with the column it read: the queue carries what was
staged, so a penalty applied and unannounced cannot happen.

Nothing here raises: this runs inside start-up, and one round unrecoverable must not stop the rest.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiosqlite
import discord

from leaguebot.core.models.change import ChangeOrigin
from leaguebot.core.models.round import RoundStatus
from leaguebot.core.services.change_queue import ever_asked
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.results.services import review_open_change
from leaguebot.results.services.review_changes import round_in_hand

log = logging.getLogger(__name__)

_BOT = ChangeOrigin.BOT

__all__ = ["Abandon", "recover_review"]

#: Clears a resubmission a restart cut short: the bot, the round, its channel (``None`` where it is
#: gone), the Cancel announcement's id, who started it and the round's name in the log.
Abandon = Callable[..., Awaitable[None]]


async def recover_review(
    bot: LeagueBot,
    row: aiosqlite.Row,
    guild: discord.Guild | None,
    abandon: Abandon,
) -> bool:
    """Put round ``row["round_id"]``'s review back, or leave it to the queue; give ``False`` where
    the round is no review but a first paste cut short.

    *row* is the round's open submission channel row with its round's ``status``, ``division_id``,
    ``round_number`` and the division's ``division_name``. *guild* is the league's server, or
    ``None`` where it is not in the cache, in which case a round that is a review is left as it is
    for the next restart to find.
    """
    round_id = int(row["round_id"])
    try:
        if await round_in_hand(bot.db_path, round_id):
            log.info("Recovery: round %s's review is in hand on the queue, left to it", round_id)
            return True
        stage = await _stage(bot.db_path, row)
        if stage is None:
            return False
        await _put_back(bot, row, guild, abandon, stage)
    except Exception:
        log.exception("Recovery: failed to restore the review of round %s", round_id)
    return True


async def _stage(db_path: str, row: aiosqlite.Row) -> str | None:
    """The stage a round with no change in hand was left at: ``"final"``, ``"appeals"``,
    ``"report"`` or ``"opening"``, or ``None`` where it is a first paste cut short."""
    status = row["status"] or ""
    if status == RoundStatus.FINAL.value:
        return "final"
    if row["in_penalty_review"]:
        return "appeals" if status == RoundStatus.AWAITING_APPEAL_VERDICTS.value else "report"
    round_id = int(row["round_id"])
    if any(
        payload.get("round_id") == round_id
        for payload in await ever_asked(db_path, [review_open_change.KIND])
    ):
        return "opening"
    return None


async def _put_back(
    bot: LeagueBot,
    row: aiosqlite.Row,
    guild: discord.Guild | None,
    abandon: Abandon,
    stage: str,
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
    channel = as_text_channel(guild.get_channel(channel_id))
    if channel is None:
        log.warning(
            "Recovery: channel %s not found, cannot restore the review of round %s",
            channel_id, round_id,
        )
        if resubmitting and stage == "report":
            # The round must not stay stuck as resubmitting with its channel gone.
            await abandon(
                bot, round_id, None, row["resubmit_prompt_message_id"],
                started_by=row["resubmit_started_by"], round_label=round_label,
            )
        return

    if stage == "final":
        await bot.change_queue.ask(
            review_open_change.CLOSE_STALE_KIND, {"round_id": round_id},
            origin=_BOT, what=f"closing the stale review of {round_label}",
        )
    elif stage == "appeals":
        payload: dict[str, Any] = {
            "round_id": round_id, "division_id": int(row["division_id"]),
        }
        if row["appeals_prompt_message_id"] is not None:
            payload["old_prompt_id"] = int(row["appeals_prompt_message_id"])
        await bot.change_queue.ask(
            review_open_change.APPEALS_OPEN_KIND, payload, origin=_BOT,
            what=f"posting the appeals review of {round_label} again",
        )
    else:
        if resubmitting:
            await abandon(
                bot, round_id, channel, row["resubmit_prompt_message_id"],
                started_by=row["resubmit_started_by"], round_label=round_label,
            )
        payload = {
            "round_id": round_id, "label": "Provisional Results",
            "publish": not row["results_posted"],
        }
        if row["prompt_message_id"] is not None:
            payload["old_prompt_id"] = int(row["prompt_message_id"])
        await bot.change_queue.ask(
            review_open_change.KIND, payload, origin=_BOT,
            what=f"opening the penalty review of {round_label} again",
        )
    log.info("Recovery: asked the queue to put back the %s of round %s", stage, round_id)
