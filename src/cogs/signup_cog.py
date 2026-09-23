"""SignupCog — /signup command group for managing the signup module.

Commands:
  /signup channel  <channel>                  — set signup channel
  /signup config view                         — view current config
  /signup nationality toggle                  — toggle nationality requirement
  /signup time-type toggle                    — cycle time type setting
  /signup time-image toggle                   — toggle time image requirement
  /signup time-slot add   <day> <time>        — add availability slot
  /signup time-slot remove <slot_id>          — remove slot by sequence ID
  /signup time-slot list                      — list all slots
  /signup open [track_ids] [close_time]       — open signup window
  /signup close                               — close signup window
  /signup close-time add    <close_time>      — arm an auto-close time
  /signup close-time cancel                   — clear the auto-close time
  /signup close-time modify <close_time>      — replace the armed auto-close time

The league's base role and driver role, which this module reads, are core's and are set by
`/bot base-role` and `/bot driver-role` (issue #276).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from cogs.module_cog import RETURNED_BY_CLOSE, execute_forced_close
from db.database import get_connection
from models.driver_profile import DriverState
from models.signup_module import SignupModuleConfig, SignupModuleSettings
from services import track_service
from utils.input_validator import parse_datetime
from utils.league_bot import LeagueBot, bot_of
from utils.time_parsing import parse_time_of_day
from utils.channel_guard import league_manager_only, league_role_faults
from utils.league_server import CallbackButton, LeagueView, channel_id_of, is_foreign_guild
from utils.message_builder import discord_ts

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

# Commands exempt from the signup-module-enabled check (config-view only; the
# new channel/role config commands require the module to already be enabled)
_EXEMPT_COMMANDS = {"view"}

_MAX_SLOTS = 25


#: How an unsettled signup that holds no seed is marked in `/signup unassigned list`.
_REVIEW_LABELS = {
    "PENDING_ADMIN_APPROVAL": "Awaiting approval",
    "AWAITING_CORRECTION_PARAMETER": "Awaiting approval",
    "PENDING_DRIVER_CORRECTION": "Correcting",
}


def _parse_time(raw: str) -> str | None:
    """Parse a time of day to a normalised ``HH:MM``. Returns None on failure.

    Delegates to the shared parser: a league that learns `7pm` works when naming the daily
    portrait-refresh time will type it here too, and two parsers would have disagreed. This
    accepts everything the previous local one did, and more besides.
    """
    return parse_time_of_day(raw)


def _parse_close_time(
    raw: str, *, now: datetime | None = None
) -> tuple[str | None, str | None]:
    """Parse an auto-close instant to a normalised ISO 8601 UTC string.

    Returns ``(iso, None)`` on success and ``(None, message)`` on failure, where the
    message is the refusal to send straight back to the manager.

    One parser, two entry points. `/signup open close_time:` arms the timer as the window
    opens and `/signup close-time add` arms it afterwards, and the rule they hold to — ISO
    8601, a value with no timezone read as UTC, and the instant in the future — has to be
    one rule or a league gets two answers to the same question. `close_time:` was kept on
    `/signup open` deliberately when the close-time group was added, on the condition that
    the two share this function (decided 2026-09-15, issue #125).
    """
    moment = parse_datetime(raw)
    if moment is None:
        return None, (
            "❌ `close_time` is not a valid ISO 8601 datetime "
            "(e.g. `2025-06-15T20:00:00`)."
        )
    parsed = moment.replace(tzinfo=timezone.utc)
    if parsed <= (now if now is not None else datetime.now(timezone.utc)):
        return None, "❌ `close_time` must be a future datetime."
    return parsed.isoformat(), None


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
    stored_user_id: str | None,
) -> tuple:
    """Return (bot, discord_user_id) for a persistent-view callback.

    When the stored value is None (view registered for restart recovery via
    ``bot.add_view``), looks up the wizard by channel ID to identify the
    owning driver.
    """
    bot = bot_of(interaction)
    if stored_user_id is not None:
        return bot, stored_user_id
    wizard = await bot.wizard_service.get_wizard_by_channel(
        channel_id_of(interaction)
    )
    return bot, (wizard.discord_user_id if wizard else None)


#: The states a driver may stand in when they press Sign Up having already got a profile,
#: split by the refusal each earns. Between them they cover every state that is not
#: ``NOT_SIGNED_UP``, which is what lets the callback below use a bare ``else`` for the
#: approved arm: a state belonging to neither set gets a refusal rather than falling
#: through into the wizard and signing somebody up by accident. Add a state — a ban, when
#: the stewarding module brings one — and you must put it in one of these.
#:
#: ``APPROVED_STATES`` is deliberately not branched on: the ``else`` is what reads it, and
#: naming the set here is how that ``else`` says which states it believes it is catching.
#: It is **not** dead code — deleting it as unused takes the cover check in
#: ``tests/unit/test_signup_button_driver_states.py`` with it, which is the only thing
#: standing between a new driver state and a silent, wrongly granted signup. That test pins
#: both the cover and the disjointness.
IN_PROGRESS_STATES = {
    DriverState.PENDING_SIGNUP_COMPLETION,
    DriverState.PENDING_ADMIN_APPROVAL,
    DriverState.AWAITING_CORRECTION_PARAMETER,
    DriverState.PENDING_DRIVER_CORRECTION,
}
APPROVED_STATES = {
    DriverState.UNASSIGNED,
    DriverState.ASSIGNED,
}


class SignupButtonView(LeagueView):
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
        bot = bot_of(interaction)
        discord_user_id = str(interaction.user.id)

        # A real driver never joins a server that is under test: test mode and a real
        # league may not share one, and leaving test mode deletes every fake driver.
        server_cfg = await bot.config_service.get_server_config()
        if server_cfg is not None and server_cfg.test_mode_active:
            await interaction.response.send_message(
                "⛔ Signups are closed while this server is in test mode. "
                "Ask an admin to turn it off.",
                ephemeral=True,
            )
            return

        # A past account of a driver signs nobody up (issue #243). The signup would be kept
        # under an account the driver no longer uses, and the account belongs to that driver
        # already, so it may not start a profile of its own either.
        current = await bot.driver_service.current_account(discord_user_id)
        if current != discord_user_id:
            await interaction.response.send_message(
                f"⛔ This account is a past account of a driver in this league. Sign up from "
                f"<@{current}>, or ask a league manager to make this account the current one.",
                ephemeral=True,
            )
            return

        profile = await bot.driver_service.get_profile(discord_user_id)
        if profile is not None and profile.current_state != DriverState.NOT_SIGNED_UP:
            if profile.current_state in IN_PROGRESS_STATES:
                await interaction.response.send_message(
                    "⛔ You already have a signup in progress — "
                    "check your private wizard channel.",
                    ephemeral=True,
                )
            else:
                # Every remaining state is an approved one: the two sets above cover all
                # six states that are not NOT_SIGNED_UP. An `elif` here would drop a state
                # belonging to neither straight through into the wizard, signing somebody
                # up who should have been refused — so the approved arm takes what is left
                # and a new state gets a wrong message rather than a wrong signup.
                await interaction.response.send_message(
                    "⛔ Your signup has already been approved. "
                    "You cannot sign up again.",
                    ephemeral=True,
                )
            return

        await interaction.response.defer(ephemeral=True)
        channel = await bot.wizard_service.start_wizard(interaction)
        if channel is None:
            await interaction.followup.send(
                "❌ Signup module is not configured. Contact an admin.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"✅ Your signup channel has been created: {channel.mention}",
            ephemeral=True,
        )


#: How many drivers each group in the close confirmation names before it gives a count of the
#: rest. Thirty mid-signup would otherwise overflow the message Discord will accept.
_CLOSE_LIST_LIMIT = 10


def _close_confirmation(returned: list[str], kept: list[str]) -> str:
    """The confirmation ``/signup close`` asks for while anyone is mid-signup.

    *returned* and *kept* hold one line per driver: those the close will return to Not
    Signed Up, and those in review, whom it leaves alone. Each group is told only what the
    close does to it. A single list once warned that everyone in it would be dropped, when
    the close drops only the first group (issue #128). Each list is cut short on its own, so
    a long review queue cannot push a driver about to be dropped out of view.
    """

    def _listed(lines: list[str]) -> str:
        shown = "\n".join(f"• {line}" for line in lines[:_CLOSE_LIST_LIMIT])
        if len(lines) > _CLOSE_LIST_LIMIT:
            shown += f"\n…and {len(lines) - _CLOSE_LIST_LIMIT} more"
        return shown

    parts: list[str] = []
    if returned:
        parts.append(
            f"⚠️ **Closing signups will return {len(returned)} driver(s) to Not Signed Up.** "
            "They have not finished signing up and would have to start again:\n"
            + _listed(returned)
        )
        if kept:
            parts.append(
                f"**{len(kept)} driver(s) awaiting approval or a correction will keep their "
                "place.** You can still approve, reject or correct them once the window has "
                "closed:\n" + _listed(kept)
            )
        parts.append("Are you sure?")
    else:
        parts.append(
            f"**Nobody will lose their signup.** {len(kept)} driver(s) awaiting approval or a "
            "correction will keep their place, and you can still approve, reject or correct "
            "them once the window has closed:\n" + _listed(kept)
        )
        parts.append("Close signups?")
    return "\n\n".join(parts)


class ConfirmCloseView(LeagueView):
    """Confirmation dialog for closing signups with in-progress drivers (T018)."""

    def __init__(self, bot: LeagueBot) -> None:
        super().__init__(timeout=300)
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
            self._bot, audit_action="SIGNUP_FORCE_CLOSE"
        )
        await interaction.followup.send("✅ Signups force-closed.", ephemeral=True)
        await self._bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /signup close (force) | Success\n"
            f"  in_progress_drivers_discarded: true",
        )

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


class WithdrawButtonView(LeagueView):
    """Withdrawal button view — visible throughout all in-wizard driver states (T027).

    Posted in the private wizard channel immediately after the channel is
    created.  The driver can press Withdraw at any point while in any
    in-wizard state.
    """

    def __init__(
        self,
        discord_user_id: str | None = None,
        bot: LeagueBot | None = None,
    ) -> None:
        super().__init__(timeout=None)
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
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message(
                "⛔ This button is not for you.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.withdraw(
            _user_id, interaction.guild
        )
        await interaction.followup.send(
            "✅ Your signup has been withdrawn.", ephemeral=True
        )


class NoNotesButtonView(LeagueView):
    """Step 9 view — 'No Notes' shortcut alongside Cancel Signup."""

    def __init__(
        self,
        discord_user_id: str | None = None,
        bot: LeagueBot | None = None,
    ) -> None:
        super().__init__(timeout=None)
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
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message(
                "⛔ This button is not for you.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.handle_no_notes(
            _user_id, interaction.guild
        )

    @discord.ui.button(
        label="Cancel Signup",
        style=discord.ButtonStyle.danger,
        custom_id="no_notes_cancel_button",
    )
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message(
                "⛔ This button is not for you.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.withdraw(
            _user_id, interaction.guild
        )
        await interaction.followup.send(
            "✅ Your signup has been withdrawn.", ephemeral=True
        )


class PlatformButtonView(LeagueView):
    """Step 2 — one button per platform, plus Cancel Signup."""

    def __init__(
        self,
        discord_user_id: str | None = None,
        bot: LeagueBot | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self._discord_user_id = discord_user_id
        self._bot = bot

    async def _pick(self, interaction: discord.Interaction, platform: str) -> None:
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message("⛔ This button is not for you.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.handle_platform_button(
            _user_id, platform, interaction.guild
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
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message("⛔ This button is not for you.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.withdraw(
            _user_id, interaction.guild
        )
        await interaction.followup.send("✅ Your signup has been withdrawn.", ephemeral=True)


class DriverTypeButtonView(LeagueView):
    """Step 5 — Full-Time / Reserve buttons, plus Cancel Signup."""

    def __init__(
        self,
        discord_user_id: str | None = None,
        bot: LeagueBot | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self._discord_user_id = discord_user_id
        self._bot = bot

    async def _pick(self, interaction: discord.Interaction, driver_type: str) -> None:
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message("⛔ This button is not for you.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.handle_driver_type_button(
            _user_id, driver_type, interaction.guild
        )

    @discord.ui.button(label="Full-Time Driver", style=discord.ButtonStyle.primary, custom_id="dtype_fulltime")
    async def full_time(self, i: discord.Interaction, b: discord.ui.Button) -> None:
        await self._pick(i, "Full-Time Driver")

    @discord.ui.button(label="Reserve Driver", style=discord.ButtonStyle.secondary, custom_id="dtype_reserve")
    async def reserve(self, i: discord.Interaction, b: discord.ui.Button) -> None:
        await self._pick(i, "Reserve Driver")

    @discord.ui.button(label="Cancel Signup", style=discord.ButtonStyle.danger, custom_id="dtype_cancel")
    async def cancel(self, interaction: discord.Interaction, b: discord.ui.Button) -> None:
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message("⛔ This button is not for you.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.withdraw(
            _user_id, interaction.guild
        )
        await interaction.followup.send("✅ Your signup has been withdrawn.", ephemeral=True)


class PreferredTeamsButtonView(LeagueView):
    """Step 6 — one button per available team, No Preference, and Cancel Signup."""

    def __init__(
        self,
        discord_user_id: str | None = None,
        bot: LeagueBot | None = None,
        team_names: list[str] | None = None,
        excluded: list[str] | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self._discord_user_id = discord_user_id
        self._bot = bot

        if team_names is not None:
            available = [n for n in team_names if n not in (excluded or [])]
            for i, name in enumerate(available):
                btn = CallbackButton(
                    label=name,
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"pteam_{i}",
                    on_press=self._make_team_callback(i),
                )
                self.add_item(btn)
        else:
            # Registration-mode: create stub handlers for all possible team slots
            for i in range(_MAX_TEAM_BUTTONS):
                btn = CallbackButton(
                    label=str(i + 1),
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"pteam_{i}",
                    on_press=self._make_team_callback(i),
                )
                self.add_item(btn)

        no_pref = CallbackButton(
            label="No Preference",
            style=discord.ButtonStyle.secondary,
            custom_id="pteam_nopref",
            on_press=self._no_preference_callback,
        )
        self.add_item(no_pref)

        cancel_btn = CallbackButton(
            label="Cancel Signup",
            style=discord.ButtonStyle.danger,
            custom_id="pteam_cancel",
            on_press=self._cancel_callback,
        )
        self.add_item(cancel_btn)

    def _make_team_callback(self, i: int):
        """Create callback for team button at index i.

        Always resolves team name dynamically from current wizard state so
        the correct team is selected even after a bot restart.
        """
        async def callback(interaction: discord.Interaction) -> None:
            _bot, _user_id = await _resolve_view_context(
                interaction, self._discord_user_id
            )
            if _user_id is None or str(interaction.user.id) != _user_id:
                await interaction.response.send_message("⛔ This button is not for you.", ephemeral=True)
                return
            # Resolve team name by index from live wizard state
            wizard = await _bot.wizard_service.get_wizard_by_channel(
                interaction.channel_id
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
            await _bot.wizard_service.handle_preferred_teams_button(
                _user_id, available[i], interaction.guild
            )
        return callback

    async def _no_preference_callback(self, interaction: discord.Interaction) -> None:
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message("⛔ This button is not for you.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.handle_preferred_teams_button(
            _user_id, None, interaction.guild
        )

    async def _cancel_callback(self, interaction: discord.Interaction) -> None:
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message("⛔ This button is not for you.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.withdraw(
            _user_id, interaction.guild
        )
        await interaction.followup.send("✅ Your signup has been withdrawn.", ephemeral=True)


class NoPreferenceTeammateView(LeagueView):
    """Step 7 — No Preference shortcut plus Cancel Signup."""

    def __init__(
        self,
        discord_user_id: str | None = None,
        bot: LeagueBot | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self._discord_user_id = discord_user_id
        self._bot = bot

    @discord.ui.button(label="No Preference", style=discord.ButtonStyle.secondary, custom_id="tmmate_nopref")
    async def no_preference(self, interaction: discord.Interaction, b: discord.ui.Button) -> None:
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message("⛔ This button is not for you.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.handle_no_preference_teammate(
            _user_id, interaction.guild
        )

    @discord.ui.button(label="Cancel Signup", style=discord.ButtonStyle.danger, custom_id="tmmate_cancel")
    async def cancel(self, interaction: discord.Interaction, b: discord.ui.Button) -> None:
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await interaction.response.send_message("⛔ This button is not for you.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _bot.wizard_service.withdraw(
            _user_id, interaction.guild
        )
        await interaction.followup.send("✅ Your signup has been withdrawn.", ephemeral=True)


class SignupCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Gate all commands on signup module enabled, except config subcommands."""
        cmd = interaction.command
        if cmd and cmd.name in _EXEMPT_COMMANDS:
            return True
        enabled = await self.bot.module_service.is_signup_enabled()
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
        if await is_foreign_guild(self.bot, message.guild.id):
            return
        wizard = await self.bot.wizard_service.get_wizard_by_channel(
            message.channel.id
        )
        if wizard is None or wizard.discord_user_id != str(message.author.id):
            return
        await self.bot.wizard_service.handle_message(wizard, message)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """T048: Clean up wizard state when a member leaves the server (FR-027).

        Also posts a log notification for UNASSIGNED and ASSIGNED drivers who
        leave without going through the wizard path.

        Only a member leaving the league's own server counts: the same person leaving another
        server the bot sits in is nothing to the league.
        """
        if await is_foreign_guild(self.bot, member.guild.id):
            return
        await self.bot.wizard_service.handle_member_remove(
            str(member.id), member.guild
        )

        # Handle UNASSIGNED / ASSIGNED drivers (not covered by wizard_service)
        try:
            async with get_connection(self.bot.db_path) as db:
                cursor = await db.execute(
                    "SELECT current_state FROM driver_profiles "
                    "WHERE discord_user_id = ?",
                    (str(member.id),),
                )
                row = await cursor.fetchone()
            if row is None:
                return
            state = row["current_state"]
            if state not in ("UNASSIGNED", "ASSIGNED"):
                return
            # Fetch display name from signup_records
            try:
                async with get_connection(self.bot.db_path) as db:
                    cursor = await db.execute(
                        "SELECT server_display_name, discord_username "
                        "FROM signup_records WHERE discord_user_id = ? "
                        "ORDER BY id DESC LIMIT 1",
                        (str(member.id),),
                    )
                    rec = await cursor.fetchone()
                display_name = (
                    (rec["server_display_name"] or rec["discord_username"] or str(member.id))
                    if rec is not None
                    else (member.display_name or str(member.id))
                )
            except Exception:
                display_name = member.display_name or str(member.id)
            await self.bot.output_router.post_log(
                f"Driver left server: **{display_name}** (<@{member.id}>) | state: {state}",
            )
        except Exception:
            log.warning(
                "on_member_remove: failed to post log for %s/%s",
                member.guild.id, member.id,
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

    @config_group.command(name="view", description="View current signup module configuration.")
    @league_manager_only
    async def config_view(self, interaction: discord.Interaction) -> None:
        cfg = await self.bot.signup_module_service.get_config()
        settings = await self.bot.signup_module_service.get_settings()

        guild = interaction.guild
        assert guild is not None

        embed = discord.Embed(title="Signup Module Configuration", color=discord.Color.blue())

        # The two roles are the league's (issue #276), shown here because signups cannot
        # open without them, and shown whether or not the module is enabled.
        server_cfg = await self.bot.config_service.get_server_config()

        def _role_value(role_id: int | None, command: str) -> str:
            if role_id is None:
                return f"*(not configured)* — `{command}`"
            role = guild.get_role(role_id)
            return role.mention if role else "*(not found)*"

        if cfg:
            ch = guild.get_channel(cfg.signup_channel_id) if cfg.signup_channel_id else None
            ch_val = ch.mention if ch else ("*(not configured)*" if cfg.signup_channel_id is None else "*(not found)*")
            embed.add_field(name="Channel", value=ch_val, inline=False)
        else:
            embed.add_field(name="Channel", value="Not set", inline=False)
        embed.add_field(
            name="Base Role",
            value=_role_value(server_cfg.base_role_id if server_cfg else None, "/bot base-role"),
            inline=True,
        )
        embed.add_field(
            name="Driver Role",
            value=_role_value(
                server_cfg.driver_role_id if server_cfg else None, "/bot driver-role"
            ),
            inline=True,
        )
        embed.add_field(
            name="Signups Open", value="Yes" if cfg and cfg.signups_open else "No", inline=True
        )

        nat_val = "ON" if settings.nationality_required else "OFF"
        tt_val = "Time Trial" if settings.time_type == "TIME_TRIAL" else "Short Qualification"
        img_val = "ON" if settings.time_image_required else "OFF"
        embed.add_field(name="Nationality Required", value=nat_val, inline=True)
        embed.add_field(name="Time Type", value=tt_val, inline=True)
        embed.add_field(name="Time Image Required", value=img_val, inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /signup channel (T011) ─────────────────────────────────────────

    @signup.command(name="channel", description="Set the signup channel and apply permission overwrites.")
    @app_commands.describe(channel="Channel for signup interactions")
    @league_manager_only
    async def signup_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        if await self._refuse_while_configuration_fixed(interaction, "/signup channel"):
            return
        guild = interaction.guild
        assert guild is not None

        cfg = await self.bot.signup_module_service.get_config()
        if cfg is None:
            await interaction.response.send_message(
                "❌ Signup module is not configured.", ephemeral=True
            )
            return

        # A channel does one job (decided 2026-09-06). This stood as a guard against the
        # bot interaction channel alone; every configurable channel of the server is
        # checked now, the interaction channel among them.
        from services.channel_registry_service import (
            ChannelUse,
            find_channel_use,
            refusal,
        )

        use = await find_channel_use(self.bot.db_path, channel.id)
        if use is not None:
            await interaction.response.send_message(
                refusal(
                    channel.mention, use, same_setting=(use == ChannelUse("signup"))
                ),
                ephemeral=True,
            )
            return

        # Check bot perms
        bot_user = self.bot.user
        bot_member = guild.get_member(bot_user.id) if bot_user is not None else None
        if bot_member:
            perms = channel.permissions_for(bot_member)
            if not (perms.manage_channels or perms.manage_roles):
                await interaction.response.send_message(
                    f"❌ Bot is missing `manage_roles` permission on {channel.mention}.",
                    ephemeral=True,
                )
                return

        await interaction.response.defer(ephemeral=True)
        old_channel_id = cfg.signup_channel_id

        # Revert bot-applied overwrites on old channel (if changing)
        if old_channel_id and old_channel_id != channel.id:
            old_channel = guild.get_channel(old_channel_id)
            if old_channel and isinstance(old_channel, discord.TextChannel):
                try:
                    await old_channel.edit(overwrites={})
                except Exception:
                    log.warning("signup_channel: could not revert overwrites on old channel %s", old_channel_id)

        # Apply overwrites to new channel. The server config is read here for the
        # interaction role; it used to be read further up, by the guard against reusing
        # the bot's own command channel, and this line was left orphaned when that guard
        # became the server-wide `find_channel_use` check.
        server_cfg = await self.bot.config_service.get_server_config()
        interaction_role = guild.get_role(server_cfg.interaction_role_id) if server_cfg else None
        # The league admin role gets the same sight of the channel as the interaction role.
        # A league admin holds the league manager tier within their own, so a channel opened
        # to one tier and not the other would show them a signup they may action and no way
        # to read it (issue #116). Skipped where the two are the same role, or where the
        # league has not set an admin role yet.
        admin_role = (
            guild.get_role(server_cfg.league_admin_role_id)
            if server_cfg and server_cfg.league_admin_role_id
            else None
        )
        # The base role is the league's, not this module's (issue #276).
        base_role = (
            guild.get_role(server_cfg.base_role_id)
            if server_cfg and server_cfg.base_role_id
            else None
        )
        overwrites: dict = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        if base_role:
            overwrites[base_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,
                use_application_commands=True,
            )
        for role in (interaction_role, admin_role):
            if role is not None:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True
                )
        try:
            await channel.edit(overwrites=overwrites)
        except Exception as exc:
            await interaction.followup.send(
                f"❌ Failed to apply channel permission overwrites: {exc}", ephemeral=True
            )
            return

        # Persist
        cfg.signup_channel_id = channel.id
        await self.bot.signup_module_service.save_config(cfg)

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, 'SIGNUP_CHANNEL_SET', ?, ?, ?)",
                (interaction.user.id, str(interaction.user),
                 json.dumps({"channel_id": old_channel_id}),
                 json.dumps({"channel_id": channel.id}), now),
            )
            await db.commit()

        await interaction.followup.send(
            f"✅ Signup channel set to {channel.mention}.", ephemeral=True
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /signup channel | Success\n"
            f"  channel: #{channel.name}",
        )

    # ── /signup nationality toggle (T020) ──────────────────────────────

    @signup.command(name="nationality", description="Toggle whether nationality is required in signups.")
    @league_manager_only
    async def nationality(self, interaction: discord.Interaction) -> None:
        if await self._refuse_while_configuration_fixed(interaction, "/signup nationality"):
            return
        settings = await self.bot.signup_module_service.get_settings()
        old_val = settings.nationality_required
        settings.nationality_required = not old_val
        await self.bot.signup_module_service.save_settings(settings)

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, 'SIGNUP_SETTINGS_CHANGE', ?, ?, ?)",
                (interaction.user.id, str(interaction.user),
                 json.dumps({"field": "nationality_required", "value": old_val}),
                 json.dumps({"field": "nationality_required", "value": settings.nationality_required}),
                 now),
            )
            await db.commit()

        state = "**ON**" if settings.nationality_required else "**OFF**"
        await interaction.response.send_message(
            f"✅ Nationality requirement: {state}.", ephemeral=True
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /signup nationality | Success\n"
            f"  nationality_required: {settings.nationality_required}",
        )

    # ── /signup time-type toggle (T021) ────────────────────────────────

    @signup.command(name="time-type", description="Toggle the time type setting (Time Trial / Short Qualification).")
    @league_manager_only
    async def time_type(self, interaction: discord.Interaction) -> None:
        if await self._refuse_while_configuration_fixed(interaction, "/signup time-type"):
            return
        settings = await self.bot.signup_module_service.get_settings()
        old_val = settings.time_type
        settings.time_type = (
            "SHORT_QUALIFICATION" if old_val == "TIME_TRIAL" else "TIME_TRIAL"
        )
        await self.bot.signup_module_service.save_settings(settings)

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, 'SIGNUP_SETTINGS_CHANGE', ?, ?, ?)",
                (interaction.user.id, str(interaction.user),
                 json.dumps({"field": "time_type", "value": old_val}),
                 json.dumps({"field": "time_type", "value": settings.time_type}),
                 now),
            )
            await db.commit()

        label = "Time Trial" if settings.time_type == "TIME_TRIAL" else "Short Qualification"
        await interaction.response.send_message(
            f"✅ Time type: **{label}**.", ephemeral=True
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /signup time-type | Success\n"
            f"  time_type: {settings.time_type}",
        )

    # ── /signup time-image toggle (T022) ───────────────────────────────

    @signup.command(name="time-image", description="Toggle whether a time image is required in signups.")
    @league_manager_only
    async def time_image(self, interaction: discord.Interaction) -> None:
        if await self._refuse_while_configuration_fixed(interaction, "/signup time-image"):
            return
        settings = await self.bot.signup_module_service.get_settings()
        old_val = settings.time_image_required
        settings.time_image_required = not old_val
        await self.bot.signup_module_service.save_settings(settings)

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, 'SIGNUP_SETTINGS_CHANGE', ?, ?, ?)",
                (interaction.user.id, str(interaction.user),
                 json.dumps({"field": "time_image_required", "value": old_val}),
                 json.dumps({"field": "time_image_required", "value": settings.time_image_required}),
                 now),
            )
            await db.commit()

        state = "**ON**" if settings.time_image_required else "**OFF**"
        await interaction.response.send_message(
            f"✅ Time image requirement: {state}.", ephemeral=True
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /signup time-image | Success\n"
            f"  time_image_required: {settings.time_image_required}",
        )

    # ── /signup time-slot (sub-group) ──────────────────────────────────

    time_slot_group = app_commands.Group(
        name="time-slot",
        description="Manage signup availability time slots.",
        parent=signup,
    )

    async def _refuse_while_configuration_fixed(
        self, interaction: discord.Interaction, command: str
    ) -> bool:
        """Refuse a change to the signup module's settings once a season has fixed them.

        The settings are free while the server holds no active season and while its season
        stands in Configuration; confirming that configuration fixes them until the season
        ends (issue #220). This replaces the older guards that blocked a slot change while
        the window was open or drivers awaited placement — both only ever happen inside a
        season whose configuration is already fixed.

        Returns True when the command replied and must stop.
        """
        from services.season_lifecycle_service import configuration_fixed

        season_number = await configuration_fixed(self.bot.db_path)
        if season_number is None:
            return False
        await interaction.response.send_message(
            f"❌ The signup module's settings are fixed for Season {season_number} now that "
            f"its configuration has been confirmed. `{command}` is available again once the "
            "season has ended, or while a new season is in configuration.",
            ephemeral=True,
        )
        return True

    @time_slot_group.command(name="add", description="Add an availability time slot.")
    @app_commands.describe(day="Day of week", time="Time in HH:MM 24h or 12h format (e.g. 14:30 or 2:30pm)")
    @app_commands.choices(day=_DAY_CHOICES)
    @league_manager_only
    async def time_slot_add(
        self,
        interaction: discord.Interaction,
        day: app_commands.Choice[str],
        time: str,
    ) -> None:

        if await self._refuse_while_configuration_fixed(interaction, "/signup time-slot add"):
            return

        # Guard: max slots
        existing_slots = await self.bot.signup_module_service.get_slots()
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
            await self.bot.signup_module_service.add_slot(day_int, normalized)
        except ValueError:
            await interaction.response.send_message(
                "❌ That time slot already exists.", ephemeral=True
            )
            return

        updated = await self.bot.signup_module_service.get_slots()
        new_slot = next((s for s in updated if s.day_of_week == day_int and s.time_hhmm == normalized), None)

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, 'SIGNUP_SLOT_ADD', '', ?, ?)",
                (interaction.user.id, str(interaction.user),
                 # The durable slot ID, not the display ordinal: an audit entry outlives
                 # the list it was written against.
                 json.dumps({"day": day_int, "time": normalized,
                             "slot_id": new_slot.slot_id if new_slot else None}), now),
            )
            await db.commit()

        await interaction.response.send_message(
            _format_slots(updated), ephemeral=True
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /signup time-slot add | Success\n"
            f"  day: {day.name}\n"
            f"  time: {normalized}",
        )

    @time_slot_group.command(name="remove", description="Remove an availability time slot by its sequence ID.")
    @app_commands.describe(slot_id="Stable sequence ID shown in /signup time-slot list")
    @league_manager_only
    async def time_slot_remove(
        self, interaction: discord.Interaction, slot_id: int
    ) -> None:

        if await self._refuse_while_configuration_fixed(interaction, "/signup time-slot remove"):
            return

        slots = await self.bot.signup_module_service.get_slots()
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

        await self.bot.signup_module_service.remove_slot_by_rank(slot_id)

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, 'SIGNUP_SLOT_REMOVE', ?, '', ?)",
                (interaction.user.id, str(interaction.user),
                 # The durable slot ID, not the display ordinal the manager typed.
                 json.dumps({"slot_id": target.slot_id, "day": target.day_of_week,
                             "time": target.time_hhmm}), now),
            )
            await db.commit()

        updated = await self.bot.signup_module_service.get_slots()
        await interaction.response.send_message(
            _format_slots(updated), ephemeral=True
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /signup time-slot remove | Success\n"
            f"  slot_id: {slot_id}\n"
            f"  slot: {target.display_label}",
        )

    @time_slot_group.command(name="list", description="List all configured availability time slots.")
    @league_manager_only
    async def time_slot_list(self, interaction: discord.Interaction) -> None:
        slots = await self.bot.signup_module_service.get_slots()
        await interaction.response.send_message(
            _format_slots(slots), ephemeral=True
        )

    # ── /signup close-time (sub-group) ─────────────────────────────────

    close_time_group = app_commands.Group(
        name="close-time",
        description="Manage the signup window's auto-close time.",
        parent=signup,
    )

    async def _close_time_context(
        self, interaction: discord.Interaction
    ) -> SignupModuleConfig | None:
        """Return the config for a close-time command, or reply and return None.

        All three close-time commands need an open signup window: `close_at` only means
        anything while one is running, and `set_window_closed` clears it, so a timer armed
        against a closed window could only ever fire a forced close on nothing.
        """
        cfg = await self.bot.signup_module_service.get_config()
        if cfg is None or not cfg.signups_open:
            await interaction.response.send_message(
                "❌ Signups are not currently open, so there is no auto-close time to "
                "manage. Set one when you open the window with "
                "`/signup open close_time:`.",
                ephemeral=True,
            )
            return None
        return cfg

    async def _record_close_time_change(
        self,
        interaction: discord.Interaction,
        *,
        change_type: str,
        old_value: str,
        new_value: str,
        log_line: str,
    ) -> None:
        """Write the audit row and the log line shared by all three close-time commands."""
        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, ?, ?, ?, ?)",
                (interaction.user.id, str(interaction.user),
                 change_type, old_value, new_value, now),
            )
            await db.commit()
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | {log_line}",
        )

    @close_time_group.command(
        name="add", description="Arm an auto-close time for the open signup window."
    )
    @app_commands.describe(
        close_time="Auto-close UTC datetime in ISO 8601 format (e.g. 2025-06-15T20:00:00)",
    )
    @league_manager_only
    async def close_time_add(
        self, interaction: discord.Interaction, close_time: str
    ) -> None:
        cfg = await self._close_time_context(interaction)
        if cfg is None:
            return

        if cfg.close_at is not None:
            armed = datetime.fromisoformat(cfg.close_at)
            await interaction.response.send_message(
                f"❌ Signups already auto-close at {discord_ts(armed)} "
                f"({discord_ts(armed, 'R')}). Use `/signup close-time modify` to change "
                "it, or `/signup close-time cancel` to clear it.",
                ephemeral=True,
            )
            return

        close_at_iso, close_error = _parse_close_time(close_time)
        if close_error is not None:
            await interaction.response.send_message(close_error, ephemeral=True)
            return
        assert close_at_iso is not None

        await self.bot.signup_module_service.set_close_at(close_at_iso)
        self.bot.scheduler_service.schedule_signup_close_timer(close_at_iso)

        armed = datetime.fromisoformat(close_at_iso)
        await interaction.response.send_message(
            f"✅ Signups will auto-close at {discord_ts(armed)} "
            f"({discord_ts(armed, 'R')}).",
            ephemeral=True,
        )
        await self._record_close_time_change(
            interaction,
            change_type="SIGNUP_CLOSE_TIME_ADD",
            old_value="",
            new_value=close_at_iso,
            log_line=f"/signup close-time add | Success\n  close_time: {close_at_iso}",
        )

    @close_time_group.command(
        name="cancel", description="Clear the auto-close time, leaving signups open."
    )
    @league_manager_only
    async def close_time_cancel(self, interaction: discord.Interaction) -> None:
        cfg = await self._close_time_context(interaction)
        if cfg is None:
            return

        if cfg.close_at is None:
            await interaction.response.send_message(
                "❌ No auto-close time is set. Signups stay open until you run "
                "`/signup close`.",
                ephemeral=True,
            )
            return

        previous = cfg.close_at
        self.bot.scheduler_service.cancel_signup_close_timer()
        await self.bot.signup_module_service.set_close_at(None)

        await interaction.response.send_message(
            "✅ Auto-close time cleared. Signups stay open until you close them with "
            "`/signup close`.",
            ephemeral=True,
        )
        await self._record_close_time_change(
            interaction,
            change_type="SIGNUP_CLOSE_TIME_CANCEL",
            old_value=previous,
            new_value="",
            log_line=f"/signup close-time cancel | Success\n  was: {previous}",
        )

    @close_time_group.command(
        name="modify", description="Replace the armed auto-close time with a new one."
    )
    @app_commands.describe(
        close_time="New auto-close UTC datetime in ISO 8601 format (e.g. 2025-06-15T20:00:00)",
    )
    @league_manager_only
    async def close_time_modify(
        self, interaction: discord.Interaction, close_time: str
    ) -> None:
        cfg = await self._close_time_context(interaction)
        if cfg is None:
            return

        if cfg.close_at is None:
            await interaction.response.send_message(
                "❌ No auto-close time is set, so there is nothing to change. Use "
                "`/signup close-time add` to arm one.",
                ephemeral=True,
            )
            return

        close_at_iso, close_error = _parse_close_time(close_time)
        if close_error is not None:
            await interaction.response.send_message(close_error, ephemeral=True)
            return
        assert close_at_iso is not None

        previous = cfg.close_at
        self.bot.scheduler_service.cancel_signup_close_timer()
        await self.bot.signup_module_service.set_close_at(close_at_iso)
        self.bot.scheduler_service.schedule_signup_close_timer(close_at_iso)

        armed = datetime.fromisoformat(close_at_iso)
        await interaction.response.send_message(
            f"✅ Signups will now auto-close at {discord_ts(armed)} "
            f"({discord_ts(armed, 'R')}).",
            ephemeral=True,
        )
        await self._record_close_time_change(
            interaction,
            change_type="SIGNUP_CLOSE_TIME_MODIFY",
            old_value=previous,
            new_value=close_at_iso,
            log_line=(
                "/signup close-time modify | Success\n"
                f"  was: {previous}\n  close_time: {close_at_iso}"
            ),
        )

    # ── /signup open (T017) ───────────────────────────────────────────

    @signup.command(name="open", description="Open the signup window.")
    @app_commands.describe(
        track_ids="Optional: space- or comma-separated track IDs (e.g. '1 3 12')",
        close_time="Optional: auto-close UTC datetime in ISO 8601 format (e.g. 2025-06-15T20:00:00)",
    )
    @league_manager_only
    async def signup_open(
        self,
        interaction: discord.Interaction,
        track_ids: str | None = None,
        close_time: str | None = None,
    ) -> None:

        # Refused under test mode, for the same reason the Sign Up button is: no real
        # driver may sign up while the server is under test, so a window opened now
        # would be one nobody could use.
        server_cfg = await self.bot.config_service.get_server_config()
        if server_cfg is not None and server_cfg.test_mode_active:
            await interaction.response.send_message(
                "⛔ Signups cannot be opened while test mode is active. "
                "Turn it off with `/test-mode toggle` first.",
                ephemeral=True,
            )
            return

        cfg = await self.bot.signup_module_service.get_config()
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

        # A window opens only while the season waits for one, or while it is being raced
        # with no window already run and unplaced (issue #220).
        from services.season_lifecycle_service import WINDOW_OPENS_FROM, live_season_stage

        live = await live_season_stage(self.bot.db_path)
        if live is None or live[1] not in WINDOW_OPENS_FROM:
            await interaction.response.send_message(
                "❌ Signups can only be opened while the season is waiting for its signup "
                "window, once its configuration is confirmed, or while it is ongoing with "
                "no placements left to confirm.",
                ephemeral=True,
            )
            return

        # Guard: the channel and the league's two roles must all be set (issue #276), and both
        # roles still on the server, the driver role one the bot can grant (#374). A base role
        # gone pings nobody and opens a channel nobody can see; a driver role gone or refused
        # lets every approval of the window grant nothing, with only the host's log told.
        base_role_id = server_cfg.base_role_id if server_cfg is not None else None
        driver_role_id = server_cfg.driver_role_id if server_cfg is not None else None
        missing = []
        if cfg.signup_channel_id is None:
            missing.append("`signup channel` (use `/signup channel`)")
        if base_role_id is None:
            missing.append("`base role` (use `/bot base-role`)")
        if driver_role_id is None:
            missing.append("`driver role` (use `/bot driver-role`)")
        missing += league_role_faults(interaction.guild, base_role_id, driver_role_id)
        if missing:
            await interaction.response.send_message(
                "❌ Signups cannot be opened until the signup module's configuration is "
                "put right:\n"
                + "\n".join(f"  • {m}" for m in missing),
                ephemeral=True,
            )
            return

        # Guard: at least one slot configured
        slots = await self.bot.signup_module_service.get_slots()
        if not slots:
            await interaction.response.send_message(
                "❌ At least one availability time slot must be configured before opening signups.",
                ephemeral=True,
            )
            return

        # Parse close_time — the same rule `/signup close-time add` holds to
        close_at_iso: str | None = None
        if close_time and close_time.strip():
            close_at_iso, close_error = _parse_close_time(close_time)
            if close_error is not None:
                await interaction.response.send_message(close_error, ephemeral=True)
                return

        # Parse track_ids
        track_list: list[str] = []
        track_name_map: dict[str, str] = {}
        if track_ids and track_ids.strip():
            parts = [t.strip() for t in re.split(r"[,\s]+", track_ids.strip()) if t.strip()]
            async with get_connection(self.bot.db_path) as db:
                track_name_map = await track_service.get_track_name_map(db)
            unknown = [t for t in parts if t not in track_name_map]
            if unknown:
                bad = ", ".join(f"'{t}'" for t in unknown)
                await interaction.response.send_message(
                    f"❌ Unknown track ID(s): {bad}. Valid IDs are 1\u2013{len(track_name_map)}.",
                    ephemeral=True,
                )
                return
            track_list = parts

        await interaction.response.defer(ephemeral=True)

        # Build and post signup button + info message
        settings = await self.bot.signup_module_service.get_settings()
        guild = interaction.guild
        assert guild is not None
        signup_channel = guild.get_channel(cfg.signup_channel_id) if cfg.signup_channel_id else None
        base_role = guild.get_role(base_role_id) if base_role_id else None
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
            track_names = [track_name_map[t] for t in track_list]
            tracks_display = "\n".join(f"• {n}" for n in track_names)
        else:
            tracks_display = "No tracks specified"

        tt_label = "Time Trial" if settings.time_type == "TIME_TRIAL" else "Short Qualification"
        img_label = "Required" if settings.time_image_required else "Not required"
        nat_label = "Required" if settings.nationality_required else "Not required"

        role_mention = base_role.mention if base_role else ""
        role_line = f"\n\n{role_mention} — click below to sign up!" if role_mention else ""

        close_line = ""
        if close_at_iso:
            parsed_utc = datetime.fromisoformat(close_at_iso)
            close_line = f"\n**Auto-closes:** {discord_ts(parsed_utc)} ({discord_ts(parsed_utc, 'R')})"

        info_embed = discord.Embed(
            title="🏁 Driver Signups Are Open!",
            description=(
                f"**Available time slots:**\n"
                + "\n".join(f"• {s.display_label}" for s in slots)
                + f"\n\n**Tracks:**\n{tracks_display}"
                + f"\n\n**Time type:** {tt_label}"
                + f"\n**Time image proof:** {img_label}"
                + f"\n**Nationality:** {nat_label}"
                + close_line
                + role_line
            ),
            color=discord.Color.green(),
        )

        view = SignupButtonView()
        allowed_mentions = discord.AllowedMentions(roles=[base_role]) if base_role else discord.AllowedMentions.none()
        try:
            posted_msg = await signup_channel.send(embed=info_embed, view=view, allowed_mentions=allowed_mentions)
        except Exception as exc:
            await interaction.followup.send(
                f"❌ Failed to post signup message: {exc}", ephemeral=True
            )
            return

        await self.bot.signup_module_service.set_window_open(
            posted_msg.id, track_list
        )
        from services.season_lifecycle_service import advance_on_window_open

        await advance_on_window_open(self.bot.db_path)

        if close_at_iso:
            await self.bot.signup_module_service.set_close_at(close_at_iso)
            self.bot.scheduler_service.schedule_signup_close_timer(close_at_iso)

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, 'SIGNUP_OPEN', '', ?, ?)",
                (interaction.user.id, str(interaction.user),
                 json.dumps({"track_ids": track_list}), now),
            )
            await db.commit()

        close_notice = (
            f" Auto-close scheduled for {discord_ts(datetime.fromisoformat(close_at_iso))} ({discord_ts(datetime.fromisoformat(close_at_iso), 'R')})."
            if close_at_iso
            else ""
        )
        await interaction.followup.send(
            f"✅ Signups opened. Button posted in {signup_channel.mention}.{close_notice}",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /signup open | Success"
            + (f"\n  track_ids: {', '.join(track_list)}" if track_list else ""),
        )

    # ── /signup close (T019) ──────────────────────────────────────────

    @signup.command(name="close", description="Close the signup window.")
    @league_manager_only
    async def signup_close(self, interaction: discord.Interaction) -> None:

        cfg = await self.bot.signup_module_service.get_config()
        if cfg is None or not cfg.signups_open:
            await interaction.response.send_message(
                "❌ Signups are not currently open.", ephemeral=True
            )
            return

        # Guard: auto-close timer is armed — manual close is blocked (T019).
        # The refusal stays, so closing early remains two deliberate steps, but it names
        # `/signup close-time cancel`, which exists; it used to name `/signup cancel-timer`,
        # which never did, leaving `/module disable signup` as the only escape (issue #125).
        if cfg.close_at is not None:
            armed = datetime.fromisoformat(cfg.close_at)
            await interaction.response.send_message(
                f"❌ Signups will auto-close at {discord_ts(armed)} "
                f"({discord_ts(armed, 'R')}). Clear the timer with "
                "`/signup close-time cancel` if you need to close manually, or move it "
                "with `/signup close-time modify`.",
                ephemeral=True,
            )
            return

        # Every driver mid-signup is listed, in two groups: those the close will return to
        # Not Signed Up, and those in review, whom it leaves alone. AWAITING_CORRECTION_
        # PARAMETER is in the second group. Leaving it out meant a driver parked there alone
        # let `/signup close` shut on the spot with no confirmation at all (issue #129).
        # Warning the second group that it would be dropped as well was issue #128.
        async with get_connection(self.bot.db_path) as db:
            placeholders = ",".join("?" for _ in IN_PROGRESS_STATES)
            cursor = await db.execute(
                f"SELECT discord_user_id, current_state FROM driver_profiles "
                f"WHERE current_state IN ({placeholders}) ORDER BY id",
                tuple(state.value for state in IN_PROGRESS_STATES),
            )
            rows = await cursor.fetchall()

        if not rows:
            # No in-progress drivers — immediate close
            await interaction.response.defer(ephemeral=True)
            await execute_forced_close(self.bot, audit_action="SIGNUP_CLOSE")
            await interaction.followup.send("✅ Signups closed.", ephemeral=True)
            await self.bot.output_router.post_log(
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | /signup close | Success",
            )
            return

        # Present confirmation view
        _guild = interaction.guild

        def _name_for_uid(uid: str) -> str:
            member = _guild.get_member(int(uid)) if _guild else None
            return member.display_name if member else uid

        returned: list[str] = []
        kept: list[str] = []
        for row in rows:
            group = returned if DriverState(row["current_state"]) in RETURNED_BY_CLOSE else kept
            group.append(_name_for_uid(row["discord_user_id"]))

        view = ConfirmCloseView(self.bot)
        await interaction.response.send_message(
            _close_confirmation(returned, kept),
            view=view,
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /signup unassigned (group with list + export subcommands)
    # ------------------------------------------------------------------

    unassigned_group = app_commands.Group(
        name="unassigned",
        description="Commands for listing and exporting the unsettled signups.",
        parent=signup,
    )

    @unassigned_group.command(
        name="list",
        description="List the unsettled signups: Unassigned drivers by seed, then those still in review.",
    )
    @league_manager_only
    async def signup_unassigned_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        drivers = await self.bot.placement_service.get_unassigned_drivers_seeded()
        if not drivers:
            await interaction.followup.send(
                "No unsettled signups found.", ephemeral=True
            )
            return

        # Availability is the question a division is built around, so it is named in full
        # here rather than summarised: a league may configure 25 slots, and a driver who
        # ticks every one of them costs a ~500-character line, taking the worst single
        # driver block to some 800 — inside the 1900-character chunk budget below, which
        # splits between drivers and so could not divide one (decided 2026-09-20, #184).
        #
        # The labels are matched on the slot's durable ``slot_id`` and rendered in the
        # league's own chronological order. Matching on ``slot_sequence_id`` instead would
        # be issue #126: the ordinal is recomputed on every read, so a slot added or removed
        # since the driver signed up would show them against somebody else's time.
        slots_ordered = sorted(
            await self.bot.signup_module_service.get_slots(),
            key=lambda s: s.slot_sequence_id,
        )
        slot_labels = {s.slot_id: s.display_label for s in slots_ordered}

        lines: list[str] = [f"**Unsettled Signups — Seeded** ({len(drivers)} total)\n"]
        for d in drivers:
            preferred = ", ".join(d["preferred_teams"]) if d["preferred_teams"] else "—"
            teammate = d["preferred_teammate"] or "—"
            chosen = set(d["availability_slot_ids"])
            # An answer naming a slot the league has since removed is reported as unknown
            # rather than printed raw: the durable ID is a storage form no league should see.
            availability = [s.display_label for s in slots_ordered if s.slot_id in chosen]
            availability += ["Unknown slot"] * len(chosen - slot_labels.keys())
            marker = f"#{d['seed']}" if d["seed"] is not None else _REVIEW_LABELS.get(
                d["state"], "In review"
            )
            lines.append(
                f"**{marker}** **{d['server_display_name']}** (`{d['discord_user_id']}`)\n"
                f"  Platform: {d['platform']} | Type: {d['driver_type']} | Lap total: {d['total_lap_fmt']}\n"
                f"  Available: {', '.join(availability) if availability else '—'}\n"
                f"  Teams: {preferred} | Teammate: {teammate}"
            )
            if d["notes"]:
                lines[-1] += f"\n  Notes: {d['notes']}"

        # Discord has a 2000-char limit; chunk if needed
        output = "\n\n".join(lines)
        if len(output) <= 1900:
            await interaction.followup.send(output, ephemeral=True)
        else:
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

    @unassigned_group.command(
        name="export",
        description="Export the unsettled signups to a CSV file.",
    )
    @league_manager_only
    async def signup_unassigned_export(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        slots = await self.bot.signup_module_service.get_slots()
        slots_ordered = sorted(slots, key=lambda s: s.slot_sequence_id)

        drivers = await self.bot.placement_service.get_unassigned_drivers_for_export(
            slots_ordered
        )
        if not drivers:
            await interaction.followup.send("No unsettled signups found.", ephemeral=True)
            return

        # Build CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)

        # Header
        slot_headers = [s.display_label for s in slots_ordered]
        writer.writerow(
            ["Seed", "Display Name", "Discord User ID", "Driver Type", "Lap Total"]
            + slot_headers
            + ["Preferred Team 1", "Preferred Team 2", "Preferred Team 3", "Platform", "Platform ID"]
        )

        # Rows
        for d in drivers:
            slot_cols = ["X" if d["slot_presence"].get(s.slot_sequence_id) else "" for s in slots_ordered]
            writer.writerow(
                [
                    d["seed"] if d["seed"] is not None else "",
                    d["display_name"],
                    d["discord_user_id"],
                    d["driver_type"],
                    d["total_lap_fmt"],
                ]
                + slot_cols
                + [
                    d["preferred_team_1"],
                    d["preferred_team_2"],
                    d["preferred_team_3"],
                    d["platform"],
                    d["platform_id"],
                ]
            )

        buf = io.BytesIO(output.getvalue().encode("utf-8-sig"))
        file = discord.File(buf, filename="unassigned_drivers.csv")
        await interaction.followup.send(
            f"{len(drivers)} Unassigned driver(s) exported.",
            file=file,
            ephemeral=True,
        )
