"""Proving that a template's evaluation runs off the event loop, so the bot keeps answering.

`Held` stands in for an evaluation (a sweep, a parse): each call waits for a coroutine on the
loop to release it, then answers what *then* answers for the same arguments, or raises what it
raises. `answered_while` awaits an entry point with that coroutine running beside it. In a
worker thread the loop runs the coroutine and the call goes through. On the loop itself nothing
could run it, so the call gives up after `LOOP_HELD` seconds rather than deadlocking the test:
it raises, and counts the hold in `held_loop` for an entry point that swallows the exception.

`answered_while` also holds the entry point to the rule `load_svg` states: **nothing parsed in
a worker thread leaves it, neither a return value nor an exception.** `recording_threads`
records every `asyncio.to_thread` call made meanwhile, what it returned and what it raised, and
`assert_no_tree_crossed` fails where any of them holds an lxml element, tree or iterator,
however deeply: within a container, an object's attributes, or an exception's arguments,
cause, context or traceback frames.
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import threading
import types
from collections.abc import Iterator
from typing import Any, Awaitable, Callable

from lxml import etree

#: How long a call waits to be released before it says the loop was held.
LOOP_HELD = 2.0

#: What lxml hands out that belongs to the document it was parsed into.
LXML_KINDS: tuple[type, ...] = tuple(
    getattr(etree, name) for name in (
        "_Element", "_ElementTree", "_Attrib", "_ElementIterator", "_ElementMatchIterator",
        "AncestorsIterator", "ElementChildIterator", "ElementDepthFirstIterator",
        "ElementTextIterator", "SiblingsIterator", "iterwalk",
    ) if hasattr(etree, name)
)


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


def holds_a_tree(value: Any, _seen: set[int] | None = None) -> bool:
    """Whether *value* is, or holds anywhere within it, something lxml parsed."""
    seen = set() if _seen is None else _seen
    if id(value) in seen:
        return False
    seen.add(id(value))
    if isinstance(value, LXML_KINDS):
        return True
    if value is None or isinstance(value, (str, bytes, int, float, bool, type, types.ModuleType)):
        return False

    def within(*items: Any) -> bool:
        return any(holds_a_tree(item, seen) for item in items)

    if isinstance(value, BaseException):
        frames = []
        trace = value.__traceback__
        while trace is not None:
            frames.extend(trace.tb_frame.f_locals.values())
            trace = trace.tb_next
        return within(*value.args, value.__cause__, value.__context__, *frames)
    if isinstance(value, dict):
        return within(*value.keys(), *value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return within(*value)
    if isinstance(value, types.FunctionType):
        return within(*(cell.cell_contents for cell in value.__closure__ or ()))
    if dataclasses.is_dataclass(value):
        return within(*(getattr(value, each.name) for each in dataclasses.fields(value)))
    slots = [
        name for kind in type(value).__mro__ for name in getattr(kind, "__slots__", ())
        if isinstance(name, str) and hasattr(value, name)
    ]
    attributes = list(vars(value).values()) if hasattr(value, "__dict__") else []
    return within(*(getattr(value, name) for name in slots), *attributes)


@contextlib.contextmanager
def recording_threads() -> Iterator[list[Any]]:
    """Record what every `asyncio.to_thread` call made within returns, and what it raises."""
    to_thread = asyncio.to_thread
    crossed: list[Any] = []

    async def recording(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        try:
            result = await to_thread(func, *args, **kwargs)
        except BaseException as exc:
            crossed.append(exc)
            raise
        crossed.append(result)
        return result

    asyncio.to_thread = recording
    try:
        yield crossed
    finally:
        asyncio.to_thread = to_thread


def assert_no_tree_crossed(crossed: list[Any]) -> None:
    """Fail where a worker thread handed the loop something it parsed, returned or raised."""
    assert not any(holds_a_tree(each) for each in crossed), (
        "something parsed in a worker thread came back to the loop"
    )


async def answered_while(held: Held, entry: Callable[[], Awaitable[Any]]) -> Any:
    """Await *entry*, while a coroutine on the loop releases each call *held* waits in; fail
    where a worker thread hands back to the loop anything it parsed."""

    async def release() -> None:
        while True:
            held.release_waiting()
            await asyncio.sleep(0.01)

    with recording_threads() as crossed:
        releaser = asyncio.create_task(release())
        try:
            answer = await asyncio.wait_for(entry(), timeout=LOOP_HELD * 4)
        finally:
            releaser.cancel()
    assert crossed, "nothing was run in a worker thread"
    assert_no_tree_crossed(crossed)
    return answer
