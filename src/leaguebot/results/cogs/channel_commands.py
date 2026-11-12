"""`ChannelCommands`: the `/results channel` subgroup, the channels a division posts to.

The commands that set a division's results, standings and verdicts channels, each asking the
change queue for the kind of change its channel names (`_CHANNEL_KINDS`).
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands

from leaguebot.results.cogs.results_group import RESULTS_GROUP, ResultsSubgroup
from leaguebot.results.services.module.channel_settings import (
    NO_ACCESS,
    NOT_ENABLED,
    RESULTS_CHANNEL,
    STANDINGS_CHANNEL,
    VERDICTS_CHANNEL,
    verdicts_division_not_found,
)
from leaguebot.core.services.settings.division_settings import NO_SEASON_REFUSAL, division_not_found
from leaguebot.core.utils.channel_guard import league_manager_only
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.log_lines import refuse

log = logging.getLogger(__name__)

#: The kind of change each `/results channel` command asks of the queue.
_CHANNEL_KINDS = {
    "results": RESULTS_CHANNEL,
    "standings": STANDINGS_CHANNEL,
    "verdicts": VERDICTS_CHANNEL,
}


class ChannelCommands(ResultsSubgroup):
    # ------------------------------------------------------------------
    # /results channel group
    # ------------------------------------------------------------------

    channel_group = app_commands.Group(
        name="channel",
        description="Set the channels a division's results are posted to",
        parent=RESULTS_GROUP,
    )

    async def _set_division_channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
        channel_type: str,  # "results" | "standings" | "verdicts"
    ) -> None:
        """Ask the change queue to set a division's results, standings or verdicts channel, for
        the three commands below.

        Results' own commands, under results' own group. Each checks its module first in its own
        words, not `_module_gate`'s.

        **The live season's division**: a division's channels belong to the season being built or
        raced, and an archived one's no longer matter. Pending completion is live, so a channel
        lost before the season completes may be repaired. The command finds the division from
        the name typed, since the change is for it by its id (`results_channel_settings`).

        **A channel does one job** (`channel_registry_service`, judged by the setting's `check`),
        as the queue is asked and again as the change starts, so a refusal leaves the
        configuration exactly as it stood — the value the setting already holds included. The
        queue's acknowledgement is the command's response, so nothing is deferred; the queue
        writes the channel and its audit entry in one save and answers and logs after it.

        **Verdicts' channel is one the bot must be able to post in**, refused before the season
        is read, and its division is refused in words of its own.

        Every refusal is recorded: `refuse` answers and writes one line naming the command,
        which is read from the interaction.
        """
        if not await self.bot.module_service.is_results_enabled():
            await refuse(interaction, NOT_ENABLED, what=describe(interaction))
            return

        if channel_type == "verdicts":
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
            not_found = (
                verdicts_division_not_found if channel_type == "verdicts" else division_not_found
            )
            await refuse(interaction, not_found(name), what=describe(interaction))
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
        name="results",
        description="Set the results posting channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Results channel")
    @league_manager_only
    async def channel_results(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        await self._set_division_channel(interaction, name, channel, "results")

    @channel_group.command(
        name="standings",
        description="Set the standings posting channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Standings channel")
    @league_manager_only
    async def channel_standings(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        await self._set_division_channel(interaction, name, channel, "standings")

    @channel_group.command(
        name="verdicts",
        description="Set the verdicts (penalty announcement) channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Verdicts announcement channel")
    @league_manager_only
    async def channel_verdicts(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        """Set the channel a division's verdicts are announced in, by `_set_division_channel`."""
        await self._set_division_channel(interaction, name, channel, "verdicts")
