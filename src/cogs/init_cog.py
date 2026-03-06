"""InitCog — /bot init command.

This command is intentionally exempt from channel_guard (chicken-and-egg:
no config exists before init). It requires MANAGE_GUILD permission instead.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from models.server_config import ServerConfig
from utils.channel_guard import admin_only

log = logging.getLogger(__name__)


class InitCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="bot-init",
        description="One-time bot setup: register interaction role and channels.",
    )
    @app_commands.describe(
        interaction_role="The role allowed to use bot commands.",
        interaction_channel="The channel where bot commands are accepted.",
        log_channel="The channel where calculation logs are posted.",
        force="Set to True to overwrite existing configuration.",
    )
    @admin_only
    async def handle_bot_init(
        self,
        interaction: discord.Interaction,
        interaction_role: discord.Role,
        interaction_channel: discord.TextChannel,
        log_channel: discord.TextChannel,
        force: bool = False,
    ) -> None:
        """Register the bot configuration for this server."""
        server_id = interaction.guild_id

        existing = await self.bot.config_service.get_server_config(server_id)
        if existing and not force:
            await interaction.response.send_message(
                "⚠️ This server is already configured. "
                "Use `force: True` to overwrite the existing configuration.",
                ephemeral=True,
            )
            return

        cfg = ServerConfig(
            server_id=server_id,
            interaction_role_id=interaction_role.id,
            interaction_channel_id=interaction_channel.id,
            log_channel_id=log_channel.id,
        )
        await self.bot.config_service.save_server_config(cfg)

        # Seed default F1 teams + Reserve for this server if none exist yet
        await self.bot.team_service.seed_default_teams_if_empty(server_id)  # type: ignore[attr-defined]

        action = "updated" if existing else "saved"
        await interaction.response.send_message(
            f"✅ Bot configuration {action}!\n"
            f"**Interaction role**: {interaction_role.mention}\n"
            f"**Interaction channel**: {interaction_channel.mention}\n"
            f"**Log channel**: {log_channel.mention}",
            ephemeral=True,
        )
        log.info(
            "Bot configured for server %s by %s (force=%s)",
            server_id, interaction.user, force,
        )
