"""What the bot does as it stops, read from its source (#540).

The connections the bot keeps idle to lend again each hold a thread of aiosqlite's, which is not a
daemon: left open, they would keep the process alive once the bot had stopped, and a Ctrl-C would
hang. So the entry point closes them however `bot.start` ends. Read from the source, because no
test runs the entry point whole: it connects to Discord.
"""

from __future__ import annotations

import ast
import inspect

import pytest


def _calls_named(nodes: list[ast.stmt], name: str) -> list[ast.Call]:
    """The awaited calls under *nodes* made by *name*: ``name(...)`` or ``x.name(...)``."""
    return [
        node.value
        for statement in nodes
        for node in ast.walk(statement)
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
        and name in (getattr(node.value.func, "id", None), getattr(node.value.func, "attr", None))
    ]


def test_the_bot_closes_its_connections_however_it_stops():
    """`main` awaits `bot.start` inside a `try` whose `finally` awaits `close_connections`, so the
    idle connections are closed whether the bot stops cleanly, on Ctrl-C, or on an error."""
    from leaguebot import __main__ as bot_module

    main = ast.parse(inspect.getsource(bot_module.main)).body[0]
    guarded = [
        node for node in ast.walk(main)
        if isinstance(node, ast.Try)
        and _calls_named(node.body, "start")
        and _calls_named(node.finalbody, "close_connections")
    ]

    assert _calls_named(main.body, "start"), "precondition: main starts the bot"
    assert guarded, "bot.start is not inside a try whose finally closes the connections"
