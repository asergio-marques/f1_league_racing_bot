"""ResultsCog — /results config, /results amend, /results reserves, /round results commands."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from models.points_config import SessionType
from services import points_config_service, season_points_service
from services.points_config_service import (
    ConfigAlreadyExistsError,
    ConfigNotFoundError,
    InvalidSessionTypeError,
)
from services.season_points_service import (
    ConfigAlreadyAttachedError,
    ConfigNotAttachedError,
    SeasonNotInSetupError,
)

log = logging.getLogger(__name__)

_SESSION_CHOICES = [
    app_commands.Choice(name="Sprint Qualifying", value="SPRINT_QUALIFYING"),
    app_commands.Choice(name="Sprint Race", value="SPRINT_RACE"),
    app_commands.Choice(name="Feature Qualifying", value="FEATURE_QUALIFYING"),
    app_commands.Choice(name="Feature Race", value="FEATURE_RACE"),
]

_RACE_SESSION_CHOICES = [
    app_commands.Choice(name="Sprint Race", value="SPRINT_RACE"),
    app_commands.Choice(name="Feature Race", value="FEATURE_RACE"),
]


class ResultsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Gate helpers
    # ------------------------------------------------------------------

    async def _module_gate(self, interaction: discord.Interaction) -> bool:
        if not await self.bot.module_service.is_results_enabled(interaction.guild_id):
            await interaction.response.send_message(
                "\u274c The Results & Standings module is not enabled on this server.",
                ephemeral=True,
            )
            return False
        return True

    async def _server_admin_gate(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "\u274c This command requires server admin permissions.",
                ephemeral=True,
            )
            return False
        return True

    # ------------------------------------------------------------------
    # /results config group
    # ------------------------------------------------------------------

    results_group = app_commands.Group(name="results", description="Results & Standings commands")
    config_group = app_commands.Group(
        name="config", description="Points configuration management", parent=results_group
    )

    @config_group.command(name="add", description="Add a named points configuration to this server.")
    @app_commands.describe(name="Unique config name, e.g. '100%'")
    async def config_add(self, interaction: discord.Interaction, name: str) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await points_config_service.create_config(self.bot.db_path, interaction.guild_id, name)
        except ConfigAlreadyExistsError:
            await interaction.followup.send(
                f"\u274c A config named **{name}** already exists on this server.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"\u2705 Config **{name}** created. All positions default to 0 points.", ephemeral=True
        )

    @config_group.command(name="remove", description="Remove a named points configuration.")
    @app_commands.describe(name="Config name to remove")
    async def config_remove(self, interaction: discord.Interaction, name: str) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await points_config_service.remove_config(self.bot.db_path, interaction.guild_id, name)
        except ConfigNotFoundError:
            await interaction.followup.send(
                f"\u274c Config **{name}** not found.", ephemeral=True
            )
            return
        await interaction.followup.send(f"\u2705 Config **{name}** removed.", ephemeral=True)

    @config_group.command(name="session", description="Set points for a finishing position in a session type.")
    @app_commands.describe(
        name="Config name",
        session="Session type",
        position="Finishing position (1-indexed)",
        points="Points awarded",
    )
    @app_commands.choices(session=_SESSION_CHOICES)
    async def config_session(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str],
        position: int,
        points: int,
    ) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await points_config_service.set_session_points(
                self.bot.db_path,
                interaction.guild_id,
                name,
                SessionType(session.value),
                position,
                points,
            )
        except ConfigNotFoundError:
            await interaction.followup.send(f"\u274c Config **{name}** not found.", ephemeral=True)
            return
        await interaction.followup.send(
            f"\u2705 Set **{session.name}** position {position} \u2192 {points} pts in config **{name}**.",
            ephemeral=True,
        )

    @config_group.command(name="fl", description="Set the fastest-lap bonus for a race session type.")
    @app_commands.describe(
        name="Config name",
        session="Race session type (Sprint Race or Feature Race)",
        points="Bonus points for fastest lap",
    )
    @app_commands.choices(session=_RACE_SESSION_CHOICES)
    async def config_fl(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str],
        points: int,
    ) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await points_config_service.set_fl_bonus(
                self.bot.db_path, interaction.guild_id, name, SessionType(session.value), points
            )
        except ConfigNotFoundError:
            await interaction.followup.send(f"\u274c Config **{name}** not found.", ephemeral=True)
            return
        except InvalidSessionTypeError:
            await interaction.followup.send(
                "\u274c Fastest-lap bonus cannot be set for qualifying sessions.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"\u2705 Set fastest-lap bonus for **{session.name}** \u2192 {points} pts in config **{name}**.",
            ephemeral=True,
        )

    @config_group.command(name="fl-plimit", description="Set the position eligibility limit for fastest-lap bonus.")
    @app_commands.describe(
        name="Config name",
        session="Race session type (Sprint Race or Feature Race)",
        limit="Highest eligible position (e.g. 10 → positions 1–10 eligible)",
    )
    @app_commands.choices(session=_RACE_SESSION_CHOICES)
    async def config_fl_plimit(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str],
        limit: int,
    ) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await points_config_service.set_fl_position_limit(
                self.bot.db_path, interaction.guild_id, name, SessionType(session.value), limit
            )
        except ConfigNotFoundError:
            await interaction.followup.send(f"\u274c Config **{name}** not found.", ephemeral=True)
            return
        except InvalidSessionTypeError:
            await interaction.followup.send(
                "\u274c Position limit cannot be set for qualifying sessions.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"\u2705 Set fastest-lap position limit for **{session.name}** \u2192 top {limit} eligible in config **{name}**.",
            ephemeral=True,
        )

    @config_group.command(name="append", description="Attach a server config to the current season in SETUP.")
    @app_commands.describe(name="Config name to attach")
    async def config_append(self, interaction: discord.Interaction, name: str) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        season = await self.bot.season_service.get_season_for_server(interaction.guild_id)
        if season is None:
            await interaction.followup.send("\u274c No season found for this server.", ephemeral=True)
            return
        try:
            await season_points_service.attach_config(
                self.bot.db_path, season.id, name, season.status
            )
        except SeasonNotInSetupError:
            await interaction.followup.send(
                "\u274c Config attachment is only allowed for seasons in SETUP.", ephemeral=True
            )
            return
        except ConfigAlreadyAttachedError:
            await interaction.followup.send(
                f"\u2139\ufe0f Config **{name}** is already attached to this season.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"\u2705 Config **{name}** attached to the current season.", ephemeral=True
        )

    @config_group.command(name="detach", description="Detach a config from the current season in SETUP.")
    @app_commands.describe(name="Config name to detach")
    async def config_detach(self, interaction: discord.Interaction, name: str) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        season = await self.bot.season_service.get_season_for_server(interaction.guild_id)
        if season is None:
            await interaction.followup.send("\u274c No season found for this server.", ephemeral=True)
            return
        try:
            await season_points_service.detach_config(
                self.bot.db_path, season.id, name, season.status
            )
        except SeasonNotInSetupError:
            await interaction.followup.send(
                "\u274c Config detachment is only allowed for seasons in SETUP.", ephemeral=True
            )
            return
        except ConfigNotAttachedError:
            await interaction.followup.send(
                f"\u2139\ufe0f Config **{name}** is not attached to this season.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"\u2705 Config **{name}** detached from the current season.", ephemeral=True
        )

    # ------------------------------------------------------------------
    # /results config view — implemented in Phase 6 (T019)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # /results amend group — implemented in Phase 9 (T025)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # /results reserves group — implemented in Phase 10 (T026)
    # ------------------------------------------------------------------
