"""What the bot does when a command, button or form fails (issue #156).

discord.py's own handlers log a traceback to the host and do nothing else, so a failure
reached the league as Discord's "The application did not respond", or as a "thinking"
indicator that quietly expired. `report_failure` is what `LeagueCommandTree`, `LeagueView`
and `LeagueModal` call instead. It does three things, each independent of the others:

- **The host's log keeps the traceback**, exactly as the library logged it before.
- **The member is told, seen by them alone**, what did not finish and that the fault is the
  bot's rather than anything they entered. The reply names no exception: a Python type name
  tells a league manager nothing. It says the command may have been partly done, because
  nothing is rolled back and "nothing was changed" would be a promise the bot cannot keep.
  Naming the command and the kind of fault is what keeps it clear of the constitution's ban
  on a generic "something went wrong" (decided 2026-09-19).
- **The log channel gets one line** naming the member, what failed and the exception's type,
  so the league holds something to report. No traceback: the channel is for people.

**The handler never raises.** It runs where the failure already happened: the interaction's
token may have expired, or the fault may be the database `post_log` reads its channel from.
The reply and the log line are therefore each attempted on their own, and a failure of
either is logged and leaves the other to go ahead (decided 2026-09-19).

Only the tier guards' refusals are *not* failures, and they never reach this. The guards
run inside each command's body and answer the member themselves, rather than raising
`app_commands.CheckFailure`, so everything that arrives here is a real fault.

Autocomplete does not reach this either. `CommandTree._call` logs an autocomplete failure
and returns before any `on_error` runs; see `utils/log_filters.py`.
"""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands

log = logging.getLogger(__name__)

#: What the member who ran a failed command, button or form is told; `{what}` is `describe`'s.
FAILURE_REPLY = (
    "❌ {what} stopped on a fault in the bot, not on anything you entered, and did not "
    "finish. It may have been partly done — check before running it again. The fault is "
    "recorded in the log channel."
)


def failure_reply(what: str) -> str:
    """The reply for *what*, its first letter raised to open the sentence."""
    return FAILURE_REPLY.format(what=what[:1].upper() + what[1:])


def describe(interaction: discord.Interaction, item: Any = None) -> str:
    """Name what failed as the log channel should read it.

    A slash command by its full name, a button or menu by its label, a form by its title.
    """
    if item is not None:
        label = getattr(item, "label", None)
        if label:
            return f"the “{label}” button"
        placeholder = getattr(item, "placeholder", None)
        if placeholder:
            return f"the “{placeholder}” menu"
        return f"the `{getattr(item, 'custom_id', None) or type(item).__name__}` control"
    command = getattr(interaction, "command", None)
    if command is not None:
        return f"`/{command.qualified_name}`"
    return "an interaction"


def describe_form(modal: Any) -> str:
    """Name a form by its title, or by its class where it has none."""
    title = getattr(modal, "title", None)
    return f"the “{title}” form" if title else f"the `{type(modal).__name__}` form"


async def report_failure(
    interaction: discord.Interaction, error: BaseException, *, what: str
) -> None:
    """Tell the host, the member and the log channel that *what* failed with *error*."""
    if isinstance(error, app_commands.CommandInvokeError):
        error = error.original
    user_id = getattr(interaction.user, "id", None)
    log.error("%s failed for user %s", what, user_id, exc_info=error)

    reply = failure_reply(what)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(reply, ephemeral=True)
        else:
            await interaction.response.send_message(reply, ephemeral=True)
    except Exception as exc:  # noqa: BLE001 — the handler must never raise
        log.error("could not tell user %s that %s failed: %s", user_id, what, exc)

    router = getattr(interaction.client, "output_router", None)
    if router is None:
        return
    try:
        await router.post_log(
            f"❌ {what} failed for <@{user_id}> — {type(error).__name__}. "
            f"The details are in the host's log."
        )
    except Exception as exc:  # noqa: BLE001 — the handler must never raise
        log.error("could not record in the log channel that %s failed: %s", what, exc)
