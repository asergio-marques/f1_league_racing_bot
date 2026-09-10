"""Reach a command's body past however many guards it wears.

Most cog tests want to exercise a command's logic, not its permission guard: they build a
fake interaction that would fail the guard, and step over it.

They used to do that by writing `callback.__wrapped__.__wrapped__` — which reads as "strip
the guards" but actually says "strip exactly two decorators". That was true while every
command carried `channel_guard` plus one of `admin_only` or `server_admin_only`, and it
stopped being true the moment a command's tier was declared by a single decorator instead of
a pair. Thirteen files would have broken at once, none of them for a reason connected to
what they test.

Walking to the bottom says what was meant, and does not need revisiting the next time the
guards are rearranged. A test that cares how many decorators a command carries — and one
does, deliberately — asserts that directly rather than relying on this.
"""
from __future__ import annotations

from typing import Any, Callable


def undecorate(command: Any) -> Callable:
    """The undecorated body of *command*.

    Accepts an `app_commands.Command`, or a callable already taken off one.
    """
    func = getattr(command, "callback", command)
    while hasattr(func, "__wrapped__"):
        func = func.__wrapped__
    return func
