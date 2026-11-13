"""The `/results` command tree, pinned as the bot's league sees it in Discord.

Every group and command under `/results`, with its description, and every option's name,
description, type, whether it is required and the choices it offers. The cog is composed from
one class per subgroup (`docs/design/architecture.md`, the cog bullet), and composing it is
meant to change nothing a league sees: this is the test that says so, written from the tree as
it stood before the split. It is read off a cog built on a stand-in bot, which is what discord.py
syncs: the copies it makes for the instance, each bound to that instance. No gateway is needed.
"""
from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import MagicMock

import pytest
from discord import app_commands

from leaguebot.results.cogs.results_cog import ResultsCog
from leaguebot.results.cogs.results_group import RESULTS_GROUP

#: Each as (qualified name, kind, description, options), an option being (name, description,
#: type, required, choices as (name, value)).
TREE = (
    ('results', 'group', 'Results & Standings commands', ()),
    ('results amend', 'group', 'Mid-season points amendment management', ()),
    ('results amend bulk-session',
     'command',
     'Bulk-update points in the modification store for multiple positions via a modal.',
     (('name', 'Config name', 'string', True, ()),
      ('session',
       'Session type',
       'string',
       True,
       (('Sprint Qualifying', 'SPRINT_QUALIFYING'),
        ('Sprint Race', 'SPRINT_RACE'),
        ('Feature Qualifying', 'FEATURE_QUALIFYING'),
        ('Feature Race', 'FEATURE_RACE'))))),
    ('results amend fl',
     'command',
     'Set fastest-lap bonus in the modification store.',
     (('name', 'Config name', 'string', True, ()),
      ('session',
       'Race session type',
       'string',
       True,
       (('Sprint Race', 'SPRINT_RACE'), ('Feature Race', 'FEATURE_RACE'))),
      ('points', 'FL bonus points', 'integer', True, ()))),
    ('results amend fl-plimit',
     'command',
     'Set fastest-lap position limit in the modification store.',
     (('name', 'Config name', 'string', True, ()),
      ('session',
       'Race session type',
       'string',
       True,
       (('Sprint Race', 'SPRINT_RACE'), ('Feature Race', 'FEATURE_RACE'))),
      ('limit', 'Highest eligible position', 'integer', True, ()))),
    ('results amend revert',
     'command',
     'Revert all modification store changes to the season points.',
     ()),
    ('results amend review',
     'command',
     'Review modification store changes and approve or reject.',
     ()),
    ('results amend session',
     'command',
     'Set points in the modification store for a session position.',
     (('name', 'Config name', 'string', True, ()),
      ('session',
       'Session type',
       'string',
       True,
       (('Sprint Qualifying', 'SPRINT_QUALIFYING'),
        ('Sprint Race', 'SPRINT_RACE'),
        ('Feature Qualifying', 'FEATURE_QUALIFYING'),
        ('Feature Race', 'FEATURE_RACE'))),
      ('position', 'Finishing position', 'integer', True, ()),
      ('points', 'Points to award', 'integer', True, ()))),
    ('results amend toggle',
     'command',
     'Enable or disable amendment mode for the current season.',
     ()),
    ('results channel', 'group', "Set the channels a division's results are posted to", ()),
    ('results channel results',
     'command',
     'Set the results posting channel for a division.',
     (('name', 'Division name', 'string', True, ()),
      ('channel', 'Results channel', 'channel', True, ()))),
    ('results channel standings',
     'command',
     'Set the standings posting channel for a division.',
     (('name', 'Division name', 'string', True, ()),
      ('channel', 'Standings channel', 'channel', True, ()))),
    ('results channel verdicts',
     'command',
     'Set the verdicts (penalty announcement) channel for a division.',
     (('name', 'Division name', 'string', True, ()),
      ('channel', 'Verdicts announcement channel', 'channel', True, ()))),
    ('results config', 'group', 'Points configuration management', ()),
    ('results config add',
     'command',
     'Add a named points configuration to this server.',
     (('name', "Unique config name, e.g. '100%'", 'string', True, ()),)),
    ('results config append',
     'command',
     'Attach a server config to the current season in SETUP.',
     (('name', 'Config name to attach', 'string', True, ()),)),
    ('results config bulk-session',
     'command',
     'Bulk-set points for multiple positions in a session type via a modal.',
     (('name', 'Config name', 'string', True, ()),
      ('session',
       'Session type',
       'string',
       True,
       (('Sprint Qualifying', 'SPRINT_QUALIFYING'),
        ('Sprint Race', 'SPRINT_RACE'),
        ('Feature Qualifying', 'FEATURE_QUALIFYING'),
        ('Feature Race', 'FEATURE_RACE'))))),
    ('results config detach',
     'command',
     'Detach a config from the current season in SETUP.',
     (('name', 'Config name to detach', 'string', True, ()),)),
    ('results config fl',
     'command',
     'Set the fastest-lap bonus for a race session type.',
     (('name', 'Config name', 'string', True, ()),
      ('session',
       'Race session type (Sprint Race or Feature Race)',
       'string',
       True,
       (('Sprint Race', 'SPRINT_RACE'), ('Feature Race', 'FEATURE_RACE'))),
      ('points', 'Bonus points for fastest lap', 'integer', True, ()))),
    ('results config fl-plimit',
     'command',
     'Set the position eligibility limit for fastest-lap bonus.',
     (('name', 'Config name', 'string', True, ()),
      ('session',
       'Race session type (Sprint Race or Feature Race)',
       'string',
       True,
       (('Sprint Race', 'SPRINT_RACE'), ('Feature Race', 'FEATURE_RACE'))),
      ('limit',
       'Highest eligible position (e.g. 10 → positions 1–10 eligible)',
       'integer',
       True,
       ()))),
    ('results config list',
     'command',
     'List the points configurations a store holds.',
     (('scope',
       "Which store to read — the season's, or this server's",
       'string',
       True,
       (('Season (what this season scores by)', 'SEASON'),
        ('Server (what this server holds)', 'SERVER'))),)),
    ('results config remove',
     'command',
     'Remove a named points configuration.',
     (('name', 'Config name to remove', 'string', True, ()),)),
    ('results config session',
     'command',
     'Set points for a finishing position in a session type.',
     (('name', 'Config name', 'string', True, ()),
      ('session',
       'Session type',
       'string',
       True,
       (('Sprint Qualifying', 'SPRINT_QUALIFYING'),
        ('Sprint Race', 'SPRINT_RACE'),
        ('Feature Qualifying', 'FEATURE_QUALIFYING'),
        ('Feature Race', 'FEATURE_RACE'))),
      ('position', 'Finishing position (1-indexed)', 'integer', True, ()),
      ('points', 'Points awarded', 'integer', True, ()))),
    ('results config view',
     'command',
     'View a points config from a chosen store.',
     (('scope',
       "Which store to read — the season's, or this server's",
       'string',
       True,
       (('Season (what this season scores by)', 'SEASON'),
        ('Server (what this server holds)', 'SERVER'))),
      ('name', 'Config name', 'string', True, ()),
      ('session',
       'Optional: filter to a specific session type',
       'string',
       False,
       (('Sprint Qualifying', 'SPRINT_QUALIFYING'),
        ('Sprint Race', 'SPRINT_RACE'),
        ('Feature Qualifying', 'FEATURE_QUALIFYING'),
        ('Feature Race', 'FEATURE_RACE'))))),
    ('results config xml-import',
     'command',
     'Import a full points configuration from an XML payload (modal or file attachment).',
     (('name', 'Config name', 'string', True, ()),
      ('file', 'Optional XML file attachment (skips modal)', 'attachment', False, ()))),
    ('results reserves', 'group', 'Reserve driver visibility in standings', ()),
    ('results reserves toggle',
     'command',
     'Toggle reserve driver visibility in division standings.',
     (('division', 'Division name', 'string', True, ()),)),
    ('results rounds', 'group', 'Round results commands', ()),
    ('results rounds amend',
     'command',
     'Re-submit results for one session of a completed round.',
     (('division_name', 'Division name', 'string', True, ()),
      ('round_number', 'Round number', 'integer', True, ()),
      ('session',
       'Session to amend (if omitted, bot will ask)',
       'string',
       False,
       (('Sprint Qualifying', 'SPRINT_QUALIFYING'),
        ('Sprint Race', 'SPRINT_RACE'),
        ('Feature Qualifying', 'FEATURE_QUALIFYING'),
        ('Feature Race', 'FEATURE_RACE'))))),
    ('results rounds sync',
     'command',
     'Force a results repost for all rounds in a division.',
     (('division', 'Division name', 'string', True, ()),)),
    ('results standings', 'group', 'Standings commands', ()),
    ('results standings sync',
     'command',
     'Force a standings repost for a division.',
     (('division', 'Division name', 'string', True, ()),)),
)


def _walk(objects):
    for obj in objects:
        yield obj
        if isinstance(obj, app_commands.Group):
            yield from _walk(obj.commands)


def _read(cog: ResultsCog) -> list[tuple]:
    rows = []
    for obj in sorted(_walk(cog.get_app_commands()), key=lambda c: c.qualified_name):
        if isinstance(obj, app_commands.Group):
            rows.append((obj.qualified_name, "group", obj.description, ()))
        else:
            options = tuple(
                (
                    p.name,
                    p.description,
                    p.type.name,
                    p.required,
                    tuple((choice.name, choice.value) for choice in p.choices),
                )
                for p in obj.parameters
            )
            rows.append((obj.qualified_name, "command", obj.description, options))
    return rows


def test_the_results_command_tree_is_what_a_league_sees():
    """Every command and group keeps its name, description and options as listed."""
    assert _read(ResultsCog(MagicMock())) == list(TREE)


def test_every_results_command_is_bound_to_the_cog_that_is_loaded():
    """A subgroup's command is bound to the cog instance, so it runs with its bot and gates."""
    cog = ResultsCog(MagicMock())
    commands = [obj for obj in _walk(cog.get_app_commands()) if not isinstance(obj, app_commands.Group)]
    assert commands
    assert all(command.binding is cog for command in commands)



#: The order a league has always seen `/results`' subgroups in.
LISTED_ORDER = ["config", "amend", "reserves", "standings", "rounds", "channel"]

#: Imports one subgroup's file before the cog, as a test that reaches the subgroup alone does
#: when its worker collects it first, and prints the order the cog's group is sent in.
_IMPORTED_FIRST = """
import importlib, sys
from unittest.mock import MagicMock
importlib.import_module(sys.argv[1])
from leaguebot.results.cogs.results_cog import ResultsCog
(group,) = ResultsCog(MagicMock()).get_app_commands()
print(",".join(command.name for command in group.commands))
"""

#: Why each subgroup's file imported first still breaks the order.
_DECLARED_APART = (
    "#285: each /results subgroup is still declared in its own file, so the first file a "
    "process imports sends its subgroup first"
)


def test_the_results_subgroups_are_sent_in_their_listed_order():
    """Discord is sent `/results`'s subgroups in the order they joined the group, which is the
    order `results_group.py` declares them in: the order a league has always seen them in."""
    assert [command.name for command in RESULTS_GROUP.commands] == LISTED_ORDER


@pytest.mark.parametrize("module", [
    pytest.param(
        "leaguebot.results.cogs.channel_commands",
        marks=pytest.mark.xfail(strict=True, reason=_DECLARED_APART),
    ),
    pytest.param(
        "leaguebot.results.cogs.rounds_commands",
        marks=pytest.mark.xfail(strict=True, reason=_DECLARED_APART),
    ),
    pytest.param(
        "leaguebot.results.cogs.reserves_commands",
        marks=pytest.mark.xfail(strict=True, reason=_DECLARED_APART),
    ),
])
def test_the_listed_order_holds_whichever_subgroup_s_file_is_imported_first(module):
    """The subgroups are declared together in `results_group.py`, so the order does not hang on
    which file a process imports first. It did while each subgroup's file declared its own: a
    process that imported `channel_commands.py` before the cog sent `channel` first. A fresh
    interpreter, since the order is fixed once per process."""
    ran = subprocess.run(
        [sys.executable, "-c", _IMPORTED_FIRST, module],
        capture_output=True, text=True, check=True, env=os.environ.copy(),
    )
    assert ran.stdout.strip().split(",") == LISTED_ORDER
