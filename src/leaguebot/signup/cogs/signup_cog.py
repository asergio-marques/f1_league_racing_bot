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
import logging
import re
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.signup.services.window_close import RETURNED_BY_CLOSE, same_instant
from leaguebot.signup.services.signup_views import IN_PROGRESS_STATES, SignupButtonView
from leaguebot.signup.services.channel_setting import KIND as SIGNUP_CHANNEL_KIND
from leaguebot.signup.services.close_time_settings import kind_of as close_time_kind
from leaguebot.signup.services.signup_settings import fixed_refusal, format_slots, kind_of
from leaguebot.signup.services.wizard_answers import take_typed_answer
from leaguebot.signup.services.wizard_changes import (
    DELETED, ask_channel_gone, ask_member_left,
)
from leaguebot.signup.services.window_changes import (
    AUDIT_CLOSE,
    AUDIT_FORCE_CLOSE,
    armed_close_refusal,
    season_end_close_refusal,
)
from leaguebot.core.services.rounds import track_service
from leaguebot.core.utils.input_validator import parse_datetime
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.time_parsing import parse_time_of_day
from leaguebot.core.utils.catch_up_gate import after_the_catch_up
from leaguebot.core.utils.channel_guard import league_manager_only, league_role_faults, changes_nothing
from leaguebot.core.utils.league_server import LeagueView, is_foreign_guild
from leaguebot.core.utils.messages import chunk_message
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.log_lines import name_of_member, record_abandoned, refuse
from leaguebot.core.utils.formatting import discord_ts

#: Waits for the catch-up after a restart before a command answered off the change queue acts.
_after_the_catch_up = after_the_catch_up(lambda cog: cog.bot.change_queue)


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
    parsed = moment
    if parsed <= (now if now is not None else datetime.now(timezone.utc)):
        return None, "❌ `close_time` must be a future datetime."
    return parsed.isoformat(), None



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
    """Confirmation dialog for closing signups with in-progress drivers (T018).

    It keeps the interaction of the `/signup close` that asked, so that a cancel and a lapse
    are recorded as that command's, naming the manager who ran it, and so that a lapse can take
    the buttons down through the command's own reply. Asking records nothing by itself: only
    the outcome does, and a restart that drops the question drops it unrecorded.

    It also keeps the Sign Up button message of the window it asked about. The buttons stand for
    five minutes, and Confirm hands that message to the close, which refuses where the window is
    no longer that one (#491); Confirm then answers and records the refusal, and closes nothing.
    """

    #: What a cancel and a lapse record as cancelled or lapsed: the command that asked.
    _WHAT = "`/signup close`"

    #: What a cancel and a lapse leave beneath their line: nothing was closed, and what to do.
    _STAYS_OPEN = "Signups remain open. Run /signup close again to close them."

    def __init__(
        self, bot: LeagueBot, asked: discord.Interaction, window: int | None
    ) -> None:
        super().__init__(timeout=300)
        self._bot = bot
        self._asked = asked
        self._window = window
        self.confirmed = False

    @discord.ui.button(label="Confirm Close", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.confirmed = True
        self.stop()
        # The count is the close's own, not the confirmation's: a driver may have finished
        # signing up, or started, in the five minutes the buttons stand (issue #128). The close
        # is judged again as it comes up to run, and refused where the window is no longer this
        # one (#491).
        await self._bot.change_queue.ask(
            "signup.close",
            {
                "cause": "command",
                "window": self._window,
                "confirmed": True,
                "audit_action": AUDIT_FORCE_CLOSE,
            },
            interaction=interaction,
            what="Closing signups",
            refusal_what=f"the “Confirm Close” button of {self._WHAT}",
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.stop()
        await interaction.response.send_message(
            "Action cancelled. Signups remain open.", ephemeral=True
        )
        await record_abandoned(
            self._bot,
            interaction.user,
            what=self._WHAT,
            lapsed=False,
            detail=self._STAYS_OPEN,
        )

    async def on_timeout(self) -> None:
        """Record that nobody answered, and take the buttons down through the command's reply.

        The takedown goes through the command's interaction, never a message's own `.edit`:
        the confirmation is an ephemeral reply, which has no message the bot may edit. A reply
        that cannot be edited is left, and does not cost the league its record.
        """
        await record_abandoned(
            self._bot,
            self._asked.user,
            what=self._WHAT,
            lapsed=True,
            detail=self._STAYS_OPEN,
        )
        try:
            await self._asked.edit_original_response(view=None)
        except Exception:  # noqa: BLE001 — the lapse is recorded whether or not the reply goes
            log.warning("could not take the close confirmation's buttons down", exc_info=True)


class SignupCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    async def _module_gate(
        self, interaction: discord.Interaction, *, record: bool = True
    ) -> bool:
        """Whether the signup module is on; where it is not, refuse and return False.

        Every `/signup` command but `config view` runs this in its own body, after its tier
        guard, as the results cog's gate does. It used to be the cog's `interaction_check`,
        which answered the member and then raised `CheckFailure`: the error handler answered
        a second time and wrote a failure line for what was only a refusal. One reply, then,
        and one refusal line — unless *record* is False, which the lists pass, since they
        change nothing and record nothing. A command run in a DM never gets this far: the
        tier guard has sent it to the host log.
        """
        if await self.bot.module_service.is_signup_enabled():
            return True
        reply = "⛔ Signup module is not enabled. Use `/module enable signup` first."
        if record:
            await refuse(interaction, reply, what=describe(interaction))
        else:
            await interaction.response.send_message(reply, ephemeral=True)
        return False

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
        await take_typed_answer(
            self.bot, message, wizard, queue=self.bot.change_queue,
            router=self.bot.output_router,
        )

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        """A channel of the server was deleted: where a wizard names it, its signup is ended.

        The ending is the change queue's: the listener asks it, as the bot, for `signup.end` with
        the reason `CHANNEL_GONE`, found "deleted", and the change's own check decides whether the
        signup is still under way. The bot's own deletions of held channels fire this too, and
        each finds its driver no longer signing up and is dropped. A channel no wizard names is
        nothing to the signup module.

        Only a channel of the league's own server counts.
        """
        if await is_foreign_guild(self.bot, channel.guild.id):
            return
        wizard = await self.bot.signup_module_service.get_wizard_by_channel(channel.id)
        if wizard is None:
            return
        await ask_channel_gone(
            self.bot.change_queue,
            user_id=wizard.discord_user_id,
            channel_id=channel.id,
            named=await name_of_member(self.bot, int(wizard.discord_user_id)),
            found=DELETED,
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """T048: Clean up wizard state when a member leaves the server (FR-027).

        The clean-up is the change queue's: the listener asks it, as the bot, for
        `signup.member_left` for every member who leaves, and the change's own check decides what
        there is to clean up. A member who leaves while the queue is stopped keeps their channel
        and state until it clears. Its line, "Driver left server", is the change's too, for a
        driver approved or placed as for one still signing up.

        Only a member leaving the league's own server counts: the same person leaving another
        server the bot sits in is nothing to the league.
        """
        if await is_foreign_guild(self.bot, member.guild.id):
            return
        await ask_member_left(
            self.bot.change_queue, self.bot.db_path, str(member.id),
            member.display_name or str(member.id),
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
    @changes_nothing
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
    @_after_the_catch_up
    async def signup_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        """Ask the change queue for `signup.setting.channel` (`channel_setting`): every refusal
        is the setting's check, made as it is asked and again as the change starts, and the
        permissions are set after the save."""
        await self.bot.change_queue.ask(
            SIGNUP_CHANNEL_KIND,
            {"channel_id": channel.id, "channel_name": channel.name},
            interaction=interaction, what=describe(interaction),
        )

    # ── /signup nationality toggle (T020) ──────────────────────────────

    async def _ask_setting(
        self, interaction: discord.Interaction, command: str, payload: dict | None = None
    ) -> None:
        """Ask the change queue to carry out `/signup <command>`: the setting's check judges every
        refusal as it is asked and again as the change starts (`signup_settings`), so the command
        holds no rule of its own. The queue's acknowledgement is the command's response, and it
        writes the value, its audit entry and its log line in one save."""
        await self.bot.change_queue.ask(
            kind_of(command), payload or {}, interaction=interaction, what=describe(interaction)
        )

    @signup.command(name="nationality", description="Toggle whether nationality is required in signups.")
    @league_manager_only
    @_after_the_catch_up
    async def nationality(self, interaction: discord.Interaction) -> None:
        await self._ask_setting(interaction, "nationality")

    # ── /signup time-type toggle (T021) ────────────────────────────────

    @signup.command(name="time-type", description="Toggle the time type setting (Time Trial / Short Qualification).")
    @league_manager_only
    @_after_the_catch_up
    async def time_type(self, interaction: discord.Interaction) -> None:
        await self._ask_setting(interaction, "time-type")

    # ── /signup time-image toggle (T022) ───────────────────────────────

    @signup.command(name="time-image", description="Toggle whether a time image is required in signups.")
    @league_manager_only
    @_after_the_catch_up
    async def time_image(self, interaction: discord.Interaction) -> None:
        await self._ask_setting(interaction, "time-image")

    # ── /signup time-slot (sub-group) ──────────────────────────────────

    time_slot_group = app_commands.Group(
        name="time-slot",
        description="Manage signup availability time slots.",
        parent=signup,
    )

    async def _refuse_while_configuration_fixed(
        self, interaction: discord.Interaction, command: str, *, record: bool = True
    ) -> bool:
        """Refuse a change to the signup module's settings once a season has fixed them.

        The settings are free while the server holds no active season and while its season
        stands in Configuration; confirming that configuration fixes them until the season
        ends (issue #220). This replaces the older guards that blocked a slot change while
        the window was open or drivers awaited placement — both only ever happen inside a
        season whose configuration is already fixed.

        Returns True when the command replied and must stop, having recorded the refusal in
        the log channel unless *record* is False.
        """
        from leaguebot.core.services.seasons.season_lifecycle_service import configuration_fixed

        season_number = await configuration_fixed(self.bot.db_path)
        if season_number is None:
            return False
        reply = fixed_refusal(season_number, command)
        if record:
            await refuse(interaction, reply, what=f"`{command}`")
        else:
            await interaction.response.send_message(reply, ephemeral=True)
        return True

    @time_slot_group.command(name="add", description="Add an availability time slot.")
    @app_commands.describe(day="Day of week", time="Time in HH:MM 24h or 12h format (e.g. 14:30 or 2:30pm)")
    @app_commands.choices(day=_DAY_CHOICES)
    @league_manager_only
    @_after_the_catch_up
    async def time_slot_add(
        self,
        interaction: discord.Interaction,
        day: app_commands.Choice[str],
        time: str,
    ) -> None:
        await self._ask_setting(
            interaction, "time-slot add",
            {"day": int(day.value), "day_name": day.name, "time": _parse_time(time) or time},
        )

    @time_slot_group.command(name="remove", description="Remove an availability time slot by its sequence ID.")
    @app_commands.describe(slot_id="Stable sequence ID shown in /signup time-slot list")
    @league_manager_only
    @_after_the_catch_up
    async def time_slot_remove(
        self, interaction: discord.Interaction, slot_id: int
    ) -> None:
        # The rank is resolved to the slot's day and time here, as the list stands now: the change
        # removes that slot, whatever is removed ahead of it (`signup_settings`).
        slots = await self.bot.signup_module_service.get_slots()
        target = next((s for s in slots if s.slot_sequence_id == slot_id), None)
        await self._ask_setting(
            interaction, "time-slot remove",
            {
                "rank": slot_id,
                "day": target.day_of_week if target else None,
                "time": target.time_hhmm if target else None,
            },
        )

    @time_slot_group.command(name="list", description="List all configured availability time slots.")
    @league_manager_only
    @changes_nothing
    async def time_slot_list(self, interaction: discord.Interaction) -> None:
        if not await self._module_gate(interaction, record=False):
            return
        slots = await self.bot.signup_module_service.get_slots()
        await interaction.response.send_message(
            format_slots(slots), ephemeral=True
        )

    # ── /signup close-time (sub-group) ─────────────────────────────────

    close_time_group = app_commands.Group(
        name="close-time",
        description="Manage the signup window's auto-close time.",
        parent=signup,
    )

    async def _ask_close_time(
        self, interaction: discord.Interaction, command: str, close_time: str | None = None
    ) -> None:
        """Ask the change queue to carry out `/signup close-time <command>`: the setting's check
        judges every refusal as it is asked and again as the change starts (`close_time_settings`).
        The time the command was given is its own input, parsed here as `/signup open` parses it
        and handed to the check as the time or the refusal."""
        payload: dict = {}
        if close_time is not None:
            close_at, close_error = _parse_close_time(close_time)
            payload = {"close_at": close_at, "close_error": close_error}
        await self.bot.change_queue.ask(
            close_time_kind(command), payload, interaction=interaction, what=describe(interaction)
        )

    @close_time_group.command(
        name="add", description="Arm an auto-close time for the open signup window."
    )
    @app_commands.describe(
        close_time="Auto-close UTC datetime in ISO 8601 format (e.g. 2025-06-15T20:00:00)",
    )
    @league_manager_only
    @_after_the_catch_up
    async def close_time_add(
        self, interaction: discord.Interaction, close_time: str
    ) -> None:
        await self._ask_close_time(interaction, "add", close_time)

    @close_time_group.command(
        name="cancel", description="Clear the auto-close time, leaving signups open."
    )
    @league_manager_only
    @_after_the_catch_up
    async def close_time_cancel(self, interaction: discord.Interaction) -> None:
        await self._ask_close_time(interaction, "cancel")

    @close_time_group.command(
        name="modify", description="Replace the armed auto-close time with a new one."
    )
    @app_commands.describe(
        close_time="New auto-close UTC datetime in ISO 8601 format (e.g. 2025-06-15T20:00:00)",
    )
    @league_manager_only
    @_after_the_catch_up
    async def close_time_modify(
        self, interaction: discord.Interaction, close_time: str
    ) -> None:
        close_at, _error = _parse_close_time(close_time)
        stored = await self.bot.signup_module_service.get_config()
        if (
            close_at is not None and stored is not None and stored.signups_open
            and stored.close_at is not None and same_instant(stored.close_at, close_at)
            and await self.bot.module_service.is_signup_enabled()
        ):
            # The time asked is the one armed: said at once, nothing queued, as the command has
            # always answered it.
            armed = parse_datetime(stored.close_at)
            assert armed is not None
            await interaction.response.send_message(
                f"ℹ️ Signups already auto-close at {discord_ts(armed)} "
                f"({discord_ts(armed, 'R')}). Nothing was changed.",
                ephemeral=True,
            )
            await self.bot.output_router.post_log(
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | "
                "/signup close-time modify | Nothing changed\n"
                f"  close_time: {stored.close_at}\n"
                "  reason: it is already the armed time",
            )
            return
        await self._ask_close_time(interaction, "modify", close_time)

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
        if not await self._module_gate(interaction):
            return

        # Refused under test mode, for the same reason the Sign Up button is: no real
        # driver may sign up while the server is under test, so a window opened now
        # would be one nobody could use.
        server_cfg = await self.bot.config_service.get_server_config()
        if server_cfg is not None and server_cfg.test_mode_active:
            await refuse(
                interaction,
                "⛔ Signups cannot be opened while test mode is active. "
                "Turn it off with `/test-mode toggle` first.",
                what=describe(interaction),
            )
            return

        cfg = await self.bot.signup_module_service.get_config()
        if cfg is None:
            await refuse(
                interaction, "❌ Signup module is not configured.", what=describe(interaction)
            )
            return

        # The command's own input is judged here, ahead of the change's check; the rest of the
        # league's state (the window, the season's stage, the configuration, the channel and the
        # bot's permissions on it) is the change's, judged as it is asked and as it runs.
        close_at_iso: str | None = None
        if close_time and close_time.strip():
            close_at_iso, close_error = _parse_close_time(close_time)
            if close_error is not None:
                await refuse(interaction, close_error, what=describe(interaction))
                return

        track_list: list[str] = []
        if track_ids and track_ids.strip():
            parts = [t.strip() for t in re.split(r"[,\s]+", track_ids.strip()) if t.strip()]
            track_name_map = {
                str(t["id"]): t["name"] for t in await track_service.list_tracks(self.bot.db_path)
            }
            unknown = [t for t in parts if t not in track_name_map]
            if unknown:
                bad = ", ".join(f"'{t}'" for t in unknown)
                await refuse(
                    interaction,
                    f"❌ Unknown track ID(s): {bad}. Valid IDs are 1\u2013{len(track_name_map)}.",
                    what=describe(interaction),
                )
                return
            track_list = parts

        await self.bot.change_queue.ask(
            "signup.open",
            {
                "track_ids": track_list,
                "close_at": close_at_iso,
                "channel_id": cfg.signup_channel_id,
                "closed_message_id": cfg.signup_closed_message_id,
            },
            interaction=interaction,
            what="Opening signups",
            refusal_what=describe(interaction),
        )

    # ── /signup close (T019) ──────────────────────────────────────────

    @signup.command(name="close", description="Close the signup window.")
    @league_manager_only
    async def signup_close(self, interaction: discord.Interaction) -> None:
        if not await self._module_gate(interaction):
            return

        cfg = await self.bot.signup_module_service.get_config()
        if cfg is None or not cfg.signups_open:
            await refuse(
                interaction, "❌ Signups are not currently open.", what=describe(interaction)
            )
            return

        # The season's end closes signups itself: refused at once, before any confirmation, as
        # every request about a season whose end is in hand is (core, "Ending a season").
        ending = await season_end_close_refusal(self.bot.db_path)
        if ending is not None:
            await refuse(interaction, ending, what=describe(interaction))
            return

        # Guard: auto-close timer is armed — manual close is blocked (T019).
        # The refusal stays, so closing early remains two deliberate steps, but it names
        # `/signup close-time cancel`, which exists; it used to name `/signup cancel-timer`,
        # which never did, leaving `/module disable signup` as the only escape (issue #125).
        if cfg.close_at is not None:
            await refuse(
                interaction,
                f"❌ {armed_close_refusal(cfg.close_at)}",
                what=describe(interaction),
            )
            return

        # Every driver mid-signup is listed, in two groups: those the close will return to
        # Not Signed Up, and those in review, whom it leaves alone. AWAITING_CORRECTION_
        # PARAMETER is in the second group. Leaving it out meant a driver parked there alone
        # let `/signup close` shut on the spot with no confirmation at all (issue #129).
        # Warning the second group that it would be dropped as well was issue #128.
        #
        # Each driver is named with their signup channel, so a manager can follow the link
        # and nudge someone to finish before closing. A driver with no wizard record, or one
        # whose channel was never made, is named alone.
        rows = await self.bot.signup_module_service.drivers_mid_signup(IN_PROGRESS_STATES)

        if not rows:
            # No in-progress drivers — nothing to confirm, so the close is asked at once.
            await self.bot.change_queue.ask(
                "signup.close",
                {
                    "cause": "command",
                    "window": cfg.signup_button_message_id,
                    "confirmed": False,
                    "audit_action": AUDIT_CLOSE,
                },
                interaction=interaction,
                what="Closing signups",
                refusal_what=describe(interaction),
            )
            return

        # Present confirmation view
        _guild = interaction.guild

        def _name_for_uid(uid: str) -> str:
            member = _guild.get_member(int(uid)) if _guild else None
            return member.display_name if member else uid

        returned: list[str] = []
        kept: list[str] = []
        for user_id, state, channel_id in rows:
            group = returned if state in RETURNED_BY_CLOSE else kept
            line = _name_for_uid(user_id)
            if channel_id is not None:
                line += f" — <#{channel_id}>"
            group.append(line)

        view = ConfirmCloseView(self.bot, interaction, cfg.signup_button_message_id)
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
    @changes_nothing
    async def signup_unassigned_list(self, interaction: discord.Interaction) -> None:
        if not await self._module_gate(interaction, record=False):
            return
        await interaction.response.defer(ephemeral=True)
        drivers = await self.bot.signup_module_service.get_unassigned_drivers_seeded()
        if not drivers:
            await interaction.followup.send(
                "No unsettled signups found.", ephemeral=True
            )
            return

        # Availability is the question a division is built around, so it is named in full
        # here rather than summarised: a league may configure 25 slots, and a driver who
        # ticks every one of them costs a ~500-character line, taking the worst single
        # driver block to some 800 — inside the message limit, which the records mode of
        # `chunk_message` divides between drivers only and so could not divide one (decided 2026-09-20, #184).
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

        # Discord has a 2000-char limit; the records are divided between drivers only.
        for part in chunk_message("\n\n".join(lines), records=True):
            await interaction.followup.send(part, ephemeral=True)

    @unassigned_group.command(
        name="export",
        description="Export the unsettled signups to a CSV file.",
    )
    @league_manager_only
    @changes_nothing
    async def signup_unassigned_export(self, interaction: discord.Interaction) -> None:
        if not await self._module_gate(interaction, record=False):
            return
        await interaction.response.defer(ephemeral=True)

        slots = await self.bot.signup_module_service.get_slots()
        slots_ordered = sorted(slots, key=lambda s: s.slot_sequence_id)

        drivers = await self.bot.signup_module_service.get_unassigned_drivers_for_export(
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
