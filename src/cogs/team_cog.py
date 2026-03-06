"""TeamCog — /team command group."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.channel_guard import channel_guard, admin_only

log = logging.getLogger(__name__)


class TeamCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    team = app_commands.Group(
        name="team",
        description="Team configuration commands",
        guild_only=True,
        default_permissions=None,
    )

    # ------------------------------------------------------------------
    # /team default  (sub-group)
    # ------------------------------------------------------------------

    default_group = app_commands.Group(
        name="default",
        description="Manage server-level default team list",
        parent=team,
        guild_only=True,
    )

    @default_group.command(
        name="add",
        description="Add a new team to the server default list.",
    )
    @app_commands.describe(
        name="Name of the new team (max 50 chars).",
        seats="Number of seats (default 2, must be ≥ 1).",
    )
    @channel_guard
    @admin_only
    async def default_add(
        self,
        interaction: discord.Interaction,
        name: str,
        seats: int = 2,
    ) -> None:
        if seats < 1:
            await interaction.response.send_message(
                "⛔ Seat count must be at least 1.", ephemeral=True
            )
            return
        try:
            await self.bot.team_service.add_default_team(  # type: ignore[attr-defined]
                interaction.guild_id, name, seats
            )
        except ValueError as exc:
            await interaction.response.send_message(f"⛔ {exc}", ephemeral=True)
            return
        await interaction.response.send_message(
            f'✅ Default team "{name}" added ({seats} seats).', ephemeral=True
        )

    @default_group.command(
        name="rename",
        description="Rename an existing default team.",
    )
    @app_commands.describe(
        current_name="Exact current name of the team.",
        new_name="Replacement name (max 50 chars).",
    )
    @channel_guard
    @admin_only
    async def default_rename(
        self,
        interaction: discord.Interaction,
        current_name: str,
        new_name: str,
    ) -> None:
        try:
            await self.bot.team_service.rename_default_team(  # type: ignore[attr-defined]
                interaction.guild_id, current_name, new_name
            )
        except ValueError as exc:
            await interaction.response.send_message(f"⛔ {exc}", ephemeral=True)
            return
        await interaction.response.send_message(
            f'✅ Default team "{current_name}" renamed to "{new_name}".', ephemeral=True
        )

    @default_group.command(
        name="remove",
        description="Remove a team from the server default list.",
    )
    @app_commands.describe(name="Exact name of the team to remove.")
    @channel_guard
    @admin_only
    async def default_remove(
        self,
        interaction: discord.Interaction,
        name: str,
    ) -> None:
        # Confirm / cancel prompt
        view = _ConfirmView()
        await interaction.response.send_message(
            f'⚠️ Remove default team "{name}"?\n'
            "This will not affect team instances already created in existing divisions.",
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if not view.confirmed:
            await interaction.edit_original_response(
                content="❌ Cancelled.", view=None
            )
            return
        try:
            await self.bot.team_service.remove_default_team(  # type: ignore[attr-defined]
                interaction.guild_id, name
            )
        except ValueError as exc:
            await interaction.edit_original_response(content=f"⛔ {exc}", view=None)
            return
        await interaction.edit_original_response(
            content=f'✅ Default team "{name}" removed from server defaults.', view=None
        )

    # ------------------------------------------------------------------
    # /team season  (sub-group)
    # ------------------------------------------------------------------

    season_group = app_commands.Group(
        name="season",
        description="Manage team configuration for the current season",
        parent=team,
        guild_only=True,
    )

    @season_group.command(
        name="add",
        description="Add a team to all divisions of the current SETUP season.",
    )
    @app_commands.describe(
        name="Name of the team to add.",
        seats="Number of seats (default 2).",
    )
    @channel_guard
    @admin_only
    async def season_add(
        self,
        interaction: discord.Interaction,
        name: str,
        seats: int = 2,
    ) -> None:
        season = await self.bot.season_service.get_setup_season(  # type: ignore[attr-defined]
            interaction.guild_id
        )
        if season is None:
            await interaction.response.send_message(
                "⛔ No season is currently in setup. "
                "Team configuration can only be changed during season setup.",
                ephemeral=True,
            )
            return
        try:
            count = await self.bot.team_service.season_team_add(  # type: ignore[attr-defined]
                interaction.guild_id, season.id, name, seats
            )
        except ValueError as exc:
            await interaction.response.send_message(f"⛔ {exc}", ephemeral=True)
            return
        await interaction.response.send_message(
            f'✅ Team "{name}" added to all {count} division(s) of Season {season.season_number}.',
            ephemeral=True,
        )

    @season_group.command(
        name="rename",
        description="Rename a team across all divisions of the current SETUP season.",
    )
    @app_commands.describe(
        current_name="Exact current name (same across all divisions).",
        new_name="New name.",
    )
    @channel_guard
    @admin_only
    async def season_rename(
        self,
        interaction: discord.Interaction,
        current_name: str,
        new_name: str,
    ) -> None:
        season = await self.bot.season_service.get_setup_season(  # type: ignore[attr-defined]
            interaction.guild_id
        )
        if season is None:
            await interaction.response.send_message(
                "⛔ No season is currently in setup. "
                "Team configuration can only be changed during season setup.",
                ephemeral=True,
            )
            return
        try:
            count = await self.bot.team_service.season_team_rename(  # type: ignore[attr-defined]
                interaction.guild_id, season.id, current_name, new_name
            )
        except ValueError as exc:
            await interaction.response.send_message(f"⛔ {exc}", ephemeral=True)
            return
        await interaction.response.send_message(
            f'✅ Team "{current_name}" renamed to "{new_name}" across all {count} division(s).',
            ephemeral=True,
        )

    @season_group.command(
        name="remove",
        description="Remove a team from all divisions of the current SETUP season.",
    )
    @app_commands.describe(name="Exact team name.")
    @channel_guard
    @admin_only
    async def season_remove(
        self,
        interaction: discord.Interaction,
        name: str,
    ) -> None:
        season = await self.bot.season_service.get_setup_season(  # type: ignore[attr-defined]
            interaction.guild_id
        )
        if season is None:
            await interaction.response.send_message(
                "⛔ No season is currently in setup. "
                "Team configuration can only be changed during season setup.",
                ephemeral=True,
            )
            return

        view = _ConfirmView()
        await interaction.response.send_message(
            f'⚠️ Remove team "{name}" from all division(s) of Season {season.season_number}?\n'
            "Any seat assignments for this team will also be removed.",
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if not view.confirmed:
            await interaction.edit_original_response(content="❌ Cancelled.", view=None)
            return

        try:
            count = await self.bot.team_service.season_team_remove(  # type: ignore[attr-defined]
                interaction.guild_id, season.id, name
            )
        except ValueError as exc:
            await interaction.edit_original_response(content=f"⛔ {exc}", view=None)
            return
        await interaction.edit_original_response(
            content=f'✅ Team "{name}" removed from all {count} division(s) of Season {season.season_number}.',
            view=None,
        )


# ---------------------------------------------------------------------------
# Shared confirm/cancel UI view
# ---------------------------------------------------------------------------

class _ConfirmView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=60)
        self.confirmed: bool = False

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed = False
        self.stop()
        await interaction.response.defer()
