"""ModuleCog — /module enable and /module disable commands.

Manages the league's modules.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db.database import get_connection
from models.driver_profile import DriverState
from utils.channel_guard import league_admin_only
from utils.league_server import LeagueView, league_guild
from utils.output_router import _chunk_message

log = logging.getLogger(__name__)

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


async def execute_forced_close(bot: commands.Bot, *, audit_action: str) -> None:
    """Force-close the signup window.

    1. Transition in-progress drivers to NOT_SIGNED_UP.
    2. Delete signup button message (graceful NotFound).
    3. Post "signups are closed" to signup channel.
    4. Set window closed.
    5. Emit audit entry.
    """
    cfg = await bot.signup_module_service.get_config()
    if cfg is None:
        return

    # 1. Transition in-progress drivers (only PENDING_SIGNUP_COMPLETION; approved/correcting
    #    drivers retain their state per FR-002/FR-003)
    in_progress_states = {
        DriverState.PENDING_SIGNUP_COMPLETION,
    }
    async with get_connection(bot.db_path) as db:
        placeholders = ",".join("?" for _ in in_progress_states)
        cursor = await db.execute(
            f"SELECT discord_user_id FROM driver_profiles "
            f"WHERE current_state IN ({placeholders})",
            (*[s.value for s in in_progress_states],),
        )
        rows = await cursor.fetchall()

    for row in rows:
        try:
            await bot.driver_service.transition(
                row["discord_user_id"], DriverState.NOT_SIGNED_UP
            )
        except Exception:
            log.exception("forced_close: failed to transition driver %s", row["discord_user_id"])

    # T046: cancel wizard APScheduler jobs for each force-transitioned driver
    svc = bot.scheduler_service  # type: ignore[attr-defined]
    for row in rows:
        uid = row["discord_user_id"]
        from services.wizard_service import channel_delete_job_id, inactivity_job_id

        for job_id in (inactivity_job_id(uid), channel_delete_job_id(uid)):
            try:
                svc._scheduler.remove_job(job_id)
            except Exception:
                pass  # Job already fired or never existed

    # Post cancellation notice in each wizard channel and schedule deletion.
    # This mirrors the withdraw() path so drivers see a message and the channel
    # is cleaned up after a 24-hour hold.
    _guild = await league_guild(bot)
    if _guild is not None:
        _wizard_svc = bot.wizard_service  # type: ignore[attr-defined]
        for row in rows:
            try:
                await _wizard_svc._trigger_channel_hold(
                    row["discord_user_id"], _guild,
                    "🔒 Signups have closed. This channel will be automatically deleted in 24 hours.",
                )
            except Exception:
                log.exception("forced_close: _trigger_channel_hold failed for driver %s", row["discord_user_id"])

    # 2. Delete button message
    if cfg.signup_button_message_id:
        guild = await league_guild(bot)
        if guild:
            channel = guild.get_channel(cfg.signup_channel_id)
            if channel:
                try:
                    msg = await channel.fetch_message(cfg.signup_button_message_id)
                    await msg.delete()
                except discord.NotFound:
                    pass
                except Exception:
                    log.exception("forced_close: could not delete button message")

    # 3. Post closed message; capture ID so it can be deleted when re-opening
    closed_msg_id: int | None = None
    guild = await league_guild(bot)
    if guild:
        channel = guild.get_channel(cfg.signup_channel_id)
        if channel:
            try:
                closed_msg = await channel.send("🔒 Signups are now closed.")
                closed_msg_id = closed_msg.id
            except Exception:
                log.exception("forced_close: could not post closed message")

    # 4. Set window closed (persists closed_msg_id)
    await bot.signup_module_service.set_window_closed(closed_msg_id=closed_msg_id)

    # 4b. Move the season on (issue #220). Every close reaches here — the command, the
    #     close timer and the restart sweep — so every close moves the season alike. A
    #     failure is logged and never undoes the close: the window is shut either way.
    from services.season_lifecycle_service import advance_on_window_close

    try:
        await advance_on_window_close(bot.db_path)
    except Exception:  # noqa: BLE001
        log.exception("forced_close: could not move the season on")

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
            await interaction.response.send_message("⛔ Not your action.", ephemeral=True)
            return
        self.stop()
        await interaction.response.defer(ephemeral=True)
        await self._cog._apply_results_disable(
            interaction, cascade_attendance=self._cascade_attendance
        )

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self._actor_id:
            await interaction.response.send_message("⛔ Not your action.", ephemeral=True)
            return
        self.stop()
        await interaction.response.send_message(
            "Cancelled. Both modules remain enabled."
            if self._cascade_attendance
            else "Cancelled. Results & Standings remains enabled and nothing was deleted.",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# ModuleCog
# ---------------------------------------------------------------------------


class ModuleCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
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

    async def _refuse_module_change(
        self,
        interaction: discord.Interaction,
        module: str,
        action: str,
    ) -> bool:
        """Refuse enabling or disabling a module where the season's stage forbids it.

        Issue #220, in the order a league meets the rules:

        - the signup module is enabled and disabled only with no active season, or while its
          season is in Configuration;
        - no other module is enabled once the season's placements have been confirmed;
        - no module is disabled while the season is in Pending completion.

        Checked here, before the module's own handler, so every module answers alike. Returns
        True where the command was refused, having answered the interaction.
        """
        from services.season_lifecycle_service import (
            modules_frozen_for_completion,
            configuration_fixed,
        )

        if module == "signup":
            season_number = await configuration_fixed(self.bot.db_path)
            if season_number is not None:
                await interaction.response.send_message(
                    f"❌ The signup module is fixed for Season {season_number} now that its "
                    f"configuration has been confirmed. It can be {action}d again once the "
                    "season has ended, or while a new season is in configuration.",
                    ephemeral=True,
                )
                return True
        elif action == "enable":
            if await self.bot.season_service.get_confirmed_season() is not None:
                await interaction.response.send_message(
                    "❌ A module cannot be enabled once the season's placements have been "
                    "confirmed. Enable it before then, or once the season has ended.",
                    ephemeral=True,
                )
                return True

        if action == "disable" and await modules_frozen_for_completion(
            self.bot.db_path
        ):
            await interaction.response.send_message(
                "❌ No module can be disabled while the season is pending completion. "
                "Complete it with `/season complete` first.",
                ephemeral=True,
            )
            return True
        return False

    # ── Weather enable (T011) ──────────────────────────────────────────

    async def _enable_weather(
        self, interaction: discord.Interaction
    ) -> None:
        # 1. Guard already-enabled
        if await self.bot.module_service.is_weather_enabled():
            await interaction.response.send_message(
                "⚠️ Weather module is already enabled.", ephemeral=True
            )
            return

        # A season whose placements are confirmed refuses the enable before this handler is
        # reached (issue #220), so there is never a running season to catch up on: its
        # forecast channels are checked, and its phases armed, when placements are confirmed.

        await interaction.response.defer(ephemeral=True)

        # 3. Atomically set flag + audit
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
        except Exception as exc:
            await interaction.followup.send(
                f"❌ Weather module enable failed: {exc}. Module remains disabled.",
                ephemeral=True,
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
            await interaction.response.send_message(
                "⚠️ Weather module is already disabled.", ephemeral=True
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
            await interaction.response.send_message(
                "⚠️ Results & Standings module is already enabled.", ephemeral=True
            )
            return

        # 2. Block if ACTIVE season exists (FR-003)
        active_season = await self.bot.season_service.get_confirmed_season()
        if active_season is not None:
            await interaction.response.send_message(
                "❌ Results & Standings module cannot be enabled once a season's placements are confirmed.",
                ephemeral=True,
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
            await interaction.response.send_message(
                "⚠️ Results & Standings module is already disabled.", ephemeral=True
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

        await interaction.response.defer(ephemeral=True)
        await self._apply_results_disable(interaction, cascade_attendance=False)

    async def _apply_results_disable(
        self,
        interaction: discord.Interaction,
        *,
        cascade_attendance: bool,
    ) -> None:
        """Write the results disable, erase the season's results, and report what went.

        *interaction* must already be deferred — this only ever sends a followup, so it
        serves both the plain command path and the confirmation button's.

        The order matters. The flag goes down first, so that nothing the erasure disturbs can
        post again on its way out; the season's results are then deleted; and only then are the
        rounds still awaiting results closed, because closing the last of them finishes its
        division and a division finishing is what lets `/season complete` run. Between seasons
        all three steps are still taken and the last two simply find nothing to do.

        **The flag stays first even though the erasure can fail** (decided 2026-09-21). Written
        last, it would read "on" for as long as the erasure runs — minutes, on a large season,
        every message being a call to Discord — and a round reaching its start in that time
        would open a submission channel nothing then closes, or, starting after the rounds were
        closed, wait on results for ever. Instead the rounds are closed **whether or not the
        erasure finished**: a failure part-way leaves some of the season's messages posted,
        which the league is told to delete by hand, but never a season that cannot complete —
        issue #167 over again.
        """
        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO results_module_config (id, module_enabled) VALUES (1, 0)"
            )
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, 'MODULE_DISABLE', ?, '', ?)",
                (interaction.user.id, str(interaction.user),
                 json.dumps({"module": "results"}), now),
            )
            await db.commit()

        from services.results_purge_service import purge_season_results

        try:
            purged = await purge_season_results(self.bot.db_path, self.bot)
        except Exception:  # noqa: BLE001 — the rounds below must be closed whatever happened
            log.exception("could not erase this season's results")
            purged = None
        closed = await self.bot.season_service.end_rounds_awaiting_results(
            interaction.user.id, str(interaction.user)
        )

        # The division finishing may have been the season's last: a season with a window open or
        # placements to confirm is wound down and moves to Pending completion at once (#220).
        try:
            await self.bot.season_service.wind_down_ongoing(self.bot)
        except Exception:  # noqa: BLE001 — never fail the disabling on the season's next stage
            log.exception("could not wind the season down")

        if purged is None or purged["rounds"]:
            outcome = (
                {"incomplete": True, "rounds_closed": len(closed)}
                if purged is None
                else {**purged, "rounds_closed": len(closed)}
            )
            async with get_connection(self.bot.db_path) as db:
                await db.execute(
                    "INSERT INTO audit_entries "
                    "(actor_id, actor_name, division_id, change_type, old_value, "
                    "new_value, timestamp) "
                    "VALUES (?, ?, NULL, 'RESULTS_SEASON_PURGED', '', ?, ?)",
                    (
                        interaction.user.id,
                        str(interaction.user),
                        json.dumps(outcome),
                        now,
                    ),
                )
                await db.commit()

        # **Every message the bot could not remove is named, with a link** (decided 2026-09-21,
        # #189). Its record went with the season, so this reply and the log are the only places
        # left that can say where it is.
        left_standing = purged["left_standing"] if purged is not None else []
        if purged is None:
            summary = (
                " | Incomplete\n  the erase of this season's results stopped part-way: some "
                "results, standings and verdicts may still be posted\n"
                f"  rounds closed with no results: {len(closed)}"
            )
        elif purged["rounds"]:
            summary = (
                " | Success"
                f"\n  season results deleted: {purged['sessions']} session results, "
                f"{purged['standings']} standings rows, {purged['messages']} messages, "
                f"{purged['verdicts']} verdicts\n"
                f"  rounds closed with no results: {len(closed)}"
            )
        else:
            summary = " | Success"
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /module disable results"
            + summary
            + (
                f"\n  left standing, to delete by hand: {len(left_standing)}\n"
                + "\n".join(f"  {link}" for link in left_standing)
                if left_standing
                else ""
            ),
        )

        season_note = ""
        if purged is None:
            season_note = (
                "\n⚠️ The erase of this season's results stopped part-way, so some of its "
                "results, standings and verdicts may still be posted — delete them by hand. "
                "Every round still waiting on results was closed all the same "
                f"({len(closed)} round(s)), so the season can still be completed."
            )
        elif purged["rounds"]:
            tail = f", {len(closed)} round(s) closed with no results." if closed else "."
            season_note = (
                f"\n🗑️ This season's results are gone: {purged['sessions']} session "
                f"result(s) and {purged['standings']} standings row(s) deleted, "
                f"{purged['messages']} results and standings message(s) and "
                f"{purged['verdicts']} verdict(s) removed" + tail
                + "\nPoints configurations and division channels are kept."
            )
        if left_standing:
            season_note += (
                f"\n⚠️ {len(left_standing)} message(s) could not be removed — delete them "
                "by hand:\n" + "\n".join(left_standing)
            )

        # Cascade: disable attendance if it is currently enabled
        cascaded = (
            cascade_attendance
            and await self.bot.module_service.is_attendance_enabled()
        )
        if cascaded:
            await self._disable_attendance(interaction, cascade=True)
            reply = (
                "✅ Results & Standings module disabled.\n"
                "✅ Attendance module disabled with it. Its per-division check-in and "
                "attendance channels have been cleared; its timings, penalties and "
                "thresholds are kept." + season_note
            )
        else:
            reply = "✅ Results & Standings module disabled." + season_note
        # Split, because a season's worth of links can outrun Discord's limit on one message.
        for chunk in _chunk_message(reply):
            await interaction.followup.send(chunk, ephemeral=True)

    # ── Attendance enable ──────────────────────────────────────────────

    async def _enable_attendance(
        self, interaction: discord.Interaction
    ) -> None:
        # 1. Guard: R&S must be enabled first
        if not await self.bot.module_service.is_results_enabled():
            await interaction.response.send_message(
                "❌ The Attendance module requires the Results & Standings module to be enabled first.",
                ephemeral=True,
            )
            return

        # 2. Guard: no ACTIVE season
        active_season = await self.bot.season_service.get_confirmed_season()
        if active_season is not None:
            await interaction.response.send_message(
                "❌ Attendance module cannot be enabled once a season's placements are confirmed.",
                ephemeral=True,
            )
            return

        # 3. Guard: already enabled
        if await self.bot.module_service.is_attendance_enabled():
            await interaction.response.send_message(
                "⚠️ Attendance module is already enabled.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # 4. Atomically insert config row with defaults + audit entry
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
        except Exception as exc:
            await interaction.followup.send(
                f"❌ Attendance module enable failed: {exc}. Module remains disabled.",
                ephemeral=True,
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
        from services.image_render_service import (
            converter_absent_message,
            converter_available,
        )

        if await self.bot.module_service.is_images_enabled():
            await interaction.response.send_message(
                "⚠️ Image module is already enabled.", ephemeral=True
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
        except Exception as exc:
            await self.bot.module_service.set_images_enabled(False)
            await interaction.followup.send(
                f"❌ Image module enable failed: {exc}. Module remains disabled.",
                ephemeral=True,
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
            await interaction.response.send_message(
                "⚠️ Image module is already disabled.", ephemeral=True
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

    async def _disable_attendance(
        self, interaction: discord.Interaction, *, cascade: bool = False
    ) -> None:

        if not cascade:
            if not await self.bot.module_service.is_attendance_enabled():
                await interaction.response.send_message(
                    "⚠️ Attendance module is already disabled.", ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True)

        change_type = (
            "ATTENDANCE_MODULE_CASCADE_DISABLED" if cascade else "ATTENDANCE_MODULE_DISABLED"
        )
        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute("UPDATE attendance_config SET module_enabled = 0")
            await db.execute("DELETE FROM attendance_division_config")
            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, ?, '', '', ?)",
                (interaction.user.id, str(interaction.user), change_type, now),
            )
            await db.commit()

        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /module disable attendance | Success",
        )
        if not cascade:
            await interaction.followup.send(
                "✅ Attendance module disabled.", ephemeral=True
            )

    # ── Signup enable (T010) ───────────────────────────────────────────

    async def _enable_signup(
        self,
        interaction: discord.Interaction,
    ) -> None:
        # Guard already-enabled
        if await self.bot.module_service.is_signup_enabled():
            await interaction.response.send_message(
                "⚠️ Signup module is already enabled.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Upsert a bare config row — its channel NULL. The league's roles are core's (#276).
        from models.signup_module import SignupModuleConfig
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
            await interaction.response.send_message(
                "⚠️ Signup module is already disabled.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        signup_cfg = await self.bot.signup_module_service.get_config()

        # Force-close if signups are open
        if signup_cfg and signup_cfg.signups_open:
            await execute_forced_close(self.bot, audit_action="SIGNUP_FORCE_CLOSE")

        # Cancel any active signup close timer
        self.bot.scheduler_service.cancel_signup_close_timer()

        # Remove bot-applied permission overwrites (only those set by /signup channel)
        if signup_cfg and signup_cfg.signup_channel_id is not None:
            guild = await league_guild(self.bot)
            if guild:
                channel = guild.get_channel(signup_cfg.signup_channel_id)
                if channel and isinstance(channel, discord.TextChannel):
                    targets_to_revert = [guild.default_role, guild.me]
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
            scheduler = self.bot.scheduler_service._scheduler
            for wiz in active_wizards:
                from services.wizard_service import channel_delete_job_id, inactivity_job_id

                for job_id in (
                    inactivity_job_id(wiz.discord_user_id),
                    channel_delete_job_id(wiz.discord_user_id),
                ):
                    try:
                        scheduler.remove_job(job_id)
                    except Exception:
                        pass

        # Delete config (cascades to settings + slots)
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

        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /module disable signup | Success",
        )
        await interaction.followup.send(
            "✅ Signup module disabled. Its channel has been cleared; its time slots and "
            "question settings are kept, and so are the league's base role and driver role.",
            ephemeral=True,
        )
