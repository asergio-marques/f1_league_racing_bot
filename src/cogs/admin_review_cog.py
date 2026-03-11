"""AdminReviewCog — admin signup review panel (Approve / Request Changes / Reject).

Views and their Discord interaction callbacks are implemented here.
The heavy state-machine logic is delegated to WizardService.

T031: AdminReviewView (Approve, Request Changes, Reject buttons)
T035: CorrectionParameterView (one button per collectable parameter)
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from models.driver_profile import DriverState

log = logging.getLogger(__name__)


async def _is_tier2_or_admin(interaction: discord.Interaction) -> bool:
    """Return True if the user has tier-2 role or Manage Guild permission."""
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False
    member = interaction.user
    if member.guild_permissions.manage_guild:
        return True
    bot = interaction.client  # type: ignore[attr-defined]
    try:
        server_cfg = await bot.config_service.get_server_config(interaction.guild.id)  # type: ignore[attr-defined]
        if server_cfg and server_cfg.interaction_role_id:
            role = interaction.guild.get_role(server_cfg.interaction_role_id)
            if role is not None and role in member.roles:
                return True
    except Exception:
        pass
    return False


class AdminReviewView(discord.ui.View):
    """Approve / Request Changes / Reject buttons for admin signup review (T031).

    Restricted to tier-2 role or Manage Guild permission.
    First action wins; subsequent interactions receive an ephemeral error.
    FR-039, A-004.
    """

    def __init__(self, server_id: int, discord_user_id: str, bot: commands.Bot) -> None:
        super().__init__(timeout=None)
        self._server_id = server_id
        self._discord_user_id = discord_user_id
        self._bot = bot

    async def _guard(self, interaction: discord.Interaction) -> bool:
        """Check permissions and race-condition guard.  Returns True to proceed."""
        if not await _is_tier2_or_admin(interaction):
            await interaction.response.send_message(
                "⛔ Insufficient permissions.", ephemeral=True
            )
            return False
        # Race-condition guard: driver must still be in PENDING_ADMIN_APPROVAL
        profile = await self._bot.driver_service.get_profile(  # type: ignore[attr-defined]
            self._server_id, self._discord_user_id
        )
        if profile is None or profile.driver_state != DriverState.PENDING_ADMIN_APPROVAL:
            await interaction.response.send_message(
                "⛔ This signup has already been actioned.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self._guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        await self._bot.wizard_service.approve_signup(  # type: ignore[attr-defined]
            self._server_id, self._discord_user_id, interaction.guild, interaction.user
        )
        await interaction.followup.send("✅ Signup approved.", ephemeral=True)

    @discord.ui.button(label="Request Changes", style=discord.ButtonStyle.secondary)
    async def request_changes_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self._guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        await self._bot.wizard_service.request_changes(  # type: ignore[attr-defined]
            self._server_id, self._discord_user_id, interaction.guild, interaction.user
        )
        await interaction.followup.send("✅ Correction requested.", ephemeral=True)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self._guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        await self._bot.wizard_service.reject_signup(  # type: ignore[attr-defined]
            self._server_id, self._discord_user_id, interaction.guild, interaction.user
        )
        await interaction.followup.send("✅ Signup rejected.", ephemeral=True)


class CorrectionParameterView(discord.ui.View):
    """One button per collectable wizard parameter; admin selects which to re-collect (T035).

    Restricted to tier-2 role or Manage Guild permission.
    Calls WizardService.select_correction_parameter() with the chosen parameter label.
    FR-042.
    """

    _PARAMETERS = [
        ("Nationality",         "nationality"),
        ("Platform",            "platform"),
        ("Platform ID",         "platform_id"),
        ("Availability",        "availability"),
        ("Driver Type",         "driver_type"),
        ("Preferred Teams",     "preferred_teams"),
        ("Preferred Teammate",  "preferred_teammate"),
        ("Lap Times",           "lap_times"),
        ("Notes",               "notes"),
    ]

    def __init__(self, server_id: int, discord_user_id: str, bot: commands.Bot) -> None:
        super().__init__(timeout=300)
        self._server_id = server_id
        self._discord_user_id = discord_user_id
        self._bot = bot

        for label, param_key in self._PARAMETERS:
            btn: discord.ui.Button = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"correct_{param_key}",
            )

            def make_callback(p: str) -> ...:  # type: ignore[return]
                async def callback(inter: discord.Interaction) -> None:
                    if not await _is_tier2_or_admin(inter):
                        await inter.response.send_message(
                            "⛔ Insufficient permissions.", ephemeral=True
                        )
                        return
                    await inter.response.defer(ephemeral=True)
                    self.stop()
                    await self._bot.wizard_service.select_correction_parameter(  # type: ignore[attr-defined]
                        self._server_id, self._discord_user_id, p, inter.guild
                    )
                    await inter.followup.send(
                        f"✅ Re-collecting **{p.replace('_', ' ')}**.", ephemeral=True
                    )
                return callback

            btn.callback = make_callback(param_key)  # type: ignore[assignment]
            self.add_item(btn)


class AdminReviewCog(commands.Cog):
    """Cog that holds the admin review views for signup approvals."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminReviewCog(bot))
