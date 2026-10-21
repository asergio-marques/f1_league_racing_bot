"""`core.clean`: `/clean-bot` as a change of the queue (#439, 6b).

The command deletes the newest *count* of the bot's own messages in the channel it was run in, at
most ten, and nothing else. It is a member's change, repeatable, carried out on the league lane
whole, even in a channel the bot keeps for a conversation (one command, one lane).

The messages are chosen **when the change's first job runs**, not when the command is typed: the
`choose` job reads the channel's last two hundred messages and plans one `delete_message` job
for each of the newest *count* of the bot's, so a message the bot posted while the command waited
may be among them. A last job, `close`, writes the log channel's line. A delete Discord refuses
stops the queue until it is retried or discarded; a Discard leaves that message standing, uncounted,
and the member's reply names it. A message already gone by its delete completes the job without
stopping the queue, uncounted, and is not made up for by another.

**Why the scan is bounded.** The count is of the bot's messages, and a channel a league is talking
in holds other people's between them, so the scan reaches further than the count. It is still a
bound: a channel whose last two hundred messages hold none of the bot's deletes nothing rather than
reading to the beginning of time.

**Why ten.** Deletion on Discord has no undo, and the command once swept five hundred. Ten is
enough for the job it is for: clearing the tail of a multi-message command that did not clear
itself. The cap is the command's parameter (`MAX_DELETIONS`); the change holds to the *count* it is
given.
"""
from __future__ import annotations

import dataclasses
from typing import Any

import aiosqlite
import discord

from leaguebot.core.models.change import (
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services import standing_posts
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import league_guild

KIND = "core.clean"
CHOOSE = "choose"
DELETE_MESSAGE = "delete_message"
CLOSE = "close"

#: How far back the change looks to find the bot messages it was asked for.
SCAN_LIMIT = 200

#: The most a single invocation will delete; the command's parameter is bounded by it.
MAX_DELETIONS = 10

_COMMAND = "/clean-bot"


async def _channel(bot: LeagueBot, channel_id: int) -> discord.TextChannel | None:
    """The text channel *channel_id*, found in the cache or on the league's server, or None where
    it is on neither."""
    channel = bot.get_channel(channel_id)
    if channel is None:
        guild = await league_guild(bot)
        channel = None if guild is None else guild.get_channel(channel_id)
    return as_text_channel(channel)


async def _check(ctx: CheckContext) -> Verdict:
    """A text channel the bot may manage messages in."""
    channel = await _channel(ctx.bot, int(ctx.payload["channel_id"]))
    if not isinstance(channel, discord.TextChannel):
        return Verdict.refuse("⛔ This command can only be used in a text channel.")
    if not channel.permissions_for(channel.guild.me).manage_messages:
        return Verdict.refuse(
            "⛔ I need the Manage Messages permission in this channel to delete my messages."
        )
    return Verdict.go()


async def _choose(ctx: StepContext) -> StepResult:
    """Read the channel as it stands now and plan a `delete_message` job for each of the newest
    *count* of the bot's own messages. A channel gone since the change started (its check found
    it) stops the queue naming it."""
    channel_id = int(ctx.payload["channel_id"])
    count = int(ctx.payload["count"])
    channel = await _channel(ctx.bot, channel_id)
    if channel is None:
        raise StepFailedOnDiscord(f"the channel <#{channel_id}> no longer exists")
    me = ctx.bot.user
    chosen: list[int] = []
    # Newest first, which is what `channel.history` yields by default and what the command
    # promises: the most recent messages, so the tail of a command just run is what goes.
    async for message in channel.history(limit=SCAN_LIMIT):
        if len(chosen) >= count:
            break
        if message.author == me:
            chosen.append(message.id)
    return StepResult(
        result={"chosen": len(chosen)},
        then=tuple(
            PlannedStep(DELETE_MESSAGE, {
                "channel_id": channel_id, "message_id": each, "what": "bot message",
            })
            for each in chosen
        ),
    )


async def _describe_choose(ctx: StepContext) -> str:
    return f"choosing the bot's messages to delete in <#{ctx.payload['channel_id']}>"


async def _describe_delete(ctx: StepContext) -> str:
    payload = ctx.step_payload
    return f"deleting one of the bot's messages in <#{payload['channel_id']}>"


def _tally(ctx: OutcomeContext) -> tuple[int, int, list[str]]:
    """How many messages were deleted, how many were chosen, and the links of those a Discard
    left standing."""
    chosen = 0
    deleted = 0
    left: list[str] = []
    for step in ctx.steps:
        result = step.result or {}
        if step.name == CHOOSE:
            chosen = int(result.get("chosen", 0))
        elif step.name == DELETE_MESSAGE:
            if "discarded" in result:
                left.append(str(result.get("link", "a message")))
            elif result.get("deleted"):
                deleted += 1
    return chosen, deleted, left


async def _close(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
    """Write the log channel's line: a clean that deleted nothing is still recorded, the command
    having acted and found nothing."""
    _, deleted, left = _tally(ctx)
    return StepResult(
        result={"closed": True},
        lines=(
            f"{ctx.named} | {_COMMAND} | Success\n"
            f"  channel: <#{ctx.payload['channel_id']}>\n"
            f"  asked: {ctx.payload['count']}\n"
            f"  deleted: {deleted}\n"
            f"  could not delete: {len(left)}",
        ),
    )


async def _describe_close(_ctx: StepContext) -> str:
    return "recording the clean-up"


def _outcome(ctx: OutcomeContext) -> str:
    chosen, deleted, left = _tally(ctx)
    parts = [f"✅ Deleted {deleted} bot message(s)."]
    if chosen < int(ctx.payload["count"]):
        parts.append(f"Only {chosen} of the last {SCAN_LIMIT} messages were the bot's.")
    if left:
        parts.append(
            f"⚠️ {len(left)} message(s) could not be deleted and a league admin discarded "
            f"the job: {', '.join(left)}"
        )
    return " ".join(parts)


def clean_change() -> ChangeType:
    """The `core.clean` change type, for the builder to register."""
    steps: dict[str, Step] = {
        CHOOSE: Step(CHOOSE, StepKind.ACT, _choose, describe=_describe_choose),
        DELETE_MESSAGE: dataclasses.replace(
            standing_posts.delete_replaced_step(DELETE_MESSAGE), describe=_describe_delete
        ),
        CLOSE: Step(CLOSE, StepKind.SAVE, _close, describe=_describe_close),
    }

    def key(payload: dict[str, Any]) -> str:
        return f"{KIND}:{payload['channel_id']}:{payload['count']}"

    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(CHOOSE), PlannedStep(CLOSE)),
        steps=steps,
        check=_check,
        key=key,
        doing=lambda payload: (
            f"Deleting the bot's {payload['count']} newest message(s) in "
            f"<#{payload['channel_id']}>"
        ),
        outcome=_outcome,
        repeatable=True,
    )
