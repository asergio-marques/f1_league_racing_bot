"""SignupCog — /signup command group for managing the signup module.

Commands:
  /signup config channel  <channel>           — set signup channel
  /signup config roles    <base> <signed_up>  — set roles
  /signup config view                         — view current config
  /signup nationality toggle                  — toggle nationality requirement
  /signup time-type toggle                    — cycle time type setting
  /signup time-image toggle                   — toggle time image requirement
  /signup time-slot add   <day> <time>        — add availability slot
  /signup time-slot remove <slot_id>          — remove slot by sequence ID
  /signup time-slot list                      — list all slots
  /signup open [track_ids]                    — open signup window
  /signup close                               — close signup window
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from cogs.module_cog import execute_forced_close
from db.database import get_connection
from models.driver_profile import DriverState
from models.signup_module import SignupModuleConfig, SignupModuleSettings
from models.track import TRACK_IDS
from utils.channel_guard import admin_only, channel_guard

log = logging.getLogger(__name__)

_DAY_CHOICES = [
    app_commands.Choice(name="Monday", value="1"),
    app_commands.Choice(name="Tuesday", value="2"),
    app_commands.Choice(name="Wednesday", value="3"),
    app_commands.Choice(name="Thursday", value="4"),
    app_commands.Choice(name="Friday", value="5"),
    app_commands.Choice(name="Saturday", value="6"),
    app_commands.Choice(name="Sunday", value="7"),
]

# Commands exempt from the signup-module-enabled check
_EXEMPT_COMMANDS = {"channel", "roles", "view"}

_MAX_SLOTS = 25


def _parse_time(raw: str) -> str | None:
    """Parse HH:MM or h:mm am/pm → normalised HH:MM. Returns None on failure."""
    raw = raw.strip()
    # 24h HH:MM
    m24 = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
    if m24:
        h, mn = int(m24.group(1)), int(m24.group(2))
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return f"{h:02d}:{mn:02d}"
        return None
    # 12h with am/pm
    m12 = re.fullmatch(r"(\d{1,2}):(\d{2})\s*(am|pm)", raw, re.IGNORECASE)
    if m12:
        h, mn, meridiem = int(m12.group(1)), int(m12.group(2)), m12.group(3).lower()
        if not (1 <= h <= 12 and 0 <= mn <= 59):
            return None
        if meridiem == "am":
            h = 0 if h == 12 else h
        else:
            h = 12 if h == 12 else h + 12
        return f"{h:02d}:{mn:02d}"
    return None


def _format_slots(slots: list) -> str:
    if not slots:
        return "No availability slots configured."
    lines = [f"**Availability Time Slots**"]
    for slot in slots:
        lines.append(f"#{slot.slot_sequence_id} — {slot.display_label}")
    return "\n".join(lines)


_MAX_TEAM_BUTTONS = 20  # upper bound for pteam_N stub handlers in registration mode


async def _resolve_view_context(
    interaction: discord.Interaction,
    stored_server_id: int | None,
    stored_user_id: str | None,
) -> tuple:
    """Return (bot, server_id, discord_user_id) for a persistent-view callback.

    When stored values are None (view registered for restart recovery via
    ``bot.add_view``), looks up the wizard by channel ID to identify the
    owning driver.
    """
    bot = interaction.client
    server_id: int = stored_server_id or interaction.guild_id  # type: ignore[assignment]
    if stored_user_id is not None:
        return bot, server_id, stored_user_id
    wizard = await bot.wizard_service.get_wizard_by_channel(  # type: ignore[attr-defined]
        server_id, interaction.channel_id
    )
    return bot, server_id, (wizard.discord_user_id if wizard else None)


class SignupButtonView(discord.ui.View):
    """Persistent signup button view (T016).

    Posted in the signup channel when signups are opened.  The button
    callback is fully implemented in T028 (wizard integration).
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Sign Up",
        style=discord.ButtonStyle.primary,
        custom_id="signup_button",
    )
    async def signup_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """T028: Check driver state then launch the signup wizard."""
        if not interaction.guild:
            return
        bot = interaction.client  # type: ignore[attr-defined]
        server_id: int = interaction.guild.id
        discord_user_id = str(interaction.user.id)

        profile = await bot.driver_service.get_profile(server_id, discord_user_id)  # type: ignore[attr-defined]
        if profile is not None and profile.driver_state != DriverState.NOT_SIGNED_UP:
            _IN_PROGRESS_STATES = {
                DriverState.PENDING_SIGNUP_COMPLETION,
                DriverState.PENDING_ADMIN_APPROVAL,
                DriverState.AWAITING_CORRECTION_PARAMETER,
                DriverState.PENDING_DRIVER_CORRECTION,
            }
            _APPROVED_STATES = {
                DriverState.UNASSIGNED,
                DriverState.ASSIGNED,
                DriverState.SEASON_BANNED,
                DriverState.LEAGUE_BANNED,
            }
            if profile.driver_state in _IN_PROGRESS_STATES:
                await interaction.response.send_message(
                    "⛔ You already have a signup in progress — "
                    "check your private wizard channel.",
                    ephemeral=True,
                )
            elif profile.driver_state in _APPROVED_STATES:
                await interaction.response.send_message(
                    "⛔ Your signup has already been approved. "
                    "You cannot sign up again.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "⛔ You are not eligible to sign up at this time.",
                    ephemeral=True,
                )
            return

        await interaction.response.defer(ephemeral=True)
        channel = await bot.wizard_service.start_wizard(interaction, server_id)  # type: ignore[attr-defined]
        if channel is None:
            await interaction.followup.send(
                "❌ Signup module is not configured. Contact an admin.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"✅ Your signup channel has been created: {channel.mention}",
            ephemeral=True,
        )


class ConfirmCloseView(discord.ui.View):
    """Confirmation dialog for closing signups with in-progress drivers (T018)."""

    def __init__(self, server_id: int, bot: commands.Bot) -> None:
        super().__init__(timeout=300)
        self._server_id = server_id
        self._bot = bot
        self.confirmed = False

    @discord.ui.button(label="Confirm Close", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.confirmed = True
        self.stop()
        await interaction.response.defer(ephemeral=True)
        await execute_forced_close(
            self._server_id, self._bot, audit_action="SIGNUP_FORCE_CLOSE"
        )
        await interaction.followup.send("✅ Signups force-closed.", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.stop()
        await interaction.response.send_message(
            "Action cancelled. Signups remain open.", ephemeral=True
        )

    async def on_timeout(self) -> None:
        pass


class WithdrawButtonView(discord.ui.View):
    """Withdrawal button view — visible throughout all in-wizard driver states (T027).

    Posted in the private wizard channel immediately after the channel is
    created.  The driver can press Withdraw at any point while in any
    in-wizard state.
    """

    def __init__(
        self,
        server_id: int | None = None,
        discord_user_id: str | None = None,
        bot: commands.Bot | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self._server_id = server_id
        self._discord_user_id = discord_user_id
        self._bot = bot

    @discord.ui.button(
        label="Cancel Signup",
        style=discord.ButtonStyle.danger,
        custom_id="withdraw_button",
    )
    async def withdraw_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """T041: Full implementation — verify user, call wizard_service.withdraw()."""
        _bot, _server_id, _user_id = await _resolve_view_context(
            interaction, self._server_id, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message(
                "⛔ This button is not for you.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.withdraw(  # type: ignore[attr-defined]
            _server_id, _user_id, interaction.guild
        )
        await interaction.followup.send(
            "✅ Your signup has been withdrawn.", ephemeral=True
        )


class NoNotesButtonView(discord.ui.View):
    """Step 9 view — 'No Notes' shortcut alongside Cancel Signup."""

    def __init__(
        self,
        server_id: int | None = None,
        discord_user_id: str | None = None,
        bot: commands.Bot | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self._server_id = server_id
        self._discord_user_id = discord_user_id
        self._bot = bot

    @discord.ui.button(
        label="No Notes",
        style=discord.ButtonStyle.secondary,
        custom_id="no_notes_button",
    )
    async def no_notes_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        _bot, _server_id, _user_id = await _resolve_view_context(
            interaction, self._server_id, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message(
                "⛔ This button is not for you.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.handle_no_notes(  # type: ignore[attr-defined]
            _server_id, _user_id, interaction.guild
        )

    @discord.ui.button(
        label="Cancel Signup",
        style=discord.ButtonStyle.danger,
        custom_id="no_notes_cancel_button",
    )
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        _bot, _server_id, _user_id = await _resolve_view_context(
            interaction, self._server_id, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message(
                "⛔ This button is not for you.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.withdraw(  # type: ignore[attr-defined]
            _server_id, _user_id, interaction.guild
        )
        await interaction.followup.send(
            "✅ Your signup has been withdrawn.", ephemeral=True
        )


class PlatformButtonView(discord.ui.View):
    """Step 2 — one button per platform, plus Cancel Signup."""

    def __init__(
        self,
        server_id: int | None = None,
        discord_user_id: str | None = None,
        bot: commands.Bot | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self._server_id = server_id
        self._discord_user_id = discord_user_id
        self._bot = bot

    async def _pick(self, interaction: discord.Interaction, platform: str) -> None:
        _bot, _server_id, _user_id = await _resolve_view_context(
            interaction, self._server_id, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message("⛔ This button is not for you.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.handle_platform_button(  # type: ignore[attr-defined]
            _server_id, _user_id, platform, interaction.guild
        )

    @discord.ui.button(label="Steam", style=discord.ButtonStyle.secondary, custom_id="plat_steam")
    async def steam(self, i: discord.Interaction, b: discord.ui.Button) -> None:
        await self._pick(i, "Steam")

    @discord.ui.button(label="EA", style=discord.ButtonStyle.secondary, custom_id="plat_ea")
    async def ea(self, i: discord.Interaction, b: discord.ui.Button) -> None:
        await self._pick(i, "EA")

    @discord.ui.button(label="Xbox", style=discord.ButtonStyle.secondary, custom_id="plat_xbox")
    async def xbox(self, i: discord.Interaction, b: discord.ui.Button) -> None:
        await self._pick(i, "Xbox")

    @discord.ui.button(label="PlayStation", style=discord.ButtonStyle.secondary, custom_id="plat_ps")
    async def playstation(self, i: discord.Interaction, b: discord.ui.Button) -> None:
        await self._pick(i, "PlayStation")

    @discord.ui.button(label="Cancel Signup", style=discord.ButtonStyle.danger, custom_id="plat_cancel")
    async def cancel(self, interaction: discord.Interaction, b: discord.ui.Button) -> None:
        _bot, _server_id, _user_id = await _resolve_view_context(
            interaction, self._server_id, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message("⛔ This button is not for you.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.withdraw(  # type: ignore[attr-defined]
            _server_id, _user_id, interaction.guild
        )
        await interaction.followup.send("✅ Your signup has been withdrawn.", ephemeral=True)


class DriverTypeButtonView(discord.ui.View):
    """Step 5 — Full-Time / Reserve buttons, plus Cancel Signup."""

    def __init__(
        self,
        server_id: int | None = None,
        discord_user_id: str | None = None,
        bot: commands.Bot | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self._server_id = server_id
        self._discord_user_id = discord_user_id
        self._bot = bot

    async def _pick(self, interaction: discord.Interaction, driver_type: str) -> None:
        _bot, _server_id, _user_id = await _resolve_view_context(
            interaction, self._server_id, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message("⛔ This button is not for you.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.handle_driver_type_button(  # type: ignore[attr-defined]
            _server_id, _user_id, driver_type, interaction.guild
        )

    @discord.ui.button(label="Full-Time Driver", style=discord.ButtonStyle.primary, custom_id="dtype_fulltime")
    async def full_time(self, i: discord.Interaction, b: discord.ui.Button) -> None:
        await self._pick(i, "Full-Time Driver")

    @discord.ui.button(label="Reserve Driver", style=discord.ButtonStyle.secondary, custom_id="dtype_reserve")
    async def reserve(self, i: discord.Interaction, b: discord.ui.Button) -> None:
        await self._pick(i, "Reserve Driver")

    @discord.ui.button(label="Cancel Signup", style=discord.ButtonStyle.danger, custom_id="dtype_cancel")
    async def cancel(self, interaction: discord.Interaction, b: discord.ui.Button) -> None:
        _bot, _server_id, _user_id = await _resolve_view_context(
            interaction, self._server_id, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message("⛔ This button is not for you.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.withdraw(  # type: ignore[attr-defined]
            _server_id, _user_id, interaction.guild
        )
        await interaction.followup.send("✅ Your signup has been withdrawn.", ephemeral=True)


class PreferredTeamsButtonView(discord.ui.View):
    """Step 6 — one button per available team, No Preference, and Cancel Signup."""

    def __init__(
        self,
        server_id: int | None = None,
        discord_user_id: str | None = None,
        bot: commands.Bot | None = None,
        team_names: list[str] | None = None,
        excluded: list[str] | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self._server_id = server_id
        self._discord_user_id = discord_user_id
        self._bot = bot

        if team_names is not None:
            available = [n for n in team_names if n not in (excluded or [])]
            for i, name in enumerate(available):
                btn: discord.ui.Button = discord.ui.Button(
                    label=name,
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"pteam_{i}",
                )
                btn.callback = self._make_team_callback(i)
                self.add_item(btn)
        else:
            # Registration-mode: create stub handlers for all possible team slots
            for i in range(_MAX_TEAM_BUTTONS):
                btn = discord.ui.Button(
                    label=str(i + 1),
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"pteam_{i}",
                )
                btn.callback = self._make_team_callback(i)
                self.add_item(btn)

        no_pref: discord.ui.Button = discord.ui.Button(
            label="No Preference",
            style=discord.ButtonStyle.secondary,
            custom_id="pteam_nopref",
        )
        no_pref.callback = self._no_preference_callback
        self.add_item(no_pref)

        cancel_btn: discord.ui.Button = discord.ui.Button(
            label="Cancel Signup",
            style=discord.ButtonStyle.danger,
            custom_id="pteam_cancel",
        )
        cancel_btn.callback = self._cancel_callback
        self.add_item(cancel_btn)

    def _make_team_callback(self, i: int):
        """Create callback for team button at index i.

        Always resolves team name dynamically from current wizard state so
        the correct team is selected even after a bot restart.
        """
        async def callback(interaction: discord.Interaction) -> None:
            _bot, _server_id, _user_id = await _resolve_view_context(
                interaction, self._server_id, self._discord_user_id
            )
            if _user_id is None or str(interaction.user.id) != _user_id:
                await interaction.response.send_message("⛔ This button is not for you.", ephemeral=True)
                return
            # Resolve team name by index from live wizard state
            wizard = await _bot.wizard_service.get_wizard_by_channel(  # type: ignore[attr-defined]
                _server_id, interaction.channel_id
            )
            if wizard is None or wizard.config_snapshot is None:
                await interaction.response.send_message("⛔ Wizard session not found.", ephemeral=True)
                return
            current_picks: list[str] = list(wizard.draft_answers.get("preferred_teams") or [])
            available = [t for t in wizard.config_snapshot.team_names if t not in current_picks]
            if i >= len(available):
                await interaction.response.send_message("⛔ That option is no longer available.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            await _bot.wizard_service.handle_preferred_teams_button(  # type: ignore[attr-defined]
                _server_id, _user_id, available[i], interaction.guild
            )
        return callback

    async def _no_preference_callback(self, interaction: discord.Interaction) -> None:
        _bot, _server_id, _user_id = await _resolve_view_context(
            interaction, self._server_id, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message("⛔ This button is not for you.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.handle_preferred_teams_button(  # type: ignore[attr-defined]
            _server_id, _user_id, None, interaction.guild
        )

    async def _cancel_callback(self, interaction: discord.Interaction) -> None:
        _bot, _server_id, _user_id = await _resolve_view_context(
            interaction, self._server_id, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message("⛔ This button is not for you.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.withdraw(  # type: ignore[attr-defined]
            _server_id, _user_id, interaction.guild
        )
        await interaction.followup.send("✅ Your signup has been withdrawn.", ephemeral=True)


class NoPreferenceTeammateView(discord.ui.View):
    """Step 7 — No Preference shortcut plus Cancel Signup."""

    def __init__(
        self,
        server_id: int | None = None,
        discord_user_id: str | None = None,
        bot: commands.Bot | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self._server_id = server_id
        self._discord_user_id = discord_user_id
        self._bot = bot

    @discord.ui.button(label="No Preference", style=discord.ButtonStyle.secondary, custom_id="tmmate_nopref")
    async def no_preference(self, interaction: discord.Interaction, b: discord.ui.Button) -> None:
        _bot, _server_id, _user_id = await _resolve_view_context(
            interaction, self._server_id, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message("⛔ This button is not for you.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.handle_no_preference_teammate(  # type: ignore[attr-defined]
            _server_id, _user_id, interaction.guild
        )

    @discord.ui.button(label="Cancel Signup", style=discord.ButtonStyle.danger, custom_id="tmmate_cancel")
    async def cancel(self, interaction: discord.Interaction, b: discord.ui.Button) -> None:
        _bot, _server_id, _user_id = await _resolve_view_context(
            interaction, self._server_id, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message("⛔ This button is not for you.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.withdraw(  # type: ignore[attr-defined]
            _server_id, _user_id, interaction.guild
        )
        await interaction.followup.send("✅ Your signup has been withdrawn.", ephemeral=True)


class SignupCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Gate all commands on signup module enabled, except config subcommands."""
        cmd = interaction.command
        if cmd and cmd.name in _EXEMPT_COMMANDS:
            return True
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        enabled = await self.bot.module_service.is_signup_enabled(server_id)
        if not enabled:
            await interaction.response.send_message(
                "⛔ Signup module is not enabled. Use `/module enable signup` first.",
                ephemeral=True,
            )
            return False
        return True

    # ── Wizard message listener ────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """T029: Route messages in wizard channels to the wizard state machine."""
        if message.author.bot or not message.guild:
            return
        wizard = await self.bot.wizard_service.get_wizard_by_channel(  # type: ignore[attr-defined]
            message.guild.id, message.channel.id
        )
        if wizard is None or wizard.discord_user_id != str(message.author.id):
            return
        await self.bot.wizard_service.handle_message(wizard, message)  # type: ignore[attr-defined]

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """T048: Clean up wizard state when a member leaves the server (FR-027)."""
        await self.bot.wizard_service.handle_member_remove(  # type: ignore[attr-defined]
            member.guild.id, str(member.id), member.guild
        )

    # ── /signup (root group) ───────────────────────────────────────────

    signup = app_commands.Group(
        name="signup",
        description="Manage the signup module.",
        default_permissions=None,
    )

    # ── /signup config ─────────────────────────────────────────────────

    config_group = app_commands.Group(
        name="config",
        description="Configure the signup module.",
        parent=signup,
    )

    @config_group.command(name="channel", description="Set the signup channel.")
    @app_commands.describe(channel="Channel for signup interactions")
    @channel_guard
    @admin_only
    async def config_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        server_id: int = interaction.guild_id  # type: ignore[assignment]

        # Check bot has send_messages + manage_channels on channel
        guild = interaction.guild
        assert guild is not None
        bot_member = guild.get_member(self.bot.user.id)  # type: ignore[union-attr]
        if bot_member:
            perms = channel.permissions_for(bot_member)
            if not (perms.send_messages and (perms.manage_channels or perms.manage_roles)):
                await interaction.response.send_message(
                    f"❌ Bot is missing required permissions on {channel.mention}.",
                    ephemeral=True,
                )
                return

        existing = await self.bot.signup_module_service.get_config(server_id)
        if existing:
            existing.signup_channel_id = channel.id
            await self.bot.signup_module_service.save_config(existing)
        else:
            new_cfg = SignupModuleConfig(
                server_id=server_id,
                signup_channel_id=channel.id,
                base_role_id=0,
                signed_up_role_id=0,
                signups_open=False,
                signup_button_message_id=None,
                selected_tracks=[],
            )
            await self.bot.signup_module_service.save_config(new_cfg)

        await interaction.response.send_message(
            f"✅ Signup channel set to {channel.mention}.", ephemeral=True
        )

    @config_group.command(name="roles", description="Set the signup roles.")
    @app_commands.describe(
        base_role="Role granted to all members eligible to sign up",
        signed_up_role="Role granted on successful signup completion",
    )
    @channel_guard
    @admin_only
    async def config_roles(
        self,
        interaction: discord.Interaction,
        base_role: discord.Role,
        signed_up_role: discord.Role,
    ) -> None:
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        existing = await self.bot.signup_module_service.get_config(server_id)
        if existing:
            existing.base_role_id = base_role.id
            existing.signed_up_role_id = signed_up_role.id
            await self.bot.signup_module_service.save_config(existing)
        else:
            new_cfg = SignupModuleConfig(
                server_id=server_id,
                signup_channel_id=0,
                base_role_id=base_role.id,
                signed_up_role_id=signed_up_role.id,
                signups_open=False,
                signup_button_message_id=None,
                selected_tracks=[],
            )
            await self.bot.signup_module_service.save_config(new_cfg)

        await interaction.response.send_message("✅ Signup roles configured.", ephemeral=True)

    @config_group.command(name="view", description="View current signup module configuration.")
    async def config_view(self, interaction: discord.Interaction) -> None:
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        cfg = await self.bot.signup_module_service.get_config(server_id)
        settings = await self.bot.signup_module_service.get_settings(server_id)

        guild = interaction.guild
        assert guild is not None

        embed = discord.Embed(title="Signup Module Configuration", color=discord.Color.blue())

        if cfg:
            ch = guild.get_channel(cfg.signup_channel_id)
            embed.add_field(name="Channel", value=ch.mention if ch else "*(not found)*", inline=False)
            br = guild.get_role(cfg.base_role_id)
            embed.add_field(name="Base Role", value=br.mention if br else "*(not found)*", inline=True)
            sr = guild.get_role(cfg.signed_up_role_id)
            embed.add_field(name="Signed-Up Role", value=sr.mention if sr else "*(not found)*", inline=True)
            embed.add_field(name="Signups Open", value="Yes" if cfg.signups_open else "No", inline=True)
        else:
            embed.add_field(name="Channel", value="Not set", inline=False)
            embed.add_field(name="Base Role", value="Not set", inline=True)
            embed.add_field(name="Signed-Up Role", value="Not set", inline=True)
            embed.add_field(name="Signups Open", value="No", inline=True)

        nat_val = "ON" if settings.nationality_required else "OFF"
        tt_val = "Time Trial" if settings.time_type == "TIME_TRIAL" else "Short Qualification"
        img_val = "ON" if settings.time_image_required else "OFF"
        embed.add_field(name="Nationality Required", value=nat_val, inline=True)
        embed.add_field(name="Time Type", value=tt_val, inline=True)
        embed.add_field(name="Time Image Required", value=img_val, inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /signup nationality toggle (T020) ──────────────────────────────

    @signup.command(name="nationality", description="Toggle whether nationality is required in signups.")
    @channel_guard
    @admin_only
    async def nationality(self, interaction: discord.Interaction) -> None:
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        settings = await self.bot.signup_module_service.get_settings(server_id)
        old_val = settings.nationality_required
        settings.nationality_required = not old_val
        await self.bot.signup_module_service.save_settings(settings)

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, NULL, 'SIGNUP_SETTINGS_CHANGE', ?, ?, ?)",
                (server_id, interaction.user.id, str(interaction.user),
                 json.dumps({"field": "nationality_required", "value": old_val}),
                 json.dumps({"field": "nationality_required", "value": settings.nationality_required}),
                 now),
            )
            await db.commit()

        state = "**ON**" if settings.nationality_required else "**OFF**"
        await interaction.response.send_message(
            f"✅ Nationality requirement: {state}.", ephemeral=True
        )

    # ── /signup time-type toggle (T021) ────────────────────────────────

    @signup.command(name="time-type", description="Toggle the time type setting (Time Trial / Short Qualification).")
    @channel_guard
    @admin_only
    async def time_type(self, interaction: discord.Interaction) -> None:
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        settings = await self.bot.signup_module_service.get_settings(server_id)
        old_val = settings.time_type
        settings.time_type = (
            "SHORT_QUALIFICATION" if old_val == "TIME_TRIAL" else "TIME_TRIAL"
        )
        await self.bot.signup_module_service.save_settings(settings)

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, NULL, 'SIGNUP_SETTINGS_CHANGE', ?, ?, ?)",
                (server_id, interaction.user.id, str(interaction.user),
                 json.dumps({"field": "time_type", "value": old_val}),
                 json.dumps({"field": "time_type", "value": settings.time_type}),
                 now),
            )
            await db.commit()

        label = "Time Trial" if settings.time_type == "TIME_TRIAL" else "Short Qualification"
        await interaction.response.send_message(
            f"✅ Time type: **{label}**.", ephemeral=True
        )

    # ── /signup time-image toggle (T022) ───────────────────────────────

    @signup.command(name="time-image", description="Toggle whether a time image is required in signups.")
    @channel_guard
    @admin_only
    async def time_image(self, interaction: discord.Interaction) -> None:
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        settings = await self.bot.signup_module_service.get_settings(server_id)
        old_val = settings.time_image_required
        settings.time_image_required = not old_val
        await self.bot.signup_module_service.save_settings(settings)

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, NULL, 'SIGNUP_SETTINGS_CHANGE', ?, ?, ?)",
                (server_id, interaction.user.id, str(interaction.user),
                 json.dumps({"field": "time_image_required", "value": old_val}),
                 json.dumps({"field": "time_image_required", "value": settings.time_image_required}),
                 now),
            )
            await db.commit()

        state = "**ON**" if settings.time_image_required else "**OFF**"
        await interaction.response.send_message(
            f"✅ Time image requirement: {state}.", ephemeral=True
        )

    # ── /signup time-slot (sub-group) ──────────────────────────────────

    time_slot_group = app_commands.Group(
        name="time-slot",
        description="Manage signup availability time slots.",
        parent=signup,
    )

    @time_slot_group.command(name="add", description="Add an availability time slot.")
    @app_commands.describe(day="Day of week", time="Time in HH:MM 24h or 12h format (e.g. 14:30 or 2:30pm)")
    @app_commands.choices(day=_DAY_CHOICES)
    @channel_guard
    @admin_only
    async def time_slot_add(
        self,
        interaction: discord.Interaction,
        day: app_commands.Choice[str],
        time: str,
    ) -> None:
        server_id: int = interaction.guild_id  # type: ignore[assignment]

        # Guard: signups must be closed
        if await self.bot.signup_module_service.get_window_state(server_id):
            await interaction.response.send_message(
                "❌ Slots cannot be modified while signups are open. Close signups first with `/signup close`.",
                ephemeral=True,
            )
            return

        # Guard: max slots
        existing_slots = await self.bot.signup_module_service.get_slots(server_id)
        if len(existing_slots) >= _MAX_SLOTS:
            await interaction.response.send_message(
                f"❌ Maximum of {_MAX_SLOTS} time slots reached.", ephemeral=True
            )
            return

        # Parse time
        normalized = _parse_time(time)
        if normalized is None:
            await interaction.response.send_message(
                f"❌ Could not parse time '{time}'. Use HH:MM 24h or 12h with am/pm.",
                ephemeral=True,
            )
            return

        day_int = int(day.value)
        try:
            await self.bot.signup_module_service.add_slot(server_id, day_int, normalized)
        except ValueError:
            await interaction.response.send_message(
                "❌ That time slot already exists.", ephemeral=True
            )
            return

        updated = await self.bot.signup_module_service.get_slots(server_id)
        new_slot = next((s for s in updated if s.day_of_week == day_int and s.time_hhmm == normalized), None)

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, NULL, 'SIGNUP_SLOT_ADD', '', ?, ?)",
                (server_id, interaction.user.id, str(interaction.user),
                 json.dumps({"day": day_int, "time": normalized,
                             "slot_id": new_slot.slot_sequence_id if new_slot else None}), now),
            )
            await db.commit()

        await interaction.response.send_message(
            _format_slots(updated), ephemeral=True
        )

    @time_slot_group.command(name="remove", description="Remove an availability time slot by its sequence ID.")
    @app_commands.describe(slot_id="Stable sequence ID shown in /signup time-slot list")
    @channel_guard
    @admin_only
    async def time_slot_remove(
        self, interaction: discord.Interaction, slot_id: int
    ) -> None:
        server_id: int = interaction.guild_id  # type: ignore[assignment]

        if await self.bot.signup_module_service.get_window_state(server_id):
            await interaction.response.send_message(
                "❌ Slots cannot be modified while signups are open.",
                ephemeral=True,
            )
            return

        slots = await self.bot.signup_module_service.get_slots(server_id)
        if not slots:
            await interaction.response.send_message(
                "❌ No slots configured.", ephemeral=True
            )
            return

        target = next((s for s in slots if s.slot_sequence_id == slot_id), None)
        if target is None:
            await interaction.response.send_message(
                f"❌ Slot #{slot_id} does not exist.", ephemeral=True
            )
            return

        await self.bot.signup_module_service.remove_slot_by_rank(server_id, slot_id)

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, NULL, 'SIGNUP_SLOT_REMOVE', ?, '', ?)",
                (server_id, interaction.user.id, str(interaction.user),
                 json.dumps({"slot_id": slot_id, "day": target.day_of_week,
                             "time": target.time_hhmm}), now),
            )
            await db.commit()

        updated = await self.bot.signup_module_service.get_slots(server_id)
        await interaction.response.send_message(
            _format_slots(updated), ephemeral=True
        )

    @time_slot_group.command(name="list", description="List all configured availability time slots.")
    async def time_slot_list(self, interaction: discord.Interaction) -> None:
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        slots = await self.bot.signup_module_service.get_slots(server_id)
        await interaction.response.send_message(
            _format_slots(slots), ephemeral=True
        )

    # ── /signup open (T017) ───────────────────────────────────────────

    @signup.command(name="open", description="Open the signup window.")
    @app_commands.describe(
        track_ids="Optional: space- or comma-separated track IDs (e.g. '01 03 12')"
    )
    @channel_guard
    @admin_only
    async def signup_open(
        self,
        interaction: discord.Interaction,
        track_ids: str | None = None,
    ) -> None:
        server_id: int = interaction.guild_id  # type: ignore[assignment]

        cfg = await self.bot.signup_module_service.get_config(server_id)
        if cfg is None:
            await interaction.response.send_message(
                "❌ Signup module is not configured.", ephemeral=True
            )
            return

        if cfg.signups_open:
            await interaction.response.send_message(
                "❌ Signups are already open.", ephemeral=True
            )
            return

        # Guard: active season required
        active_season = await self.bot.season_service.get_active_season(server_id)
        if active_season is None:
            await interaction.response.send_message(
                "❌ An approved season setup is required before opening signups.",
                ephemeral=True,
            )
            return

        # Guard: at least one slot configured
        slots = await self.bot.signup_module_service.get_slots(server_id)
        if not slots:
            await interaction.response.send_message(
                "❌ At least one availability time slot must be configured before opening signups.",
                ephemeral=True,
            )
            return

        # Parse track_ids
        track_list: list[str] = []
        if track_ids and track_ids.strip():
            parts = [t.strip() for t in re.split(r"[,\s]+", track_ids.strip()) if t.strip()]
            unknown = [t for t in parts if t not in TRACK_IDS]
            if unknown:
                bad = ", ".join(f"'{t}'" for t in unknown)
                await interaction.response.send_message(
                    f"❌ Unknown track ID(s): {bad}.", ephemeral=True
                )
                return
            track_list = parts

        await interaction.response.defer(ephemeral=True)

        # Build and post signup button + info message
        settings = await self.bot.signup_module_service.get_settings(server_id)
        guild = interaction.guild
        assert guild is not None
        signup_channel = guild.get_channel(cfg.signup_channel_id)
        if signup_channel is None or not isinstance(signup_channel, discord.TextChannel):
            await interaction.followup.send(
                "❌ Configured signup channel not found.", ephemeral=True
            )
            return

        # Delete any existing "signups closed" status message
        if cfg.signup_closed_message_id:
            try:
                old_msg = await signup_channel.fetch_message(cfg.signup_closed_message_id)
                await old_msg.delete()
            except discord.NotFound:
                pass
            except Exception:
                log.warning("signup_open: could not delete closed status message")

        if track_list:
            track_names = [TRACK_IDS[t] for t in track_list]
            tracks_display = "\n".join(f"• {n}" for n in track_names)
        else:
            tracks_display = "No tracks specified"

        tt_label = "Time Trial" if settings.time_type == "TIME_TRIAL" else "Short Qualification"
        img_label = "Required" if settings.time_image_required else "Not required"
        nat_label = "Required" if settings.nationality_required else "Not required"

        info_embed = discord.Embed(
            title="🏁 Driver Signups Are Open!",
            description=(
                f"**Available time slots:**\n"
                + "\n".join(f"• {s.display_label}" for s in slots)
                + f"\n\n**Tracks:**\n{tracks_display}"
                + f"\n\n**Time type:** {tt_label}"
                + f"\n**Time image proof:** {img_label}"
                + f"\n**Nationality:** {nat_label}"
            ),
            color=discord.Color.green(),
        )

        view = SignupButtonView()
        try:
            posted_msg = await signup_channel.send(embed=info_embed, view=view)
        except Exception as exc:
            await interaction.followup.send(
                f"❌ Failed to post signup message: {exc}", ephemeral=True
            )
            return

        await self.bot.signup_module_service.set_window_open(
            server_id, posted_msg.id, track_list
        )

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, NULL, 'SIGNUP_OPEN', '', ?, ?)",
                (server_id, interaction.user.id, str(interaction.user),
                 json.dumps({"track_ids": track_list}), now),
            )
            await db.commit()

        await interaction.followup.send(
            f"✅ Signups opened. Button posted in {signup_channel.mention}.",
            ephemeral=True,
        )

    # ── /signup close (T019) ──────────────────────────────────────────

    @signup.command(name="close", description="Close the signup window.")
    @channel_guard
    @admin_only
    async def signup_close(self, interaction: discord.Interaction) -> None:
        server_id: int = interaction.guild_id  # type: ignore[assignment]

        cfg = await self.bot.signup_module_service.get_config(server_id)
        if cfg is None or not cfg.signups_open:
            await interaction.response.send_message(
                "❌ Signups are not currently open.", ephemeral=True
            )
            return

        # Query in-progress drivers
        in_progress_states = (
            "PENDING_SIGNUP_COMPLETION",
            "PENDING_ADMIN_APPROVAL",
            "PENDING_DRIVER_CORRECTION",
        )
        async with get_connection(self.bot.db_path) as db:
            placeholders = ",".join("?" for _ in in_progress_states)
            cursor = await db.execute(
                f"SELECT discord_user_id FROM driver_profiles "
                f"WHERE server_id = ? AND current_state IN ({placeholders})",
                (server_id, *in_progress_states),
            )
            rows = await cursor.fetchall()

        if not rows:
            # No in-progress drivers — immediate close
            await interaction.response.defer(ephemeral=True)
            await execute_forced_close(server_id, self.bot, audit_action="SIGNUP_CLOSE")
            await interaction.followup.send("✅ Signups closed.", ephemeral=True)
            return

        # Present confirmation view
        driver_ids = [row["discord_user_id"] for row in rows]
        count = len(driver_ids)
        driver_list = "\n".join(f"• <@{uid}>" for uid in driver_ids[:10])
        if count > 10:
            driver_list += f"\n…and {count - 10} more"

        view = ConfirmCloseView(server_id, self.bot)
        await interaction.response.send_message(
            f"⚠️ **{count} driver(s) are currently in progress:**\n{driver_list}\n\n"
            "Closing signups will transition all in-progress drivers to **Not Signed Up**.\n"
            "Are you sure?",
            view=view,
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /signup unassigned
    # ------------------------------------------------------------------

    @signup.command(
        name="unassigned",
        description="List all Unassigned drivers, seeded by total lap time.",
    )
    @channel_guard
    @admin_only
    async def signup_unassigned(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        drivers = await self.bot.placement_service.get_unassigned_drivers_seeded(server_id)  # type: ignore[attr-defined]
        if not drivers:
            await interaction.followup.send(
                "No Unassigned drivers found.", ephemeral=True
            )
            return

        lines: list[str] = [f"**Unassigned Drivers — Seeded** ({len(drivers)} total)\n"]
        for d in drivers:
            preferred = ", ".join(d["preferred_teams"]) if d["preferred_teams"] else "—"
            teammate = d["preferred_teammate"] or "—"
            lines.append(
                f"**#{d['seed']}** <@{d['discord_user_id']}> (**{d['server_display_name']}**)\n"
                f"  Platform: {d['platform']} | Type: {d['driver_type']} | Lap total: {d['total_lap_fmt']}\n"
                f"  Teams: {preferred} | Teammate: {teammate}"
            )
            if d["notes"]:
                lines[-1] += f"\n  Notes: {d['notes']}"

        # Discord has a 2000-char limit; chunk if needed
        output = "\n\n".join(lines)
        if len(output) <= 1900:
            await interaction.followup.send(output, ephemeral=True)
        else:
            # Split into chunks of ~1900 chars
            chunk, chunks = "", []
            for line in lines:
                if len(chunk) + len(line) + 2 > 1900:
                    chunks.append(chunk)
                    chunk = line
                else:
                    chunk = f"{chunk}\n\n{line}" if chunk else line
            if chunk:
                chunks.append(chunk)
            for i, part in enumerate(chunks):
                if i == 0:
                    await interaction.followup.send(part, ephemeral=True)
                else:
                    await interaction.followup.send(part, ephemeral=True)
