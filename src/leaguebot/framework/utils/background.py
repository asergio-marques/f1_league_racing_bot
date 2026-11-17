"""The one helper that starts background work and keeps hold of it.

Python holds only a weak reference to a task, so one started with `asyncio.ensure_future` and
dropped can be collected part-way through, and a failure in it reaches nobody
(`docs/design/architecture.md`, "Background work is started through one helper"). `start_background`
creates the task, holds it in `_running` until it ends, and runs its work through the job runner
(`framework/utils/job_runner.py`), so that a fault is logged with its traceback and, where a
*report* is given, written to the log channel as one line. The helper adds no catch-all of its
own: the runner's is the one.

**A cancelled task is no failure.** The runner lets a cancellation pass, so the task ends
cancelled, neither logged as a fault nor reported; a caller that holds the task and cancels it
(a signup review's lapse) awaits it as it would any task.

**No report where the log channel is what failed**, or where the work's own closing line is told
elsewhere: the fault then goes to the host's log alone.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from leaguebot.framework.utils.job_runner import Report, run_job

#: The tasks started here that have not ended, each dropped as it does: tasks, not league state.
_running: set[asyncio.Task[None]] = set()


def start_background(
    coro: Coroutine[Any, Any, object], *, name: str, report: Report | None = None
) -> asyncio.Task[None]:
    """Start *coro* as a task named *name*, hold it until it ends, and return it.

    A fault in *coro* is logged naming *name* and, where *report* is given, reported to the log
    channel by one line; a fault in *report* is logged and dropped. The returned task never
    raises, only ends cancelled where it is cancelled.
    """

    async def run() -> None:
        await run_job(name, lambda: coro, report=report)

    task = asyncio.ensure_future(run())
    task.set_name(name)
    _running.add(task)

    def ended(finished: asyncio.Task[None]) -> None:
        _running.discard(finished)
        # A task cancelled before it started never awaited the work: close it, that Python does
        # not warn of a coroutine never awaited. A coroutine that ran to its end closes harmlessly.
        coro.close()

    task.add_done_callback(ended)
    return task
