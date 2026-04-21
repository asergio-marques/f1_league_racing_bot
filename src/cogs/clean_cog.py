"""CleanCog — /clean-bot command.

Deletes all messages in the current channel that were sent by the bot.
Requires the bot to have the Manage Messages permission in that channel.
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.channel_guard import admin_only, channel_guard

log = logging.getLogger(__name__)

_MAX_MESSAGES = 500  # upper bound on messages to scan per invocation


class CleanCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="clean-bot",
        description="Delete all bot messages in this channel.",
    )
    @channel_guard
    @admin_only
    async def clean_bot(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                "⛔ This command can only be used in a text channel.", ephemeral=True
            )
            return

        bot_user = self.bot.user
        deleted = 0
        errors = 0

        async for message in channel.history(limit=_MAX_MESSAGES):
            if message.author == bot_user:
                try:
                    await message.delete()
                    deleted += 1
                except discord.HTTPException:
                    errors += 1

        parts = [f"✅ Deleted {deleted} bot message(s)."]
        if errors:
            parts.append(f"⚠️ {errors} message(s) could not be deleted.")
        await interaction.followup.send(" ".join(parts), ephemeral=True)

        log.info(
            "clean-bot: server=%s channel=%s deleted=%d errors=%d by %s",
            interaction.guild_id,
            channel.id,
            deleted,
            errors,
            interaction.user,
        )
