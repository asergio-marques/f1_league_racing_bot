"""The bot's declared type: every attribute `__main__.py` attaches, named on `LeagueBot`.

Issue #228. `__main__.py` hangs the services on the bot one assignment at a time, and discord.py's
`commands.Bot` declares none of them, so every read of one was silenced for the type checker —
and a silenced read is `Any`, through which nothing a service returns is ever checked.
`LeagueBot` declares them; these tests keep the declaration and `__main__.py` in step, which the
checker can only do in one direction: it refuses an attachment nobody declared, but not a
declaration nobody attaches, which would pass the check and fail at runtime.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock

from leaguebot.__main__ import create_bot
from leaguebot.core.utils.league_bot import LeagueBot, bot_of

SRC = Path(__file__).resolve().parents[2] / "src" / "leaguebot"


def _attached_in_bot_py() -> set[str]:
    """Every `bot.<name> = …` in `__main__.py`."""
    tree = ast.parse((SRC / "__main__.py").read_text(encoding="utf-8"))
    return {
        target.attr
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "bot"
    }


def _declared() -> set[str]:
    return set(inspect.get_annotations(LeagueBot))


def test_every_attribute_bot_py_attaches_is_declared():
    attached = _attached_in_bot_py()
    assert attached, "found no attachments in __main__.py — the scan has stopped seeing them"
    assert sorted(attached - _declared()) == [], "attached in __main__.py but not declared"
    assert sorted(_declared() - attached) == [], "declared on LeagueBot but never attached"


async def test_create_bot_builds_a_league_bot():
    assert isinstance(create_bot(), LeagueBot)


def test_bot_of_is_the_interactions_client():
    interaction = MagicMock()
    assert bot_of(interaction) is interaction.client


def test_the_wizard_has_its_bot_before_the_gateway_opens():
    """The signup wizard reaches the league's services through its bot. It was bound late in
    `on_ready`, after the restart recovery, while the persistent views are routed from the
    moment the gateway opens — so a signup press in between found no bot and failed (#228).
    It is bound in `main` itself, before `bot.start`, and in no handler."""
    tree = ast.parse((SRC / "__main__.py").read_text(encoding="utf-8"))
    main = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "main"
    )
    binds = [
        node for node in ast.walk(main)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "bot.wizard_service.set_bot"
    ]
    assert len(binds) == 1, "the wizard is bound once"
    in_main_itself = {id(node) for stmt in main.body for node in ast.walk(stmt)} - {
        id(node)
        for inner in ast.walk(main)
        if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)) and inner is not main
        for node in ast.walk(inner)
    }
    assert id(binds[0]) in in_main_itself, "bound inside a handler, not in main"
    start = next(
        node for node in ast.walk(main)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "bot.start"
    )
    assert binds[0].lineno < start.lineno
