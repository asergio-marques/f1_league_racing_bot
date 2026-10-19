"""A message typed into a conversation's channel, answered as a turn of a change (#439, 6b).

A listener that receives a message it would act on does one of two things. Where a turn is still
waiting in the message's channel (`turn_waiting`), it turns the message away (`turn_away`). Where
none is, it marks the message with ⏳ at once (`mark`) and asks the change that answers it, a change
type declaring `turn_channel`; that change's last job is `clear_mark_step`, which takes the ⏳ off.

**A channel waits on the queue's records, never on memory.** `turn_waiting` reads the changes in
hand of the kinds that declare a `turn_channel`, so a restart, a stop and a Discard all leave it
right: a stopped turn still holds its channel, a discarded one frees it. A league change a turn
handed over (a signup sent for review) declares the channel too, and so holds it until it is done;
a league stop alone, at a change that names no turn channel, holds nothing.

**The turn-away is off the queue.** Its note and the deletion of the turned-away message are made
at once, so that the member is told while the earlier answer waits; queued, they would wait behind
it. A refusal of either is logged on the host and lost, never retried, and the note stays in the
channel. *Rejected:* queueing them, which would put the note after the very turn it explains and
could stop a lane for a courtesy.

**The mark is a courtesy and the clear is a job.** A mark Discord refuses is logged and the turn
goes ahead. The clearing of it is an ordinary job, so that a mark which cannot be taken off stops
the lane and is tried again; a message since deleted completes it.
"""
from __future__ import annotations

import logging
from collections.abc import Collection

import discord

from leaguebot.core.models.change import StepFailedOnDiscord, StepKind, StepResult
from leaguebot.core.services.change_queue import ChangeType, Step, StepContext, unfinished
from leaguebot.core.services.output_router import OutputRouter

log = logging.getLogger(__name__)

#: The mark a typed message wears while its turn waits.
MARK = "⏳"

#: The job that takes the mark off.
CLEAR_MARK = "clear_mark"

#: What the turn-away says; `{member}` is the author's mention, the only one the note makes.
TURN_AWAY_NOTE = (
    "⏳ {member}, your earlier message here is still waiting its turn, so this one was not "
    "taken — send it again once that has been answered."
)


async def turn_waiting(
    db_path: str, types: Collection[ChangeType], channel_id: int
) -> bool:
    """Whether a turn is waiting in *channel_id*: a change of a kind in *types* that declares
    `turn_channel` is queued, running or stopped, and names that channel."""
    declaring = {t.kind: t.turn_channel for t in types if t.turn_channel is not None}
    for kind, turn_channel in declaring.items():
        for payload in await unfinished(db_path, [kind]):
            if turn_channel(payload) == channel_id:
                return True
    return False


async def mark(message: discord.Message) -> None:
    """Mark *message* with ⏳ at once. A mark Discord refuses is logged: the turn goes ahead."""
    try:
        await message.add_reaction(MARK)
    except discord.HTTPException:
        log.warning("could not mark message %s in channel %s", message.id, message.channel.id,
                    exc_info=True)


async def _clear_mark(ctx: StepContext) -> StepResult:
    """Take the bot's ⏳ off the typed message. A message or channel since gone completes the job;
    any other refusal stops the lane, to be tried again."""
    # The message is named by the job's own payload where it carries it (a change that hands the
    # clearing to another reaches it so), else by the change's.
    payload = ctx.step_payload if "message_id" in ctx.step_payload else ctx.payload
    bot_user = ctx.bot.user
    if bot_user is None:
        raise StepFailedOnDiscord("the bot is not connected to Discord")
    channel_id = int(payload["channel_id"])
    found = ctx.bot.get_channel(channel_id)
    partial = (
        found if isinstance(found, (discord.TextChannel, discord.Thread))
        else ctx.bot.get_partial_messageable(channel_id)
    )
    message = partial.get_partial_message(int(payload["message_id"]))
    try:
        await message.remove_reaction(MARK, bot_user)
    except discord.NotFound:
        return StepResult(result={"gone": True})
    except discord.HTTPException as exc:
        raise StepFailedOnDiscord("the mark on the typed message could not be taken off") from exc
    return StepResult(result={"cleared": True})


async def _describe_clear(ctx: StepContext) -> str:
    return f"taking the ⏳ off a message in <#{ctx.payload['channel_id']}>"


def clear_mark_step() -> Step:
    """The `clear_mark` job, the last of a turn: it reads the message's `channel_id` and
    `message_id` from the change's payload."""
    return Step(CLEAR_MARK, StepKind.ACT, _clear_mark, describe=_describe_clear)


async def turn_away(message: discord.Message, note: str) -> None:
    """Turn *message* away at once, off the queue: post *note* (with `{member}` filled in) in its
    channel, mentioning the author alone, and delete the message. Each is tried once; a refusal is
    logged and lost, and the note stays. The posting is `OutputRouter.turn_away`'s."""
    await OutputRouter.turn_away(message, note)
