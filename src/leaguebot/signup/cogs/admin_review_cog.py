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

from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.utils.channel_guard import is_league_manager
from leaguebot.core.utils.league_bot import LeagueBot, bot_of
from leaguebot.core.utils.league_server import CallbackButton, Handler, LeagueView, channel_id_of, guild_of, is_foreign_guild

log = logging.getLogger(__name__)

# Maps (channel_id, admin_user_id) → pending action context.
# Used to capture the admin's reason message before executing the action.
_PENDING_REASONS: dict[tuple[int, int], dict] = {}


async def _may_review_signup(interaction: discord.Interaction) -> bool:
    """Whether the presser holds the league manager tier, or better.

    This panel is posted publicly into the driver's own signup channel, which the driver
    themselves can read — so this check is the only thing standing between a driver and
    approving their own signup. It is not a formality.

    It used to admit Discord's Manage Guild permission, a level the two-tier model has no
    room for, and it read the interaction role by hand. Both are now one question asked of
    `is_league_manager`, so the button and the commands agree by construction rather than by
    two implementations happening to match.

    The bare `except` is kept deliberately: a config-service failure must not become a
    silent *grant*, so it falls through to False.
    """
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False
    bot = bot_of(interaction)
    try:
        server_cfg = await bot.config_service.get_server_config()
    except Exception:
        return False
    if server_cfg is None:
        return False
    return is_league_manager(server_cfg, interaction.user)


class AdminReviewView(LeagueView):
    """Approve / Request Changes / Reject buttons for admin signup review (T031).

    Restricted to the league manager tier — the interaction role, or the league admin role.
    First action wins; subsequent interactions receive an ephemeral error.
    FR-039, A-004.
    """

    def __init__(self, discord_user_id: str | None = None, bot: LeagueBot | None = None) -> None:
        super().__init__(timeout=None)
        self._discord_user_id = discord_user_id
        self._bot = bot

    async def _resolve(self, interaction: discord.Interaction):
        """Return (bot, discord_user_id) resolving from channel when not stored."""
        _bot = self._bot or bot_of(interaction)
        _user_id = self._discord_user_id
        if _user_id is None:
            wizard = await _bot.wizard_service.get_wizard_by_channel(
                channel_id_of(interaction)
            )
            _user_id = wizard.discord_user_id if wizard else None
        return _bot, _user_id

    async def _guard(self, interaction: discord.Interaction):
        """Check permissions and race-condition guard.  Returns (True, bot, user_id) to proceed."""
        if not await _may_review_signup(interaction):
            await interaction.response.send_message(
                "⛔ Insufficient permissions.", ephemeral=True
            )
            return False, None, None
        _bot, _user_id = await self._resolve(interaction)
        if _user_id is None:
            await interaction.response.send_message(
                "⛔ Could not identify driver for this signup.", ephemeral=True
            )
            return False, None, None
        # Race-condition guard: driver must still be in PENDING_ADMIN_APPROVAL
        profile = await _bot.driver_service.get_profile(
            _user_id
        )
        if profile is None or profile.current_state != DriverState.PENDING_ADMIN_APPROVAL:
            await interaction.response.send_message(
                "⛔ This signup has already been actioned.", ephemeral=True
            )
            return False, None, None
        return True, _bot, _user_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="admin_approve")
    async def approve_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        ok, _bot, _user_id = await self._guard(interaction)
        if not ok:
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.approve_signup(
            _user_id, interaction.guild, interaction.user
        )
        await interaction.followup.send("✅ Signup approved.", ephemeral=True)

    @discord.ui.button(label="Request Changes", style=discord.ButtonStyle.secondary, custom_id="admin_request_changes")
    async def request_changes_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        ok, _bot, _user_id = await self._guard(interaction)
        if not ok:
            return
        await interaction.response.defer(ephemeral=True)
        _PENDING_REASONS[(channel_id_of(interaction), interaction.user.id)] = {
            "action": "request_changes",
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
        ok, _bot, _user_id = await self._guard(interaction)
        if not ok:
            return
        await interaction.response.defer(ephemeral=True)
        _PENDING_REASONS[(channel_id_of(interaction), interaction.user.id)] = {
            "action": "reject",
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


class CorrectionParameterView(LeagueView):
    """One button per collectable wizard parameter; admin selects which to re-collect (T035).

    Restricted to the league manager tier or above, through `_may_review_signup`.
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

    def __init__(self, discord_user_id: str | None = None, bot: LeagueBot | None = None) -> None:
        super().__init__(timeout=None)  # persistent — logical timeout enforced by asyncio task
        self._discord_user_id = discord_user_id
        self._bot = bot

        for label, param_key in self._PARAMETERS:
            def make_callback(p: str) -> Handler:
                async def callback(inter: discord.Interaction) -> None:
                    if not await _may_review_signup(inter):
                        await inter.response.send_message(
                            "⛔ Insufficient permissions.", ephemeral=True
                        )
                        return
                    _bot = self._bot or bot_of(inter)
                    _user_id = self._discord_user_id
                    if _user_id is None:
                        wizard = await _bot.wizard_service.get_wizard_by_channel(
                            channel_id_of(inter)
                        )
                        _user_id = wizard.discord_user_id if wizard else None
                    if _user_id is None:
                        await inter.response.send_message(
                            "⛔ Could not identify driver for this correction.", ephemeral=True
                        )
                        return
                    await inter.response.defer(ephemeral=True)
                    await _bot.wizard_service.select_correction_parameter(
                        _user_id, p, guild_of(inter)
                    )
                    await inter.followup.send(
                        f"✅ Re-collecting **{p.replace('_', ' ')}**.", ephemeral=True
                    )
                return callback

            btn = CallbackButton(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"correct_{param_key}",
                on_press=make_callback(param_key),
            )
            self.add_item(btn)


class AdminReviewCog(commands.Cog):
    """Cog that holds the admin review views for signup approvals."""

    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Capture the admin's reason message for Request Changes / Reject."""
        if message.author.bot or not message.guild:
            return
        if await is_foreign_guild(self.bot, message.guild.id):
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
            await self.bot.wizard_service.request_changes(
                pending["discord_user_id"], pending["guild"], pending["actor"], reason=reason,
            )
            await followup.send("✅ Correction requested.", ephemeral=True)
        elif action == "reject":
            await self.bot.wizard_service.reject_signup(
                pending["discord_user_id"],
                pending["guild"], pending["actor"], reason=reason,
            )
            await followup.send("✅ Signup rejected.", ephemeral=True)


async def setup(bot: LeagueBot) -> None:
    await bot.add_cog(AdminReviewCog(bot))
