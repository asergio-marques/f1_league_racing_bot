"""InitCog — core server configuration: /bot-init and the three settings beside it.

Every command here is exempt from `channel_guard` and gated on MANAGE_GUILD instead.
For `/bot-init` that is the original chicken-and-egg: no configuration exists to gate
against. For the other three it is load-bearing in a different way — `channel_guard`
admits a command only in the configured interaction channel and only to holders of the
configured interaction role, so gating these on the settings they exist to repair would
lock an administrator out of the exact failure they are for. A deleted interaction channel
would otherwise be unrecoverable short of wiping the configuration.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from models.server_config import ServerConfig
from utils.channel_guard import admin_only

log = logging.getLogger(__name__)

#: Named in every refusal, so an administrator meeting one is told where to go next.
_SETTINGS_COMMANDS = (
    "`/bot-log-channel`, `/bot-interaction-channel` and `/bot-interaction-role`"
)


class InitCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # /bot-init — once per server
    # ------------------------------------------------------------------

    @app_commands.command(
        name="bot-init",
        description="One-time bot setup: register interaction role and channels.",
    )
    @app_commands.describe(
        interaction_role="The role allowed to use bot commands.",
        interaction_channel="The channel where bot commands are accepted.",
        log_channel="The channel where calculation logs are posted.",
    )
    @admin_only
    async def handle_bot_init(
        self,
        interaction: discord.Interaction,
        interaction_role: discord.Role,
        interaction_channel: discord.TextChannel,
        log_channel: discord.TextChannel,
    ) -> None:
        """Register the bot configuration for this server, once.

        There is no `force`: a second run is refused outright rather than overwriting.
        Each of the three settings has a command of its own now, so the only thing an
        overwrite offered was the chance to reset the others by accident — which is
        precisely what it did to test mode. `/bot-reset full:True` removes the row for a
        server that genuinely means to start again.
        """
        server_id = interaction.guild_id

        existing = await self.bot.config_service.get_server_config(server_id)
        if existing:
            await interaction.response.send_message(
                "⚠️ This server is already configured, and `/bot-init` runs once.\n"
                f"To change a setting use {_SETTINGS_COMMANDS}.\n"
                "To start over entirely, use `/bot-reset full:True` first.",
                ephemeral=True,
            )
            return

        cfg = ServerConfig(
            server_id=server_id,
            interaction_role_id=interaction_role.id,
            interaction_channel_id=interaction_channel.id,
            log_channel_id=log_channel.id,
        )
        created = await self.bot.config_service.save_server_config(cfg)
        if not created:
            # Lost a race with a concurrent /bot-init. Report the same refusal rather than
            # claiming a success that wrote nothing.
            await interaction.response.send_message(
                "⚠️ This server is already configured, and `/bot-init` runs once.\n"
                f"To change a setting use {_SETTINGS_COMMANDS}.",
                ephemeral=True,
            )
            return

        # Seed default F1 teams + Reserve for this server if none exist yet
        await self.bot.team_service.seed_default_teams_if_empty(server_id)  # type: ignore[attr-defined]

        await interaction.response.send_message(
            f"✅ Bot configuration saved!\n"
            f"**Interaction role**: {interaction_role.mention}\n"
            f"**Interaction channel**: {interaction_channel.mention}\n"
            f"**Log channel**: {log_channel.mention}",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /bot-init | Success\n"
            f"  interaction_role: {interaction_role.name} (<@&{interaction_role.id}>)\n"
            f"  interaction_channel: <#{interaction_channel.id}>\n"
            f"  log_channel: <#{log_channel.id}>",
        )
        log.info("Bot configured for server %s by %s", server_id, interaction.user)

    # ------------------------------------------------------------------
    # The three settings, each written on its own
    # ------------------------------------------------------------------

    async def _set_one(
        self,
        interaction: discord.Interaction,
        *,
        column: str,
        value: int,
        command: str,
        label: str,
        mention: str,
    ) -> None:
        """Shared body of the three setting commands.

        Writes a single column, so nothing else in the row — test mode, the module flags,
        the other two settings — can be carried over stale from a half-built model.
        """
        server_id = interaction.guild_id

        # A channel does one job (decided 2026-09-06). Keyed on the column, because this
        # body also carries the interaction *role*, which no channel rule governs.
        _setting = {
            "interaction_channel_id": "interaction",
            "log_channel_id": "log",
        }.get(column)
        if _setting is not None:
            from services.channel_registry_service import (
                ChannelUse,
                find_channel_use,
                refusal,
            )

            use = await find_channel_use(self.bot.db_path, server_id, value)
            if use is not None:
                await interaction.response.send_message(
                    refusal(mention, use, same_setting=(use == ChannelUse(_setting))),
                    ephemeral=True,
                )
                return

        changed = await self.bot.config_service.set_core_setting(server_id, column, value)
        if not changed:
            await interaction.response.send_message(
                "⛔ This server is not configured yet — run `/bot-init` first.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ **{label}** set to {mention}.", ephemeral=True
        )
        # Posted after the write, so a new log channel is told about itself and an
        # administrator sees at once that the repair took.
        await self.bot.output_router.post_log(
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | {command} | Success\n"
            f"  {column}: {mention}",
        )
        log.info("%s set %s for server %s", interaction.user, column, server_id)

    @app_commands.command(
        name="bot-log-channel",
        description="Change the channel the bot writes its calculation log to.",
    )
    @app_commands.describe(channel="The channel where calculation logs are posted.")
    @admin_only
    async def handle_log_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        await self._set_one(
            interaction,
            column="log_channel_id",
            value=channel.id,
            command="/bot-log-channel",
            label="Log channel",
            mention=f"<#{channel.id}>",
        )

    @app_commands.command(
        name="bot-interaction-channel",
        description="Change the channel the bot accepts commands in.",
    )
    @app_commands.describe(channel="The channel where bot commands are accepted.")
    @admin_only
    async def handle_interaction_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        await self._set_one(
            interaction,
            column="interaction_channel_id",
            value=channel.id,
            command="/bot-interaction-channel",
            label="Interaction channel",
            mention=f"<#{channel.id}>",
        )

    @app_commands.command(
        name="bot-interaction-role",
        description="Change the role allowed to use bot commands.",
    )
    @app_commands.describe(role="The role allowed to use bot commands.")
    @admin_only
    async def handle_interaction_role(
        self, interaction: discord.Interaction, role: discord.Role
    ) -> None:
        await self._set_one(
            interaction,
            column="interaction_role_id",
            value=role.id,
            command="/bot-interaction-role",
            label="Interaction role",
            mention=f"<@&{role.id}>",
        )
