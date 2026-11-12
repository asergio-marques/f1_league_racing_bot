"""A real change queue for a test, on the test's own database, with "now" pinned (#439).

The queue is the one the bot builds (`leaguebot.core.services.queue.change_queue.ChangeQueue`), handed the
router the test gives it, or the bot's own. `built_bot` is the real bot instead, every service
and change type as the entry point's `build` makes them (#283), its queue's clock pinned the same way.

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

A message a member types into a conversation's channel is a turn (#439, 6b): `typed_message` is
one, recording the marks the bot holds on it, and `ask_turn` does with it what a listener does,
turning it away where a turn in its channel still waits, else marking it and asking its turn.

A function that posts refuses to run outside a job (`posts_in_a_job`). A unit test calling one
directly, with no queue, wraps the call in `in_a_job()`, which runs it as a job would.

A queue `restart_queue` starts has a worker running in the background, and is stopped before the
test ends: `tests/conftest.py` calls `stop_started_queues` once the test has run, while its event
loop is still open, and it waits there for any database thread a stopped worker left running.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import sqlite3
import threading
import time
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from leaguebot.core.db.database import get_connection
from tests.support.connections import close_lent_connections

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
    from leaguebot.core.services.queue.change_queue import ChangeQueue

    clock = now if isinstance(now, Clock) else Clock(now)
    chosen = router if router is not None else bot.output_router
    queue = ChangeQueue(db_path, bot, chosen, bot.config_service, clock=clock)
    bot.change_queue = queue
    setup = _Setup(db_path, clock, chosen, real_types=types is None, types=list(types or ()))
    setattr(bot, _SETUP, setup)
    _register(bot, queue, setup)
    return queue


async def built_bot(tmp_path: Any, *, now: datetime | Clock) -> Any:
    """The real bot, as the entry point's `build` makes it (#283), on settings under *tmp_path*
    (`bot.db` and `jobs.db`), its schema migrated first and its change queue's clock *now*.

    Every service is the builder's and every change type, timer kind and scheduler callback its
    registration, so a test gets the real set of services; nothing is started. `restart_queue`
    gives it a fresh queue as it does any other bot. A test needing one service keeps its
    hand-made double."""
    from leaguebot.__main__ import Settings, build
    from leaguebot.core.db.database import run_migrations

    settings = Settings(token="not-a-real-token", db_path=str(tmp_path / "bot.db"),
                        jobstore_path=str(tmp_path / "jobs.db"))
    await run_migrations(settings.db_path)
    bot = await maybe_await(build(settings))
    clock = now if isinstance(now, Clock) else Clock(now)
    # The builder's queue reads the host's clock; the test's is pinned.
    bot.change_queue._clock = clock
    setattr(bot, _SETUP, _Setup(settings.db_path, clock, bot.output_router, real_types=True))
    return bot


def register(bot: Any, *change_types: Any) -> None:
    """Register *change_types* on the bot's queue, and again on every queue `restart_queue` builds."""
    setup: _Setup = getattr(bot, _SETUP)
    for change_type in change_types:
        bot.change_queue.register(change_type)
        setup.types.append(change_type)


async def run_queue(bot: Any, *, steps: int | None = None) -> None:
    """Run the bot's queue until nothing can run now, or until *steps* steps are done.

    Every lane is run, the league's and each conversation's, one change at a time: the queue's own
    `run_until_idle` is the tests' serial runner, taking the runnable change asked first."""
    if steps is None:
        await bot.change_queue.run_until_idle()
    else:
        await bot.change_queue.run_until_idle(steps=steps)


#: How long `stop_started_queues` waits, in all, for aiosqlite's threads to finish.
#:
#: A test that pays it has left a connection open past its call: a task it abandoned still
#: holding one, as a hung autocomplete did until its test let the hang go and waited for it. A
#: survey of the whole suite found that test the only one (#540), besides the test of this wait
#: itself. Isolated stalls of several seconds on the Windows runner are not this wait: they did
#: not recur from run to run, and are the garbage collector's and the runner's own.
THREAD_GRACE_SECONDS = 5.0

#: Every queue `restart_queue` has started, with the event loop its worker runs on.
_STARTED: list[tuple[Any, asyncio.AbstractEventLoop]] = []


async def restart_queue(bot: Any) -> Any:
    """A fresh queue on the same database, clock and router, as after a restart, and started.

    The old queue is stopped first where it was started. The same change types are registered.
    The queue is remembered, for `stop_started_queues` to stop once the test has run.
    """
    from leaguebot.core.services.queue.change_queue import ChangeQueue

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


def stop_started_queues(*, started_before: Collection[threading.Thread] = ()) -> None:
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
    loop is left to close; none is alive where nothing was cut off. A thread in *started_before*,
    alive before the test's call began (a connection a fixture holds until its teardown), is not
    waited for: it would never finish in time, and every test holding one would pay the whole
    grace period.

    **The connections the bot keeps idle to lend again are closed between the two**, once the
    queues are stopped and before the wait (`tests/support/connections.py`). Each idle one keeps
    its thread alive until it is closed, so a test that only gave its connections back would
    otherwise pay the whole grace period
    (`test_stop_started_queues_waits_on_no_idle_lent_connection`).
    """
    _stop_queues()
    close_lent_connections()
    _wait_for_database_threads(started_before)


def _stop_queues() -> None:
    """Stop every queue `restart_queue` started, on the loop it runs on, and forget them."""
    started, _STARTED[:] = list(_STARTED), []
    for queue, loop in started:
        if not loop.is_closed() and not loop.is_running():
            loop.run_until_complete(queue.stop())


def _wait_for_database_threads(started_before: Collection[threading.Thread]) -> None:
    """Wait for each aiosqlite thread not in *started_before*, up to `THREAD_GRACE_SECONDS` in
    all."""
    deadline = time.monotonic() + THREAD_GRACE_SECONDS
    earlier = set(started_before)
    for thread in threading.enumerate():
        # aiosqlite names no thread; Python names it after its target.
        if thread.name.endswith("(_connection_worker_thread)") and thread not in earlier:
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
    its `id`, `content` and `view`, which an `edit` with `view=` replaces, and a `delete` that
    takes it out of `posted`. `fetch_message` and `get_partial_message` find a posted message by
    id; one removed from `posted` is gone, and `NotFound` is raised for it: by an edit, a delete,
    or a reaction added, removed or cleared.
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

        async def _delete(*_args: Any, **_kwargs: Any) -> None:
            if message not in channel.posted:
                raise http_error(discord.NotFound, status=404, text="Unknown Message")
            channel.posted.remove(message)

        message.edit = AsyncMock(side_effect=_edit)
        message.delete = AsyncMock(side_effect=_delete)
        channel.posted.append(message)
        return message

    def _gone(message_id: int) -> Any:
        gone = MagicMock(id=message_id)
        for name in ("edit", "delete", "add_reaction", "remove_reaction", "clear_reaction"):
            setattr(gone, name, AsyncMock(
                side_effect=http_error(discord.NotFound, status=404, text="Unknown Message")
            ))
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


def history_channel(channel_id: int) -> Any:
    """A text channel whose messages stand newest first in `messages`, and each one deleted is kept
    in `deleted`. `history` reads what stands when it is read; `get_partial_message` and
    `fetch_message` find a message standing, and `NotFound` is raised for one gone. The bot may
    manage its messages. A message is added by `post_in`."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.mention = f"<#{channel_id}>"
    channel.messages = []
    channel.deleted = []
    channel.permissions_for = MagicMock(return_value=MagicMock(manage_messages=True))

    async def _history(*, limit: int = 100, **_kwargs: Any) -> Any:
        for message in list(channel.messages)[:limit]:
            yield message

    def _find(message_id: int) -> Any:
        found = [m for m in channel.messages if m.id == message_id]
        if found:
            return found[0]
        gone = MagicMock(id=message_id)
        gone.delete = AsyncMock(
            side_effect=http_error(discord.NotFound, status=404, text="Unknown Message")
        )
        return gone

    async def _fetch(message_id: int) -> Any:
        found = [m for m in channel.messages if m.id == message_id]
        if not found:
            raise http_error(discord.NotFound, status=404, text="Unknown Message")
        return found[0]

    channel.history = _history
    channel.get_partial_message = MagicMock(side_effect=_find)
    channel.fetch_message = AsyncMock(side_effect=_fetch)
    return channel


#: The ids `post_in` gives the messages it posts.
_POSTED_IDS = itertools.count(9001)


def post_in(channel: Any, author: Any, *, fails: Exception | None = None) -> Any:
    """A message *author* posts in *channel*, now its newest. `fails` refuses its deletion."""
    message = MagicMock(spec=discord.Message)
    message.id = next(_POSTED_IDS)
    message.author = author
    message.channel = channel
    message.jump_url = f"https://discord.test/{channel.id}/{message.id}"
    message.fails = fails

    async def _delete(*_args: Any, **_kwargs: Any) -> None:
        if message not in channel.messages:
            raise http_error(discord.NotFound, status=404, text="Unknown Message")
        if message.fails is not None:
            raise message.fails
        channel.messages.remove(message)
        channel.deleted.append(message)

    message.delete = AsyncMock(side_effect=_delete)
    channel.messages.insert(0, message)
    return message


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


def league_double(db_path: str, *, now: Any = None) -> Any:
    """A bot double with the league's log and interaction channels, and a real `OutputRouter`.

    The router's clock is *now* where given, else the change queue's, read through the bot as the
    builder hands it, so that a test attaching a queue moves the router's time with the queue's.

    `bot.log_channel` and `bot.interaction_channel` record what each is sent. The league's server
    is `SERVER_ID`, as `config_service.get_league_server_id` gives it; `bot.get_guild` finds no
    guild for it until a test gives one.
    `bot.attendance_after_review` is the attendance hook the builder hands the review's change
    types (#439), as a league with attendance off sees it (`attendance_hook_double`). A test that
    runs the hook replaces it.
    """
    from leaguebot.core.services.posting.output_router import OutputRouter

    bot = MagicMock()
    bot.db_path = db_path
    _stub_discord(bot)
    bot.output_router = OutputRouter(
        bot, bot.config_service, retry_db_path=db_path,
        now=now if now is not None else (lambda: bot.change_queue.now()),
    )
    bot.attendance_after_review = attendance_hook_double()
    return bot


def _stub_discord(bot: Any, *, real_config: bool = False) -> None:
    """The Discord side `league_double` gives, over *bot*: the log and interaction channels,
    recording what each is sent and found by id, no guild, a double of every partial messageable,
    the persistent views it is given recorded and not served, and, unless *real_config*, the
    league's server and channels as the config service reads them."""
    bot.log_channel = text_channel(LOG_CHANNEL_ID)
    bot.interaction_channel = text_channel(INTERACTION_CHANNEL_ID)
    channels = {LOG_CHANNEL_ID: bot.log_channel, INTERACTION_CHANNEL_ID: bot.interaction_channel}
    bot.get_channel = MagicMock(side_effect=channels.get)
    bot.fetch_channel = AsyncMock(side_effect=lambda cid: channels[cid])
    bot.get_guild = MagicMock(return_value=None)
    # A message reached by id alone, as the views' recoveries reach theirs.
    bot.get_partial_messageable = MagicMock()
    bot.add_view = MagicMock()
    if real_config:
        return
    # The hub's channel is read from the database once, here, rather than fixed: a test that
    # writes `hub_channel_id` before building the bot (as setting the hub up for a refresh does)
    # is seen, and one that never sets one, or whose migrations have not run yet (some build the
    # bot first), is correctly read as having none — a MagicMock attribute left to its default is
    # truthy, and every module switch's hub-refresh follow-on (#283) would try a hub that is not
    # there. A static `return_value`, as every other attribute keeps, so a test may still set one
    # by hand afterwards (`.return_value.attr = …`).
    hub_channel_id = None
    try:
        with sqlite3.connect(bot.db_path) as conn:
            row = conn.execute(
                "SELECT hub_channel_id FROM server_configs WHERE server_id IS NOT NULL"
            ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is not None:
        hub_channel_id = row[0]
    bot.config_service.get_server_config = AsyncMock(
        return_value=MagicMock(
            server_id=SERVER_ID,
            log_channel_id=LOG_CHANNEL_ID,
            interaction_channel_id=INTERACTION_CHANNEL_ID,
            interaction_role_id=MANAGER_ROLE_ID,
            league_admin_role_id=ADMIN_ROLE_ID,
            hub_channel_id=hub_channel_id,
        )
    )
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)


def attendance_hook_double() -> Any:
    """Attendance's share of a round's review as a league with attendance off sees it: every
    method does nothing, no driver is owed a sanction, no channel of attendance's stands in an
    amendment's way, no check-in reading is planned for a round ended not held, an amendment's
    snapshot keeps no pardons, no driver has an attendance row for the pardon form to read, no
    division has a scored round, and the sync hint names `/attendance sync`."""
    hook = MagicMock()
    for name in ("record_on", "rewrite_pardons_on", "put_back_pardons_on", "recalculate_on",
                 "post_sheet",
                 "apply_sanction", "announce_sanction", "refresh_lineup", "record_sheet_on",
                 "check_sanction", "apply_sanction_on", "change_sanction_roles"):
        setattr(hook, name, AsyncMock(return_value=None))
    hook.sanction_candidates = AsyncMock(return_value=[])
    hook.recalculation_faults = AsyncMock(return_value=[])
    hook.check_in_read_on = AsyncMock(return_value=[])
    hook.pardons_of_on = AsyncMock(return_value=[])
    hook.attendance_row_of = AsyncMock(return_value=None)
    hook.latest_scored_round = AsyncMock(return_value=None)
    hook.sync_hint = AsyncMock(return_value="Repair the cause, then run `/attendance sync`.")
    hook.pardons_to_review = AsyncMock(side_effect=lambda pardons: list(pardons))
    return hook


#: The classes `league_bot` hands the builder a double of, so that the change types it
#: registers hold the double: by name, as `build` imports them.
#: The season service is doubled only where a league gives one.
_DOUBLED_AT_BUILD = {
    "scheduler": ("leaguebot.core.services.scheduler_service", "SchedulerService"),
    "attendance_hook": ("leaguebot.attendance.services.attendance_after_review",
                        "AttendanceAfterReview"),
    "season_service": ("leaguebot.core.services.seasons.season_service", "SeasonService"),
}


async def league_bot(db_path: str, *, scheduler: Any = None, attendance_hook: Any = None,
                     attendance_hook_of: Any = None, season_service: Any = None,
                     real_config: bool = False) -> Any:
    """The bot of a test league on *db_path*, the Discord side `league_double` gives stubbed over
    it, its scheduler *scheduler* (a bare double if None), its attendance hook
    *attendance_hook* (`attendance_hook_double`'s if None) and, where given, its season service
    *season_service*; its queue is attached by `league_queue`, once the league has set its own
    services (#283). With *real_config*, the league's config service reads the database, as a
    league building its own `ConfigService` has it.

    *attendance_hook_of*, where given, makes the hook in place of *attendance_hook*: called with
    the bot and *db_path* as the builder makes attendance's hook, so that a league can hand it
    the real `AttendanceAfterReview`, which holds the bot, with a placement service of its own.
    A factory calling the real class holds it from before the call, since the builder's is the
    double for the build's length.

    The bot is the one the entry point's `build` makes, on settings for *db_path* (the job store
    `jobs.db` beside it), handed the doubles in place of the scheduler, attendance's hook and the
    season service as it builds them, so the change types it registers hold the doubles; every
    other service is the builder's."""
    import importlib
    import os
    from contextlib import ExitStack
    from unittest.mock import patch

    scheduler = scheduler if scheduler is not None else MagicMock()
    if attendance_hook is None and attendance_hook_of is None:
        attendance_hook = attendance_hook_double()
    from leaguebot.__main__ import Settings, build

    settings = Settings(token="not-a-real-token", db_path=db_path,
                        jobstore_path=os.path.join(os.path.dirname(db_path), "jobs.db"))
    import leaguebot.__main__ as entry_point

    doubles = {"scheduler": scheduler, "attendance_hook": attendance_hook or attendance_hook_of,
               "season_service": season_service}
    with ExitStack() as stack:
        for key, (module_name, name) in _DOUBLED_AT_BUILD.items():
            if doubles[key] is None:
                continue
            made = MagicMock(return_value=doubles[key])
            if key == "attendance_hook" and attendance_hook_of is not None:
                made = MagicMock(side_effect=lambda bot, *_args, **_kwargs: attendance_hook_of(
                    bot, db_path))
            stack.enter_context(patch.object(importlib.import_module(module_name), name, made))
            if hasattr(entry_point, name):
                stack.enter_context(patch.object(entry_point, name, made))
        bot = await maybe_await(build(settings))
    _stub_discord(bot, real_config=real_config)
    # A bot that has not logged in has no user of its own.
    bot._connection.user = MagicMock()
    return bot


def set_guilds(bot: Any, guilds: list[Any]) -> None:
    """The servers *bot* is in, as its connection holds them, since a `discord.Client`'s
    `guilds` cannot be set."""
    bot._connection._guilds = {guild.id: guild for guild in guilds}


def set_user(bot: Any, user: Any) -> None:
    """The bot's own Discord user, as its connection holds it, since a `discord.Client`'s `user`
    cannot be set."""
    bot._connection.user = user


def league_queue(bot: Any, db_path: str, *, now: datetime | Clock) -> Any:
    """The league's change queue, its clock *now*: the builder's queue with the change types it
    registered, the clock pinned."""
    clock = now if isinstance(now, Clock) else Clock(now)
    # The builder's queue reads the host's clock; the test's is pinned.
    bot.change_queue._clock = clock
    setattr(bot, _SETUP, _Setup(db_path, clock, bot.output_router, real_types=True))
    return bot.change_queue


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


class _InItsSlot:
    """*interaction*, its response read from discord.py's response slot (`_cs_response`), as a
    real interaction's is, so that what puts another response in the slot is answered through."""

    def __init__(self, interaction: Any) -> None:
        self.__dict__["_inner"] = interaction

    @discord.utils.cached_slot_property("_cs_response")
    def response(self) -> Any:
        return self._inner.response

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_cs_"):
            # An empty slot, which a double would otherwise invent.
            raise AttributeError(name)
        return getattr(self._inner, name)


def response_in_its_slot(interaction: Any) -> Any:
    """*interaction*, a double, answering through its response slot as a real interaction does
    (#440): a command held for the catch-up after a restart has its response replaced there by
    `after_the_catch_up`, which a double's own `response` attribute would never see. The double's
    response, follow-ups and everything else are still the ones it records."""
    return _InItsSlot(interaction)


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


async def stopped_job(db_path: str, lane: str | None = None) -> dict[str, Any] | None:
    """The job the queue is stopped at: not done, and failed since `failing_since`; or None.

    Only a job of a change still QUEUED or RUNNING counts: a change that has ended (discarded
    at a job, say) leaves the job it stopped at as it was. With *lane* (`"league"`, or a
    conversation's `"channel:<id>"`), only a job of a change on that lane; a change saved with no
    lane is the league's."""
    live = {row["id"] for row in await change_rows(db_path)
            if row["state"] in ("QUEUED", "RUNNING")
            and (lane is None or row.get("lane", "league") == lane)}
    stopped = [
        row for row in await step_rows(db_path)
        if row["change_id"] in live and row["done_at"] is None
        and row["failing_since"] is not None
    ]
    return stopped[0] if stopped else None


#: The words a change that overtakes a stop adds to its acknowledgement, after the queue's own.
PAST_THE_STOP = (
    " The queue is stopped at job #{job}, so this request runs at once, past the stop; that job "
    "stays stopped."
)


async def stop_the_league_lane(bot: Any, db_path: str) -> dict[str, Any]:
    """Stop the league's lane on another job: a change whose one job fails on a fault in the bot
    is asked and run. Gives the job it stopped at."""
    from leaguebot.core.models.change import PlannedStep, StepKind, StepResult, Verdict
    from leaguebot.core.services.queue.change_types import ChangeType, Step

    async def fails(_ctx: Any) -> StepResult:
        raise RuntimeError("a fault in the bot")

    async def go(_ctx: Any) -> Verdict:
        return Verdict.go()

    register(bot, ChangeType(
        kind="test.stopping",
        opening=(PlannedStep("fail"),),
        steps={"fail": Step("fail", StepKind.ACT, fails)},
        check=go,
        key=lambda _payload: "test.stopping",
        doing=lambda _payload: "Doing another job",
        outcome=lambda _ctx: "Done.",
    ))
    other = await bot.change_queue.ask("test.stopping", {}, actor=member(), what="another job")
    await run_queue(bot)
    stopped = await stopped_job(db_path)
    assert stopped is not None and stopped["change_id"] == other
    return stopped


async def refuse_the_job(db_path: str, kind: str, job: str, refusing: bool) -> None:
    """While *refusing*, a fault of the database refuses to save the job *job* of any change of
    *kind* as done, so that its save fails and is rolled back and the queue stops at it; otherwise
    the fault is lifted. A league admin's Discard of the job is still saved."""
    async with get_connection(db_path) as db:
        if refusing:
            await db.execute(
                "CREATE TRIGGER refuse_the_job BEFORE UPDATE OF done_at ON queued_change_steps "
                f"WHEN NEW.name = '{job}' AND NEW.done_at IS NOT NULL "
                "AND COALESCE(NEW.result, '') NOT LIKE '%\"discarded\"%' AND (SELECT kind FROM "
                f"queued_changes WHERE id = NEW.change_id) = '{kind}' "
                "BEGIN SELECT RAISE(ABORT, 'disk fault'); END"
            )
        else:
            await db.execute("DROP TRIGGER refuse_the_job")
        await db.commit()


async def _press(bot: Any, action: str, user: Any, run: bool, lane: str | None = None) -> Any:
    job = await stopped_job(bot.db_path, lane)
    assert job is not None, f"no job stops the queue for {action} to act on"
    interaction = member_interaction(bot, user=user)
    interaction.message.id = job["notice_message_id"]
    await maybe_await(getattr(bot.change_queue, action)(job["notice_message_id"], interaction))
    if run:
        await run_queue(bot)
    return interaction


async def retry_job(bot: Any, *, user: Any = None, run: bool = True,
                    lane: str | None = None) -> Any:
    """Press Retry on the stop notice of the job the queue is stopped at, then run the queue.

    The presser is *user*, or a league manager. With *lane*, the job that lane is stopped at.
    Gives the presser's interaction.
    """
    return await _press(bot, "retry", user or tier_member("manager"), run, lane)


async def discard_job(bot: Any, *, user: Any = None, run: bool = True,
                      lane: str | None = None) -> Any:
    """Press Discard on the stop notice of the job the queue is stopped at, then run the queue.

    The presser is *user*, or a league admin. With *lane*, the job that lane is stopped at.
    Gives the presser's interaction.
    """
    return await _press(bot, "discard", user or tier_member("admin"), run, lane)


#: The job number `in_a_job` runs a unit test's call as.
TEST_JOB = 1


@contextmanager
def in_a_job(job_id: int = TEST_JOB) -> Iterator[None]:
    """Run the body as job *job_id* runs, for a unit test calling a function that posts directly,
    with no queue to run it in a job. Where the bot keeps no running job's number yet
    (`leaguebot.core.utils.in_a_job`, #439 6b), no function refuses a post outside a job, and the
    body runs as it is."""
    try:
        from leaguebot.core.utils.in_a_job import running_job
    except ModuleNotFoundError as missing:
        if missing.name != "leaguebot.core.utils.in_a_job":
            raise
        yield
        return
    with running_job(job_id):
        yield


# ---------------------------------------------------------------------------
# A typed message, and a turn asked for it
# ---------------------------------------------------------------------------

#: Ids for the messages members type, apart from those the bot posts (numbered from 1).
_TYPED_IDS = itertools.count(5001)

#: The mark the bot adds to a typed message at once, and clears when its turn is done.
MARK = "\u23f3"


def typed_message(channel: Any, author: Any, content: str = "", *,
                  created_at: datetime) -> Any:
    """A message *author* typed into *channel* (a `text_channel`) at *created_at*.

    `marks` holds the reactions the bot holds on it: `add_reaction` adds one, and
    `remove_reaction` or `clear_reaction` takes it off. Set `fails_marks` to an exception to refuse
    a reaction, and `fails_delete` to refuse its deletion. It is found by the channel's
    `fetch_message` and `get_partial_message` until it is deleted, by `delete()` or
    `typed_message_deleted`; once deleted, every reaction and delete raises `NotFound`.
    """
    message = MagicMock(spec=discord.Message)
    message.id = next(_TYPED_IDS)
    message.channel = channel
    # Typed in a server: the server of the channel, as a listener asks which it came from.
    message.guild = getattr(channel, "guild", None)
    message.author = author
    message.content = content
    message.created_at = created_at
    message.attachments = []
    message.marks = set()
    message.fails_marks = None
    message.fails_delete = None
    message.gone = False
    message.jump_url = f"https://discord.test/{channel.id}/{message.id}"

    def _check(fails: Any) -> None:
        if message.gone:
            raise http_error(discord.NotFound, status=404, text="Unknown Message")
        if fails is not None:
            raise fails

    async def _add(emoji: Any) -> None:
        _check(message.fails_marks)
        message.marks.add(str(emoji))

    async def _remove(emoji: Any, *_member: Any) -> None:
        _check(message.fails_marks)
        message.marks.discard(str(emoji))

    async def _delete(*_args: Any, **_kwargs: Any) -> None:
        _check(message.fails_delete)
        typed_message_deleted(message)

    message.add_reaction = AsyncMock(side_effect=_add)
    message.remove_reaction = AsyncMock(side_effect=_remove)
    message.clear_reaction = AsyncMock(side_effect=_remove)
    message.delete = AsyncMock(side_effect=_delete)
    channel.posted.append(message)
    return message


async def held_on(queue: Any, channel_id: int, *, timeout: float = 5.0) -> None:
    """Wait until a message typed in *channel_id* is held by the catch-up after a restart: its
    turn is asked and waits for the catch-up to end, the channel among the queue's held turn
    channels (#440)."""
    async def _held() -> None:
        while channel_id not in queue.held_turn_channels():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_held(), timeout)


def typed_message_deleted(message: Any) -> None:
    """*message* is deleted from its channel, by the member or by Discord."""
    message.gone = True
    if message in message.channel.posted:
        message.channel.posted.remove(message)


def turn_payload(message: Any) -> dict[str, Any]:
    """The payload a listener asks a typed message's turn with: its channel, its id, its author,
    what it says, its attachments' URLs and when it was typed."""
    return {
        "channel_id": message.channel.id,
        "message_id": message.id,
        "author_id": message.author.id,
        "content": message.content,
        "attachments": [attachment.url for attachment in message.attachments],
        "typed_at": message.created_at.isoformat(),
    }


async def ask_turn(bot: Any, kind: str, message: Any, *, types: Any = None,
                   what: str = "a typed answer") -> int | None:
    """Do what a listener does with a typed *message* it would act on, and give the change's id.

    Where a turn in the message's channel is still waiting, the message is turned away at once,
    off the queue, with the note (`typed_turns.turn_away`), and nothing is asked: None. Otherwise
    it is marked at once and the turn of *kind* asked for as the member's, with no interaction.
    *types* are the change types `turn_waiting` reads, by default those the test registered.
    """
    from leaguebot.core.services.queue import typed_turns

    chosen = types if types is not None else getattr(bot, _SETUP).types
    if await typed_turns.turn_waiting(bot.db_path, chosen, message.channel.id):
        await typed_turns.turn_away(message, typed_turns.TURN_AWAY_NOTE)
        return None
    await typed_turns.mark(message)
    return await bot.change_queue.ask(
        kind, turn_payload(message), actor=message.author, what=what,
    )
