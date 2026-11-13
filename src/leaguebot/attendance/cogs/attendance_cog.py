"""`AttendanceCog`: the `/attendance` group, composed from one class for each of its subgroups.

The subgroups' commands live in `config_commands.py`, `channel_commands.py` and
`test_commands.py`, one file for each subgroup, and the commands on the group itself in
`group_commands.py`, under the one `/attendance` group declared in `attendance_group.py`. The cog
stays one cog for one group: it holds `/attendance` as a class attribute, each subgroup's class
takes its subgroup, declared beneath that group in `attendance_group.py`, and discord.py binds a
cog's group to the cog together with every child beneath it, so every command runs with this cog's
`self`. A cog too large to read in one file is split by subgroup rather than into a cog for each,
as the architecture's cog bullet gives it (`docs/design/architecture.md`).

What is left here is what the subgroups share: the cog's bot and the gate their commands call.
The check-in's buttons are in `check_in_buttons.py`.
"""
from __future__ import annotations

import discord
from discord.ext import commands

from leaguebot.attendance.cogs.attendance_group import ATTENDANCE_GROUP

# The subgroups join `/attendance` in `attendance_group.py`, in the order a league sees them, and
# the commands on the group itself after them, so the order of these imports does not matter.
from leaguebot.attendance.cogs.config_commands import ConfigCommands
from leaguebot.attendance.cogs.channel_commands import ChannelCommands
from leaguebot.attendance.cogs.test_commands import TestCommands
from leaguebot.attendance.cogs.group_commands import GroupCommands
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.log_lines import refuse


class AttendanceCog(
    ConfigCommands,
    ChannelCommands,
    TestCommands,
    GroupCommands,
    commands.Cog,
):
    attendance = ATTENDANCE_GROUP

    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    # ── Gate helper ────────────────────────────────────────────────────────

    async def _module_gate(self, interaction: discord.Interaction, *, record: bool = True) -> bool:
        """Whether the attendance module is on; where it is not, refuse and return False.

        The refusal is recorded in the log channel, as every refusal of a command that acts is,
        unless *record* is False, which `config show` passes: it changes nothing and records
        nothing.
        """
        if not await self.bot.module_service.is_attendance_enabled():
            reply = (
                "❌ The Attendance module is not enabled. "
                "Use `/module enable attendance` first."
            )
            if record:
                await refuse(interaction, reply, what=describe(interaction))
            else:
                await interaction.response.send_message(reply, ephemeral=True)
            return False
        return True
