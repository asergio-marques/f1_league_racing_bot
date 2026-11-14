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

`Hook` takes plain subscribers. `AwaitedHook` takes awaited ones, for a moment whose subscribers
read or write the database. Where core holds a connection open at the moment (the save's, where
the moment falls inside one), `call` hands it, and the subscribers read and write on it; the
windows hook, whose subscribers open their own, hands none. Its `call` is a coroutine.

`StartingHook` is not this type. It holds core's own start-up steps, signed up by name, then each
module's, signed up as one function giving that module's named steps in its own order, under the
module's switch key. It is not called: the sweep reads it and runs each step contained, a module's
only where its switch is on, save a step the module marks with `runs_while_off`, which runs
whatever its switch says (architecture.md, "Timed work and restarts").
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, overload

#: A start-up step: its name, for the log and the log channel, and what it runs, handed nothing.
StartUpStep = tuple[str, Callable[[], Awaitable[None]]]


class _RunsWhileOff:
    """What a step's run is wrapped in by `runs_while_off`: the same call, and a mark the sweep reads."""

    def __init__(self, run: Callable[[], Awaitable[None]]) -> None:
        self._run = run

    def __call__(self) -> Awaitable[None]:
        return self._run()


def runs_while_off(step: StartUpStep) -> StartUpStep:
    """Mark *step* as one the start-up sweep runs whatever its module's switch says.

    A module marks a step only where it finishes work the module announced while it was on and
    which nothing else could carry out once it is off: the work is owed to those it was announced
    to, not to the module's switch (architecture.md, "Timed work and restarts")."""
    name, run = step
    return name, _RunsWhileOff(run)


def runs_whatever_the_switch_says(step: StartUpStep) -> bool:
    """Whether *step* was marked with `runs_while_off`."""
    return isinstance(step[1], _RunsWhileOff)


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


@dataclass(frozen=True)
class _CoreStep:
    step: StartUpStep


@dataclass(frozen=True)
class _ModuleSteps:
    module: str
    steps: Callable[[], Sequence[StartUpStep]]


class StartingHook:
    """The bot's start-up steps: core's own, then each module's, in the order they signed up.

    Core's steps are signed up by *name*, a module's as *steps*, the module's wiring function bound
    to the bot (a `functools.partial`), handed nothing and giving the module's named steps in its
    own order, under *module*, the module's switch key: the name its switch reader is signed up
    under, which is `images` for the image module."""

    def __init__(self) -> None:
        self._subscribers: list[_CoreStep | _ModuleSteps] = []

    @overload
    def subscribe(self, step: Callable[[], Awaitable[None]], *, name: str) -> None: ...

    @overload
    def subscribe(self, step: Callable[[], Sequence[StartUpStep]], *, module: str) -> None: ...

    def subscribe(
        self,
        step: Callable[[], Any],
        *,
        name: str | None = None,
        module: str | None = None,
    ) -> None:
        """Sign up one of core's own steps (*name*), or a module's steps (*module*), after those
        already signed up."""
        if (name is None) == (module is None):
            raise ValueError("a start-up sign-up names a core step or a module, not both or neither")
        if name is not None:
            self._subscribers.append(_CoreStep((name, step)))
        else:
            assert module is not None
            self._subscribers.append(_ModuleSteps(module, step))

    def core_steps(self) -> list[StartUpStep]:
        """Core's own steps, in the order they signed up."""
        return [entry.step for entry in self._subscribers if isinstance(entry, _CoreStep)]

    def modules(self) -> list[tuple[str, Callable[[], Sequence[StartUpStep]]]]:
        """Each module's switch key and its function giving its steps, in the order they signed up."""
        return [(entry.module, entry.steps) for entry in self._subscribers
                if isinstance(entry, _ModuleSteps)]
