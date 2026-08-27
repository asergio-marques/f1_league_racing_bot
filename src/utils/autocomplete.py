"""bounded_autocomplete decorator — keeps an autocomplete inside Discord's budget.

Discord allows an autocomplete callback **three seconds** and provides no way to defer one:
`interaction.response.defer()` exists for commands, and there is no equivalent here. Answer
late and the interaction token has already expired, so the reply comes back as
`404 Unknown interaction` (error code 10062) — which is exactly what a `/images test lineup`
autocomplete did on 2026-08-25.

Offering no choices is a far better outcome than answering into a dead token: the manager
types another character and gets a fresh interaction. This decorator makes that the failure
mode, and logs the slow calls so a latency regression is visible before it starts failing.

It is the outer net only. The fix for the underlying contention is WAL and the job store
split; see `db/database.py` and `services/scheduler_service.py`.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

#: How long a callback may take before we stop waiting and offer nothing.
#:
#: Leaves roughly half the three-second budget for discord.py's HTTP send and for event-loop
#: latency. Deliberately not tighter: the failure this guards against is the *loop itself*
#: stalling, and a tighter deadline would give up on calls that would have made it.
DEFAULT_DEADLINE = 1.5

#: How long a *successful* call may take before it is worth a WARNING.
DEFAULT_SLOW_AFTER = 0.75


def bounded_autocomplete(
    *, deadline: float = DEFAULT_DEADLINE, slow_after: float = DEFAULT_SLOW_AFTER
) -> Callable:
    """Bound an autocomplete callback's runtime, returning no choices if it overruns.

    Usage:
        @bounded_autocomplete()
        async def _my_autocomplete(self, interaction, current) -> list[Choice[str]]:
            ...

    Any failure — an exception, or simply taking too long — becomes an empty list. An
    autocomplete never breaks the command it decorates.
    """

    def decorate(func: Callable) -> Callable:
        # functools.wraps is load-bearing, not cosmetic. discord.py decides whether a
        # callback is a bound method from its __qualname__, and reads its parameters with
        # inspect.signature, which follows __wrapped__. Without wraps, registration breaks.
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> list:
            started = time.perf_counter()
            task = asyncio.ensure_future(func(*args, **kwargs))

            # asyncio.wait, NOT asyncio.wait_for, and the task is deliberately left running.
            #
            # aiosqlite queues every operation to a single worker thread, and its close() is
            # queued *behind* whatever statement is already running. Cancelling the awaiting
            # coroutine does not stop that thread, and wait_for awaits the cancelled task
            # before it raises — so wait_for(timeout=1.0) around a slow query was measured
            # taking 9.2s to report a 1s timeout. It does not bound anything.
            #
            # aiosqlite.Connection.interrupt() would genuinely abort the query, and does work
            # identically on both the pinned and the distribution-packaged versions. It is
            # not used here because this decorator wraps an arbitrary coroutine and holds no
            # handle on any connection; plumbing one through would be a wider change. If the
            # abandoned tasks below ever become a problem, that is the way to fix it.
            done, pending = await asyncio.wait({task}, timeout=deadline)

            if pending:
                log.warning(
                    "autocomplete %s exceeded %.2fs and was answered with no choices; "
                    "it is still running and will be discarded",
                    getattr(func, "__qualname__", func),
                    deadline,
                )
                # Consume the eventual result so the loop never reports it as retrieved-never
                # exception, and so a genuine fault still reaches the log.
                task.add_done_callback(_discard)
                return []

            elapsed = time.perf_counter() - started
            try:
                result = task.result()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception(
                    "autocomplete %s failed; offering no choices",
                    getattr(func, "__qualname__", func),
                )
                return []

            if elapsed >= slow_after:
                log.warning(
                    "autocomplete %s took %.2fs, within the %.1fs budget but close to it",
                    getattr(func, "__qualname__", func),
                    elapsed,
                    deadline,
                )
            return result

        return wrapper

    return decorate


def _discard(task: asyncio.Task) -> None:
    """Swallow the outcome of a call we already gave up waiting for."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.warning("abandoned autocomplete finished with an error: %r", exc)
