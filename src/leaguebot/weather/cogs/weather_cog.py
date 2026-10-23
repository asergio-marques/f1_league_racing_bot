"""WeatherCog — /weather command group.

Provides /weather config phase-1-deadline, phase-2-deadline, phase-3-deadline
for configuring the league's weather pipeline horizons, /weather config view
for reading them back, and /weather channel for setting the channel a division's
forecasts are posted to.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.core.services import audit_service
from leaguebot.core.services.channel_registry_service import channel_refusal
from leaguebot.core.utils.channel_guard import league_manager_only, changes_nothing
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.log_lines import refuse
from leaguebot.weather.services.weather_settings import DEADLINE

log = logging.getLogger(__name__)


class WeatherCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # /weather config group
    # ------------------------------------------------------------------

    weather = app_commands.Group(name="weather", description="Weather module commands")
    config_group = app_commands.Group(
        name="config", description="Configure weather pipeline settings", parent=weather
    )

    # ------------------------------------------------------------------
    # Shared pre-condition checks
    # ------------------------------------------------------------------

    async def _module_gate(
        self, interaction: discord.Interaction, *, record: bool = True
    ) -> bool:
        """Return True if the weather module is enabled; otherwise answer and return False.

        The refusal is recorded in the log channel, as every refusal of a command that changes
        something is. A command that changes nothing (`record=False`) answers and records nothing.
        """
        if await self.bot.module_service.is_weather_enabled():
            return True
        reply = "❌ The Weather module is not enabled."
        if record:
            await refuse(interaction, reply, what=describe(interaction))
        else:
            await interaction.response.send_message(reply, ephemeral=True)
        return False

    async def _ask_deadline(self, interaction: discord.Interaction, column: str, value: int) -> None:
        """Ask the change that sets the deadline *column* to *value*.

        Not deferred: the change's check judges the module, the season, the range and the
        ordering in that order, at the ask and again as it runs (`weather_settings`), and the
        queue answers (core specification, "How a change is carried out").
        """
        await self.bot.change_queue.ask(
            DEADLINE,
            {"column": column, "value": value},
            interaction=interaction,
            what=describe(interaction),
        )

    # ------------------------------------------------------------------
    # /weather config phase-1-deadline
    # ------------------------------------------------------------------

    @config_group.command(
        name="phase-1-deadline",
        description="Set days before round to publish Phase 1 weather (default 5).",
    )
    @app_commands.describe(days="Number of days before the round (positive integer)")
    @league_manager_only
    async def phase_1_deadline(self, interaction: discord.Interaction, days: int) -> None:
        await self._ask_deadline(interaction, "phase_1_days", days)

    # ------------------------------------------------------------------
    # /weather config phase-2-deadline
    # ------------------------------------------------------------------

    @config_group.command(
        name="phase-2-deadline",
        description="Set days before round to publish Phase 2 weather (default 2).",
    )
    @app_commands.describe(days="Number of days before the round (positive integer)")
    @league_manager_only
    async def phase_2_deadline(self, interaction: discord.Interaction, days: int) -> None:
        await self._ask_deadline(interaction, "phase_2_days", days)

    # ------------------------------------------------------------------
    # /weather config phase-3-deadline
    # ------------------------------------------------------------------

    @config_group.command(
        name="phase-3-deadline",
        description="Set hours before round to publish Phase 3 weather (default 2).",
    )
    @app_commands.describe(hours="Number of hours before the round (positive integer)")
    @league_manager_only
    async def phase_3_deadline(self, interaction: discord.Interaction, hours: int) -> None:
        await self._ask_deadline(interaction, "phase_3_hours", hours)

    # ------------------------------------------------------------------
    # /weather config view
    # ------------------------------------------------------------------

    @config_group.command(
        name="view",
        description="Show the three weather deadlines currently set.",
    )
    @league_manager_only
    @changes_nothing
    async def config_view(self, interaction: discord.Interaction) -> None:
        """Read the three deadlines back (issue #118).

        **Not gated on the season**, unlike the setters beside it. The deadlines belong to the
        server rather than to a season, and before this command they could be read only in the
        configuration and placements reviews, each tied to one stage of one season. While a
        season's placements are confirmed the setters are refused, so the values shown then
        are the ones the season runs on.
        """
        if not await self._module_gate(interaction, record=False):
            return

        await interaction.response.defer(ephemeral=True)

        from leaguebot.weather.services.weather_config_service import (
            describe_deadlines,
            get_weather_pipeline_config,
        )
        config = await get_weather_pipeline_config(self.bot.db_path)

        await interaction.followup.send(
            "\n".join(["**Weather deadlines**", *describe_deadlines(config)]),
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /weather channel
    # ------------------------------------------------------------------

    @weather.command(
        name="channel",
        description="Set the weather forecast channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Weather forecast channel")
    @league_manager_only
    async def channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        """Set the channel a division's forecasts are posted to (#462).

        Weather's own command, under weather's own group: it sat under core's `/division`
        until #462 moved it, and only its name changed. It keeps its own module check, worded as
        `_module_gate` words it, so the two commands refuse in one voice.

        **The live season's division** (#220): a division's channels belong to the season being
        built or raced, and an archived one's no longer matter. Pending completion is live,
        so a channel lost before the season completes may be repaired.

        **A channel does one job** (`channel_refusal`), checked before the write, so a
        refusal leaves the configuration exactly as it stood — the value the setting already
        holds included. The change is recorded by core's `audit_service`, as it was when core
        wrote it: `DIVISION_CHANNEL_SET`, with its `channel_type`.
        """
        if not await self.bot.module_service.is_weather_enabled():
            await refuse(
                interaction,
                "\u274c The Weather module is not enabled.",
                what=describe(interaction),
            )
            return

        season = await self.bot.season_service.get_setup_or_active_season()
        if season is None:
            await refuse(
                interaction,
                "\u274c No season is live. A division's channels belong to the season being built or raced \u2014 start one with `/season setup`.",
                what=describe(interaction),
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await refuse(
                interaction,
                f"\u274c Division **{name}** not found in the current season.",
                what=describe(interaction),
            )
            return

        refused = await channel_refusal(
            self.bot.db_path, channel, "weather", division_name=div.name
        )
        if refused is not None:
            await refuse(interaction, refused, what=describe(interaction))
            return

        old_id = await self.bot.season_service.set_division_forecast_channel(div.id, channel.id)
        await audit_service.record_change(
            self.bot.db_path,
            actor_id=interaction.user.id,
            actor_name=str(interaction.user),
            change_type="DIVISION_CHANNEL_SET",
            old_value={"channel_type": "weather", "channel_id": old_id},
            new_value={"channel_type": "weather", "channel_id": channel.id},
            now=datetime.now(timezone.utc),
            division_id=div.id,
        )

        # "Updated" says a channel was moved rather than assigned afresh (issue #212).
        verb = "set" if old_id is None else "updated"
        await interaction.response.send_message(
            f"\u2705 Weather forecast channel for **{name}** {verb} to {channel.mention}.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /weather channel | Success\n"
            f"  division: {name}\n"
            f"  channel: #{channel.name}",
        )


async def setup(bot: LeagueBot) -> None:
    await bot.add_cog(WeatherCog(bot))
