"""One of a module's start-up steps run through the real start-up sweep, for the tests that pin
what the sweep reports when a recovery fails (architecture.md, "The sweep is core's own steps,
then each enabled module's turn")."""
from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from typing import Any


async def run_results_start_up_step(bot: Any, name: str) -> tuple[list[str], BaseException | None]:
    """Run results' start-up step *name*, as `start_up_steps(bot)` gives it, alone in results' turn
    of the start-up sweep, with results on. Gives the lines the sweep posted to the log channel and
    the error the step raised, if any."""
    return await _run_start_up_step(bot, "results", name)


async def run_attendance_start_up_step(
    bot: Any, name: str
) -> tuple[list[str], BaseException | None]:
    """Run attendance's start-up step *name*, as `start_up_steps(bot)` gives it, alone in
    attendance's turn of the start-up sweep, with attendance on. Gives the lines the sweep posted
    to the log channel and the error the step raised, if any."""
    return await _run_start_up_step(bot, "attendance", name)


async def _run_start_up_step(
    bot: Any, module: str, name: str
) -> tuple[list[str], BaseException | None]:
    """Run *module*'s start-up step *name*, from `leaguebot.<module>.wiring.start_up_steps(bot)`,
    alone in *module*'s turn of the real start-up sweep, the module on and core's queue and
    scheduler stood in for."""
    import leaguebot.core.services.upkeep.start_up as start_up_module
    from leaguebot.core.utils.hooks import StartingHook

    wiring = importlib.import_module(f"leaguebot.{module}.wiring")
    step = dict(wiring.start_up_steps(bot))[name]
    raised: list[BaseException] = []

    async def watched() -> None:
        try:
            await step()
        except Exception as error:
            raised.append(error)
            raise

    starting = StartingHook()
    starting.subscribe(lambda: [(name, watched)], module=module)

    class _Modules:
        async def is_enabled(self, module: str) -> bool:
            return True

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

    lines: list[str] = []

    async def post_log(content: str, **_: Any) -> None:
        lines.append(content)

    start_up = importlib.reload(start_up_module).start_up
    await start_up(starting=starting, modules=_Modules(), change_queue=_Queue(),
                   scheduler=_Scheduler(), post_log=post_log)
    return lines, (raised[0] if raised else None)


async def run_signup_start_up(bot: Any, *, on: bool) -> list[str]:
    """Run every one of signup's start-up steps, as `start_up_steps(bot)` gives them, in signup's
    turn of the real start-up sweep, signup's switch reading *on*, core's queue and scheduler stood
    in for (each step asks the bot's own change queue). Gives the lines the sweep posted to the log
    channel."""
    import leaguebot.core.services.upkeep.start_up as start_up_module
    from leaguebot.core.utils.hooks import StartingHook
    from leaguebot.signup import wiring

    starting = StartingHook()
    starting.subscribe(lambda: wiring.start_up_steps(bot), module="signup")

    class _Modules:
        async def is_enabled(self, module: str) -> bool:
            return on

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

    lines: list[str] = []

    async def post_log(content: str, **_: Any) -> None:
        lines.append(content)

    start_up = importlib.reload(start_up_module).start_up
    await start_up(starting=starting, modules=_Modules(), change_queue=_Queue(),
                   scheduler=_Scheduler(), post_log=post_log)
    return lines


def round_failures(
    error: BaseException | None, *, module: str = "results"
) -> dict[int, BaseException]:
    """The rounds a *module*'s recovery could not recover, each with its error, as the one error it
    raised at the end of its step carries them: an exception type of the bot's own, its `failures`
    keyed by the round's id. Results' own type for a results recovery; for another module's, any
    type of the bot's, as the module may raise its own or one core names for every module."""
    assert error is not None, "the recovery raised nothing"
    owner = "leaguebot.results." if module == "results" else "leaguebot."
    assert type(error).__module__.startswith(owner), type(error)
    failures = getattr(error, "failures")
    return dict(failures)
