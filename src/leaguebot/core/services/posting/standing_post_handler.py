"""The standing-post handler: the one place a standing post is posted, edited and deleted.

A standing post is one the bot keeps up to date in place: a calendar, a team lineup, the hub
panel, a weather forecast. It is one of the output handlers (`docs/design/architecture.md`, "Posting to Discord"),
and the handler sends and nothing else: **the caller says whom the post may notify**
(*mentions*, required, so that no standing post leaves it to the bot's default), `post_standing`
gives back the id of the message for the caller to keep, `edit_standing` edits it in place, and
`delete_standing` deletes the one a new post replaced. Whatever Discord raises reaches the
caller unchanged, a `discord.NotFound` included, so that the change queue's own handling of a
message already gone goes on as before; a caller that wants a reason of its own wraps the
failure in `StepFailedOnDiscord` itself.

What the caller keeps is its own: the jobs that remember the new id and delete the old message
are `standing_posts`', and a picture a post carries is built and closed by the caller. Every
function runs in a job of the change queue and carries `posts_in_a_job`; a post off the queue
cannot go through it.
"""

from __future__ import annotations

import discord
from discord.utils import MISSING

from leaguebot.core.utils.in_a_job import posts_in_a_job


@posts_in_a_job
async def post_standing(
    channel,
    content: str | None = None,
    *,
    mentions: discord.AllowedMentions,
    embed: discord.Embed | None = None,
    file: discord.File | None = None,
    view: discord.ui.View | None = None,
) -> int:
    """Post a standing post to *channel*, notifying only whom *mentions* names, and give the
    id of the message sent, raising whatever Discord raises.

    The post is whichever of *content*, *embed*, *file* and *view* are given.
    """
    parts: dict = {}
    if embed is not None:
        parts["embed"] = embed
    if file is not None:
        parts["file"] = file
    if view is not None:
        parts["view"] = view
    message = await channel.send(content, allowed_mentions=mentions, **parts)
    return message.id


@posts_in_a_job
async def edit_standing(
    message,
    *,
    mentions: discord.AllowedMentions,
    content: str | None = MISSING,
    embed: discord.Embed | None = MISSING,
    view: discord.ui.View | None = MISSING,
) -> None:
    """Edit the standing post *message* in place with whichever of *content*, *embed* and *view*
    are given, notifying only whom *mentions* names, raising whatever Discord raises, a message
    already gone (``discord.NotFound``) included.

    A part left out is left as it stands; a part given as ``None`` is taken off the post, as the
    hub panel's buttons are when nothing is offered.
    """
    given = {"content": content, "embed": embed, "view": view}
    parts = {name: part for name, part in given.items() if part is not MISSING}
    await message.edit(allowed_mentions=mentions, **parts)


@posts_in_a_job
async def delete_standing(message) -> None:
    """Delete the standing post *message*, the one a new post replaced, raising whatever Discord
    raises, a message already gone (``discord.NotFound``) included. What a refusal means is the
    caller's to decide (`standing_posts`)."""
    await message.delete()
