"""The notice handler: the one place core sends, edits and deletes a notice.

A notice is a message posted to say something to whoever is waiting in a channel. It is one of
the output handlers (`docs/design/architecture.md`, "Posting to Discord"), and the handler sends
and nothing else: **the caller says whom the notice may notify** (*mentions*, required, so that
no notice leaves it to the bot's default), the handler gives back the message, and whatever
Discord raises reaches the caller unchanged, a `discord.NotFound` included, so that the change
queue's own handling of a refusal goes on as before. A caller that wants a reason of its own
wraps the failure in `StepFailedOnDiscord` itself. Every function runs in a job of the change
queue and carries `posts_in_a_job`; a post off the queue cannot go through it.

**The "drawing graphics" notice is one use of it**, and the one this file began as.
Rendering is slow — Inkscape on the Raspberry Pi the bot runs on takes seconds per
graphic, and several of the bot's jobs draw eight or more in a row. That is a long
silence in a channel somebody is watching, with nothing to say the bot is still working.

**That notice goes where the command that caused it was issued**, not where the graphics
land. A batch may post into three different channels — results, standings and verdicts
each have their own — but the person waiting is the one who ran the command, and the
readers of a standings channel are not waiting for anything. So there are two homes in
practice: the bot interaction channel for what a manager types, and the round's results
submission channel for what the results flow does, that flow's "command" being a button
press inside it.

That notice is **deleted**, never edited into a summary. Nothing it says survives, so
nothing worth keeping may go in it: faults belong in the posts themselves and in the log,
which is where they already go. It is posted and taken down by two jobs of the change that draws
the graphics; `send_notice` and `delete_notice` are what those jobs call.

**`send_notice` is also the season review's posting point** (`season_review_changes`): its
pictures, report sections and question are sent through it with their other parts (*parts*), so
that core keeps one send of its own rather than a second beside it for the review alone.
`edit_notice` edits a notice in place; no job of the bot edits one yet.

**`post_cancellation_notice` is the one shape attendance's, weather's and results' own
cancellation notices share** (#283): a channel id read from the module's own table, answered "no
channel is set" where there is none; a channel gone from the server raising `StepFailedOnDiscord`
with no cause; a send Discord refuses raising it `from` that refusal; any other exception a fault
of the bot's own, left to propagate. Each module keeps only what is its own: the channel read, the
note's wording, and its mentions and silence. **`require_division`** is the matching lookup: the
division a job's own payload names, or `LookupError` where it is gone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord

from leaguebot.core.models.change import StepFailedOnDiscord
from leaguebot.core.utils.in_a_job import posts_in_a_job

if TYPE_CHECKING:
    from leaguebot.core.services.season_service import SeasonService


@posts_in_a_job
async def send_notice(channel, text: str, *, mentions: discord.AllowedMentions, **parts):
    """Send the notice *text* to *channel* and give the message, raising whatever Discord raises.

    *mentions* says whom the notice may notify, and is required: a caller that wants nobody
    notified says so with ``AllowedMentions.none()``. *parts* are the message's other parts (a
    ``file``, a ``view``, ``silent``) for a post that is more than a line: the season review's
    pictures and question, and a cancellation note sent silently.
    """
    return await channel.send(text, allowed_mentions=mentions, **parts)


@posts_in_a_job
async def edit_notice(message, *, mentions: discord.AllowedMentions, **parts) -> None:
    """Edit the notice *message* in place with *parts* (its ``content``, ``embed`` or ``view``),
    raising whatever Discord raises, a message already gone (``discord.NotFound``) included.

    *mentions* says whom the edited notice may notify, and is required.
    """
    await message.edit(allowed_mentions=mentions, **parts)


@posts_in_a_job
async def post_cancellation_notice(
    guild, channel_id: int | None, text: str, *, mentions: discord.AllowedMentions, **parts,
) -> str | None:
    """Post a module's cancellation notice *text* to *channel_id* on *guild*, as a job on the
    change queue, returning "no channel is set" where *channel_id* is None, raising
    `StepFailedOnDiscord` (no cause) where the channel is set and gone, and `from` Discord's
    refusal where it refuses the send. Any other exception is a fault of the bot's own and
    propagates unchanged.

    The one shape attendance's, weather's and results' own cancellation notices share (#283): each
    module reads its own channel id and builds its own wording, mentions and silence (*parts*),
    and calls this to post it."""
    if not channel_id:
        return "no channel is set"
    channel = guild.get_channel(int(channel_id)) if guild is not None else None
    if channel is None:
        raise StepFailedOnDiscord(f"the channel <#{channel_id}> is no longer on the server")
    try:
        await send_notice(channel, text, mentions=mentions, **parts)
    except discord.HTTPException as exc:
        raise StepFailedOnDiscord(
            f"the message could not be posted to <#{channel_id}>: {exc}"
        ) from exc
    return None


async def require_division(seasons: "SeasonService", season_id: int, division_id: int) -> Any:
    """The division *division_id* of season *season_id*, as `seasons.get_divisions` gives it, or
    `LookupError` where it is no longer there — the lookup a cancellation notice job makes for the
    division its own payload names (#283)."""
    division = next(
        (each for each in await seasons.get_divisions(int(season_id)) if each.id == int(division_id)),
        None,
    )
    if division is None:
        raise LookupError(f"division {division_id} is no longer there")
    return division


@posts_in_a_job
async def delete_notice(message) -> None:
    """Delete the notice *message*, raising whatever Discord raises, a message already gone
    (``discord.NotFound``) included.

    The delete of a notice, for a caller that decides for itself what a notice
    already gone, or one that cannot be deleted, means.
    """
    await message.delete()
