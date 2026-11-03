"""Core's hook: a list of subscribers, called in the order they signed up (architecture.md, "A hook
core declares is a list of subscribers").

A module signs a function up with `subscribe`; core, at the moment the hook stands for, makes
one `call`, which hands every subscriber what the moment hands over and gathers what each hands
back, in the order they signed up. What core does with those, if anything, is the moment's.

**A hook is an object the builder makes**, signs each module up to and hands to whoever calls it:
never a module-level registry, so that a test's subscribers cannot outlive the test.

**A subscriber that raises is not caught.** The error stops the call, the subscribers after it
are not called, and it reaches the caller as a direct call's would: the caller's own failure path
(the command's, or the change queue's for a queued step) has it.

`Hook` takes plain subscribers. `AwaitedHook` takes awaited ones, for a moment that falls inside a
save, where the subscribers run on the connection `call` is handed; its `call` is a coroutine.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


class Hook:
    """Plain subscribers, called in the order they signed up."""

    def __init__(self) -> None:
        self._subscribers: list[Callable[..., Any]] = []

    def subscribe(self, subscriber: Callable[..., Any]) -> None:
        """Sign *subscriber* up, after those already signed up."""
        self._subscribers.append(subscriber)

    def call(self, *args: Any, **kwargs: Any) -> list[Any]:
        """Call each subscriber with what was handed; return what each handed back, in order."""
        return [subscriber(*args, **kwargs) for subscriber in self._subscribers]


class AwaitedHook:
    """Awaited subscribers, called one after another in the order they signed up."""

    def __init__(self) -> None:
        self._subscribers: list[Callable[..., Awaitable[Any]]] = []

    def subscribe(self, subscriber: Callable[..., Awaitable[Any]]) -> None:
        """Sign *subscriber* up, after those already signed up."""
        self._subscribers.append(subscriber)

    async def call(self, *args: Any, **kwargs: Any) -> list[Any]:
        """Await each subscriber with what was handed; return what each handed back, in order."""
        return [await subscriber(*args, **kwargs) for subscriber in self._subscribers]
