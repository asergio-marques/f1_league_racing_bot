"""The standard lines a refusal, a cancel and a lapse write to the log channel.

Every command, button and form that changes something, or tries to, records every outcome in
the log channel — a success, a refusal or a failure, whoever used it (the core specification's
"The record of what changed"). A failure is `report_failure`'s, in
`core/utils/interaction_errors.py`. This module holds the other two, so that each standard line
is formed in one place:

- **`refuse`** answers the member, seen by them alone, and writes one line:
  "⛔ {what} refused for {member} — {reason}". The line itself is **`record_refusal`**'s, which
  writes it without answering, for a refusal whose reply is not an interaction's: an answer
  typed into the signup wizard is a message, refused by a public reply in the driver's channel.
- **`record_abandoned`** writes the line for a confirmation cancelled or left to lapse:
  "↩️ {what} cancelled by {member}", or "⌛ {what} lapsed unconfirmed (started by {member})".

**A member is named by their server display name and their mention**, "Alex (<@4242>)": the
name for the person reading the channel, the mention for the account it was. A member no longer
on the server is named by mention alone, and a line with nobody recorded as "a member".

**Neither ever raises.** Each runs where the command is already ending, and each half is
attempted on its own: a log channel that cannot be written must not cost the member their
answer, nor a reply that cannot be sent cost the league its record. A failure of either is
logged with its traceback.

**Every line goes through `OutputRouter.post_log`**, which divides a record too long for one
message; a reply too long for one is sent in parts through `chunk_message`, never cut off.

It lives apart from `interaction_errors` because it reaches the guild through `league_guild`,
and `league_server`, which defines that, imports `interaction_errors`. The two helpers that name a
member from what an interaction carries, `member_named` and `interaction_member`, live in
`core/utils/member_names.py` so that `interaction_errors` can use them too; this module imports
them and so still offers them to its callers.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.member_names import interaction_member, member_named  # noqa: F401
from leaguebot.core.utils.messages import chunk_message

log = logging.getLogger(__name__)

#: The marks a reply opens with, which the log line's own mark replaces.
_REPLY_MARKS = ("❌", "⛔", "⚠️", "⚠", "ℹ️", "ℹ", "⏳", "⛓", "⏸️", "⏸")


async def name_of_member(bot: Any, member_id: int | None) -> str:
    """The member *member_id*, named as the log channel names them, found on the league's server.

    For a line written without an interaction — a scheduled lapse. A member who has left the
    server is named by mention alone; nobody recorded is "a member". Never raises.
    """
    if member_id is None:
        return "a member"
    member = None
    try:
        guild = await league_guild(bot)
        member = guild.get_member(int(member_id)) if guild is not None else None
    except Exception:  # noqa: BLE001 — the mention alone still names them
        log.warning("could not look up member %s on the league's server", member_id, exc_info=True)
    return member_named(getattr(member, "display_name", None), int(member_id))


def reply_reason(reply: str) -> str:
    """The reply's first line, without the mark it opens with: a refusal's line gives it as its reason.

    `refuse` takes it by default, and so does the change queue where it refuses a change when it
    runs.
    """
    lines = reply.strip().splitlines()
    first = lines[0].strip() if lines else ""
    for mark in _REPLY_MARKS:
        if first.startswith(mark):
            return first[len(mark):].lstrip("️").strip()
    return first


async def _named(bot: Any, member: int | discord.abc.User | None) -> str:
    """*member* named as the log channel names them: a member object by the display name it
    carries, an id (or an object with no name) found on the league's server, nobody as "a member"."""
    if member is None or isinstance(member, int):
        return await name_of_member(bot, member)
    if isinstance(getattr(member, "display_name", None), str):
        return member_named(member.display_name, member.id)
    return await name_of_member(bot, member.id)


def refusal_line(named: str, what: str, reason: str, *, detail: str | None = None) -> str:
    """The log channel's line for *what* refused for *named*, because of *reason*.

    *detail*, where given, is written beneath the line on a line of its own: what the reason
    leaves out, for the log channel and not the member's reply.

    The one place a refusal's line is formed: `record_refusal` writes it, and so does the change
    queue where it refuses a change when it runs.
    """
    line = f"⛔ {what} refused for {named} — {reason}"
    return f"{line}\n{detail}" if detail else line


def stop_line(job_id: int, job: str, request: str, asker: str, fault: str) -> str:
    """The log channel's line for the queue stopping at job *job_id*, *job* being what it does,
    *request* the change it belongs to, *asker* who asked and *fault* the kind of fault.

    The change queue's line, formed here with the others.
    """
    return (
        f"❌ The queue is stopped at job #{job_id}: {job} for {request} ({asker}) failed "
        f"({fault}). The bot tries again 1, 5, 10, 15, 30 and 60 minutes after this; a league "
        f"manager or admin may press Retry at any time, and a league admin may press Discard "
        f"to drop it."
    )


def hour_line(job_id: int, job: str) -> str:
    """The log channel's line for job *job_id* still failing after an hour: the bot tries no more
    on its own."""
    return (
        f"❌ Job #{job_id} ({job}) still fails after an hour. The bot has stopped trying on its "
        f"own: press Retry on its notice, or Discard."
    )


async def record_refusal(
    bot: Any,
    member: int | discord.abc.User | None,
    *,
    what: str,
    reason: str,
    detail: str | None = None,
) -> None:
    """Record that *what* was refused for *member*, because of *reason*, without answering anyone.
    *detail*, where given, goes beneath the line (`refusal_line`).

    `refuse` writes its own through here, the line being formed by `refusal_line`. For a
    refusal whose reply is not an interaction's, and so has no `refuse`; *member* is named as
    `record_abandoned` names its own. Never raises.
    """
    try:
        named = await _named(bot, member)
        await bot.output_router.post_log(refusal_line(named, what, reason, detail=detail))
    except Exception:  # noqa: BLE001 — the refusal has already been answered
        log.warning("could not record in the log channel that %s was refused", what, exc_info=True)


async def refuse(
    interaction: discord.Interaction,
    reply: str,
    *,
    what: str,
    reason: str | None = None,
    detail: str | None = None,
) -> None:
    """Answer the member with *reply*, seen by them alone, and record the refusal of *what*.

    *what* names what was refused as the log channel should read it — `describe`'s name of the
    command, or that name with its context ("`/round amend` of round 3"). *reason* is the line's
    detail; it defaults to the reply's first line, and a refusal whose reply is a list (the bad
    lines of a paste) passes the list, joined on new lines. *detail* is written beneath the line,
    apart from the reply.

    The reply goes by `followup.send` once the interaction is answered or deferred, and by
    `response.send_message` otherwise, in as many parts as it needs. Never raises.
    """
    user_id = getattr(interaction.user, "id", None)
    try:
        for part in chunk_message(reply):
            if interaction.response.is_done():
                await interaction.followup.send(part, ephemeral=True)
            else:
                await interaction.response.send_message(part, ephemeral=True)
    except Exception:  # noqa: BLE001 — the refusal is still recorded
        log.warning("could not tell user %s that %s was refused", user_id, what, exc_info=True)

    bot = getattr(interaction, "client", None)
    if getattr(bot, "output_router", None) is None:
        return
    beneath = {"detail": detail} if detail else {}
    await record_refusal(
        bot,
        interaction.user,
        what=what,
        reason=reason if reason is not None else reply_reason(reply),
        **beneath,
    )


async def record_abandoned(
    bot: Any,
    member: int | discord.abc.User | None,
    *,
    what: str,
    lapsed: bool,
    detail: str | None = None,
) -> None:
    """Record that *what* was cancelled by *member*, or lapsed unconfirmed, having been started
    by them.

    *member* is the member who pressed or started it: the interaction's own member where there
    is one, named by the display name it carries, or their id — a scheduled lapse has only
    that — found on the league's server (`name_of_member`). *detail*, where given, goes beneath
    the line: what became of the change and what to do next. Never raises.
    """
    try:
        named = await _named(bot, member)
        line = (
            f"⌛ {what} lapsed unconfirmed (started by {named})"
            if lapsed
            else f"↩️ {what} cancelled by {named}"
        )
        if detail:
            line += "".join(f"\n  {text}" for text in detail.splitlines())
        await bot.output_router.post_log(line)
    except Exception:  # noqa: BLE001 — the confirmation has already ended
        log.warning("could not record in the log channel that %s ended", what, exc_info=True)
