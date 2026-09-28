"""The standard lines a refusal, a cancel and a lapse write to the log channel.

Every command, button and form that changes something, or tries to, records every outcome in
the log channel — a success, a refusal or a failure, whoever used it (the core specification's
"The record of what changed"). A failure is `report_failure`'s, in
`core/utils/interaction_errors.py`. This module holds the other two, so that each standard line
is formed in one place:

- **`refuse`** answers the member, seen by them alone, and writes one line:
  "⛔ {what} refused for {member} — {reason}".
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
and `league_server`, which defines that, imports `interaction_errors`.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.messages import chunk_message

log = logging.getLogger(__name__)

#: The marks a reply opens with, which the log line's own mark replaces.
_REPLY_MARKS = ("❌", "⛔", "⚠️", "⚠")


def member_named(display_name: str | None, member_id: int) -> str:
    """"Alex (<@4242>)", or the mention alone where the member has no name to give."""
    return f"{display_name} (<@{member_id}>)" if display_name else f"<@{member_id}>"


def interaction_member(interaction: discord.Interaction) -> str:
    """The member who used *interaction*, named as the log channel names them."""
    user = interaction.user
    return member_named(getattr(user, "display_name", None), user.id)


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


def _first_line(reply: str) -> str:
    """The reply's first line, without the mark it opens with."""
    lines = reply.strip().splitlines()
    first = lines[0].strip() if lines else ""
    for mark in _REPLY_MARKS:
        if first.startswith(mark):
            return first[len(mark):].lstrip("️").strip()
    return first


async def refuse(
    interaction: discord.Interaction,
    reply: str,
    *,
    what: str,
    reason: str | None = None,
) -> None:
    """Answer the member with *reply*, seen by them alone, and record the refusal of *what*.

    *what* names what was refused as the log channel should read it — `describe`'s name of the
    command, or that name with its context ("`/round amend` of round 3"). *reason* is the line's
    detail; it defaults to the reply's first line, and a refusal whose reply is a list (the bad
    lines of a paste) passes the list, joined on new lines.

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

    router = getattr(interaction.client, "output_router", None)
    if router is None:
        return
    try:
        detail = reason if reason is not None else _first_line(reply)
        await router.post_log(f"⛔ {what} refused for {interaction_member(interaction)} — {detail}")
    except Exception:  # noqa: BLE001 — the member has still been answered
        log.warning("could not record in the log channel that %s was refused", what, exc_info=True)


async def record_abandoned(
    bot: Any,
    member_id: int | None,
    *,
    what: str,
    lapsed: bool,
    detail: str | None = None,
) -> None:
    """Record that *what* was cancelled by *member_id*, or lapsed unconfirmed, having been
    started by them.

    *detail*, where given, goes beneath the line — what became of the change and what to do
    next. The member is found on the league's server (`name_of_member`), so a scheduled lapse
    names them as a press does. Never raises.
    """
    try:
        member = await name_of_member(bot, member_id)
        line = (
            f"⌛ {what} lapsed unconfirmed (started by {member})"
            if lapsed
            else f"↩️ {what} cancelled by {member}"
        )
        if detail:
            line += "".join(f"\n  {text}" for text in detail.splitlines())
        await bot.output_router.post_log(line)
    except Exception:  # noqa: BLE001 — the confirmation has already ended
        log.warning("could not record in the log channel that %s ended", what, exc_info=True)
