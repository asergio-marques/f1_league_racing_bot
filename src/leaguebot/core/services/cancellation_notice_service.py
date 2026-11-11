"""Core's own part of a round, a division or a season called off.

Core announces nothing of its own: telling the drivers a race is off is each enabled module's
own notice, in its own words, found and posted through its own subscriber to the
`cancellation_notices` hook (`cancellation_changes`, "Cancellation notices";
`attendance.services.cancellation_notice`, `weather.services.cancellation_notice`,
`results.services.cancellation_notice`). A round's cancellation, a division's, a season's, and a
round ended because every session was entered as not held (results' `submission_changes`) all
call the hook, inside the save, for every division it tells; the calendar, owned by core, is
refreshed whatever the modules, through its own step.

What stays core's own:

- `NoticeFailure`, `failure_lines` and `failure_log_lines` — the one shape every failure to
  notify is reported in, whichever module or job it came from;
- `round_label` — the round's name in a notice, shared because attendance's, weather's and
  results' notices word a round the same way;
- `post_cancellation_notice` and `require_division` — the one shape attendance's, weather's and
  results' own notice jobs share: posting to a channel id read from the module's own table,
  answered "no channel is set" where there is none, a channel gone from the server or a send
  Discord refuses raising `StepFailedOnDiscord`; and finding the division a job's own payload
  names, or `LookupError` where it is gone. Each module keeps only what is its own: the channel
  read, the note's wording, and its mentions and silence. Posting itself is `send_notice`
  (`notice_handler`), which stays the one place that sends, edits and deletes a notice; this file
  is core's own part of a cancellation, not the handler, so the "no channel is set" answer and the
  division lookup, neither of them a send, live here instead.

A module's own notice is a job of its own on the change queue (`cancellation_changes`): one
Discord refuses stops the queue until it is retried or discarded, and is named only once
discarded, through `notice_target` on the job's own payload (`cancellation_changes.not_notified`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import discord

from leaguebot.core.models.change import StepFailedOnDiscord
from leaguebot.core.services.posting.notice_handler import send_notice
from leaguebot.core.services.season_service import SeasonService
from leaguebot.core.utils.in_a_job import posts_in_a_job


@dataclass(frozen=True)
class NoticeFailure:
    """One place a cancellation could not reach."""

    division_name: str
    #: What was not reached, in the words the command's reply uses.
    target: str
    reason: str

    def describe(self) -> str:
        return f"**{self.division_name}** — {self.target}: {self.reason}"


def round_label(round_number: int, track_name: str | None) -> str:
    """A round's name in a cancellation notice, word for word: shared because attendance's,
    weather's and results' own notices all word a round the same way."""
    return f"Round {round_number} ({track_name or 'Mystery'})"


@posts_in_a_job
async def post_cancellation_notice(
    guild, channel_id: int | None, text: str, *, mentions: discord.AllowedMentions, **parts,
) -> str | None:
    """Post a module's cancellation notice *text* to *channel_id* on *guild*, as a job on the
    change queue, returning "no channel is set" where *channel_id* is None, raising
    `StepFailedOnDiscord` (no cause) where the channel is set and gone, and `from` Discord's
    refusal where it refuses the send. Any other exception is a fault of the bot's own and
    propagates unchanged.

    The one shape attendance's, weather's and results' own cancellation notices share: each
    module reads its own channel id and builds its own wording, mentions and silence (*parts*),
    and calls this to post it. Posting itself is `notice_handler.send_notice`; this merely answers
    "no channel is set" and finds the channel before that send, which is why it lives here and not
    in the handler that only sends, edits and deletes."""
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


async def require_division(seasons: SeasonService, season_id: int, division_id: int) -> Any:
    """The division *division_id* of season *season_id*, as `seasons.get_divisions` gives it, or
    `LookupError` where it is no longer there — the lookup a cancellation notice job makes for the
    division its own payload names."""
    division = next(
        (each for each in await seasons.get_divisions(int(season_id)) if each.id == int(division_id)),
        None,
    )
    if division is None:
        raise LookupError(f"division {division_id} is no longer there")
    return division


#: How many failures a reply names before summing up the rest, and how long one line may run.
#: A reply is one Discord message, capped at 2,000 characters; a season of many divisions whose
#: every channel is gone, or one long error from Discord, would otherwise lose the whole reply.
#: The log channel carries every failure in full.
MAX_LINES = 8
MAX_LINE = 210


def failure_lines(failures: list[NoticeFailure]) -> str:
    """The part of a command's reply naming what could not be reached, or an empty string."""
    if not failures:
        return ""
    lines = []
    for failure in failures[:MAX_LINES]:
        text = failure.describe()
        if len(text) > MAX_LINE:
            text = text[: MAX_LINE - 1] + "…"
        lines.append(f"  • {text}")
    if len(failures) > MAX_LINES:
        lines.append(f"  • and {len(failures) - MAX_LINES} more; the log channel names them all")
    return "\n⚠️ **Not notified**\n" + "\n".join(lines)


def failure_log_lines(failures: list[NoticeFailure]) -> str:
    """Every failure, one line each, for the command's entry in the log channel."""
    return "".join(f"\n  not notified: {failure.describe()}" for failure in failures)
