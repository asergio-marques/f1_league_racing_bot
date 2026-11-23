"""What the bot's start-up runs: read from its source (#439, slice 6b, C72–C74), and driven through
its ready handler (#446).

A close of the signup window is a change on the queue, which a restart resumes like any other
(`signup.close`), and which saves the window's close and the season's move in one job. So no
close is ever left half done off the queue: the start-up has no step finishing an off-queue
close's closing notices, and none moving on a season a close left in its window's stage. Read
from the source, because the start-up's steps are defined in the entry point, among a dozen
others no test drives whole.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, call

import discord
import pytest

PENDING = "#600: start-up and `!sync` show no `/driver` by the test-mode flag"


async def _ready_bot(tmp_path, *, test_mode: bool):
    """The bot the entry point builds, on the league's server, found by id, with test mode as
    *test_mode*: its syncs recorded, none reaching Discord, and its log-channel posts recorded."""
    from leaguebot.framework.db.database import get_connection, run_migrations
    from tests.support.change_queue import league_bot, seed_server

    path = str(tmp_path / "ready.db")
    await run_migrations(path)
    await seed_server(path)
    async with get_connection(path) as db:
        await db.execute("UPDATE server_configs SET test_mode_active = ?", (int(test_mode),))
        cursor = await db.execute("SELECT server_id FROM server_configs")
        server_id = (await cursor.fetchone())["server_id"]
        await db.commit()
    bot = await league_bot(path, real_config=True)
    guild = discord.Object(id=server_id)
    bot.get_guild = MagicMock(return_value=guild)
    bot.output_router.post_log = AsyncMock()
    return bot, guild


def _syncs_seeing_driver(bot) -> list[tuple[object, object]]:
    """Make each sync record which tree it synced (None for the global one) and the `/driver` that
    tree carried as it was synced; gives the record."""
    seen: list[tuple[object, object]] = []

    async def sync(*, guild=None):
        seen.append((guild, bot.tree.get_command("driver", guild=guild)))
        return []

    bot.tree.sync = AsyncMock(side_effect=sync)
    return seen


def test_the_start_up_runs_no_step_for_an_off_queue_close():
    """The bot's start-up names neither the finish of an off-queue close nor the move of a
    season a close left behind, the bot's module defines neither, and no change type finishing
    an off-queue close (`signup.close.finish`) is registered anywhere in its source."""
    from leaguebot import __main__ as bot_module

    source = inspect.getsource(bot_module)
    for gone in ("_recover_off_queue_closing_notices",
                 "_recover_season_left_by_a_closed_window",
                 "signup.close.finish", "close_finish_change"):
        assert gone not in source, gone
        assert not hasattr(bot_module, gone), gone


@pytest.mark.xfail(strict=True, reason=PENDING)
async def test_a_second_ready_runs_no_start_up_step_and_syncs_nothing(tmp_path):
    """discord.py reports the bot ready a second time in one run, as after a network drop it could
    not resume (#446). The bot the entry point builds is in test mode. The first ready runs the
    start-up's one step and syncs the commands once globally and once on the league's server; the
    second runs no step and syncs nothing, so no recovery undoes work still in progress. Driven
    through the bot's own ready handler, so that a recovery called from it directly, past the
    start-up's run-once guard, fails here where `test_start_up_runs_once_on_a_second_ready` cannot
    see it."""
    import importlib
    from contextlib import asynccontextmanager
    from typing import Any

    from leaguebot import __main__ as bot_module
    from leaguebot.framework.utils.hooks import StartingHook

    # A fresh run-once guard: the guard lives in the start-up's module, once per process.
    importlib.reload(importlib.import_module("leaguebot.framework.services.jobs.start_up"))

    ran: list[str] = []

    async def step() -> None:
        ran.append("recovery")

    starting = StartingHook()
    starting.subscribe(step, name="recovery")

    class _Queue:
        @asynccontextmanager
        async def sweeping(self) -> Any:
            yield

        async def start(self) -> None:
            pass

        def release(self) -> None:
            pass

    class _Scheduler:
        def start(self, *, paused: bool) -> None:
            pass

        def resume(self) -> None:
            pass

    bot, guild = await _ready_bot(tmp_path, test_mode=True)
    bot.starting = starting
    bot.change_queue = _Queue()
    bot.scheduler_service = _Scheduler()

    await bot_module._when_ready(bot)
    await bot_module._when_ready(bot)

    assert ran == ["recovery"]
    assert sorted(map(str, bot.tree.sync.await_args_list)) == sorted(
        map(str, [call(), call(guild=guild)])
    )
    bot.output_router.post_log.assert_not_awaited()


@pytest.mark.xfail(strict=True, reason=PENDING)
@pytest.mark.parametrize("test_mode", [True, False], ids=["test-mode-on", "test-mode-off"])
async def test_start_up_shows_the_driver_the_flag_calls_for(tmp_path, monkeypatch, test_mode):
    """The bot the entry point builds is reported ready. In test mode, the global tree no longer
    carries `/driver` when it is synced, and the league's server is synced carrying the test-mode
    `/driver`, the bot's own cog's group. Out of it, the server still carries the test-mode group,
    as a swap Discord refused would leave it: the global tree is synced carrying the member
    `/driver`, and the server is synced carrying none. Nothing is posted to the log channel."""
    from leaguebot import __main__ as bot_module

    monkeypatch.setattr(bot_module, "start_up", AsyncMock(return_value=True))
    bot, guild = await _ready_bot(tmp_path, test_mode=test_mode)
    member_group = bot.get_cog("DriverCog").driver
    test_group = bot.get_cog("DriverTestCog").driver
    if not test_mode:
        bot.tree.add_command(test_group, guild=guild)
    seen = _syncs_seeing_driver(bot)

    await bot_module._when_ready(bot)

    assert sorted(seen, key=lambda each: each[0] is not None) == (
        [(None, None), (guild, test_group)] if test_mode
        else [(None, member_group), (guild, None)]
    )
    bot.output_router.post_log.assert_not_awaited()


@pytest.mark.xfail(strict=True, reason=PENDING)
async def test_a_server_sync_refused_at_start_up_is_one_failure_line(tmp_path, monkeypatch):
    """The bot the entry point builds is in test mode, and Discord refuses the sync of the
    league's server as the bot is reported ready. The global sync is made all the same, and the
    refusal is posted to the log channel once, as the start-up's failure line for it."""
    from leaguebot import __main__ as bot_module
    from tests.support.change_queue import http_error

    monkeypatch.setattr(bot_module, "start_up", AsyncMock(return_value=True))
    lines: list[tuple[str, BaseException]] = []
    made = bot_module.start_up_failure_line

    def failure_line(step: str, error: BaseException) -> str:
        lines.append((step, error))
        return made(step, error)

    monkeypatch.setattr(bot_module, "start_up_failure_line", failure_line)
    bot, guild = await _ready_bot(tmp_path, test_mode=True)
    refused = http_error()

    async def sync(*, guild=None):
        if guild is not None:
            raise refused
        return []

    bot.tree.sync = AsyncMock(side_effect=sync)

    await bot_module._when_ready(bot)

    assert call() in bot.tree.sync.await_args_list
    [(step, error)] = lines
    assert error is refused
    bot.output_router.post_log.assert_awaited_once_with(made(step, refused))


@pytest.mark.xfail(strict=True, reason=PENDING)
@pytest.mark.parametrize("test_mode", [True, False], ids=["test-mode-on", "test-mode-off"])
async def test_the_sync_command_leaves_the_driver_the_flag_calls_for(tmp_path, test_mode):
    """The bot the entry point builds, with test mode as the case gives, its league's server
    carrying a left-over copy of the member `/driver`. The owner sends `!sync`: the server's
    overrides are cleared, and once both trees are synced the server shows the `/driver` the flag
    calls for: in test mode the test-mode group on the server and none on the global tree;
    otherwise the member group on the global tree and none on the server."""
    bot, guild = await _ready_bot(tmp_path, test_mode=test_mode)
    member_group = bot.get_cog("DriverCog").driver
    test_group = bot.get_cog("DriverTestCog").driver
    bot.tree.add_command(member_group, guild=guild)
    seen = _syncs_seeing_driver(bot)
    ctx = MagicMock(guild=guild, send=AsyncMock())
    ctx.message.delete = AsyncMock()

    await bot.get_command("sync").callback(ctx)

    expected = (None, test_group) if test_mode else (member_group, None)
    assert (bot.tree.get_command("driver"), bot.tree.get_command("driver", guild=guild)) == expected
    assert sorted(seen, key=lambda each: each[0] is not None) == [
        (None, expected[0]), (guild, expected[1])
    ]
