"""The team parameter's autocomplete (#381).

A team is typed by its shorthand, and every command that takes one offers the shorthands as the
manager types. Each suggestion carries both names and matches either, but what it sends is always
the shorthand — the one thing the command accepts.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.utils.autocomplete import team_autocomplete, team_choices  # noqa: E402

TEAMS = [
    {"name": "MCL", "full_name": "McLaren Formula 1 Team", "is_reserve": False},
    {"name": "RBR", "full_name": "Oracle Red Bull Racing", "is_reserve": False},
    {"name": "Reserve", "full_name": "Reserve", "is_reserve": True},
]


def _offered(current, *, include_reserve=True, teams=TEAMS):
    return [(c.name, c.value) for c in team_choices(teams, current, include_reserve=include_reserve)]


def test_team_autocomplete_offers_the_shorthand_named_with_the_full_name():
    assert _offered("")[:2] == [
        ("MCL — McLaren Formula 1 Team", "MCL"),
        ("RBR — Oracle Red Bull Racing", "RBR"),
    ]


def test_a_team_whose_names_are_one_is_offered_under_it_once():
    assert ("Reserve", "Reserve") in _offered("")


@pytest.mark.parametrize("typed", ["rb", "RED BULL", "oracle"])
def test_team_autocomplete_matches_either_name(typed):
    assert _offered(typed) == [("RBR — Oracle Red Bull Racing", "RBR")]


def test_team_autocomplete_leaves_out_the_reserve_team_where_it_is_refused():
    assert "Reserve" not in [value for _, value in _offered("", include_reserve=False)]


def test_team_autocomplete_offers_at_most_twenty_five():
    many = [{"name": f"T{i}", "full_name": f"Team {i}", "is_reserve": False} for i in range(40)]

    assert len(_offered("", teams=many)) == 25


async def test_the_offer_is_read_from_the_server_s_list():
    bot = SimpleNamespace(team_service=SimpleNamespace(get_teams_with_roles=AsyncMock(return_value=TEAMS)))

    choices = await team_autocomplete(bot, "mcl", include_reserve=True)

    assert [c.value for c in choices] == ["MCL"]


@pytest.mark.parametrize(
    "command,parameter",
    [
        ("leaguebot.core.cogs.driver_cog:DriverCog.assign", "team"),
        ("leaguebot.core.cogs.driver_cog:DriverCog.move", "team"),
        ("leaguebot.core.cogs.test_mode_cog:TestModeCog.roster_add", "team_name"),
    ],
)
def test_every_command_taking_a_team_offers_the_shorthands(command, parameter):
    import importlib

    module_name, attribute = command.split(":")
    owner, name = attribute.split(".")
    command_object = getattr(getattr(importlib.import_module(module_name), owner), name)

    assert command_object._params[parameter].autocomplete is not None
