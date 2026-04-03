"""TrackCog — /track command group."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from db.database import get_connection
import services.track_service as track_service
from utils.channel_guard import channel_guard, admin_only

log = logging.getLogger(__name__)


class TrackCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Command group
    # ------------------------------------------------------------------

    track = app_commands.Group(
        name="track",
        description="Track commands",
    )

    # ------------------------------------------------------------------
    # /track list
    # ------------------------------------------------------------------

    @track.command(
        name="list",
        description="List all available tracks.",
    )
    @channel_guard
    @admin_only
    async def track_list(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with get_connection(self.bot.db_path) as db:
            rows = await track_service.get_all_tracks(db)
        table = "\n".join(f"{r['id']:02d} | {r['name']} | {r['gp_name']}" for r in rows)
        await interaction.followup.send(
            f"**Tracks ({len(rows)})**\n```\n{table}\n```",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TrackCog(bot))


