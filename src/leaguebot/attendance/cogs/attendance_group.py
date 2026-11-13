"""The `/attendance` group, and what the subgroups beneath it share.

`ATTENDANCE_GROUP` is the one `/attendance` group, and its three subgroups are declared here
beneath it, in the order a league sees them; each subgroup's class takes its own as a class
attribute. A group's children are kept in the order they join it, and Discord is sent them in that
order, so declaring the subgroups in one file fixes the order whichever subgroup's file is imported
first (`test_the_listed_order_holds_whichever_subgroup_s_file_is_imported_first`). What
else is here is what two or more of them use: the wait for the catch-up after a restart, the line
the log channel is written, and the stub of what a subgroup takes from the cog it is composed into.
"""
from __future__ import annotations

from abc import abstractmethod

import discord
from discord import app_commands

from leaguebot.core.utils.catch_up_gate import after_the_catch_up
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.log_lines import interaction_member

ATTENDANCE_GROUP = app_commands.Group(
    name="attendance",
    description="Attendance module commands.",
    default_permissions=None,
)
CONFIG_GROUP = app_commands.Group(
    name="config",
    description="Configure attendance module settings.",
    parent=ATTENDANCE_GROUP,
)
CHANNEL_GROUP = app_commands.Group(
    name="channel",
    description="Set the channels a division's check-in calls and attendance are posted to.",
    parent=ATTENDANCE_GROUP,
)
TEST_GROUP = app_commands.Group(
    name="test",
    description="Test-mode attendance utilities.",
    parent=ATTENDANCE_GROUP,
)

#: Waits for the catch-up after a restart before a command answered off the change queue acts.
AFTER_THE_CATCH_UP = after_the_catch_up(lambda cog: cog.bot.change_queue)


class AttendanceSubgroup:
    """What an `/attendance` subgroup's class takes from the `AttendanceCog` it is composed into.

    A subgroup's commands read the bot and call the cog's gate, which `AttendanceCog` holds, so
    the class declares them here for the type checker. Stubs, never the gate itself: a `Protocol`
    cannot be a base beside `commands.Cog`, whose metaclass it conflicts with, and typing `self`
    in every command would put a parameter annotation on each. `AttendanceCog` comes first in the
    order methods are found in, so what it defines is what runs.
    """

    bot: LeagueBot

    @abstractmethod
    async def _module_gate(self, interaction: discord.Interaction, *, record: bool = True) -> bool:
        """Whether the attendance module is on; where it is not, refuse and return False."""


def _line(
    interaction: discord.Interaction, what: str, outcome: str, details: tuple[str, ...]
) -> str:
    return (
        f"{interaction_member(interaction)} | {what} | {outcome}"
        + "".join(f"\n  {detail}" for detail in details)
    )


async def record(
    bot: LeagueBot,
    interaction: discord.Interaction,
    what: str,
    outcome: str,
    *details: str,
) -> None:
    """Write one line in the success form, "Name (<@id>) | *what* | *outcome*", *details* beneath.

    For a success and for nothing changed, where the line is not saved with what it records (the
    settings' lines are, by their change). The post is bare: a line reporting that nothing went
    wrong is no place for a catch-all, so a post that raises reaches the command's error
    handling. A refusal, a cancel, a lapse and a fault have lines of their own
    (`core/utils/log_lines.py`, `report_failure`).
    """
    await bot.output_router.post_log(_line(interaction, what, outcome, details))

