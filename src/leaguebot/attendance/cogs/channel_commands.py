"""`ChannelCommands`: the `/attendance channel` subgroup, the channels a division posts to.

The commands that set a division's RSVP and attendance channels, each asking the change queue for
the kind of change its channel names (`_CHANNEL_KINDS`).
"""
from __future__ import annotations

import discord
from discord import app_commands

from leaguebot.attendance.cogs.attendance_group import CHANNEL_GROUP, AttendanceSubgroup
from leaguebot.attendance.services.settings.channel_settings import (
    ATTENDANCE_CHANNEL,
    NO_ACCESS,
    NOT_ENABLED,
    RSVP_CHANNEL,
    division_not_found,
)
from leaguebot.core.services.settings.division_settings import NO_SEASON_REFUSAL
from leaguebot.core.utils.channel_guard import league_manager_only
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.log_lines import refuse

#: The kind of change each `/attendance channel` command asks of the queue.
_CHANNEL_KINDS = {"rsvp": RSVP_CHANNEL, "attendance": ATTENDANCE_CHANNEL}


class ChannelCommands(AttendanceSubgroup):
    channel_group = CHANNEL_GROUP

    # ── /attendance channel rsvp and attendance ───────────────────────────
    #
    # Attendance's own commands, under attendance's own group.

    async def _set_division_channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
        channel_type: str,  # "rsvp" | "attendance"
    ) -> None:
        """Ask the change queue to set a division's RSVP or attendance channel, for the two
        commands below.

        Each checks the module first in its own words, not `_module_gate`'s, then refuses a
        channel the bot cannot post in.

        **The live season's division**: a division's channels belong to the season being built or
        raced, and an archived one's no longer matter. Pending completion is live, so a channel
        lost before the season completes may be repaired. The command finds the division from the
        name typed, since the change is for it by its id (`attendance_channel_settings`).

        **A channel does one job** (`channel_registry_service`, judged by the setting's `check`),
        as the queue is asked and again as the change starts, so a refusal leaves the
        configuration exactly as it stood — the value the setting already holds included. The
        queue's acknowledgement is the command's response, so nothing is deferred; the queue
        writes the channel and its audit entry, its ids as integers, in one save and answers and
        logs after it.

        Every refusal is recorded: `refuse` answers and writes one line naming the command, which
        is read from the interaction.
        """
        if not await self.bot.module_service.is_attendance_enabled():
            await refuse(interaction, NOT_ENABLED, what=describe(interaction))
            return

        guild = interaction.guild
        if guild is None or not channel.permissions_for(guild.me).send_messages:
            await refuse(interaction, NO_ACCESS, what=describe(interaction))
            return

        season = await self.bot.season_service.get_setup_or_active_season()
        if season is None:
            await refuse(interaction, NO_SEASON_REFUSAL, what=describe(interaction))
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await refuse(interaction, division_not_found(name), what=describe(interaction))
            return

        await self.bot.change_queue.ask(
            _CHANNEL_KINDS[channel_type],
            {
                "division_id": div.id,
                "division_name": name,
                "value": channel.id,
                "channel_name": channel.name,
            },
            interaction=interaction,
            what=describe(interaction),
        )

    @channel_group.command(
        name="rsvp",
        description="Set the RSVP notice channel for a division (attendance module).",
    )
    @app_commands.describe(name="Division name", channel="RSVP notice channel")
    @league_manager_only
    async def channel_rsvp(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        await self._set_division_channel(interaction, name, channel, "rsvp")

    @channel_group.command(
        name="attendance",
        description="Set the attendance logging channel for a division (attendance module).",
    )
    @app_commands.describe(name="Division name", channel="Attendance logging channel")
    @league_manager_only
    async def channel_attendance(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        await self._set_division_channel(interaction, name, channel, "attendance")
