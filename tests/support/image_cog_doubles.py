"""Doubles for the image cog's tests: an interaction that carries its command and its bot, and a
record of what the member was told and what the log channel was given.

Every outcome of an image command is recorded in the log channel (#482; the core
specification's "The record of what changed"): a refusal as one "⛔" line naming the command or
form, a success or a value already held as one line in the form
"Name (<@id>) | /command | outcome" with the values beneath. A refusal answers the member through
the interaction itself, so the double tracks whether it has been answered or deferred, as
discord.py's does, and keeps every reply in the order it was sent, whichever route it took.

The image settings run on the change queue (#439, slice 7): `log_bot(league)` is the real-queue
form, the bot of a `settings_league`, whose lines `logged` reads from the queue's saved log
lines, and whose acknowledgement and its update `said` reads in order, the update an edit of the
response or of the follow-up the acknowledgement went out as.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

#: The member every interaction here belongs to, as the log channel names them.
MEMBER = "Race Control (<@42>)"
#: The same member as a line saved by the queue names them, the mention wrapped so it notifies
#: nobody.
QUEUED_MEMBER = "Race Control (`<@42>`)"
#: Where the real-queue form keeps its database path on the bot.
_QUEUED_DB = "_image_doubles_queued_db"


def log_bot(league: Any = None) -> MagicMock:
    """A bot whose log channel is an `AsyncMock`, `output_router.post_log`; given *league*, a
    `settings_league`, that league's bot, with the real queue attached."""
    if league is not None:
        bot = league.bot
        setattr(bot, _QUEUED_DB, league.db_path)
        return bot
    bot = MagicMock()
    bot.output_router.post_log = AsyncMock()
    return bot


def _queued_db(bot: MagicMock) -> str | None:
    return vars(bot).get(_QUEUED_DB)


def _member(bot: MagicMock) -> str:
    return QUEUED_MEMBER if _queued_db(bot) else MEMBER


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
    async def _follow(content=None, *args, **kwargs):
        await _say(content, *args, **kwargs)
        message = MagicMock()
        message.edit = AsyncMock(side_effect=_edit)
        return message

    double.followup.send = AsyncMock(side_effect=_follow)
    double.edit_original_response = AsyncMock(side_effect=_edit)
    double.command = SimpleNamespace(qualified_name=command) if command else None
    double.client = bot if bot is not None else log_bot()
    return double


def said(double: MagicMock) -> str:
    """Everything the member was told, joined, in the order it was sent."""
    return "\n".join(text for text in double.said if text)


def logged(bot: MagicMock) -> list[str]:
    """Every line given to the log channel, in order: for the real-queue form, every line the
    queue saved for it."""
    db_path = _queued_db(bot)
    if db_path is None:
        return [call.args[0] for call in bot.output_router.post_log.await_args_list]
    from tests.support.change_queue import LOG_CHANNEL_ID

    with sqlite3.connect(db_path) as db:
        rows = db.execute(
            "SELECT content FROM pending_messages WHERE channel_id = ? ORDER BY id",
            (LOG_CHANNEL_ID,),
        ).fetchall()
    return [row[0] for row in rows]


def assert_one_refusal(bot: MagicMock, what: str) -> str:
    """One "⛔" line was recorded, naming *what* (a command as "`/images config …`", or a
    form) and the member; returns it, for a test to read its reason."""
    lines = logged(bot)
    assert len(lines) == 1, lines
    assert lines[0].startswith(f"⛔ {what} refused for {_member(bot)} — "), lines[0]
    return lines[0]


def assert_one_line(bot: MagicMock, command: str, outcome: str = "Success") -> list[str]:
    """One line was recorded, "Race Control (<@42>) | /<command> | <outcome>"; returns the
    detail lines beneath it, without their indent."""
    lines = logged(bot)
    assert len(lines) == 1, lines
    head, *details = lines[0].splitlines()
    assert head == f"{_member(bot)} | /{command} | {outcome}", head
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
