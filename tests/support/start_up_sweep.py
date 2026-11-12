"""One of results' start-up steps run through the real start-up sweep, for the tests that pin
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
    import leaguebot.core.services.upkeep.start_up as start_up_module
    from leaguebot.core.utils.hooks import StartingHook
    from leaguebot.results.wiring import start_up_steps

    step = dict(start_up_steps(bot))[name]
    raised: list[BaseException] = []

    async def watched() -> None:
        try:
            await step()
        except Exception as error:
            raised.append(error)
            raise

    starting = StartingHook()
    starting.subscribe(lambda: [(name, watched)], module="results")

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


def round_failures(error: BaseException | None) -> dict[int, BaseException]:
    """The rounds a results' recovery could not recover, each with its error, as the one error it
    raised at the end of its step carries them: an exception type of results' own, its `failures`
    keyed by the round's id."""
    assert error is not None, "the recovery raised nothing"
    assert type(error).__module__.startswith("leaguebot.results."), type(error)
    failures = getattr(error, "failures")
    return dict(failures)
