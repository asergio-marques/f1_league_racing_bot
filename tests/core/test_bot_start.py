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


async def test_a_second_ready_runs_no_start_up_step_and_syncs_nothing():
    """discord.py reports the bot ready a second time in one run, as after a network drop it could
    not resume (#446). The first ready runs the start-up's one step and syncs the commands once;
    the second runs no step and syncs nothing, so no recovery undoes work still in progress. Driven
    through the bot's own ready handler, so that a recovery called from it directly, past the
    start-up's run-once guard, fails here where `test_start_up_runs_once_on_a_second_ready` cannot
    see it."""
    import importlib
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from typing import Any
    from unittest.mock import AsyncMock

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

    bot = SimpleNamespace(
        user=SimpleNamespace(id=1),
        starting=starting,
        module_service=SimpleNamespace(),
        change_queue=_Queue(),
        scheduler_service=_Scheduler(),
        output_router=SimpleNamespace(post_log=AsyncMock()),
        tree=SimpleNamespace(sync=AsyncMock(return_value=[])),
    )

    await bot_module._when_ready(bot)
    await bot_module._when_ready(bot)

    assert ran == ["recovery"]
    assert bot.tree.sync.await_count == 1
    bot.output_router.post_log.assert_not_awaited()
