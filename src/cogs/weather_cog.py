"""WeatherCog — /weather command group.

Provides /weather config phase-1-deadline, phase-2-deadline, phase-3-deadline
for configuring per-server weather pipeline horizons.
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.channel_guard import admin_only, channel_guard

log = logging.getLogger(__name__)


class WeatherCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
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

    async def _weather_gate(self, interaction: discord.Interaction) -> bool:
        """Return True (and respond ephemerally) if weather module is not enabled."""
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        if not await self.bot.module_service.is_weather_enabled(server_id):  # type: ignore[attr-defined]
            await interaction.response.send_message(
                "❌ The weather module is not enabled.", ephemeral=True
            )
            return False
        return True

    async def _active_season_gate(self, interaction: discord.Interaction) -> bool:
        """Return True (and respond ephemerally) if a season is currently ACTIVE."""
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        season = await self.bot.season_service.get_active_season(server_id)  # type: ignore[attr-defined]
        if season is not None:
            await interaction.response.send_message(
                "❌ Phase deadline configuration cannot be changed while a season is active.",
                ephemeral=True,
            )
            return True
        return False

    # ------------------------------------------------------------------
    # /weather config phase-1-deadline
    # ------------------------------------------------------------------

    @config_group.command(
        name="phase-1-deadline",
        description="Set days before round to publish Phase 1 weather (default 5).",
    )
    @app_commands.describe(days="Number of days before the round (positive integer)")
    @channel_guard
    @admin_only
    async def phase_1_deadline(self, interaction: discord.Interaction, days: int) -> None:
        if not await self._weather_gate(interaction):
            return
        if await self._active_season_gate(interaction):
            return
        if days < 1:
            await interaction.response.send_message(
                "❌ Phase 1 deadline must be at least 1 day.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        server_id: int = interaction.guild_id  # type: ignore[assignment]

        from services.weather_config_service import set_phase_1_days
        result = await set_phase_1_days(self.bot.db_path, server_id, days)  # type: ignore[attr-defined]

        if isinstance(result, str):
            await interaction.followup.send(f"❌ {result}", ephemeral=True)
            return

        await self.bot.output_router.post_log(  # type: ignore[attr-defined]
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | WEATHER_CONFIG_PHASE1_DEADLINE | Success\n"
            f"  new_value: {days}d  (Phase 2: {result.phase_2_days}d, Phase 3: {result.phase_3_hours}h)",
        )
        await interaction.followup.send(
            f"✅ Phase 1 deadline set to **{days} day(s)** before round. "
            f"(Phase 2: {result.phase_2_days}d, Phase 3: {result.phase_3_hours}h)",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /weather config phase-2-deadline
    # ------------------------------------------------------------------

    @config_group.command(
        name="phase-2-deadline",
        description="Set days before round to publish Phase 2 weather (default 2).",
    )
    @app_commands.describe(days="Number of days before the round (positive integer)")
    @channel_guard
    @admin_only
    async def phase_2_deadline(self, interaction: discord.Interaction, days: int) -> None:
        if not await self._weather_gate(interaction):
            return
        if await self._active_season_gate(interaction):
            return
        if days < 1:
            await interaction.response.send_message(
                "❌ Phase 2 deadline must be at least 1 day.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        server_id: int = interaction.guild_id  # type: ignore[assignment]

        from services.weather_config_service import set_phase_2_days
        result = await set_phase_2_days(self.bot.db_path, server_id, days)  # type: ignore[attr-defined]

        if isinstance(result, str):
            await interaction.followup.send(f"❌ {result}", ephemeral=True)
            return

        await self.bot.output_router.post_log(  # type: ignore[attr-defined]
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | WEATHER_CONFIG_PHASE2_DEADLINE | Success\n"
            f"  new_value: {days}d  (Phase 1: {result.phase_1_days}d, Phase 3: {result.phase_3_hours}h)",
        )
        await interaction.followup.send(
            f"✅ Phase 2 deadline set to **{days} day(s)** before round. "
            f"(Phase 1: {result.phase_1_days}d, Phase 3: {result.phase_3_hours}h)",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /weather config phase-3-deadline
    # ------------------------------------------------------------------

    @config_group.command(
        name="phase-3-deadline",
        description="Set hours before round to publish Phase 3 weather (default 2).",
    )
    @app_commands.describe(hours="Number of hours before the round (positive integer)")
    @channel_guard
    @admin_only
    async def phase_3_deadline(self, interaction: discord.Interaction, hours: int) -> None:
        if not await self._weather_gate(interaction):
            return
        if await self._active_season_gate(interaction):
            return
        if hours < 1:
            await interaction.response.send_message(
                "❌ Phase 3 deadline must be at least 1 hour.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        server_id: int = interaction.guild_id  # type: ignore[assignment]

        from services.weather_config_service import set_phase_3_hours
        result = await set_phase_3_hours(self.bot.db_path, server_id, hours)  # type: ignore[attr-defined]

        if isinstance(result, str):
            await interaction.followup.send(f"❌ {result}", ephemeral=True)
            return

        await self.bot.output_router.post_log(  # type: ignore[attr-defined]
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | WEATHER_CONFIG_PHASE3_DEADLINE | Success\n"
            f"  new_value: {hours}h  (Phase 1: {result.phase_1_days}d, Phase 2: {result.phase_2_days}d)",
        )
        await interaction.followup.send(
            f"✅ Phase 3 deadline set to **{hours} hour(s)** before round. "
            f"(Phase 1: {result.phase_1_days}d, Phase 2: {result.phase_2_days}d)",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WeatherCog(bot))
