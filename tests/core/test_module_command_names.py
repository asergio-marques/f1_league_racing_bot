"""Each module's commands sit under its own group, and core keeps only its own (#462).

#282 settled that a module's commands, its test tools included, sit under the module's own
top-level group (`docs/design/architecture.md`, "How the code is laid out"). discord.py binds a
subcommand to the cog that declares its group, so a module command left under `/division`,
`/round` or `/test-mode` is written in core's code, and core has to import the module to run it.
Eight were, and this is the register of where each went.

**Only the names change.** A moved command keeps its description and every parameter — its
name, description, type, whether it is required, the choices it offers and the kinds of channel
it accepts — so what a manager types after the command is what they typed before. Those are
pinned here against the words the command carried under its old name, because once it has moved
there is nothing left to compare it with.

**Core keeps its own two.** `/division lineup-channel` and `/division calendar-channel` set the
channels every season posts to whatever modules are on, so they stay under `/division`.

**The whole set is read off the cog classes, with no bot and no gateway**, as
`test_command_tiers.py` reads it.
"""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass

import pytest
from discord import app_commands
from discord.ext import commands as discord_commands

from leaguebot.attendance.cogs.attendance_cog import AttendanceCog
from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.results.cogs.results_cog import ResultsCog
from leaguebot.weather.cogs.weather_cog import WeatherCog


@dataclass(frozen=True)
class Moved:
    """One command #462 moves: where it was, where it goes, and what it carried before."""

    old: str
    new: str
    cog: type
    description: str
    #: Each parameter as (name, description, type, required, choices as (name, value), the
    #: channel kinds it accepts, sorted by name).
    parameters: tuple[
        tuple[str, str, str, bool, tuple[tuple[str, str], ...], tuple[str, ...]], ...
    ]


#: What a `discord.TextChannel` parameter accepts: text and announcement channels, never a voice
#: channel, a thread or a category.
TEXT_CHANNEL_KINDS: tuple[str, ...] = ("news", "text")


def _channel_parameters(channel_description: str):
    return (
        ("name", "Division name", "string", True, (), ()),
        ("channel", channel_description, "channel", True, (), TEXT_CHANNEL_KINDS),
    )


MOVED: tuple[Moved, ...] = (
    Moved(
        "division weather-channel",
        "weather channel",
        WeatherCog,
        "Set the weather forecast channel for a division.",
        _channel_parameters("Weather forecast channel"),
    ),
    Moved(
        "division results-channel",
        "results channel results",
        ResultsCog,
        "Set the results posting channel for a division.",
        _channel_parameters("Results channel"),
    ),
    Moved(
        "division standings-channel",
        "results channel standings",
        ResultsCog,
        "Set the standings posting channel for a division.",
        _channel_parameters("Standings channel"),
    ),
    Moved(
        "division verdicts-channel",
        "results channel verdicts",
        ResultsCog,
        "Set the verdicts (penalty announcement) channel for a division.",
        _channel_parameters("Verdicts announcement channel"),
    ),
    Moved(
        "division rsvp-channel",
        "attendance channel rsvp",
        AttendanceCog,
        "Set the RSVP notice channel for a division (attendance module).",
        _channel_parameters("RSVP notice channel"),
    ),
    Moved(
        "division attendance-channel",
        "attendance channel attendance",
        AttendanceCog,
        "Set the attendance logging channel for a division (attendance module).",
        _channel_parameters("Attendance logging channel"),
    ),
    Moved(
        "round results amend",
        "results rounds amend",
        ResultsCog,
        "Re-submit results for one session of a completed round.",
        (
            ("division_name", "Division name", "string", True, (), ()),
            ("round_number", "Round number", "integer", True, (), ()),
            (
                "session",
                "Session to amend (if omitted, bot will ask)",
                "string",
                False,
                (
                    ("Sprint Qualifying", "SPRINT_QUALIFYING"),
                    ("Sprint Race", "SPRINT_RACE"),
                    ("Feature Qualifying", "FEATURE_QUALIFYING"),
                    ("Feature Race", "FEATURE_RACE"),
                ),
                (),
            ),
        ),
    ),
    Moved(
        "test-mode rsvp set-status",
        "attendance test rsvp",
        AttendanceCog,
        "Bulk-set RSVP statuses for test drivers in a division via a modal.",
        (
            (
                "division",
                "Division name whose active RSVP round to update.",
                "string",
                True,
                (),
                (),
            ),
        ),
    ),
)

#: The two groups that held nothing but a moved command, and go with it.
EMPTIED_GROUPS: tuple[str, ...] = ("round results", "test-mode rsvp")

#: Core's own channel commands, which stay under `/division`.
CORE_CHANNEL_COMMANDS: tuple[str, ...] = ("division lineup-channel", "division calendar-channel")


def _walk(objects):
    """Every group and command beneath *objects*, the groups included."""
    for obj in objects:
        yield obj
        if isinstance(obj, app_commands.Group):
            yield from _walk(obj.commands)


def _declared() -> dict[str, tuple[type, app_commands.Command | app_commands.Group]]:
    """Every command and group the bot declares, by its full name, with the cog declaring it."""
    import leaguebot

    found: dict[str, tuple[type, app_commands.Command | app_commands.Group]] = {}
    for module_info in pkgutil.walk_packages(leaguebot.__path__, "leaguebot."):
        if ".cogs." not in module_info.name:
            continue
        module = importlib.import_module(module_info.name)
        for attribute in sorted(dir(module)):
            candidate = getattr(module, attribute)
            if (
                isinstance(candidate, type)
                and issubclass(candidate, discord_commands.Cog)
                and candidate is not discord_commands.Cog
            ):
                for command in _walk(getattr(candidate, "__cog_app_commands__", []) or []):
                    found[command.qualified_name] = (candidate, command)
    return found


DECLARED = _declared()


def test_the_cogs_declare_commands_at_all():
    """A guard on the guard: an import that quietly found nothing would pass everything."""
    assert len(DECLARED) > 100


@pytest.mark.parametrize("new", [moved.new for moved in MOVED])
def test_each_moved_command_answers_to_its_new_name(new):
    """Declared by its own module's cog, under its new name, carrying what it carried before."""
    moved = next(m for m in MOVED if m.new == new)

    assert new in DECLARED, f"no cog declares /{new}"
    cog, command = DECLARED[new]
    assert cog is moved.cog, f"/{new} is declared by {cog.__name__}, not {moved.cog.__name__}"
    assert isinstance(command, app_commands.Command), f"/{new} is a group, not a command"
    assert command.description == moved.description
    assert tuple(
        (
            parameter.name,
            parameter.description,
            parameter.type.name,
            parameter.required,
            tuple((choice.name, choice.value) for choice in parameter.choices),
            tuple(sorted(kind.name for kind in parameter.channel_types)),
        )
        for parameter in command.parameters
    ) == moved.parameters


@pytest.mark.parametrize("old", [*(moved.old for moved in MOVED), *EMPTIED_GROUPS])
def test_no_old_name_is_left(old):
    """None of the eight answers to its old name, and the two groups emptied by the move go."""
    assert old not in DECLARED, f"/{old} is still declared, by {DECLARED[old][0].__name__}"


@pytest.mark.parametrize("name", CORE_CHANNEL_COMMANDS)
def test_core_keeps_its_own_two_channel_commands(name):
    """The lineup and calendar channels are every season's, so they stay core's."""
    assert name in DECLARED, f"no cog declares /{name}"
    cog, command = DECLARED[name]
    assert cog is SeasonCog
    assert isinstance(command, app_commands.Command)
