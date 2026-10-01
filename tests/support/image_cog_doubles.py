"""Doubles for the image cog's tests: an interaction that carries its command and its bot, and a
record of what the member was told and what the log channel was given.

Every outcome of an image command is recorded in the log channel (#482; the core
specification's "The record of what changed"): a refusal as one "⛔" line naming the command or
form, a success or a value already held as one line in the form
"Name (<@id>) | /command | outcome" with the values beneath. A refusal answers the member through
the interaction itself, so the double tracks whether it has been answered or deferred, as
discord.py's does, and keeps every reply in the order it was sent, whichever route it took.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

#: The member every interaction here belongs to, as the log channel names them.
MEMBER = "Race Control (<@42>)"


def log_bot() -> MagicMock:
    """A bot whose log channel is an `AsyncMock`, `output_router.post_log`."""
    bot = MagicMock()
    bot.output_router.post_log = AsyncMock()
    return bot


def interaction(command: str | None = None, *, bot: MagicMock | None = None) -> MagicMock:
    """An interaction from "Race Control" for `/<command>`, answered through *bot*'s client.

    *command* is the command's full name as a league types it, without the slash ("images
    config time-zone"); ``None`` is an interaction with no command, a form's or a button's.
    Every reply, by `response.send_message`, `followup.send` or an edit of the message a button
    sits on that gives it content, lands in ``.said`` in order.
    """
    double = MagicMock()
    double.guild_id = 1
    double.user.id = 42
    double.user.display_name = "Race Control"
    double.said = []

    async def _say(content=None, *args, **kwargs):
        double.said.append(content if content is not None else kwargs.get("content"))

    double.response.send_message = AsyncMock(side_effect=_say)
    double.response.defer = AsyncMock()

    async def _edit(*args, **kwargs):
        if kwargs.get("content") is not None:
            double.said.append(kwargs["content"])

    double.response.edit_message = AsyncMock(side_effect=_edit)
    double.response.send_modal = AsyncMock()
    double.response.is_done = MagicMock(
        side_effect=lambda: bool(
            double.response.send_message.await_count
            or double.response.defer.await_count
            or double.response.edit_message.await_count
            or double.response.send_modal.await_count
        )
    )
    double.followup.send = AsyncMock(side_effect=_say)
    double.edit_original_response = AsyncMock()
    double.command = SimpleNamespace(qualified_name=command) if command else None
    double.client = bot if bot is not None else log_bot()
    return double


def said(double: MagicMock) -> str:
    """Everything the member was told, joined, in the order it was sent."""
    return "\n".join(text for text in double.said if text)


def logged(bot: MagicMock) -> list[str]:
    """Every line given to the log channel, in order."""
    return [call.args[0] for call in bot.output_router.post_log.await_args_list]


def assert_one_refusal(bot: MagicMock, what: str) -> str:
    """One "⛔" line was recorded, naming *what* (a command as "`/images config …`", or a
    form) and the member; returns it, for a test to read its reason."""
    lines = logged(bot)
    assert len(lines) == 1, lines
    assert lines[0].startswith(f"⛔ {what} refused for {MEMBER} — "), lines[0]
    return lines[0]


def assert_one_line(bot: MagicMock, command: str, outcome: str = "Success") -> list[str]:
    """One line was recorded, "Race Control (<@42>) | /<command> | <outcome>"; returns the
    detail lines beneath it, without their indent."""
    lines = logged(bot)
    assert len(lines) == 1, lines
    head, *details = lines[0].splitlines()
    assert head == f"{MEMBER} | /{command} | {outcome}", head
    return [detail.strip() for detail in details]


def scheduler(*, fails: bool = False) -> MagicMock:
    """The scheduler service: `schedule_portrait_refresh` raises where *fails*, as a job store
    refusing the job would; `cancel_portrait_refresh` never raises."""
    double = MagicMock()
    double.schedule_portrait_refresh = MagicMock(
        side_effect=RuntimeError("the job store refused the job") if fails else None
    )
    double.cancel_portrait_refresh = MagicMock()
    return double
