"""The bot's declared type: every attribute `bot.py` attaches, named on `LeagueBot`.

Issue #228. `bot.py` hangs the services on the bot one assignment at a time, and discord.py's
`commands.Bot` declares none of them, so every read of one was silenced for the type checker —
and a silenced read is `Any`, through which nothing a service returns is ever checked.
`LeagueBot` declares them; these tests keep the declaration and `bot.py` in step, which the
checker can only do in one direction: it refuses an attachment nobody declared, but not a
declaration nobody attaches, which would pass the check and fail at runtime.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from unittest.mock import MagicMock

from bot import create_bot
from utils.league_bot import LeagueBot, bot_of

SRC = Path(__file__).resolve().parents[2] / "src"


def _attached_in_bot_py() -> set[str]:
    """Every `bot.<name> = …` in `bot.py`."""
    tree = ast.parse((SRC / "bot.py").read_text(encoding="utf-8"))
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
    assert attached, "found no attachments in bot.py — the scan has stopped seeing them"
    assert sorted(attached - _declared()) == [], "attached in bot.py but not declared"
    assert sorted(_declared() - attached) == [], "declared on LeagueBot but never attached"


async def test_create_bot_builds_a_league_bot():
    assert isinstance(create_bot(), LeagueBot)


def test_bot_of_is_the_interactions_client():
    interaction = MagicMock()
    assert bot_of(interaction) is interaction.client


def test_no_read_of_the_bots_services_is_silenced():
    """A read of the bot's services is typed, and never silenced back to `Any`.

    Before #228 some 440 such reads carried `# type: ignore[attr-defined]`. The type check
    refuses a silence that silences nothing, but it runs in CI alone; this runs on every host,
    and says what to do instead.
    """
    silenced = re.compile(r"#\s*type:\s*ignore\[[^\]]*\battr-defined\b")
    read = re.compile(r"\.(?:" + "|".join(sorted(_declared())) + r")\b")
    offenders = sorted(
        f"{path.relative_to(SRC).as_posix()}:{number}"
        for path in SRC.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if silenced.search(line) and read.search(line)
    )
    assert offenders == [], (
        "Type the bot as `LeagueBot`, or reach it through `bot_of(interaction)`, instead of "
        f"silencing the read: {offenders}"
    )
