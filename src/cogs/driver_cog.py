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

    # ------------------------------------------------------------------
    # /driver assign
    # ------------------------------------------------------------------

    @driver.command(
        name="assign",
        description="Assign an Unassigned driver to a team and division.",
    )
    @app_commands.describe(
        user="The Discord member to assign.",
        division="Division tier number or name.",
        team="Exact team name as it appears in the division.",
    )
    @channel_guard
    @admin_only
    async def assign(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        division: str,
        team: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        actor_id = interaction.user.id
        actor_name = str(interaction.user)

        # Resolve season
        season = await self.bot.season_service.get_active_season(server_id)  # type: ignore[attr-defined]
        if season is None:
            await interaction.followup.send(
                "⛔ No active season found.", ephemeral=True
            )
            return

        # Resolve division
        resolved = await self.bot.placement_service.resolve_division(  # type: ignore[attr-defined]
            season.id, division
        )
        if resolved is None:
            await interaction.followup.send(
                f"⛔ Division **{division}** not found in the active season.", ephemeral=True
            )
            return
        division_id, division_name = resolved

        # Fetch the driver profile
        profile = await self.bot.driver_service.get_profile(  # type: ignore[attr-defined]
            server_id, str(user.id)
        )
        if profile is None:
            await interaction.followup.send(
                f"⛔ No driver profile found for {user.mention}.", ephemeral=True
            )
            return

        try:
            result = await self.bot.placement_service.assign_driver(  # type: ignore[attr-defined]
                server_id=server_id,
                driver_profile_id=profile.id,
                division_id=division_id,
                team_name=team,
                season_id=season.id,
                acting_user_id=actor_id,
                acting_user_name=actor_name,
                guild=interaction.guild,
                discord_user_id=str(user.id),
            )
        except ValueError as exc:
            await interaction.followup.send(f"⛔ {exc}", ephemeral=True)
            return

        verb = "Assigned" if result["was_unassigned"] else "Moved"
        await interaction.followup.send(
            f"✅ {verb} {user.mention} to **{result['team_name']}** "
            f"in **{result['division_name']}**.",
            ephemeral=True,
        )
        log.info(
            "assign: server=%s user=%s → team=%s division=%s by %s",
            server_id, user.id, team, division_name, actor_name,
        )

    # ------------------------------------------------------------------
    # /driver unassign
    # ------------------------------------------------------------------

    @driver.command(
        name="unassign",
        description="Remove a driver's placement from a specific division.",
    )
    @app_commands.describe(
        user="The Discord member to unassign.",
        division="Division tier number or name.",
    )
    @channel_guard
    @admin_only
    async def unassign(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        division: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        actor_id = interaction.user.id
        actor_name = str(interaction.user)

        season = await self.bot.season_service.get_active_season(server_id)  # type: ignore[attr-defined]
        if season is None:
            await interaction.followup.send(
                "⛔ No active season found.", ephemeral=True
            )
            return

        resolved = await self.bot.placement_service.resolve_division(  # type: ignore[attr-defined]
            season.id, division
        )
        if resolved is None:
            await interaction.followup.send(
                f"⛔ Division **{division}** not found in the active season.", ephemeral=True
            )
            return
        division_id, _division_name = resolved

        profile = await self.bot.driver_service.get_profile(  # type: ignore[attr-defined]
            server_id, str(user.id)
        )
        if profile is None:
            await interaction.followup.send(
                f"⛔ No driver profile found for {user.mention}.", ephemeral=True
            )
            return

        try:
            result = await self.bot.placement_service.unassign_driver(  # type: ignore[attr-defined]
                server_id=server_id,
                driver_profile_id=profile.id,
                division_id=division_id,
                season_id=season.id,
                acting_user_id=actor_id,
                acting_user_name=actor_name,
                guild=interaction.guild,
                discord_user_id=str(user.id),
            )
        except ValueError as exc:
            await interaction.followup.send(f"⛔ {exc}", ephemeral=True)
            return

        suffix = "" if result["has_remaining_assignments"] else " (now Unassigned)"
        await interaction.followup.send(
            f"✅ Removed {user.mention} from **{result['division_name']}**{suffix}.",
            ephemeral=True,
        )
        log.info(
            "unassign: server=%s user=%s from division=%s by %s",
            server_id, user.id, division, actor_name,
        )

    # ------------------------------------------------------------------
    # /driver sack
    # ------------------------------------------------------------------

    @driver.command(
        name="sack",
        description="Sack a driver: revoke all roles, clear assignments, revert to Not Signed Up.",
    )
    @app_commands.describe(
        user="The Discord member to sack.",
    )
    @channel_guard
    @admin_only
    async def sack(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        actor_id = interaction.user.id
        actor_name = str(interaction.user)

        season = await self.bot.season_service.get_active_season(server_id)  # type: ignore[attr-defined]

        profile = await self.bot.driver_service.get_profile(  # type: ignore[attr-defined]
            server_id, str(user.id)
        )
        if profile is None:
            await interaction.followup.send(
                f"⛔ No driver profile found for {user.mention}.", ephemeral=True
            )
            return

        try:
            await self.bot.placement_service.sack_driver(  # type: ignore[attr-defined]
                server_id=server_id,
                driver_profile_id=profile.id,
                season_id=season.id if season is not None else None,
                acting_user_id=actor_id,
                acting_user_name=actor_name,
                guild=interaction.guild,
                discord_user_id=str(user.id),
            )
        except ValueError as exc:
            await interaction.followup.send(f"⛔ {exc}", ephemeral=True)
            return

        await interaction.followup.send(
            f"✅ {user.mention} has been sacked. All roles and season assignments removed.",
            ephemeral=True,
        )
        log.info(
            "sack: server=%s user=%s by %s",
            server_id, user.id, actor_name,
        )
