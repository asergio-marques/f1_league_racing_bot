"""Core's hook: a list of subscribers, called in the order they signed up (architecture.md, "A hook
core declares is a list of subscribers").

A hook is an object the builder makes, signs each module up to and hands to whoever calls it, never
a module-level registry, so no test's subscribers outlive the test. `Hook` takes plain subscribers
and `AwaitedHook` awaited ones, the second for a moment falling inside a save, whose connection it
hands over.
"""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("awaited", [False, True], ids=["plain", "awaited"])
async def test_a_hook_calls_its_subscribers_in_order_on_the_handed_connection(awaited):
    """With no subscriber a hook hands back nothing. Three subscribers signed up in turn are each
    called once, in that order, each handed the connection and the rows the call hands over, and
    what each hands back comes back in that order. A subscriber that raises stops the call: the
    error reaches the caller, and the subscriber after it is not called."""
    from leaguebot.core.utils.hooks import AwaitedHook, Hook

    connection = object()
    called: list[tuple] = []

    def subscriber(name, back):
        def plain(db, *, rows):
            called.append((name, db, rows))
            if isinstance(back, Exception):
                raise back
            return back

        async def awaited_one(db, *, rows):
            return plain(db, rows=rows)

        return awaited_one if awaited else plain

    async def call(hook):
        handed = hook.call(connection, rows=[7])
        return await handed if awaited else handed

    hook = AwaitedHook() if awaited else Hook()
    assert await call(hook) == []

    for name, back in (("first", 1), ("second", None), ("third", "three")):
        hook.subscribe(subscriber(name, back))

    assert await call(hook) == [1, None, "three"]
    assert called == [
        ("first", connection, [7]), ("second", connection, [7]), ("third", connection, [7]),
    ]

    failing = AwaitedHook() if awaited else Hook()
    for name, back in (("refuses", RuntimeError("no")), ("after", 2)):
        failing.subscribe(subscriber(name, back))
    called.clear()

    with pytest.raises(RuntimeError, match="no"):
        await call(failing)
    assert called == [("refuses", connection, [7])]
