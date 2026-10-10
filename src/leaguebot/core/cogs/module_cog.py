"""ModuleCog — /module enable and /module disable commands.

Manages the league's modules.
"""
from __future__ import annotations

import enum
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import module_off
from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.utils.channel_guard import league_admin_only
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.interaction_errors import describe, report_failure
from leaguebot.core.utils.league_server import LeagueView, league_guild
from leaguebot.core.utils.log_lines import record_abandoned, refuse
from leaguebot.core.utils.messages import chunk_message

log = logging.getLogger(__name__)


def _still_off(module: str) -> str:
    """What a failed `/module enable` tells the member: the enable undid itself, and the next step."""
    return f"The module is still off. Run `/module enable {module}` again once the fault is cleared."


_MODULE_CHOICES = [
    app_commands.Choice(name="weather", value="weather"),
    app_commands.Choice(name="signup", value="signup"),
    app_commands.Choice(name="results", value="results"),
    app_commands.Choice(name="attendance", value="attendance"),
    app_commands.Choice(name="images", value="images"),
]

# ---------------------------------------------------------------------------
# Shared forced-close sub-flow (called by both module_cog and signup_cog)
# ---------------------------------------------------------------------------


#: The states a close returns to Not Signed Up: a driver still filling in the wizard. One
#: awaiting approval, awaiting a correction parameter or correcting keeps their state
#: (FR-002/FR-003). ``signup_close`` reads this set to tell a manager which drivers the close
#: will drop and which keep their place. When it kept a list of its own, it warned that every
#: driver mid-signup would be dropped (issue #128).
RETURNED_BY_CLOSE: frozenset[DriverState] = frozenset({DriverState.PENDING_SIGNUP_COMPLETION})


#: The forced close's failed steps that do not name a driver, worded once for every caller.
#: How the change that turns results off names itself in its lines.
_RESULTS_OFF = "`/module disable results`"
_BUTTON_NOT_REMOVED = "The Sign Up button could not be removed from the signup channel."
_NOTICE_NOT_POSTED = "The closed notice could not be posted in the signup channel."


@dataclass(frozen=True)
class ForcedCloseOutcome:
    """What a forced close did: the drivers it turned away, and each step that failed.

    Every step of the close runs whatever the one before did, because the window has to end up
    closed and audited whatever Discord makes of it. So a step that fails cannot stop the close;
    it is named here instead, one plain sentence each, for the caller to tell the member and
    write beneath its line. Each failure's traceback stays in the host log.

    It lives in core, beside the function that returns it: core callers (the season's end, the
    close timer) use it too, and core imports nothing from the signup module for it.
    """

    #: How many drivers were returned to Not Signed Up, read at the moment of closing.
    returned: int
    #: One sentence per failed step, in the order the steps run. Empty where nothing failed.
    failed: tuple[str, ...] = ()
    #: Why the close was refused, when it was given a window and that window is not the one
    #: still open. Nothing was touched then. ``None`` where the close ran.
    refused: str | None = None
    #: The ids of the drivers returned, in the order the close read them. The change queue's jobs
    #: close each one's signup channel, so the close asked not to hold gives them back.
    returned_ids: tuple[str, ...] = ()


def failed_steps_reply(outcome: ForcedCloseOutcome) -> str:
    """What a reply adds beneath its own text where the close failed a step, or nothing.

    Starts with a line break, so a caller appends it to its sentence unconditionally. It lists
    every step. A close that failed one step per driver (a category the bot can no longer
    manage, say) can fail hundreds, and the whole reply then runs past Discord's 2000
    characters: the caller sends it through ``chunk_message``, in parts, rather than cutting
    the list short, so that the member is told every step the window's close failed.
    """
    if not outcome.failed:
        return ""
    steps = "\n".join(f"• {step}" for step in outcome.failed)
    return "\n⚠️ The window is closed, but not every step succeeded:\n" + steps


def failed_steps_lines(outcome: ForcedCloseOutcome) -> str:
    """What a log line carries beneath its head where the close failed a step, or nothing.

    One indented line per step, each starting with a line break, in the form the line's other
    details take.
    """
    return "".join(f"\n  failed_step: {step}" for step in outcome.failed)


class _Unasked(enum.Enum):
    """The window a close was not asked about: the default of ``execute_forced_close``'s ``window``."""

    UNASKED = enum.auto()


def armed_close_refusal(close_at: str) -> str:
    """Why signups may not be closed by hand while a close time is armed, without its mark.

    It states the armed time and names the command that clears it, as the signup
    specification requires of ``/signup close``. ``/signup close`` and the Confirm Close it
    asks with both refuse with it, so the two cannot word it differently.
    """
    armed = datetime.fromisoformat(close_at)
    if armed.tzinfo is None:
        armed = armed.replace(tzinfo=timezone.utc)
    return (
        f"Signups will auto-close at {discord.utils.format_dt(armed, 'F')} "
        f"({discord.utils.format_dt(armed, 'R')}). "
        "Clear the timer with `/signup close-time cancel` if you need to close manually, or "
        "move it with `/signup close-time modify`."
    )


async def execute_forced_close(
    bot: LeagueBot,
    *,
    audit_action: str,
    window: int | None | _Unasked = _Unasked.UNASKED,
    hold_channels: bool = True,
) -> ForcedCloseOutcome:
    """Force-close the signup window, and return what it did.

    *window* is the Sign Up button message of the window a manager was asked about: the
    confirmation ``/signup close`` shows stands for five minutes, and the window may have changed
    in them (#491). Given one, the close re-reads the configuration first and refuses, touching
    nothing, where signups are no longer open, where they were reopened on another button, or
    where a close time has been armed since (the timer would close the window a second time).
    The reason comes back in the outcome's ``refused``, for the caller to answer and record. A
    caller that was asked nothing passes no window and is not checked.

    *hold_channels* False is the change queue's form: the close notices and locks no signup
    channel itself, leaving each driver it returned (the outcome's ``returned_ids``) to the
    queue's ``signup_notice`` and ``close_signup`` jobs, so that a notice Discord refuses stops
    the queue before the channel is locked, and which leaves the season's stage where it stands
    (step 4b). Off the queue (``/signup close``, the close timer, a restart past the close time,
    turning signup off) it holds them as before, locking the channel and arming its deletion
    whatever became of the notice.

    1. Transition drivers in ``RETURNED_BY_CLOSE`` to NOT_SIGNED_UP.
    2. Delete signup button message (graceful NotFound).
    3. Post "signups are closed" to signup channel.
    4. Set window closed.
    5. Emit audit entry.

    Every step runs whatever the one before did. A step that fails is named in the outcome's
    ``failed`` and its traceback is logged; it is never swallowed. A driver whose transition the
    state machine refuses (``ValueError``) has moved on since the close read them: they are not
    counted as returned, and it is not a failure. Any other error from that transition is.

    The count is of transitions that succeeded, read at the moment of closing. The
    confirmation ``/signup close`` shows may be up to five minutes older than that.
    """
    cfg = await bot.signup_module_service.get_config()
    if window is not _Unasked.UNASKED:
        if cfg is None or not cfg.signups_open:
            return ForcedCloseOutcome(
                returned=0, refused="Signups are no longer open. Nothing was closed."
            )
        if cfg.signup_button_message_id != window:
            return ForcedCloseOutcome(
                returned=0,
                refused="Signups were reopened since this was asked. Nothing was closed. "
                "Run `/signup close` again.",
            )
        if cfg.close_at is not None:
            return ForcedCloseOutcome(returned=0, refused=armed_close_refusal(cfg.close_at))
    if cfg is None:
        return ForcedCloseOutcome(returned=0)

    failed: list[str] = []

    # 1. Transition the drivers still filling in the wizard
    async with get_connection(bot.db_path) as db:
        placeholders = ",".join("?" for _ in RETURNED_BY_CLOSE)
        cursor = await db.execute(
            f"SELECT discord_user_id FROM driver_profiles "
            f"WHERE current_state IN ({placeholders})",
            (*[s.value for s in RETURNED_BY_CLOSE],),
        )
        rows = await cursor.fetchall()

    returned = 0
    returned_ids: list[str] = []
    for row in rows:
        try:
            await bot.driver_service.transition(
                row["discord_user_id"], DriverState.NOT_SIGNED_UP
            )
            returned += 1
            returned_ids.append(str(row["discord_user_id"]))
        except ValueError:
            # The state machine's refusal: the driver moved on since they were read.
            log.info("forced_close: driver %s had moved on", row["discord_user_id"])
        except Exception:
            log.exception("forced_close: failed to transition driver %s", row["discord_user_id"])
            failed.append(
                f"<@{row['discord_user_id']}> could not be returned to Not Signed Up."
            )

    # T046: cancel wizard APScheduler jobs for each force-transitioned driver
    svc = bot.scheduler_service
    for row in rows:
        uid = row["discord_user_id"]
        from leaguebot.signup.services.wizard_service import channel_delete_job_id, inactivity_job_id

        for job_id in (inactivity_job_id(uid), channel_delete_job_id(uid)):
            svc.cancel_job(job_id)

    # Post cancellation notice in each wizard channel and schedule deletion.
    # This mirrors the withdraw() path so drivers see a message and the channel
    # is cleaned up after a 24-hour hold.
    if hold_channels:
        _guild = await league_guild(bot)
        if _guild is None:
            failed.extend(
                f"<@{row['discord_user_id']}> was not told signups had closed." for row in rows
            )
        else:
            _wizard_svc = bot.wizard_service
            for row in rows:
                try:
                    held = await _wizard_svc.trigger_channel_hold(
                        row["discord_user_id"], _guild,
                        "🔒 Signups have closed. This channel will be automatically deleted in 24 hours.",
                        arm_when_refused=True,
                    )
                    if held.channel_id is not None and held.posted is False:
                        # Held and set for deletion all the same (signup spec: closing the window).
                        failed.append(f"<@{row['discord_user_id']}> was not told signups had closed.")
                except Exception:
                    log.exception("forced_close: trigger_channel_hold failed for driver %s", row["discord_user_id"])
                    failed.append(f"<@{row['discord_user_id']}> was not told signups had closed.")

    # 2. Delete button message
    if cfg.signup_button_message_id:
        guild = await league_guild(bot)
        channel = (
            as_text_channel(guild.get_channel(cfg.signup_channel_id))
            if guild and cfg.signup_channel_id is not None
            else None
        )
        if channel:
            try:
                msg = await channel.fetch_message(cfg.signup_button_message_id)
                await msg.delete()
            except discord.NotFound:
                pass
            except Exception:
                log.exception("forced_close: could not delete button message")
                failed.append(_BUTTON_NOT_REMOVED)
        else:
            failed.append(_BUTTON_NOT_REMOVED)

    # 3. Post closed message; capture ID so it can be deleted when re-opening
    closed_msg_id: int | None = None
    guild = await league_guild(bot)
    channel = (
        as_text_channel(guild.get_channel(cfg.signup_channel_id))
        if guild and cfg.signup_channel_id is not None
        else None
    )
    if channel:
        try:
            closed_msg = await channel.send("🔒 Signups are now closed.")
            closed_msg_id = closed_msg.id
        except Exception:
            log.exception("forced_close: could not post closed message")
            failed.append(_NOTICE_NOT_POSTED)
    else:
        failed.append(_NOTICE_NOT_POSTED)

    # 4. Set window closed (persists closed_msg_id)
    await bot.signup_module_service.set_window_closed(closed_msg_id=closed_msg_id)

    # 4b. Move the season on (issue #220). Every close a member or a timer runs reaches here —
    #     the command, the close timer and the restart sweep — so each moves the season alike.
    #     A failure is logged and never undoes the close: the window is shut either way. The
    #     change queue's form (*hold_channels* False) moves it not at all: a season's end closes
    #     the window in the middle of its jobs, and a cancelled season sent on to Pending
    #     completion between them would stand there, refused its own cancellation, should the
    #     queue stop; the change that closes the window saves the stage move itself.
    if hold_channels:
        from leaguebot.core.services.season_lifecycle_service import advance_on_window_close

        try:
            await advance_on_window_close(bot.db_path)
        except Exception:  # noqa: BLE001
            log.exception("forced_close: could not move the season on")
            failed.append("The season could not be moved on.")

    # 5. Audit entry
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection(bot.db_path) as db:
        await db.execute(
            "INSERT INTO audit_entries "
            "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
            "VALUES (?, ?, NULL, ?, ?, ?, ?)",
            (0, "system", audit_action, "open", "closed", now),
        )
        await db.commit()

    return ForcedCloseOutcome(
        returned=returned, failed=tuple(failed), returned_ids=tuple(returned_ids)
    )


#: Why a close nobody ran happened, and the audit action it is recorded under.
_UNATTENDED_CAUSES: dict[str, str] = {
    "timer": "SIGNUP_AUTO_CLOSE",
    "restart": "SIGNUP_AUTO_CLOSE",
    "season end": "SIGNUP_SEASON_END_CLOSE",
    "divisions done": "SIGNUP_DIVISIONS_DONE_CLOSE",
}

_UNATTENDED_HEADS: dict[str, str] = {
    "season end": "🔒 Signups closed as the season ended",
    "divisions done": "🔒 Signups closed as every division is done",
}


async def close_signups_unattended(
    bot: LeagueBot, *, cause: str, hold_channels: bool = True
) -> ForcedCloseOutcome | None:
    """Close the signup window where no member ran the close, and record it in one line.

    *cause* is one of ``"timer"`` (the close time came), ``"restart"`` (the bot came back
    after it), ``"season end"`` and ``"divisions done"``; it picks the audit action the close
    is written under and the line's head. The line names no member, since none closed the
    window, and carries the drivers returned with each failed step beneath, as the closes a
    member runs do. Formed here, beside ``execute_forced_close``, so that no caller forms it:
    the timer and the restart sweep in ``__main__`` and the two season closes keep only the
    call.

    *hold_channels* False is the change queue's form (``execute_forced_close``): the outcome gives
    the drivers it returned and the queue's jobs tell and close each one's signup channel.

    Written only where a window was actually closed: a module that is disabled produces nothing
    (the core specification, Modules), and a window not open needs no close, so both return
    ``None`` having touched nothing. A line that cannot be written is logged, never raised: the
    window is closed either way.
    """
    audit_action = _UNATTENDED_CAUSES[cause]
    if not await bot.module_service.is_signup_enabled():
        return None
    cfg = await bot.signup_module_service.get_config()
    if cfg is None or not cfg.signups_open:
        return None
    close_at = cfg.close_at  # the close clears it, so it is read first

    outcome = await execute_forced_close(
        bot, audit_action=audit_action, hold_channels=hold_channels
    )

    head = _UNATTENDED_HEADS.get(cause, "🔒 Signups closed automatically at their set time")
    if cause in ("timer", "restart") and close_at is not None:
        armed = datetime.fromisoformat(close_at)
        if armed.tzinfo is None:
            armed = armed.replace(tzinfo=timezone.utc)
        head += f" ({discord.utils.format_dt(armed, 'F')})"
    if cause == "restart":
        head += ", at start-up"
    try:
        await bot.output_router.post_log(
            head
            + f"\n  drivers_returned_to_not_signed_up: {outcome.returned}"
            + failed_steps_lines(outcome)
        )
    except Exception:  # noqa: BLE001 — the window is closed either way
        log.warning("could not record in the log channel that signups closed (%s)", cause, exc_info=True)
    return outcome


# ---------------------------------------------------------------------------
# Confirmation for the results → attendance cascade
# ---------------------------------------------------------------------------


def _results_disable_warning(*, season_active: bool, attendance: bool) -> str:
    """The warning shown before results & standings is switched off.

    Built from what is actually at stake rather than fixed, because the two costs are
    independent: a running season loses its results, attendance goes wherever it is on, and a
    league can face either, both, or — between seasons with attendance off — neither.
    """
    lines: list[str] = []

    if season_active:
        lines.append(
            "⚠️ **Disabling Results & Standings destroys this season's results.**\n"
            "The season is running, and switching the module off does not pause it. "
            "If you continue:\n"
            "• every classification recorded this season is deleted, and every standing "
            "computed from them;\n"
            "• every results and standings message already posted is removed from its "
            "channel;\n"
            "• every penalty and appeal verdict already announced is removed from the "
            "verdicts channel, with the banner heading it;\n"
            "• every round still waiting on results, report verdicts or appeal verdicts is "
            "closed as final with no results;\n"
            "• no further results are collected for the rest of the season.\n"
            "**None of this can be undone**, and the module cannot be switched back on until "
            "the season ends.\n"
            "Your points configurations, the season's copy of them, and every division's "
            "channels are kept, and so are any auto-sack and auto-reserve announcements in "
            "the verdicts channel."
        )

    if attendance:
        lines.append(
            "⚠️ **Attendance goes with it.** Attendance depends on Results & Standings and "
            "cannot run alone. If you continue:\n"
            "• every check-in call, reminder and deadline still to come will stop;\n"
            "• every division's check-in and attendance channels will be cleared, and you "
            "will have to set them again;\n"
            "• Attendance cannot be switched back on once a season's placements are confirmed.\n"
            "Timings, penalties and thresholds are kept either way."
        )

    return "\n\n".join(lines)


class _ConfirmDisableResultsView(LeagueView):
    """Confirm disabling results & standings before anything is written.

    Shown wherever the command costs the league something it cannot get back: a running
    season, whose results the disable destroys (issue #167), or an enabled attendance module,
    which the disable takes with it (issue #114). With neither at stake — between seasons, with
    attendance already off — the command disables results straight away, as it always did.
    """

    def __init__(
        self,
        cog: "ModuleCog",
        actor_id: int,
        *,
        cascade_attendance: bool = True,
    ) -> None:
        super().__init__(timeout=120)
        self._cog = cog
        self._actor_id = actor_id
        self._cascade_attendance = cascade_attendance
        self.confirm.label = (
            "✅ Disable both" if cascade_attendance else "✅ Disable and delete the results"
        )

    @discord.ui.button(label="✅ Disable both", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self._actor_id:
            await refuse(interaction, "⛔ Not your action.", what=describe(interaction, button))
            return
        self.stop()
        await self._cog.bot.change_queue.ask(
            module_off("results"),
            {"cascade_attendance": self._cascade_attendance},
            interaction=interaction,
            what=_RESULTS_OFF,
            refusal_what=describe(interaction, button),
        )

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self._actor_id:
            await refuse(interaction, "⛔ Not your action.", what=describe(interaction, button))
            return
        self.stop()
        await interaction.response.send_message(
            f"Cancelled. {self._standing}",
            ephemeral=True,
        )
        await record_abandoned(
            interaction.client,
            interaction.user,
            what="`/module disable`",
            lapsed=False,
            detail=f"{self._standing} Run `/module disable` again to disable results.",
        )

    async def on_timeout(self) -> None:
        """Record that the confirmation lapsed unanswered, naming who started it.

        Nothing was changed, so the line says what stands and to run the command again.
        """
        await record_abandoned(
            self._cog.bot,
            self._actor_id,
            what="`/module disable`",
            lapsed=True,
            detail=f"{self._standing} Run `/module disable` again to disable results.",
        )

    @property
    def _standing(self) -> str:
        """What stands once the confirmation is not given."""
        return (
            "Both modules remain enabled."
            if self._cascade_attendance
            else "Results & Standings remains enabled and nothing was deleted."
        )


# ---------------------------------------------------------------------------
# ModuleCog
# ---------------------------------------------------------------------------


class ModuleCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    module = app_commands.Group(
        name="module",
        description="Enable or disable bot modules for this server.",
        default_permissions=None,
    )

    # ── /module enable ─────────────────────────────────────────────────

    @module.command(
        name="enable",
        description="Enable a bot module for this server.",
    )
    @app_commands.describe(
        module_name="Module to enable",
    )
    @app_commands.choices(module_name=_MODULE_CHOICES)
    @league_admin_only
    async def enable(
        self,
        interaction: discord.Interaction,
        module_name: app_commands.Choice[str],
    ) -> None:

        if await self._refuse_module_change(interaction, module_name.value, "enable"):
            return

        if module_name.value == "weather":
            await self._enable_weather(interaction)
        elif module_name.value == "results":
            await self._enable_results(interaction)
        elif module_name.value == "attendance":
            await self._enable_attendance(interaction)
        elif module_name.value == "images":
            await self._enable_images(interaction)
        else:
            await self._enable_signup(interaction)
        await self._refresh_hub(interaction, describe(interaction))

    # ── /module disable ────────────────────────────────────────────────

    @module.command(
        name="disable",
        description="Disable a bot module for this server.",
    )
    @app_commands.describe(module_name="Module to disable")
    @app_commands.choices(module_name=_MODULE_CHOICES)
    @league_admin_only
    async def disable(
        self,
        interaction: discord.Interaction,
        module_name: app_commands.Choice[str],
    ) -> None:
        if await self._refuse_module_change(interaction, module_name.value, "disable"):
            return

        if module_name.value == "weather":
            await self._disable_weather(interaction)
        elif module_name.value == "results":
            await self._disable_results(interaction)
        elif module_name.value == "attendance":
            await self._disable_attendance(interaction)
        elif module_name.value == "images":
            await self._disable_images(interaction)
        else:
            await self._disable_signup(interaction)
        if module_name.value != "results":
            # Turning results off asks the change queue, which refreshes the panel itself
            # once the flag is down.
            await self._refresh_hub(interaction, describe(interaction))

    async def _refresh_hub(self, interaction: discord.Interaction, what: str) -> None:
        """Bring the hub's panel up to date: a module's options are offered while it is on.

        Run after every enable and disable, the confirmed results disable included, whether
        or not the module offers anything — the hub asks each option, not each module. A
        panel that cannot be refreshed is logged, naming the member and *what* caused the refresh,
        and never fails the toggle behind it. The caller names it: a button's interaction
        carries no command, so `describe` could only say "an interaction" for it.
        """
        from leaguebot.core.services.hub_service import refresh_panel

        try:
            fault = await refresh_panel(self.bot)
        except Exception:  # noqa: BLE001 — see the docstring
            log.exception("module toggle: the hub panel could not be refreshed")
            return
        if fault is not None:
            await self.bot.output_router.post_log(
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | "
                f"{what} | Hub panel not refreshed: {fault}"
            )

    async def _refuse_module_change(
        self,
        interaction: discord.Interaction,
        module: str,
        action: str,
        *,
        record: bool = True,
    ) -> bool:
        """Refuse enabling or disabling a module where the season's stage forbids it.

        Issue #220, in the order a league meets the rules:

        - the signup module is enabled and disabled only with no active season, or while its
          season is in Configuration;
        - no other module is enabled once the season's placements have been confirmed;
        - no module is disabled while the season is in Pending completion.

        Checked here, before the module's own handler, so every module answers alike. Returns
        True where the command was refused, having answered the interaction and, unless
        *record* is False, recorded the refusal in the log channel.
        """
        from leaguebot.core.services.season_lifecycle_service import (
            FROZEN_FOR_COMPLETION_REFUSAL,
            modules_frozen_for_completion,
            configuration_fixed,
        )

        async def _refused(reply: str) -> bool:
            if record:
                await refuse(interaction, reply, what=describe(interaction))
            else:
                await interaction.response.send_message(reply, ephemeral=True)
            return True

        if module == "signup":
            season_number = await configuration_fixed(self.bot.db_path)
            if season_number is not None:
                return await _refused(
                    f"❌ The signup module is fixed for Season {season_number} now that its "
                    f"configuration has been confirmed. It can be {action}d again once the "
                    "season has ended, or while a new season is in configuration."
                )
        elif action == "enable":
            if await self.bot.season_service.get_confirmed_season() is not None:
                return await _refused(
                    "❌ A module cannot be enabled once the season's placements have been "
                    "confirmed. Enable it before then, or once the season has ended."
                )

        if action == "disable" and await modules_frozen_for_completion(
            self.bot.db_path
        ):
            return await _refused(FROZEN_FOR_COMPLETION_REFUSAL)
        return False

    # ── Weather enable (T011) ──────────────────────────────────────────

    async def _enable_weather(
        self, interaction: discord.Interaction
    ) -> None:
        # 1. Guard already-enabled
        if await self.bot.module_service.is_weather_enabled():
            await refuse(
                interaction, "⚠️ Weather module is already enabled.", what=describe(interaction)
            )
            return

        # A season whose placements are confirmed refuses the enable before this handler is
        # reached (issue #220), so there is never a running season to catch up on: its
        # forecast channels are checked, and its phases armed, when placements are confirmed.

        await interaction.response.defer(ephemeral=True)

        # 3. Atomically set flag + audit. The two are one write, so a fault leaves the
        # module off, and the command says so itself (see `report_failure`).
        now = datetime.now(timezone.utc).isoformat()
        try:
            async with get_connection(self.bot.db_path) as db:
                await db.execute(
                    "UPDATE server_configs SET weather_module_enabled = 1",
                )
                await db.execute(
                    "INSERT INTO audit_entries "
                    "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                    "VALUES (?, ?, NULL, 'MODULE_ENABLE', '', ?, ?)",
                    (interaction.user.id, str(interaction.user),
                     json.dumps({"module": "weather"}), now),
                )
                await db.commit()
        except Exception as exc:  # noqa: BLE001 — reported here, to say the module is still off
            await report_failure(
                interaction, exc, what=describe(interaction), outcome=_still_off("weather")
            )
            return

        # 5. Post log channel confirmation
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /module enable weather | Success",
        )
        await interaction.followup.send("✅ Weather module enabled.", ephemeral=True)

    # ── Weather disable (T012) ─────────────────────────────────────────

    async def _disable_weather(
        self, interaction: discord.Interaction
    ) -> None:
        if not await self.bot.module_service.is_weather_enabled():
            await refuse(
                interaction, "⚠️ Weather module is already disabled.", what=describe(interaction)
            )
            return

        await interaction.response.defer(ephemeral=True)

        await self.bot.scheduler_service.cancel_all_weather()
        await self.bot.module_service.set_weather_enabled(False)

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, 'MODULE_DISABLE', ?, '', ?)",
                (interaction.user.id, str(interaction.user),
                 json.dumps({"module": "weather"}), now),
            )
            await db.commit()

        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /module disable weather | Success",
        )
        await interaction.followup.send(
            "✅ Weather module disabled. All scheduled weather jobs have been cancelled.",
            ephemeral=True,
        )

    # ── Results & Standings enable ─────────────────────────────────────

    async def _enable_results(
        self, interaction: discord.Interaction
    ) -> None:
        # 1. Guard already-enabled
        if await self.bot.module_service.is_results_enabled():
            await refuse(
                interaction,
                "⚠️ Results & Standings module is already enabled.",
                what=describe(interaction),
            )
            return

        # 2. Block if ACTIVE season exists (FR-003)
        active_season = await self.bot.season_service.get_confirmed_season()
        if active_season is not None:
            await refuse(
                interaction,
                "❌ Results & Standings module cannot be enabled once a season's placements are confirmed.",
                what=describe(interaction),
            )
            return

        await interaction.response.defer(ephemeral=True)

        # 3. Atomically set flag + audit
        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO results_module_config (id, module_enabled) VALUES (1, 1)"
            )
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, 'MODULE_ENABLE', '', ?, ?)",
                (interaction.user.id, str(interaction.user),
                 json.dumps({"module": "results"}), now),
            )
            await db.commit()

        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /module enable results | Success",
        )
        await interaction.followup.send("✅ Results & Standings module enabled.", ephemeral=True)

    # ── Results & Standings disable ────────────────────────────────────

    async def _disable_results(self, interaction: discord.Interaction) -> None:
        """Disable results & standings, warning first wherever it costs the league something.

        The cascade used to happen unannounced: the reply named results alone and the league
        was told nothing about attendance going with it, its check-in and attendance channels
        being cleared, or its being unable to come back while the season runs. That was the
        silent half of issue #114.

        A running season is the other half, and was silent for longer. Disabling mid-season
        destroys that season's results entire and closes every round still waiting on them
        (issue #167), and a league with attendance already off was shown no warning at all
        before it happened. So the confirmation is now owed to a running season in its own
        right, whatever attendance is doing.
        """
        if not await self.bot.module_service.is_results_enabled():
            await refuse(
                interaction, "⚠️ Results & Standings module is already disabled.", what=describe(interaction)
            )
            return

        # Warn before anything irreversible, and write nothing until the league confirms it.
        active_season = await self.bot.season_service.get_confirmed_season()
        attendance_on = await self.bot.module_service.is_attendance_enabled()

        if active_season is not None or attendance_on:
            await interaction.response.send_message(
                _results_disable_warning(
                    season_active=active_season is not None, attendance=attendance_on
                ),
                view=_ConfirmDisableResultsView(
                    self,
                    interaction.user.id,
                    cascade_attendance=attendance_on,
                ),
                ephemeral=True,
            )
            return

        await self.bot.change_queue.ask(
            module_off("results"),
            {"cascade_attendance": False},
            interaction=interaction,
            what=_RESULTS_OFF,
            refusal_what=describe(interaction),
        )

    # ── Attendance enable ──────────────────────────────────────────────

    async def _enable_attendance(
        self, interaction: discord.Interaction
    ) -> None:
        # 1. Guard: R&S must be enabled first
        if not await self.bot.module_service.is_results_enabled():
            await refuse(
                interaction,
                "❌ The Attendance module requires the Results & Standings module to be enabled first.",
                what=describe(interaction),
            )
            return

        # 2. Guard: no ACTIVE season
        active_season = await self.bot.season_service.get_confirmed_season()
        if active_season is not None:
            await refuse(
                interaction,
                "❌ Attendance module cannot be enabled once a season's placements are confirmed.",
                what=describe(interaction),
            )
            return

        # 3. Guard: already enabled
        if await self.bot.module_service.is_attendance_enabled():
            await refuse(
                interaction, "⚠️ Attendance module is already enabled.", what=describe(interaction)
            )
            return

        await interaction.response.defer(ephemeral=True)

        # 4. Atomically insert config row with defaults + audit entry. One write, so a fault
        # leaves the module off, and the command says so itself (see `report_failure`).
        now = datetime.now(timezone.utc).isoformat()
        try:
            async with get_connection(self.bot.db_path) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO attendance_config "
                    "(id, module_enabled, rsvp_notice_days, rsvp_last_notice_hours, "
                    "rsvp_deadline_hours, no_rsvp_penalty, absent_penalty, no_show_penalty, "
                    "autoreserve_threshold, autosack_threshold) "
                    "VALUES (1, 1, 5, 24, 2, 1, 1, 1, NULL, NULL)"
                )
                await db.execute(
                    "INSERT INTO audit_entries "
                    "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                    "VALUES (?, ?, NULL, 'ATTENDANCE_MODULE_ENABLED', '', '', ?)",
                    (interaction.user.id, str(interaction.user), now),
                )
                await db.commit()
        except Exception as exc:  # noqa: BLE001 — reported here, to say the module is still off
            await report_failure(
                interaction, exc, what=describe(interaction), outcome=_still_off("attendance")
            )
            return

        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /module enable attendance | Success",
        )
        await interaction.followup.send("✅ Attendance module enabled.", ephemeral=True)

    # ── Images enable (T015) ───────────────────────────────────────────

    async def _enable_images(
        self, interaction: discord.Interaction
    ) -> None:
        from leaguebot.image.services.image_render_service import (
            converter_absent_message,
            converter_available,
        )

        if await self.bot.module_service.is_images_enabled():
            await refuse(
                interaction, "⚠️ Image module is already enabled.", what=describe(interaction)
            )
            return

        await interaction.response.defer(ephemeral=True)

        now = datetime.now(timezone.utc).isoformat()
        try:
            # create_with_defaults is idempotent: an existing configuration is left
            # exactly as it was, which is what makes re-enabling lossless (FR-004a).
            await self.bot.image_config_service.create_with_defaults()
            await self.bot.module_service.set_images_enabled(True)
            async with get_connection(self.bot.db_path) as db:
                await db.execute(
                    "INSERT INTO audit_entries "
                    "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                    "VALUES (?, ?, NULL, 'IMAGE_MODULE_ENABLED', '', '', ?)",
                    (interaction.user.id, str(interaction.user), now),
                )
                await db.commit()
        except Exception as exc:  # noqa: BLE001 — undone, then reported to say the module is still off
            # The flag goes back down first. Where that fails too the module cannot be said to be
            # off, and the second fault goes to the command's failure path instead.
            await self.bot.module_service.set_images_enabled(False)
            await report_failure(
                interaction, exc, what=describe(interaction), outcome=_still_off("images")
            )
            return

        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /module enable images | Success",
        )

        message = "✅ Image module enabled. All output aspects start disabled — use `/images config toggle`."
        # The module enables even without the rasteriser, so an administrator can finish
        # configuring while waiting for it to be installed (FR-007, FR-008).
        if not converter_available(use_cache=False):
            message += "\n\n" + converter_absent_message()

        await interaction.followup.send(message, ephemeral=True)

    # ── Images disable (T016) ──────────────────────────────────────────

    async def _disable_images(
        self, interaction: discord.Interaction
    ) -> None:
        """Clear the enabled flag and nothing else.

        No configuration row is deleted, no toggle reset and no notice history purged.
        This is the Principle X.6 exception for configuration that cannot go stale: no
        image config value names a Discord channel, role, message or scheduled job, so
        none can become a stale binding while the module is off (FR-004a). No
        ``--preserve-config`` flag is offered because nothing is cleared (FR-004b).
        """
        if not await self.bot.module_service.is_images_enabled():
            await refuse(
                interaction, "⚠️ Image module is already disabled.", what=describe(interaction)
            )
            return

        await interaction.response.defer(ephemeral=True)

        now = datetime.now(timezone.utc).isoformat()
        await self.bot.module_service.set_images_enabled(False)
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, 'IMAGE_MODULE_DISABLED', '', '', ?)",
                (interaction.user.id, str(interaction.user), now),
            )
            await db.commit()

        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /module disable images | Success",
        )
        await interaction.followup.send(
            "✅ Image module disabled. Every aspect reverts to text output.\n"
            "Your configuration is kept — re-enabling restores it exactly.",
            ephemeral=True,
        )

    # ── Attendance disable ─────────────────────────────────────────────

    async def _disable_attendance(self, interaction: discord.Interaction) -> None:
        if not await self.bot.module_service.is_attendance_enabled():
            await refuse(
                interaction, "⚠️ Attendance module is already disabled.", what=describe(interaction)
            )
            return
        await interaction.response.defer(ephemeral=True)

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await self.bot.attendance_service.switch_off_on(db)
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, 'ATTENDANCE_MODULE_DISABLED', '', '', ?)",
                (interaction.user.id, str(interaction.user), now),
            )
            await db.commit()

        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /module disable attendance | Success",
        )
        await interaction.followup.send("✅ Attendance module disabled.", ephemeral=True)

    # ── Signup enable (T010) ───────────────────────────────────────────

    async def _enable_signup(
        self,
        interaction: discord.Interaction,
    ) -> None:
        # Guard already-enabled
        if await self.bot.module_service.is_signup_enabled():
            await refuse(
                interaction, "⚠️ Signup module is already enabled.", what=describe(interaction)
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Upsert a bare config row — its channel NULL. The league's roles are core's (#276).
        from leaguebot.signup.models.signup_module import SignupModuleConfig
        new_cfg = SignupModuleConfig(
            signup_channel_id=None,
            signups_open=False,
            signup_button_message_id=None,
            selected_tracks=[],
        )
        await self.bot.signup_module_service.save_config(new_cfg)

        # Set enabled + audit
        await self.bot.module_service.set_signup_enabled(True)
        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, 'MODULE_ENABLE', '', ?, ?)",
                (interaction.user.id, str(interaction.user),
                 json.dumps({"module": "signup"}), now),
            )
            await db.commit()

        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /module enable signup | Success",
        )
        # The two roles are the league's (issue #276): named only where still to be set.
        server_cfg = await self.bot.config_service.get_server_config()
        steps = ["  • `/signup channel <channel>`"]
        if server_cfg is None or server_cfg.base_role_id is None:
            steps.append("  • `/bot base-role <role>`")
        if server_cfg is None or server_cfg.driver_role_id is None:
            steps.append("  • `/bot driver-role <role>`")
        await interaction.followup.send(
            "✅ Signup module enabled.\n"
            "Next steps — configure with:\n" + "\n".join(steps),
            ephemeral=True,
        )

    # ── Signup disable (T018) ──────────────────────────────────────────

    async def _disable_signup(
        self, interaction: discord.Interaction
    ) -> None:
        if not await self.bot.module_service.is_signup_enabled():
            await refuse(
                interaction, "⚠️ Signup module is already disabled.", what=describe(interaction)
            )
            return

        await interaction.response.defer(ephemeral=True)

        signup_cfg = await self.bot.signup_module_service.get_config()

        # Force-close if signups are open
        closed: ForcedCloseOutcome | None = None
        if signup_cfg and signup_cfg.signups_open:
            closed = await execute_forced_close(self.bot, audit_action="SIGNUP_FORCE_CLOSE")

        # Cancel any active signup close timer
        self.bot.scheduler_service.cancel_signup_close_timer()

        # Remove bot-applied permission overwrites (only those set by /signup channel)
        if signup_cfg and signup_cfg.signup_channel_id is not None:
            guild = await league_guild(self.bot)
            if guild:
                channel = guild.get_channel(signup_cfg.signup_channel_id)
                if channel and isinstance(channel, discord.TextChannel):
                    targets_to_revert: list[discord.Role | discord.Member] = [
                        guild.default_role, guild.me
                    ]
                    # The base role is the league's and outlives the module (issue #276):
                    # only its overwrite on this channel goes.
                    server_cfg = await self.bot.config_service.get_server_config()
                    if server_cfg and server_cfg.base_role_id is not None:
                        base_role = guild.get_role(server_cfg.base_role_id)
                        if base_role:
                            targets_to_revert.append(base_role)
                    if server_cfg:
                        interaction_role = guild.get_role(server_cfg.interaction_role_id)
                        if interaction_role:
                            targets_to_revert.append(interaction_role)
                    for target in targets_to_revert:
                        try:
                            await channel.set_permissions(target, overwrite=None)
                        except Exception:
                            log.exception(
                                "disable_signup: could not clear overwrite for %s", target
                            )

        # Cancel all wizard inactivity and channel-delete APScheduler jobs for this server
        if signup_cfg:
            active_wizards = await self.bot.signup_module_service.get_all_active_wizards()
            scheduler = self.bot.scheduler_service
            for wiz in active_wizards:
                from leaguebot.signup.services.wizard_service import channel_delete_job_id, inactivity_job_id

                for job_id in (
                    inactivity_job_id(wiz.discord_user_id),
                    channel_delete_job_id(wiz.discord_user_id),
                ):
                    scheduler.cancel_job(job_id)

        # Forget the channel. The time slots and question settings are kept (issue #127).
        await self.bot.signup_module_service.delete_config()

        # Set disabled + audit
        await self.bot.module_service.set_signup_enabled(False)
        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, 'MODULE_DISABLE', ?, '', ?)",
                (interaction.user.id, str(interaction.user),
                 json.dumps({"module": "signup"}), now),
            )
            await db.commit()

        detail = ""
        notes = ""
        if closed is not None:
            detail = (
                f"\n  drivers_returned_to_not_signed_up: {closed.returned}"
                + failed_steps_lines(closed)
            )
            notes = failed_steps_reply(closed)
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /module disable signup | Success"
            + detail,
        )
        for part in chunk_message(
            "✅ Signup module disabled. Its channel has been cleared; its time slots and "
            "question settings are kept, and so are the league's base role and driver role."
            + notes
        ):
            await interaction.followup.send(part, ephemeral=True)
