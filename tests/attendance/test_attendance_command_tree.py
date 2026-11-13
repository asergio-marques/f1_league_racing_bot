"""The `/attendance` command tree, pinned as the bot's league sees it in Discord.

Every group and command under `/attendance`, with its description, and every option's name,
description, type, whether it is required and the choices it offers. The cog is composed from
one class per subgroup (`docs/design/architecture.md`, the cog bullet), and composing it is
meant to change nothing a league sees: this is the test that says so, written from the tree as
it stood before the split. It is read off a cog built on a stand-in bot, which is what discord.py
syncs: the copies it makes for the instance, each bound to that instance. No gateway is needed.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from discord import app_commands

from leaguebot.attendance.cogs.attendance_cog import AttendanceCog

#: Each as (qualified name, kind, description, options), an option being (name, description,
#: type, required, choices as (name, value)).
TREE = (('attendance', 'group', 'Attendance module commands.', ()),
 ('attendance channel',
  'group',
  "Set the channels a division's check-in calls and attendance are posted to.",
  ()),
 ('attendance channel attendance',
  'command',
  'Set the attendance logging channel for a division (attendance module).',
  (('name', 'Division name', 'string', True, ()),
   ('channel', 'Attendance logging channel', 'channel', True, ()))),
 ('attendance channel rsvp',
  'command',
  'Set the RSVP notice channel for a division (attendance module).',
  (('name', 'Division name', 'string', True, ()),
   ('channel', 'RSVP notice channel', 'channel', True, ()))),
 ('attendance config', 'group', 'Configure attendance module settings.', ()),
 ('attendance config absent-penalty',
  'command',
  'Penalty for absent drivers without ACCEPTED RSVP (stacks with no-RSVP penalty for NO_RSVP '
  'drivers).',
  (('points', 'Penalty points (≥ 0)', 'integer', True, ()),)),
 ('attendance config autoreserve',
  'command',
  'Set the cumulative threshold that triggers auto-reserve (0 = disabled).',
  (('points', 'Cumulative threshold for auto-reserve (0 to disable)', 'integer', True, ()),)),
 ('attendance config autosack',
  'command',
  'Set the cumulative no-show threshold that triggers auto-sack (0 = disabled).',
  (('points', 'Cumulative threshold for auto-sack (0 to disable)', 'integer', True, ()),)),
 ('attendance config no-rsvp-penalty',
  'command',
  'Set the point penalty for failing to RSVP.',
  (('points', 'Penalty points (≥ 0)', 'integer', True, ()),)),
 ('attendance config no-show-penalty',
  'command',
  "Penalty for a driver who RSVP'd ACCEPTED but did not attend.",
  (('points', 'Penalty points (≥ 0)', 'integer', True, ()),)),
 ('attendance config rsvp-deadline',
  'command',
  'Set the RSVP deadline in hours before the race.',
  (('hours', 'Hours before the race when RSVPs close', 'integer', True, ()),)),
 ('attendance config rsvp-last-notice',
  'command',
  'Set hours before the race for the last RSVP reminder (0 = disabled).',
  (('hours', 'Hours before the race for the last notice (0 to disable)', 'integer', True, ()),)),
 ('attendance config rsvp-notice',
  'command',
  'Set how many days before the race to send the first RSVP notice.',
  (('days', 'Number of days before the race for the RSVP notice (≥ 1)', 'integer', True, ()),)),
 ('attendance config show',
  'command',
  'Show the current attendance configuration for this server.',
  ()),
 ('attendance post-check-in',
  'command',
  "Post a round's check-in call by hand, where the scheduled one never went out.",
  (('division', 'Division name', 'string', True, ()),
   ('round', 'Round number whose check-in call is missing.', 'integer', True, ()))),
 ('attendance sync',
  'command',
  "Recalculate a division's attendance from a round on, and apply any sanction still owed.",
  (('division', 'Division name', 'string', True, ()),
   ('round',
    'Round number to recalculate from; every finalised round after it follows.',
    'integer',
    True,
    ()))),
 ('attendance test', 'group', 'Test-mode attendance utilities.', ()),
 ('attendance test rsvp',
  'command',
  'Bulk-set RSVP statuses for test drivers in a division via a modal.',
  (('division', 'Division name whose active RSVP round to update.', 'string', True, ()),)))


def _walk(objects):
    for obj in objects:
        yield obj
        if isinstance(obj, app_commands.Group):
            yield from _walk(obj.commands)


def _read(cog: AttendanceCog) -> list[tuple]:
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


def test_the_attendance_command_tree_is_what_a_league_sees():
    """Every command and group keeps its name, description and options as listed."""
    assert _read(AttendanceCog(MagicMock())) == list(TREE)


def test_every_attendance_command_is_bound_to_the_cog_that_is_loaded():
    """A subgroup's command is bound to the cog instance, so it runs with its bot and gates."""
    cog = AttendanceCog(MagicMock())
    commands = [
        obj for obj in _walk(cog.get_app_commands()) if not isinstance(obj, app_commands.Group)
    ]
    assert commands
    assert all(command.binding is cog for command in commands)


def test_the_attendance_group_is_sent_its_subgroups_and_commands_in_their_listed_order():
    """Discord is sent `/attendance`'s subgroups and commands in the order they joined the group:
    the order a league has always seen them in."""
    (group,) = AttendanceCog(MagicMock()).get_app_commands()
    assert [command.name for command in group.commands] == [
        "config", "channel", "test", "sync", "post-check-in",
    ]

