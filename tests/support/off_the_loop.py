"""Proving that a template's evaluation runs off the event loop, so the bot keeps answering.

`Held` stands in for an evaluation (a sweep, a parse): each call waits for a coroutine on the
loop to release it, then answers what *then* answers for the same arguments, or raises what it
raises. `answered_while` awaits an entry point with that coroutine running beside it. In a
worker thread the loop runs the coroutine and the call goes through. On the loop itself nothing
could run it, so the call gives up after `LOOP_HELD` seconds rather than deadlocking the test:
it raises, and counts the hold in `held_loop` for an entry point that swallows the exception.

`answered_while` also holds the entry point to the rule `load_svg` states: **a tree parsed in
a worker thread never leaves that thread.** Every `asyncio.to_thread` call made meanwhile is
recorded, and one that returns an lxml element or tree, however deeply it is held in what
comes back, fails the test.
"""
from __future__ import annotations

import asyncio
import dataclasses
import threading
from typing import Any, Awaitable, Callable

from lxml import etree

#: How long a call waits to be released before it says the loop was held.
LOOP_HELD = 2.0


class Held:
    """An evaluation that answers as *then* does, once a coroutine on the loop releases it."""

    def __init__(self, then: Callable[..., Any]) -> None:
        self.then = then
        self.calls: list[tuple[Any, ...]] = []
        self.held_loop = 0
        self._lock = threading.Lock()
        self._waiting: list[threading.Event] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        gate = threading.Event()
        with self._lock:
            self.calls.append(args)
            self._waiting.append(gate)
        if not gate.wait(LOOP_HELD):
            self.held_loop += 1
            raise AssertionError("the event loop was held while the evaluation ran")
        return self.then(*args, **kwargs)

    def release_waiting(self) -> None:
        with self._lock:
            gates, self._waiting = self._waiting, []
        for gate in gates:
            gate.set()


def answering(result: Any) -> Held:
    """A `Held` that answers *result*, whatever it is asked."""
    return Held(lambda *_args, **_kwargs: result)


def holds_a_tree(value: Any) -> bool:
    """Whether *value* is, or holds anywhere within it, an lxml element or tree."""
    if isinstance(value, (etree._Element, etree._ElementTree)):
        return True
    if isinstance(value, (str, bytes, int, float, bool)) or value is None:
        return False
    if isinstance(value, dict):
        return any(holds_a_tree(key) or holds_a_tree(item) for key, item in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(holds_a_tree(item) for item in value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return any(
            holds_a_tree(getattr(value, each.name)) for each in dataclasses.fields(value)
        )
    return False


async def answered_while(held: Held, entry: Callable[[], Awaitable[Any]]) -> Any:
    """Await *entry*, while a coroutine on the loop releases each call *held* waits in; fail
    where a worker thread hands a tree back to the loop."""

    async def release() -> None:
        while True:
            held.release_waiting()
            await asyncio.sleep(0.01)

    to_thread = asyncio.to_thread
    returned: list[Any] = []

    async def recording(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        result = await to_thread(func, *args, **kwargs)
        returned.append(result)
        return result

    asyncio.to_thread = recording
    releaser = asyncio.create_task(release())
    try:
        answer = await asyncio.wait_for(entry(), timeout=LOOP_HELD * 4)
    finally:
        releaser.cancel()
        asyncio.to_thread = to_thread
    assert returned, "nothing was run in a worker thread"
    assert not any(holds_a_tree(result) for result in returned), (
        "a tree parsed in a worker thread came back to the loop"
    )
    return answer
