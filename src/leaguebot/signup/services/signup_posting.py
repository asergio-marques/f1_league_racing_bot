"""The one place signup sends a line into a channel of its own (docs/design/architecture.md,
"Posting to Discord").

Every post signup makes as a job goes through here, so the module has one posting step to list and
every post states who it may notify. The posting itself is core's: a line in a driver's signup
channel, a channel the bot made for that driver, goes through the bot-owned channel handler
(`post` and `delete_channel`), and the information message and the closed notice, which the bot
keeps standing in the signup channel, through the standing-post handler (`post_standing_message`).
What is signup's is the wrap: where Discord refuses a post, the job fails with the reason the
caller gives.
"""

from __future__ import annotations

import discord

from leaguebot.core.models.change import StepFailedOnDiscord
from leaguebot.core.services.posting.owned_channel_handler import (
    delete_owned_channel,
    post_in_owned_channel,
)
from leaguebot.core.services.posting.standing_post_handler import post_standing
from leaguebot.core.utils.in_a_job import posts_in_a_job

__all__ = ["delete_channel", "post", "post_standing_message"]


@posts_in_a_job
async def post(
    channel: discord.TextChannel, text: str, *, mentions: discord.AllowedMentions,
    refused: str, view: discord.ui.View | None = None,
) -> int:
    """Send *text*, with *view* where given, to the driver's signup *channel*, notifying only what
    *mentions* allows, and give the id of the message posted. Raises `StepFailedOnDiscord` with
    *refused* where Discord refuses it."""
    try:
        return await post_in_owned_channel(channel, text, mentions=mentions, view=view)
    except discord.HTTPException as error:
        raise StepFailedOnDiscord(refused) from error


@posts_in_a_job
async def post_standing_message(
    channel: discord.TextChannel, text: str | None = None, *, mentions: discord.AllowedMentions,
    refused: str, embed: discord.Embed | None = None, view: discord.ui.View | None = None,
) -> int:
    """Send the standing post of *text*, *embed* and *view*, whichever are given, to the signup
    *channel*, notifying only what *mentions* allows, and give the id of the message posted. Raises
    `StepFailedOnDiscord` with *refused* where Discord refuses it."""
    try:
        return await post_standing(channel, text, mentions=mentions, embed=embed, view=view)
    except discord.HTTPException as error:
        raise StepFailedOnDiscord(refused) from error


@posts_in_a_job
async def delete_channel(channel: discord.abc.GuildChannel, *, reason: str) -> None:
    """Delete *channel*, a driver's signup channel, for *reason*.

    The one place signup deletes a channel of its own as a job. Raises `discord.NotFound` where
    the channel is already gone and `discord.HTTPException` where Discord refuses; each job says
    what either means for it.
    """
    await delete_owned_channel(channel, reason=reason)
