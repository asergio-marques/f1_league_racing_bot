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

import pytest

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


def test_the_approval_windows_reader_is_declared_and_set_by_the_builder():
    """The season cog reads the enabled modules' windows through `bot.approval_windows`, which
    `LeagueBot` declares and the builder sets, so that core imports neither attendance nor
    weather, nor `__main__` (#439, slice 4a)."""
    from leaguebot.core.cogs.season_cog import SeasonCog

    assert "approval_windows" in _declared()
    assert "approval_windows" in _attached_in_bot_py()
    assert "self.bot.approval_windows()" in inspect.getsource(SeasonCog._approval_windows)


def test_the_season_approval_is_registered():
    """The builder registers the season's approval and the change that tells the review's channel
    of a refusal its member can no longer be told of (#439, slice 4a)."""
    from leaguebot.__main__ import register_change_types
    from leaguebot.core.services.season_approval_change import KIND, TELL_KIND

    bot = MagicMock()
    register_change_types(bot)

    kinds = [call.args[0].kind for call in bot.change_queue.register.call_args_list]
    assert kinds.count(KIND) == 1
    assert kinds.count(TELL_KIND) == 1


def test_the_cancellations_are_registered():
    """The builder registers the cancellation of a round and of a division, once each, as
    "season.round.cancel" and "season.division.cancel" (#439, slice 4b)."""
    from leaguebot.__main__ import register_change_types
    from leaguebot.core.services.cancellation_changes import DIVISION_CANCEL, ROUND_CANCEL

    bot = MagicMock()
    register_change_types(bot)

    kinds = [call.args[0].kind for call in bot.change_queue.register.call_args_list]
    assert (ROUND_CANCEL, DIVISION_CANCEL) == ("season.round.cancel", "season.division.cancel")
    assert kinds.count(ROUND_CANCEL) == 1
    assert kinds.count(DIVISION_CANCEL) == 1


def test_the_round_amendment_is_registered():
    """The builder registers the amendment of a round, once, as "season.round.amend" (#439,
    slice 4b, amendment A)."""
    from leaguebot.__main__ import register_change_types
    from leaguebot.core.services.round_amend_change import ROUND_AMEND

    bot = MagicMock()
    register_change_types(bot)

    kinds = [call.args[0].kind for call in bot.change_queue.register.call_args_list]
    assert ROUND_AMEND == "season.round.amend"
    assert kinds.count(ROUND_AMEND) == 1


@pytest.mark.xfail(
    strict=True, reason="#439: a season's completion, cancellation and abort are not registered"
)
def test_the_season_s_end_is_registered():
    """The builder registers the completion of a season, its cancellation and its abort, as
    "season.complete", "season.cancel" and "season.abort", and the wind-down, once each (#439,
    slice 5)."""
    from leaguebot.__main__ import register_change_types

    bot = MagicMock()
    register_change_types(bot)

    kinds = [call.args[0].kind for call in bot.change_queue.register.call_args_list]
    for kind in ("season.complete", "season.cancel", "season.abort", "season.wind_down"):
        assert kinds.count(kind) == 1, kind

    from leaguebot.core.services.cancellation_changes import SEASON_CANCEL
    from leaguebot.core.services.season_end_changes import SEASON_ABORT, SEASON_COMPLETE
    from leaguebot.core.services.season_lifecycle_service import WIND_DOWN

    assert (SEASON_COMPLETE, SEASON_CANCEL, SEASON_ABORT, WIND_DOWN) == (
        "season.complete", "season.cancel", "season.abort", "season.wind_down")


def test_only_the_two_armings_are_undiscardable():
    """Over every change type the builder registers, the jobs marked as never to be discarded
    are exactly two: the arming of an approved season's timed work ("season.approve", its "arm")
    and the arming of an amended round's ("season.round.amend", its "arm"). Without either a
    round would never run; every other job a league admin may discard (#439, slice 4b,
    amendment A)."""
    from leaguebot.__main__ import register_change_types

    bot = MagicMock()
    register_change_types(bot)

    marked = sorted(
        (change_type.kind, name)
        for change_type in (call.args[0] for call in bot.change_queue.register.call_args_list)
        for name, step in change_type.steps.items()
        if getattr(step, "undiscardable", None) is not None
    )
    assert marked == [("season.approve", "arm"), ("season.round.amend", "arm")]
