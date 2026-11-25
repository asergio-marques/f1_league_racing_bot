"""What each `/signup` command does (#600).

The commands are said twice: by the member group (`signup_cog`) and by the test-mode group
(`signup_test_cog`), which the league's server shows in its place while test mode is on. What the
two share is here, each command's body as a function of the bot and the interaction, so that a
command acts the same whichever group it is run from. Each cog holds the command's declaration,
its tier and its guard, and calls the body, which refuses, logs and asks the queue as the command
always has. There is no base class: a Discord command group is one cog's, and the cogs differ in
their fields, not in what they do.

`/signup cancel` is the one body that takes its driver as already resolved (`SignupDriver`): the
member group's field is a member and the test-mode group's is text, which names a fake driver.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import discord
from discord import app_commands

from leaguebot.core.services.rounds import track_service
from leaguebot.core.utils.input_validator import parse_datetime
from leaguebot.framework.utils.interaction_errors import describe, describe_form
from leaguebot.framework.utils.league_bot import LeagueBot
from leaguebot.framework.utils.league_server import LeagueModal, LeagueView
from leaguebot.framework.utils.log_lines import record_abandoned, refuse
from leaguebot.signup.models.switch import SWITCH_KEY
from leaguebot.signup.services.channel_setting import KIND as SIGNUP_CHANNEL_KIND
from leaguebot.signup.services.close_time_settings import kind_of as close_time_kind
from leaguebot.signup.services.driver_naming import fake_name_of
from leaguebot.signup.services.signup_settings import format_slots, kind_of
from leaguebot.signup.services.signup_views import IN_PROGRESS_STATES, reason_pending_for
from leaguebot.signup.services.window_changes import (
    AUDIT_CLOSE,
    AUDIT_FORCE_CLOSE,
    armed_close_refusal,
    season_end_close_refusal,
)
from leaguebot.signup.services.window_close import RETURNED_BY_CLOSE
from leaguebot.signup.services.wizard_changes import CANCELLED, END, cancellation_refusal
from leaguebot.utils.messages import chunk_message
from leaguebot.utils.time_parsing import parse_time_of_day

log = logging.getLogger(__name__)


#: The days of the week a time slot is added for.
DAY_CHOICES = [
    app_commands.Choice(name="Monday", value="1"),
    app_commands.Choice(name="Tuesday", value="2"),
    app_commands.Choice(name="Wednesday", value="3"),
    app_commands.Choice(name="Thursday", value="4"),
    app_commands.Choice(name="Friday", value="5"),
    app_commands.Choice(name="Saturday", value="6"),
    app_commands.Choice(name="Sunday", value="7"),
]


@dataclass(frozen=True)
class SignupDriver:
    """The driver `/signup cancel` acts on: the account they use now, and the name the command
    calls them by (a member's mention, a fake driver's test name). No member is carried: a fake
    driver has none."""

    account: str
    name: str


#: How an unsettled signup that holds no seed is marked in `/signup unassigned list`.
_REVIEW_LABELS = {
    "PENDING_ADMIN_APPROVAL": "Awaiting approval",
    "AWAITING_CORRECTION_PARAMETER": "Awaiting approval",
    "PENDING_DRIVER_CORRECTION": "Correcting",
}


def _without_blank_lines(typed: str | None) -> str:
    """*typed*, a driver's own words, with each run of blank lines made one line break: a blank
    line inside it would read as the gap between two drivers, where the unassigned list's
    records are divided."""
    return re.sub(r"\n{2,}", "\n", (typed or "").strip())


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
        except discord.HTTPException:
            log.warning("could not take the close confirmation's buttons down", exc_info=True)


#: The longest reason `/signup cancel` takes. The notice the driver's channel is told holds it
#: beside the manager's name and the hold on the channel, and has to fit one Discord message.
CANCEL_REASON_LIMIT = 1000


class CancelSignupModal(LeagueModal, title="Cancel signup"):
    """The form `/signup cancel` opens: one optional reason, told to the driver in their channel.

    Nothing is asked of the queue until it is submitted, and closing it records nothing. The
    submission judges the driver's signup again, as the command did, for a form can stay open while
    the driver withdraws, is approved, or a manager presses Reject on their review: where it is no
    longer under way, or a reason is awaited, the cancellation is refused and recorded. The queue's
    own check judges the state once more when its turn comes, from the database alone.
    """

    reason: discord.ui.TextInput = discord.ui.TextInput(
        label="Reason (told to the driver)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=CANCEL_REASON_LIMIT,
    )

    def __init__(self, bot: LeagueBot, driver: SignupDriver) -> None:
        super().__init__()
        self._bot = bot
        self._driver = driver

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot = self._bot
        driver = self._driver
        profile = await bot.driver_service.get_profile(driver.account)
        refusal = cancellation_refusal(
            None if profile is None else profile.current_state,
            driver.name,
            reason_pending_for(int(driver.account)),
        )
        if refusal is not None:
            await refuse(interaction, f"⛔ {refusal}", what=describe_form(self), reason=refusal)
            return
        await bot.change_queue.ask(
            END,
            {
                "user_id": driver.account, "reason": CANCELLED,
                "actor_id": interaction.user.id,
                "actor_name": getattr(interaction.user, "display_name", str(interaction.user)),
                "text": self.reason.value.strip() or None,
            },
            interaction=interaction, what=describe_form(self),
        )


async def module_gate(
    bot: LeagueBot,
    interaction: discord.Interaction,
    *,
    record: bool = True,
) -> bool:
    """Whether the signup module is on; where it is not, refuse and return False.

    The commands that ask no setting change run this in their own body, after their tier
    guard, as the results cog's gate does: `time-slot list`, `open`, `close` and the two
    `unassigned` commands. The setting commands leave the refusal to their change's check
    (`signup_settings.open_to_change`), which judges it again when their turn comes. It used to be the cog's `interaction_check`,
    which answered the member and then raised `CheckFailure`: the error handler answered
    a second time and wrote a failure line for what was only a refusal. One reply, then,
    and one refusal line — unless *record* is False, which the lists pass, since they
    change nothing and record nothing. A command run in a DM never gets this far: the
    tier guard has sent it to the host log.
    """
    if await bot.module_service.is_enabled(SWITCH_KEY):
        return True
    reply = "⛔ Signup module is not enabled. Use `/module enable signup` first."
    if record:
        await refuse(interaction, reply, what=describe(interaction))
    else:
        await interaction.response.send_message(reply, ephemeral=True)
    return False


async def ask_setting(
    bot: LeagueBot,
    interaction: discord.Interaction,
    command: str,
    payload: dict | None = None,
) -> None:
    """Ask the change queue to carry out `/signup <command>`: the setting's check judges every
    refusal as it is asked and again as the change starts (`signup_settings`), so the command
    holds no rule of its own. The queue's acknowledgement is the command's response, and it
    writes the value, its audit entry and its log line in one save."""
    await bot.change_queue.ask(
        kind_of(command), payload or {}, interaction=interaction, what=describe(interaction)
    )


async def ask_close_time(
    bot: LeagueBot,
    interaction: discord.Interaction,
    command: str,
    close_time: str | None = None,
) -> None:
    """Ask the change queue to carry out `/signup close-time <command>`: the setting's check
    judges every refusal as it is asked and again as the change starts (`close_time_settings`).
    The time the command was given is its own input, parsed here as `/signup open` parses it
    and handed to the check as the time or the refusal."""
    payload: dict = {}
    if close_time is not None:
        close_at, close_error = _parse_close_time(close_time)
        payload = {"close_at": close_at, "close_error": close_error}
    await bot.change_queue.ask(
        close_time_kind(command), payload, interaction=interaction, what=describe(interaction)
    )


async def config_view(bot: LeagueBot, interaction: discord.Interaction) -> None:
    cfg = await bot.signup_module_service.get_config()
    settings = await bot.signup_module_service.get_settings()

    guild = interaction.guild
    assert guild is not None

    embed = discord.Embed(title="Signup Module Configuration", color=discord.Color.blue())

    # The two roles are the league's (issue #276), shown here because signups cannot
    # open without them, and shown whether or not the module is enabled.
    server_cfg = await bot.config_service.get_server_config()

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


async def signup_channel(
    bot: LeagueBot,
    interaction: discord.Interaction,
    channel: discord.TextChannel,
) -> None:
    """Ask the change queue for `signup.setting.channel` (`channel_setting`): every refusal
    is the setting's check, made as it is asked and again as the change starts, and the
    permissions are set after the save."""
    await bot.change_queue.ask(
        SIGNUP_CHANNEL_KIND,
        {"channel_id": channel.id, "channel_name": channel.name},
        interaction=interaction, what=describe(interaction),
    )


async def nationality(bot: LeagueBot, interaction: discord.Interaction) -> None:
    await ask_setting(bot, interaction, "nationality")


async def time_type(bot: LeagueBot, interaction: discord.Interaction) -> None:
    await ask_setting(bot, interaction, "time-type")


async def time_image(bot: LeagueBot, interaction: discord.Interaction) -> None:
    await ask_setting(bot, interaction, "time-image")


async def time_slot_add(
    bot: LeagueBot,
    interaction: discord.Interaction,
    day: app_commands.Choice[str],
    time: str,
) -> None:
    await ask_setting(bot, 
        interaction, "time-slot add",
        {"day": int(day.value), "day_name": day.name, "time": _parse_time(time) or time},
    )


async def time_slot_remove(bot: LeagueBot, interaction: discord.Interaction, slot_id: int) -> None:
    # The rank is resolved to the slot's day and time here, as the list stands now: the change
    # removes that slot, whatever is removed ahead of it (`signup_settings`).
    slots = await bot.signup_module_service.get_slots()
    target = next((s for s in slots if s.slot_sequence_id == slot_id), None)
    await ask_setting(bot, 
        interaction, "time-slot remove",
        {
            "rank": slot_id,
            "day": target.day_of_week if target else None,
            "time": target.time_hhmm if target else None,
        },
    )


async def time_slot_list(bot: LeagueBot, interaction: discord.Interaction) -> None:
    if not await module_gate(bot, interaction, record=False):
        return
    slots = await bot.signup_module_service.get_slots()
    await interaction.response.send_message(
        format_slots(slots), ephemeral=True
    )


async def close_time_add(bot: LeagueBot, interaction: discord.Interaction, close_time: str) -> None:
    await ask_close_time(bot, interaction, "add", close_time)


async def close_time_cancel(bot: LeagueBot, interaction: discord.Interaction) -> None:
    await ask_close_time(bot, interaction, "cancel")


async def close_time_modify(
    bot: LeagueBot,
    interaction: discord.Interaction,
    close_time: str,
) -> None:
    await ask_close_time(bot, interaction, "modify", close_time)


async def signup_cancel(
    bot: LeagueBot,
    interaction: discord.Interaction,
    driver: SignupDriver,
) -> None:
    """End the signup *driver* is filling in, awaiting review or correcting.

    Refused, with the reason and a line in the log channel, for a driver with no signup in
    progress, an approved one (Unassigned: `/driver reject` turns them down), one placed in a
    seat, signup being off, and a driver whose signup a manager has pressed Reject or Request
    Changes on and not yet given the reason. Otherwise it opens a form for an optional reason;
    the signup ends when that is submitted (`CancelSignupModal`). The command holds no rule:
    `cancellation_refusal` judges the state, here from the driver service and again by the
    queued change from the database. The reason a manager is still to type lives in memory, so
    it is judged here and on the form's submission and never by the queued change.
    """
    if not await module_gate(bot, interaction):
        return
    profile = await bot.driver_service.get_profile(driver.account)
    refusal = cancellation_refusal(
        None if profile is None else profile.current_state,
        driver.name,
        reason_pending_for(int(driver.account)),
    )
    if refusal is not None:
        await refuse(
            interaction, f"⛔ {refusal}", what=describe(interaction), reason=refusal
        )
        return
    await interaction.response.send_modal(CancelSignupModal(bot, driver))


async def signup_open(
    bot: LeagueBot,
    interaction: discord.Interaction,
    track_ids: str | None = None,
    close_time: str | None = None,
) -> None:
    if not await module_gate(bot, interaction):
        return

    cfg = await bot.signup_module_service.get_config()
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
            str(t["id"]): t["name"] for t in await track_service.list_tracks(bot.db_path)
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

    await bot.change_queue.ask(
        "signup.open",
        {
            "track_ids": track_list,
            "close_at": close_at_iso,
            "channel_id": cfg.signup_channel_id,
            "closed_message_id": cfg.signup_closed_message_id,
            "closed_channel_id": cfg.signup_closed_message_channel_id,
        },
        interaction=interaction,
        what="Opening signups",
        refusal_what=describe(interaction),
    )


async def signup_close(bot: LeagueBot, interaction: discord.Interaction) -> None:
    if not await module_gate(bot, interaction):
        return

    cfg = await bot.signup_module_service.get_config()
    if cfg is None or not cfg.signups_open:
        await refuse(
            interaction, "❌ Signups are not currently open.", what=describe(interaction)
        )
        return

    # The season's end closes signups itself: refused at once, before any confirmation, as
    # every request about a season whose end is in hand is (core, "Ending a season").
    ending = await season_end_close_refusal(bot.db_path)
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
    rows = await bot.signup_module_service.drivers_mid_signup(IN_PROGRESS_STATES)

    if not rows:
        # No in-progress drivers — nothing to confirm, so the close is asked at once.
        await bot.change_queue.ask(
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

    async def _name_for_uid(uid: str) -> str:
        # A fake driver is no member of the server: named by its test name, never its number.
        fake = await fake_name_of(bot.db_path, uid)
        if fake is not None:
            return fake
        member = _guild.get_member(int(uid)) if _guild else None
        return member.display_name if member else uid

    returned: list[str] = []
    kept: list[str] = []
    for user_id, state, channel_id in rows:
        group = returned if state in RETURNED_BY_CLOSE else kept
        line = await _name_for_uid(user_id)
        if channel_id is not None:
            line += f" — <#{channel_id}>"
        group.append(line)

    view = ConfirmCloseView(bot, interaction, cfg.signup_button_message_id)
    await interaction.response.send_message(
        _close_confirmation(returned, kept),
        view=view,
        ephemeral=True,
    )


async def signup_unassigned_list(bot: LeagueBot, interaction: discord.Interaction) -> None:
    if not await module_gate(bot, interaction, record=False):
        return
    await interaction.response.defer(ephemeral=True)
    drivers = await bot.signup_module_service.get_unassigned_drivers_seeded()
    if not drivers:
        await interaction.followup.send(
            "No unsettled signups found.", ephemeral=True
        )
        return

    # Availability is the question a division is built around, so it is named in full
    # here rather than summarised: a league may configure 25 slots, and a driver who
    # ticks every one of them costs a ~500-character line, taking the worst single
    # driver block to some 800 — well inside the message limit, so the records mode of
    # `chunk_message` divides between drivers and never needs to divide one (decided
    # 2026-09-20, #184).
    #
    # The labels are matched on the slot's durable ``slot_id`` and rendered in the
    # league's own chronological order. Matching on ``slot_sequence_id`` instead would
    # be issue #126: the ordinal is recomputed on every read, so a slot added or removed
    # since the driver signed up would show them against somebody else's time.
    slots_ordered = sorted(
        await bot.signup_module_service.get_slots(),
        key=lambda s: s.slot_sequence_id,
    )
    slot_labels = {s.slot_id: s.display_label for s in slots_ordered}

    lines: list[str] = [f"**Unsettled Signups — Seeded** ({len(drivers)} total)\n"]
    for d in drivers:
        preferred = ", ".join(d["preferred_teams"]) if d["preferred_teams"] else "—"
        # What a driver types (the teammate and the notes) may hold a blank line, which would
        # read as the gap between two drivers, where the records are divided.
        teammate = _without_blank_lines(d["preferred_teammate"]) or "—"
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
            lines[-1] += f"\n  Notes: {_without_blank_lines(d['notes'])}"

    # Discord has a 2000-char limit; the records are divided between drivers only.
    for part in chunk_message("\n\n".join(lines), records=True):
        await interaction.followup.send(part, ephemeral=True)


async def signup_unassigned_export(bot: LeagueBot, interaction: discord.Interaction) -> None:
    if not await module_gate(bot, interaction, record=False):
        return
    await interaction.response.defer(ephemeral=True)

    slots = await bot.signup_module_service.get_slots()
    slots_ordered = sorted(slots, key=lambda s: s.slot_sequence_id)

    drivers = await bot.signup_module_service.get_unassigned_drivers_for_export(
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
