"""DriverCog — /driver command group."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.channel_guard import channel_guard, admin_only

log = logging.getLogger(__name__)


class DriverCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    driver = app_commands.Group(
        name="driver",
        description="Driver profile management commands",
        guild_only=True,
        default_permissions=None,
    )

    # ------------------------------------------------------------------
    # /driver reassign
    # ------------------------------------------------------------------

    @driver.command(
        name="reassign",
        description="Re-key a driver profile from one Discord account to another.",
    )
    @app_commands.describe(
        old_user="The existing Discord user whose profile is to be re-keyed (mention; use old_user_id for departed users).",
        old_user_id="Raw Discord snowflake ID, for users who have left the server.",
        new_user="The target Discord account. Must not already have a driver profile.",
    )
    @channel_guard
    @admin_only
    async def reassign(
        self,
        interaction: discord.Interaction,
        new_user: discord.Member,
        old_user: discord.Member | None = None,
        old_user_id: str | None = None,
    ) -> None:
        """Reassign a driver profile between Discord accounts."""
        # Resolve old user ID — accept Member mention or raw snowflake string
        if old_user is not None:
            resolved_old_id = str(old_user.id)
        elif old_user_id is not None:
            resolved_old_id = old_user_id.strip()
        else:
            await interaction.response.send_message(
                "⛔ You must supply either `old_user` (mention) or `old_user_id` (raw snowflake).",
                ephemeral=True,
            )
            return

        server_id = interaction.guild_id
        new_user_id = str(new_user.id)
        actor_id = interaction.user.id
        actor_name = str(interaction.user)

        try:
            profile = await self.bot.driver_service.reassign_user_id(  # type: ignore[attr-defined]
                server_id, resolved_old_id, new_user_id, actor_id, actor_name
            )
        except ValueError as exc:
            await interaction.response.send_message(f"⛔ {exc}", ephemeral=True)
            return

        former = "Yes" if profile.former_driver else "No"
        await interaction.response.send_message(
            f"✅ Driver profile re-keyed successfully.\n"
            f"   Old User ID : {resolved_old_id}\n"
            f"   New User ID : {new_user_id}\n"
            f"   State       : {profile.current_state.value}\n"
            f"   Former driver: {former}",
            ephemeral=True,
        )
        log.info(
            "Driver profile re-keyed on server %s: %s → %s by %s",
            server_id, resolved_old_id, new_user_id, actor_name,
        )
