"""The one place signup sends a line into a channel of its own (docs/design/architecture.md,
"Posting to Discord").

Every post signup makes as a job goes through `post`, so the module has one posting step to list
and every post states who it may notify.
"""

from __future__ import annotations

import discord

from leaguebot.core.models.change import StepFailedOnDiscord
from leaguebot.core.utils.in_a_job import posts_in_a_job

__all__ = ["post"]


@posts_in_a_job
async def post(
    channel: discord.TextChannel, text: str, *, mentions: discord.AllowedMentions, refused: str
) -> int:
    """Send *text* to *channel*, notifying only what *mentions* allows, and give the id of the
    message posted. Raises `StepFailedOnDiscord` with *refused* where Discord refuses it."""
    try:
        message = await channel.send(text, allowed_mentions=mentions)
    except discord.HTTPException as error:
        raise StepFailedOnDiscord(refused) from error
    return message.id
