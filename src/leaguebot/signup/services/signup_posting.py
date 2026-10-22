"""The one place signup sends a line into a channel of its own (docs/design/architecture.md,
"Posting to Discord").

Every post signup makes as a job goes through `post`, so the module has one posting step to list
and every post states who it may notify.
"""

from __future__ import annotations

from typing import Any

import discord

from leaguebot.core.models.change import StepFailedOnDiscord
from leaguebot.core.utils.in_a_job import posts_in_a_job

__all__ = ["delete_channel", "post"]


@posts_in_a_job
async def post(
    channel: discord.TextChannel, text: str | None = None, *, mentions: discord.AllowedMentions,
    refused: str, embed: discord.Embed | None = None, view: discord.ui.View | None = None,
) -> int:
    """Send *text*, with *embed* and *view* where given, to *channel*, notifying only what
    *mentions* allows, and give the id of the message posted. Raises `StepFailedOnDiscord` with
    *refused* where Discord refuses it."""
    extras: dict[str, Any] = {}
    if embed is not None:
        extras["embed"] = embed
    if view is not None:
        extras["view"] = view
    try:
        message = await channel.send(text, allowed_mentions=mentions, **extras)
    except discord.HTTPException as error:
        raise StepFailedOnDiscord(refused) from error
    return message.id


@posts_in_a_job
async def delete_channel(channel: discord.abc.GuildChannel, *, reason: str) -> None:
    """Delete *channel*, a driver's signup channel, for *reason*.

    The one place signup deletes a channel of its own as a job. Raises `discord.NotFound` where
    the channel is already gone and `discord.HTTPException` where Discord refuses; each job says
    what either means for it.
    """
    await channel.delete(reason=reason)
