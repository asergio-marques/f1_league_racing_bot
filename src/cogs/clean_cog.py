"""CleanCog — /clean-bot command.

Deletes the most recent messages in the current channel that were sent by the bot.
Requires the bot to have the Manage Messages permission in that channel.

**How many is asked for, and capped at ten** (2026-09-07). The command took no parameter
and swept five hundred messages, which is a great deal of channel to lose to a slip of the
finger — and irreversibly, since Discord keeps no undo. Ten is enough for the job it is
actually for: clearing the tail of a multi-message command that did not clear itself. An
approved or expired `/season review` now deletes its own report, which was the case the
five hundred was reaching for.
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.channel_guard import admin_only, channel_guard

log = logging.getLogger(__name__)

#: How far back the command will look to find the bot messages it was asked for. The
#: parameter counts *bot* messages, and a channel a league is talking in holds other
#: people's messages between them, so the scan has to reach further than the count. It is
#: still a bound: a channel whose last two hundred messages hold no bot message at all
#: deletes nothing rather than reading to the beginning of time.
_SCAN_LIMIT = 200

#: The most a single invocation will delete. See the module docstring.
MAX_DELETIONS = 10


class CleanCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="clean-bot",
        description="Delete the most recent bot messages in this channel.",
    )
    @app_commands.describe(
        count=f"How many of the bot's most recent messages to delete (1–{MAX_DELETIONS})."
    )
    @channel_guard
    @admin_only
    async def clean_bot(
        self,
        interaction: discord.Interaction,
        # The bound is the literal 10 and not `MAX_DELETIONS`: `from __future__ import
        # annotations` makes this a string, which discord.py evaluates while the class body
        # is still being read, before the module's own names are bound. A test asserts the
        # two agree, since nothing else here can.
        count: app_commands.Range[int, 1, 10],
    ) -> None:
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

        # Newest first, which is what `channel.history` yields by default and what the
        # command promises: the *most recent* messages, so the tail of a command just run
        # is what goes. A message that will not delete is counted and passed over rather
        # than consuming one of the deletions asked for.
        async for message in channel.history(limit=_SCAN_LIMIT):
            if deleted >= count:
                break
            if message.author != bot_user:
                continue
            try:
                await message.delete()
                deleted += 1
            except discord.NotFound:
                # Already gone. Not a fault, and not one of the deletions asked for.
                continue
            except discord.HTTPException:
                errors += 1

        parts = [f"✅ Deleted {deleted} bot message(s)."]
        if deleted < count:
            parts.append(
                f"Only {deleted} of the last {_SCAN_LIMIT} messages were the bot's."
            )
        if errors:
            parts.append(f"⚠️ {errors} message(s) could not be deleted.")
        await interaction.followup.send(" ".join(parts), ephemeral=True)

        log.info(
            "clean-bot: server=%s channel=%s asked=%d deleted=%d errors=%d by %s",
            interaction.guild_id,
            channel.id,
            count,
            deleted,
            errors,
            interaction.user,
        )
