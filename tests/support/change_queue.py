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

A job that fails stops the queue until it is cleared (owner, 2026-10-02): `stopped_job` reads the
job the queue is stopped at, and `retry_job` and `discard_job` press Retry and Discard on its stop
notice, as a member holding the tier `tier_member` gives them.

A queue `restart_queue` starts has a worker running in the background, and is stopped before the
test ends: `tests/conftest.py` calls `stop_started_queues` once the test has run, while its event
loop is still open, and it waits there for any database thread a stopped worker left running.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
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
#: The league's two tier roles: the interaction role is the league manager's.
MANAGER_ROLE_ID = 900
ADMIN_ROLE_ID = 901

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
    queue = ChangeQueue(db_path, bot, chosen, bot.config_service, clock=clock)
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


#: How long `stop_started_queues` waits, in all, for aiosqlite's threads to finish.
THREAD_GRACE_SECONDS = 5.0

#: Every queue `restart_queue` has started, with the event loop its worker runs on.
_STARTED: list[tuple[Any, asyncio.AbstractEventLoop]] = []


async def restart_queue(bot: Any) -> Any:
    """A fresh queue on the same database, clock and router, as after a restart, and started.

    The old queue is stopped first where it was started. The same change types are registered.
    The queue is remembered, for `stop_started_queues` to stop once the test has run.
    """
    from leaguebot.core.services.change_queue import ChangeQueue

    setup: _Setup = getattr(bot, _SETUP)
    old = bot.change_queue
    if getattr(old, "_task", None) is not None:
        await old.stop()
    queue = ChangeQueue(setup.db_path, bot, setup.router, bot.config_service, clock=setup.clock)
    bot.change_queue = queue
    _register(bot, queue, setup)
    await maybe_await(queue.start())
    _STARTED.append((queue, asyncio.get_running_loop()))
    return queue


def stop_started_queues() -> None:
    """Stop every queue `restart_queue` started, on the loop it runs on, and forget them; then
    wait for aiosqlite's threads to finish.

    Called by `tests/conftest.py` after the test's call and before its teardown, while the test's
    event loop is open and idle. A worker left running is cancelled only as the loop closes, part
    way through a read of the database: aiosqlite's thread then answers a closed loop, and pytest
    reports a `PytestUnhandledThreadExceptionWarning` ("Event loop is closed") against whichever
    test is running when it fires.

    Stopping cancels the worker wherever it stands, which may be part way through opening a
    connection: aiosqlite's thread finishes the opening after the cancel, and answers the loop
    when it does. The same holds for a queue the test started and stopped itself. So each
    aiosqlite thread still alive is waited for, up to `THREAD_GRACE_SECONDS` in all, before the
    loop is left to close; none is alive where nothing was cut off.
    """
    started, _STARTED[:] = list(_STARTED), []
    for queue, loop in started:
        if not loop.is_closed() and not loop.is_running():
            loop.run_until_complete(queue.stop())
    deadline = time.monotonic() + THREAD_GRACE_SECONDS
    for thread in threading.enumerate():
        # aiosqlite names no thread; Python names it after its target.
        if thread.name.endswith("(_connection_worker_thread)"):
            thread.join(max(deadline - time.monotonic(), 0))


async def maybe_await(value: Any) -> Any:
    """Await *value* where it is awaitable: a queue method the test need not know the colour of."""
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
    """A text channel recording what it is sent; set `fails` to an exception to refuse.

    `sent` holds the content of each message, and `posted` each message as Discord returned it:
    its `id`, `content` and `view`, which an `edit` with `view=` replaces. `fetch_message` and
    `get_partial_message` find a posted message by id; one removed from `posted` is gone, and
    `NotFound` is raised for it.
    """
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.sent = []
    channel.posted = []
    channel.fails = None

    async def _send(content: str = "", **kwargs: Any) -> Any:
        if channel.fails is not None:
            raise channel.fails
        channel.sent.append(content)
        message = MagicMock(
            id=len(channel.sent), jump_url=f"https://discord.test/{len(channel.sent)}",
            content=content, view=kwargs.get("view"),
        )
        message.channel = channel

        async def _edit(**changes: Any) -> Any:
            if message not in channel.posted:
                raise http_error(discord.NotFound, status=404, text="Unknown Message")
            for name in ("content", "view"):
                if name in changes:
                    setattr(message, name, changes[name])
            return message

        message.edit = AsyncMock(side_effect=_edit)
        channel.posted.append(message)
        return message

    def _gone(message_id: int) -> Any:
        gone = MagicMock(id=message_id)
        gone.edit = AsyncMock(
            side_effect=http_error(discord.NotFound, status=404, text="Unknown Message")
        )
        return gone

    def _partial(message_id: int) -> Any:
        found = [m for m in channel.posted if m.id == message_id]
        return found[0] if found else _gone(message_id)

    async def _fetch(message_id: int) -> Any:
        found = [m for m in channel.posted if m.id == message_id]
        if not found:
            raise http_error(discord.NotFound, status=404, text="Unknown Message")
        return found[0]

    channel.send = AsyncMock(side_effect=_send)
    channel.fetch_message = AsyncMock(side_effect=_fetch)
    channel.get_partial_message = MagicMock(side_effect=_partial)
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
            "INSERT INTO server_configs (server_id, interaction_role_id, league_admin_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, ?, ?, ?, ?)",
            (SERVER_ID, MANAGER_ROLE_ID, ADMIN_ROLE_ID, INTERACTION_CHANNEL_ID, LOG_CHANNEL_ID),
        )
        await db.commit()


def league_double(db_path: str) -> Any:
    """A bot double with the league's log and interaction channels, and a real `OutputRouter`.

    `bot.log_channel` and `bot.interaction_channel` record what each is sent. The league's server
    is `SERVER_ID`, as `config_service.get_league_server_id` gives it; `bot.get_guild` finds no
    guild for it until a test gives one.
    `bot.attendance_after_review` is the attendance hook the builder hands the review's change
    types (#439), as a league with attendance off sees it: every method does nothing, no driver
    is owed a sanction, and the sync hint names `/attendance sync`. A test that runs the hook
    replaces it.
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
            interaction_role_id=MANAGER_ROLE_ID,
            league_admin_role_id=ADMIN_ROLE_ID,
        )
    )
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.output_router = OutputRouter(bot, bot.config_service, retry_db_path=db_path)
    hook = MagicMock()
    for name in ("record_on", "rewrite_pardons_on", "recalculate_on", "post_sheet",
                 "apply_sanction", "announce_sanction", "refresh_lineup"):
        setattr(hook, name, AsyncMock(return_value=None))
    hook.sanction_candidates = AsyncMock(return_value=[])
    hook.sync_hint = AsyncMock(return_value="Repair the cause, then run `/attendance sync`.")
    bot.attendance_after_review = hook
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


def tier_member(tier: str | None, *, member_id: int = MEMBER_ID, display_name: str = "Admin",
                name: str = "Admin#0001") -> Any:
    """A member of the league's server holding the league admin role (*tier* "admin"), the league
    manager's interaction role ("manager"), or neither (None)."""
    held = {"admin": [ADMIN_ROLE_ID], "manager": [MANAGER_ROLE_ID], None: []}[tier]
    person = MagicMock(spec=discord.Member)
    person.id = member_id
    person.display_name = display_name
    person.name = name
    person.mention = f"<@{member_id}>"
    person.bot = False
    person.roles = [MagicMock(id=role_id) for role_id in held]
    person.__str__.return_value = name
    return person


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


# ---------------------------------------------------------------------------
# A stopped queue
# ---------------------------------------------------------------------------


async def stopped_job(db_path: str) -> dict[str, Any] | None:
    """The job the queue is stopped at: not done, and failed since `failing_since`; or None.

    Only a job of a change still QUEUED or RUNNING counts: a change that has ended (discarded
    at a job, say) leaves the job it stopped at as it was."""
    live = {row["id"] for row in await change_rows(db_path)
            if row["state"] in ("QUEUED", "RUNNING")}
    stopped = [
        row for row in await step_rows(db_path)
        if row["change_id"] in live and row["done_at"] is None
        and row["failing_since"] is not None
    ]
    return stopped[0] if stopped else None


async def _press(bot: Any, action: str, user: Any, run: bool) -> Any:
    job = await stopped_job(bot.db_path)
    assert job is not None, f"no job stops the queue for {action} to act on"
    interaction = member_interaction(bot, user=user)
    interaction.message.id = job["notice_message_id"]
    await maybe_await(getattr(bot.change_queue, action)(job["notice_message_id"], interaction))
    if run:
        await run_queue(bot)
    return interaction


async def retry_job(bot: Any, *, user: Any = None, run: bool = True) -> Any:
    """Press Retry on the stop notice of the job the queue is stopped at, then run the queue.

    The presser is *user*, or a league manager. Gives the presser's interaction.
    """
    return await _press(bot, "retry", user or tier_member("manager"), run)


async def discard_job(bot: Any, *, user: Any = None, run: bool = True) -> Any:
    """Press Discard on the stop notice of the job the queue is stopped at, then run the queue.

    The presser is *user*, or a league admin. Gives the presser's interaction.
    """
    return await _press(bot, "discard", user or tier_member("admin"), run)
