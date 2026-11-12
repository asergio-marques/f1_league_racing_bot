"""The bot-owned channel handler: the one place core posts in a channel the bot made, edits or
deletes a message in it, and deletes that channel.

A bot-owned channel is one the bot creates for a piece of work and deletes when the work is
done, a round's results submission channel among them. It is one of the output handlers
(`docs/design/architecture.md`, "Posting to Discord"), and the handler sends and nothing else:
**the caller says whom the post may notify** (*mentions*, required, so that no line in such a
channel leaves it to the bot's default), `post_in_owned_channel` gives back the id of the
message for the caller to keep, `edit_in_owned_channel` edits one in place,
`delete_in_owned_channel` deletes one, and `delete_owned_channel` deletes the channel whole, with
the reason the audit log shows. Whatever Discord raises reaches the caller unchanged, a
`discord.NotFound` included; a caller that wants a reason of its own wraps the failure in
`StepFailedOnDiscord` itself.

Creating the channel, and what the dialogue in it says, are the caller's. Every function runs in
a job of the change queue and carries `posts_in_a_job`; a post off the queue cannot go through
it.
"""

from __future__ import annotations

import discord

from leaguebot.core.utils.in_a_job import posts_in_a_job


@posts_in_a_job
async def post_in_owned_channel(
    channel,
    text: str,
    *,
    mentions: discord.AllowedMentions,
    view: discord.ui.View | None = None,
) -> int:
    """Post *text* in the bot-owned *channel*, notifying only whom *mentions* names, and give
    the id of the message sent, raising whatever Discord raises.

    *view*, where given, is the buttons the line carries."""
    parts: dict = {}
    if view is not None:
        parts["view"] = view
    message = await channel.send(text, allowed_mentions=mentions, **parts)
    return message.id


@posts_in_a_job
async def edit_in_owned_channel(message, *, mentions: discord.AllowedMentions, **parts) -> None:
    """Edit the message *message* of a bot-owned channel in place with *parts* (its ``content``
    and ``view``), notifying only whom *mentions* names, raising whatever Discord raises, a
    message already gone (``discord.NotFound``) included."""
    await message.edit(allowed_mentions=mentions, **parts)


@posts_in_a_job
async def delete_in_owned_channel(message) -> None:
    """Delete the message *message* of a bot-owned channel, the channel left standing, raising
    whatever Discord raises, a message already gone (``discord.NotFound``) included."""
    await message.delete()


@posts_in_a_job
async def delete_owned_channel(channel, *, reason: str) -> None:
    """Delete the bot-owned *channel* whole, *reason* being what the server's audit log shows,
    raising whatever Discord raises, a channel already gone (``discord.NotFound``) included."""
    await channel.delete(reason=reason)
