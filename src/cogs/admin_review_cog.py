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

# Maps (channel_id, admin_user_id) → pending action context.
# Used to capture the admin's reason message before executing the action.
_PENDING_REASONS: dict[tuple[int, int], dict] = {}


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

    def __init__(self, server_id: int | None = None, discord_user_id: str | None = None, bot: commands.Bot | None = None) -> None:
        super().__init__(timeout=None)
        self._server_id = server_id
        self._discord_user_id = discord_user_id
        self._bot = bot

    async def _resolve(self, interaction: discord.Interaction):
        """Return (bot, server_id, discord_user_id) resolving from channel when not stored."""
        _bot = self._bot or interaction.client
        _server_id: int = self._server_id or interaction.guild_id  # type: ignore[assignment]
        _user_id = self._discord_user_id
        if _user_id is None:
            wizard = await _bot.wizard_service.get_wizard_by_channel(  # type: ignore[attr-defined]
                _server_id, interaction.channel_id
            )
            _user_id = wizard.discord_user_id if wizard else None
        return _bot, _server_id, _user_id

    async def _guard(self, interaction: discord.Interaction):
        """Check permissions and race-condition guard.  Returns (True, bot, server_id, user_id) to proceed."""
        if not await _is_tier2_or_admin(interaction):
            await interaction.response.send_message(
                "⛔ Insufficient permissions.", ephemeral=True
            )
            return False, None, None, None
        _bot, _server_id, _user_id = await self._resolve(interaction)
        if _user_id is None:
            await interaction.response.send_message(
                "⛔ Could not identify driver for this signup.", ephemeral=True
            )
            return False, None, None, None
        # Race-condition guard: driver must still be in PENDING_ADMIN_APPROVAL
        profile = await _bot.driver_service.get_profile(  # type: ignore[attr-defined]
            _server_id, _user_id
        )
        if profile is None or profile.current_state != DriverState.PENDING_ADMIN_APPROVAL:
            await interaction.response.send_message(
                "⛔ This signup has already been actioned.", ephemeral=True
            )
            return False, None, None, None
        return True, _bot, _server_id, _user_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="admin_approve")
    async def approve_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        ok, _bot, _server_id, _user_id = await self._guard(interaction)
        if not ok:
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.approve_signup(  # type: ignore[attr-defined]
            _server_id, _user_id, interaction.guild, interaction.user
        )
        await interaction.followup.send("✅ Signup approved.", ephemeral=True)

    @discord.ui.button(label="Request Changes", style=discord.ButtonStyle.secondary, custom_id="admin_request_changes")
    async def request_changes_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        ok, _bot, _server_id, _user_id = await self._guard(interaction)
        if not ok:
            return
        await interaction.response.defer(ephemeral=True)
        _PENDING_REASONS[(interaction.channel_id, interaction.user.id)] = {
            "action": "request_changes",
            "server_id": _server_id,
            "discord_user_id": _user_id,
            "actor": interaction.user,
            "guild": interaction.guild,
            "followup": interaction.followup,
        }
        await interaction.followup.send(
            "Please type the reason for requesting changes in this channel. "
            "Your message will be automatically deleted.",
            ephemeral=True,
        )

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, custom_id="admin_reject")
    async def reject_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        ok, _bot, _server_id, _user_id = await self._guard(interaction)
        if not ok:
            return
        await interaction.response.defer(ephemeral=True)
        _PENDING_REASONS[(interaction.channel_id, interaction.user.id)] = {
            "action": "reject",
            "server_id": _server_id,
            "discord_user_id": _user_id,
            "actor": interaction.user,
            "guild": interaction.guild,
            "followup": interaction.followup,
        }
        await interaction.followup.send(
            "Please type the reason for rejecting this signup in this channel. "
            "Your message will be automatically deleted.",
            ephemeral=True,
        )


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

    def __init__(self, server_id: int | None = None, discord_user_id: str | None = None, bot: commands.Bot | None = None) -> None:
        super().__init__(timeout=None)  # persistent — logical timeout enforced by asyncio task
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
                    _bot = self._bot or inter.client
                    _server_id: int = self._server_id or inter.guild_id  # type: ignore[assignment]
                    _user_id = self._discord_user_id
                    if _user_id is None:
                        wizard = await _bot.wizard_service.get_wizard_by_channel(  # type: ignore[attr-defined]
                            _server_id, inter.channel_id
                        )
                        _user_id = wizard.discord_user_id if wizard else None
                    if _user_id is None:
                        await inter.response.send_message(
                            "⛔ Could not identify driver for this correction.", ephemeral=True
                        )
                        return
                    await inter.response.defer(ephemeral=True)
                    await _bot.wizard_service.select_correction_parameter(  # type: ignore[attr-defined]
                        _server_id, _user_id, p, inter.guild
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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Capture the admin's reason message for Request Changes / Reject."""
        if message.author.bot or not message.guild:
            return
        key = (message.channel.id, message.author.id)
        pending = _PENDING_REASONS.pop(key, None)
        if pending is None:
            return

        reason = message.content.strip() or "No specific reason given."

        try:
            await message.delete()
        except discord.HTTPException:
            pass

        action = pending["action"]
        followup: discord.Webhook = pending["followup"]
        if action == "request_changes":
            await self.bot.wizard_service.request_changes(  # type: ignore[attr-defined]
                pending["server_id"], pending["discord_user_id"],
                pending["guild"], pending["actor"], reason=reason,
            )
            await followup.send("✅ Correction requested.", ephemeral=True)
        elif action == "reject":
            await self.bot.wizard_service.reject_signup(  # type: ignore[attr-defined]
                pending["server_id"], pending["discord_user_id"],
                pending["guild"], pending["actor"], reason=reason,
            )
            await followup.send("✅ Signup rejected.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminReviewCog(bot))
