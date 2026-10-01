"""A real change queue for a test, on the test's own database, with "now" pinned (#439).

The queue is the one the bot builds (`leaguebot.core.services.change_queue.ChangeQueue`), handed
the router the test gives it, or the bot's own. Every import of the queue is made inside a function,
so a test file using this module still collects while the queue is unbuilt.

`run_queue` runs the worker until nothing can run *now*: a change waiting on a retry that is not
yet due is left waiting, and the test moves the clock on (`Clock.advance`, or setting `now`) and
runs it again. `steps=n` stops after the n-th step is done, as a stop of the bot would, leaving the
change where it stood for `restart_queue` to carry on.

`league_double` and `member_interaction` are the Discord side the queue's own tests need: a bot whose
log channel records what it is sent, and a member's interaction recording its reply and the updates
to it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from leaguebot.core.db.database import get_connection

SERVER_ID = 12408
INTERACTION_CHANNEL_ID = 100
LOG_CHANNEL_ID = 101
MEMBER_ID = 4242

#: The warning a member is given, seen by them alone, when the log channel cannot take a line.
LOG_CHANNEL_WARNING = (
    f"⚠️ Failed to write to log channel (id={LOG_CHANNEL_ID}). Please check bot permissions."
)


class Clock:
    """The queue's `clock`: called for "now", moved on by the test."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@dataclass
class _Setup:
    db_path: str
    clock: Clock
    router: Any
    real_types: bool
    types: list[Any] = field(default_factory=list)


_SETUP = "_change_queue_test_setup"


def _register(bot: Any, queue: Any, setup: _Setup) -> None:
    if setup.real_types:
        from leaguebot.__main__ import register_change_types

        register_change_types(bot)
    for change_type in setup.types:
        queue.register(change_type)


def attach_queue(bot: Any, db_path: str, *, now: datetime | Clock, router: Any = None,
                 types: Any = None) -> Any:
    """Build a real `ChangeQueue` on *db_path* for *bot*, attach it as `bot.change_queue`.

    It is handed *router*, or the bot's own `output_router`, and *now* as its clock. With no
    *types*, the bot's real change types are registered through `register_change_types`, as the
    builder registers them; with *types*, those alone.
    """
    from leaguebot.core.services.change_queue import ChangeQueue

    clock = now if isinstance(now, Clock) else Clock(now)
    chosen = router if router is not None else bot.output_router
    queue = ChangeQueue(db_path, bot, chosen, clock=clock)
    bot.change_queue = queue
    setup = _Setup(db_path, clock, chosen, real_types=types is None, types=list(types or ()))
    setattr(bot, _SETUP, setup)
    _register(bot, queue, setup)
    return queue


def register(bot: Any, *change_types: Any) -> None:
    """Register *change_types* on the bot's queue, and again on every queue `restart_queue` builds."""
    setup: _Setup = getattr(bot, _SETUP)
    for change_type in change_types:
        bot.change_queue.register(change_type)
        setup.types.append(change_type)


async def run_queue(bot: Any, *, steps: int | None = None) -> None:
    """Run the bot's queue until nothing can run now, or until *steps* steps are done."""
    if steps is None:
        await bot.change_queue.run_until_idle()
    else:
        await bot.change_queue.run_until_idle(steps=steps)


async def restart_queue(bot: Any) -> Any:
    """A fresh queue on the same database, clock and router, as after a restart, and started.

    The old queue is stopped first where it was started. The same change types are registered.
    """
    from leaguebot.core.services.change_queue import ChangeQueue

    setup: _Setup = getattr(bot, _SETUP)
    old = bot.change_queue
    if getattr(old, "_task", None) is not None:
        await old.stop()
    queue = ChangeQueue(setup.db_path, bot, setup.router, clock=setup.clock)
    bot.change_queue = queue
    _register(bot, queue, setup)
    await _maybe_await(queue.start())
    return queue


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


async def queued_log_lines(db_path: str) -> list[str]:
    """Every log line still waiting in the retry queue, oldest first."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT content FROM pending_messages ORDER BY id")
        return [row["content"] for row in await cursor.fetchall()]


def _content(call: Any) -> str:
    if call.args:
        return str(call.args[0])
    return str(call.kwargs.get("content", ""))


def acknowledgement(interaction: Any) -> str:
    """What the member was first answered with: the interaction's response."""
    calls = interaction.response.send_message.await_args_list
    return _content(calls[0]) if calls else ""


def updated_reply(interaction: Any) -> str:
    """The reply as updated: the edits of the acknowledgement, then the follow-ups, in order."""
    parts = [_content(call) for call in interaction.edit_original_response.await_args_list]
    parts += [_content(call) for call in interaction.followup.send.await_args_list]
    return "\n".join(part for part in parts if part)


# ---------------------------------------------------------------------------
# The Discord side
# ---------------------------------------------------------------------------


def text_channel(channel_id: int) -> Any:
    """A text channel recording what it is sent in `sent`; set `fails` to an exception to refuse."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.sent = []
    channel.fails = None

    async def _send(content: str = "", **_kwargs: Any) -> Any:
        if channel.fails is not None:
            raise channel.fails
        channel.sent.append(content)
        return MagicMock(id=len(channel.sent), jump_url=f"https://discord.test/{len(channel.sent)}")

    channel.send = AsyncMock(side_effect=_send)
    return channel


def http_error(cls: type[discord.HTTPException] = discord.HTTPException, *, status: int = 500,
               text: str = "Discord failed") -> discord.HTTPException:
    """An exception of *cls* as discord.py raises it."""
    response = MagicMock(status=status, reason=text)
    return cls(response, text)


async def seed_server(db_path: str) -> None:
    """The league's server, set up with its interaction and log channels."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, ?, ?)",
            (SERVER_ID, INTERACTION_CHANNEL_ID, LOG_CHANNEL_ID),
        )
        await db.commit()


def league_double(db_path: str) -> Any:
    """A bot double with the league's log and interaction channels, and a real `OutputRouter`.

    `bot.log_channel` and `bot.interaction_channel` record what each is sent.
    """
    from leaguebot.core.services.output_router import OutputRouter

    bot = MagicMock()
    bot.db_path = db_path
    bot.log_channel = text_channel(LOG_CHANNEL_ID)
    bot.interaction_channel = text_channel(INTERACTION_CHANNEL_ID)
    channels = {LOG_CHANNEL_ID: bot.log_channel, INTERACTION_CHANNEL_ID: bot.interaction_channel}
    bot.get_channel = MagicMock(side_effect=channels.get)
    bot.fetch_channel = AsyncMock(side_effect=lambda cid: channels[cid])
    bot.get_guild = MagicMock(return_value=None)
    bot.config_service.get_server_config = AsyncMock(
        return_value=MagicMock(
            server_id=SERVER_ID,
            log_channel_id=LOG_CHANNEL_ID,
            interaction_channel_id=INTERACTION_CHANNEL_ID,
        )
    )
    bot.output_router = OutputRouter(bot, retry_db_path=db_path)
    return bot


class _Member:
    def __init__(self, member_id: int, display_name: str, name: str) -> None:
        self.id = member_id
        self.display_name = display_name
        self.name = name
        self.bot = False

    def __str__(self) -> str:
        return self.name


def member(member_id: int = MEMBER_ID, display_name: str = "Admin",
           name: str = "Admin#0001") -> Any:
    """A member as Discord gives one: an id, a display name, and `str()` as the audit records it."""
    return _Member(member_id, display_name, name)


def member_interaction(bot: Any, *, user: Any = None, created_at: datetime | None = None) -> Any:
    """A member's interaction, not yet answered: its response, edits and follow-ups are recorded."""
    interaction = MagicMock()
    interaction.client = bot
    interaction.guild_id = SERVER_ID
    interaction.channel_id = INTERACTION_CHANNEL_ID
    interaction.user = user or member()
    interaction.command = None
    if created_at is not None:
        interaction.created_at = created_at
    answered = {"done": False}

    async def _answer(*_args: Any, **_kwargs: Any) -> None:
        answered["done"] = True

    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.response.is_done = MagicMock(side_effect=lambda: answered["done"])
    interaction.edit_original_response = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def change_rows(db_path: str) -> list[dict[str, Any]]:
    """Every change asked for, in the order asked."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT * FROM queued_changes ORDER BY id")
        return [dict(row) for row in await cursor.fetchall()]


async def step_rows(db_path: str, change_id: int | None = None) -> list[dict[str, Any]]:
    """The steps of *change_id*, or of every change, in order; `result` read back from JSON."""
    async with get_connection(db_path) as db:
        if change_id is None:
            cursor = await db.execute(
                "SELECT * FROM queued_change_steps ORDER BY change_id, position"
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM queued_change_steps WHERE change_id = ? ORDER BY position",
                (change_id,),
            )
        rows = [dict(row) for row in await cursor.fetchall()]
    for row in rows:
        row["result"] = json.loads(row["result"]) if row["result"] else None
    return rows
