"""The send and delete of a temporary message saying graphics are being drawn.

Rendering is slow — Inkscape on the Raspberry Pi the bot runs on takes seconds per
graphic, and several of the bot's jobs draw eight or more in a row. That is a long
silence in a channel somebody is watching, with nothing to say the bot is still working.

**The notice goes where the command that caused it was issued**, not where the graphics
land. A batch may post into three different channels — results, standings and verdicts
each have their own — but the person waiting is the one who ran the command, and the
readers of a standings channel are not waiting for anything. So there are two homes in
practice: the bot interaction channel for what a manager types, and the round's results
submission channel for what the results flow does, that flow's "command" being a button
press inside it.

The notice is **deleted**, never edited into a summary. Nothing it says survives, so
nothing worth keeping may go in it: faults belong in the posts themselves and in the log,
which is where they already go.

The notice is posted and taken down by two jobs of the change that draws the graphics; the two
functions here are what those jobs call, each raising whatever Discord raises so the queue stops
at it.

**`send_notice` is also the season review's posting point** (`season_review_changes`): its
pictures, report sections and question are sent through it with their other parts (*extra*), so
that core keeps one send of its own the direct-post check lists, rather than a second beside it
for the review alone.
"""

from __future__ import annotations

import discord

from leaguebot.core.utils.in_a_job import posts_in_a_job


@posts_in_a_job
async def send_notice(
    channel, text: str, *, allowed_mentions: discord.AllowedMentions | None = None, **extra
):
    """Send the notice *text* to *channel* and give the message, raising whatever Discord raises.

    The send of a notice, for a caller that treats a notice that cannot be
    posted as a failure of its own (a job of the change queue, which stops the queue at it).
    *allowed_mentions*, where given, says whom the notice may notify; a caller that gives none
    leaves it to the bot's default. *extra* are the message's other parts (a ``file``, a ``view``)
    for a post that is more than a line: the season review's pictures and question.
    """
    if allowed_mentions is not None:
        extra["allowed_mentions"] = allowed_mentions
    return await channel.send(text, **extra)


@posts_in_a_job
async def delete_notice(message) -> None:
    """Delete the notice *message*, raising whatever Discord raises, a message already gone
    (``discord.NotFound``) included.

    The delete of a notice, for a caller that decides for itself what a notice
    already gone, or one that cannot be deleted, means; also the delete of a standing post's
    replaced message (`standing_posts`), which decides for itself what a refusal means.
    """
    await message.delete()
