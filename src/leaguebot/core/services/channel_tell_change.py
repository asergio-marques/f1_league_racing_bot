"""`channel.tell`: a change that tells a channel one line, on the change queue (#439, 6b).

A job or a start-up step that needs a one-line notice in a channel asks this change, as the bot's.
Its one job, `tell`, posts the payload's `text` in the channel `channel_id`, and stops the queue
where Discord refuses the post or the channel is gone, to be tried again on the schedule or
discarded. The line mentions nobody but the member the payload's `mention` names, by id. It is
repeatable: two requests for the same line are two lines.

The mid-season confirmation's fallback and the review take-down's restart notice use it.
`season_approval_tell_change` is left as it is: folding it in would be a refactor.
"""
from __future__ import annotations

import discord

from leaguebot.core.models.change import (
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services.change_queue import ChangeType, CheckContext, Step, StepContext
from leaguebot.core.services.notice_handler import send_notice
from leaguebot.core.utils.in_a_job import posts_in_a_job

KIND = "channel.tell"
TELL = "tell"


@posts_in_a_job
async def _tell(ctx: StepContext) -> StepResult:
    """Post the line. A channel no longer on the server stops the queue naming it; whatever
    Discord refuses raises through, and the worker stops the queue at the job."""
    channel_id = int(ctx.payload["channel_id"])
    channel = ctx.bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await ctx.bot.fetch_channel(channel_id)
        except discord.NotFound as exc:
            raise StepFailedOnDiscord(
                f"the channel <#{channel_id}> no longer exists or cannot be posted in"
            ) from exc
    mention = ctx.payload.get("mention")
    allowed = (
        discord.AllowedMentions(everyone=False, roles=False, users=[discord.Object(id=int(mention))])
        if mention is not None
        else discord.AllowedMentions.none()
    )
    await send_notice(channel, str(ctx.payload["text"]), allowed_mentions=allowed)
    return StepResult(result={"told": True})


async def _describe(ctx: StepContext) -> str:
    return f"telling <#{ctx.payload['channel_id']}> one line"


async def _check(_ctx: CheckContext) -> Verdict:
    return Verdict.go()


def channel_tell_change() -> ChangeType:
    """The `channel.tell` change type, for the builder to register."""
    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(TELL),),
        steps={TELL: Step(TELL, StepKind.ACT, _tell, describe=_describe)},
        check=_check,
        key=lambda payload: (
            f"{KIND}:{payload['channel_id']}:{payload.get('mention')}:{payload['text']}"
        ),
        doing=lambda payload: f"Telling <#{payload['channel_id']}> one line",
        outcome=lambda _ctx: "",
        repeatable=True,
    )

