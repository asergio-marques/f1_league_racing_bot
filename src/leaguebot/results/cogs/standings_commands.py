"""`StandingsCommands`: the `/results standings` subgroup, the standings repost.

It is its own subgroup class and file, as the architecture's cog bullet gives it
(`docs/design/architecture.md`). Its command reads `self.bot` and calls `ResultsCog`'s gates, which
`ResultsSubgroup` declares for the type checker.
"""
from __future__ import annotations

import discord
from discord import app_commands

from leaguebot.core.utils.channel_guard import league_manager_only
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.league_server import guild_of
from leaguebot.core.utils.log_lines import refuse
from leaguebot.core.utils.season_gate import season_for_command
from leaguebot.results.cogs.results_group import RESULTS_GROUP, ResultsSubgroup


class StandingsCommands(ResultsSubgroup):
    standings_group = app_commands.Group(
        name="standings", description="Standings commands", parent=RESULTS_GROUP
    )

    @standings_group.command(name="sync", description="Force a standings repost for a division.")
    @app_commands.describe(division="Division name")
    @league_manager_only
    async def standings_sync(self, interaction: discord.Interaction, division: str) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        season = await season_for_command(
            interaction, self.bot.season_service, "results standings sync"
        )
        if season is None:
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division.lower()), None)
        if div is None:
            await refuse(
                interaction, f"\u274c Division '{division}' not found.", what=describe(interaction)
            )
            return
        if not await self._sync_gate(interaction, div, what=describe(interaction)):
            return

        from leaguebot.results.services.publishing.results_post_service import standings_repost_status
        from leaguebot.results.services.standings.standings_sync_change import STANDINGS_SYNC

        # The channel and the rounds are read here to answer at once, as the command always has;
        # the repost itself is a change on the queue, which checks them again when it runs.
        status = await standings_repost_status(self.bot.db_path, div.id, guild_of(interaction))

        if status == "ok":
            await self.bot.change_queue.ask(
                STANDINGS_SYNC, {"division_id": div.id, "division_name": div.name},
                interaction=interaction, what="`/results standings sync`",
                refusal_what=describe(interaction),
            )
        elif status == "no_rounds":
            await self._answer_nothing_changed(
                interaction,
                "results standings sync",
                f"No completed rounds found for **{division}**. No standings to post.",
                f"division: {div.name}",
                "reason: no completed rounds, so nothing was posted",
            )
        else:  # no_channel
            await refuse(
                interaction,
                f"\u274c Division '{division}' has no standings channel configured.",
                what=describe(interaction),
            )
