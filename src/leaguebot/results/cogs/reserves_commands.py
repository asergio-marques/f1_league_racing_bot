"""`ReservesCommands`: the `/results reserves` subgroup, the reserve drivers' visibility toggle.

It is its own subgroup class and file, as the architecture's cog bullet gives it
(`docs/design/architecture.md`). Its commands read `self.bot` and call `ResultsCog`'s gates, which
`ResultsSubgroup` declares for the type checker.
"""
from __future__ import annotations

import discord
from discord import app_commands

from leaguebot.core.utils.channel_guard import league_manager_only
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.log_lines import refuse
from leaguebot.core.utils.season_gate import season_for_command
from leaguebot.results.cogs.results_group import RESULTS_GROUP, ResultsSubgroup
from leaguebot.results.services.points.points_settings import RESERVES, reserves_toggle_request


class ReservesCommands(ResultsSubgroup):
    reserves_group = app_commands.Group(
        name="reserves", description="Reserve driver visibility in standings", parent=RESULTS_GROUP
    )

    @reserves_group.command(name="toggle", description="Toggle reserve driver visibility in division standings.")
    @app_commands.describe(division="Division name")
    @league_manager_only
    async def reserves_toggle(self, interaction: discord.Interaction, division: str) -> None:
        if not await self._module_gate(interaction):
            return
        season = await season_for_command(
            interaction, self.bot.season_service, "results reserves toggle"
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

        await self.bot.change_queue.ask(
            RESERVES,
            await reserves_toggle_request(self.bot.db_path, div.id, division),
            interaction=interaction,
            what=describe(interaction),
        )
