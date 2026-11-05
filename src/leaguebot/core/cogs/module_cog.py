"""ModuleCog — /module enable and /module disable commands.

Manages the league's modules.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import ChangeOrigin, module_off, module_on
from leaguebot.core.models.module_dependencies import needs, taken_down_with
from leaguebot.core.utils.channel_guard import league_admin_only
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.interaction_errors import describe, report_failure
from leaguebot.core.utils.league_server import LeagueView
from leaguebot.core.utils.log_lines import record_abandoned, refuse

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

#: How the change that turns results off names itself in its lines.
_RESULTS_OFF = "`/module disable results`"
#: How the change that turns signup off names itself in the acknowledgement.
_SIGNUP_OFF = "`/module disable signup`"


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
        """Ask the change queue to bring the hub's panel up to date: a module's options are
        offered while it is on.

        Run after every enable and disable, the confirmed results disable included, whether
        or not the module offers anything — the hub asks each option, not each module. The refresh
        is the bot's change, named for the member and *what* caused it: a panel Discord refuses
        stops the queue at the refresh and never fails the toggle behind it. The caller names
        it: a button's interaction carries no command, so `describe` could only say "an
        interaction" for it.
        """
        from leaguebot.core.services.hub_service import HUB_REFRESH

        try:
            await self.bot.change_queue.ask(
                HUB_REFRESH,
                {"command": what},
                actor=interaction.user,
                what="refreshing the hub panel",
                origin=ChangeOrigin.BOT,
            )
        except Exception:  # noqa: BLE001 — see the docstring
            log.exception("module toggle: the hub panel could not be asked for")

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

    # ── Weather enable ──────────────────────────────────────────────────

    async def _enable_weather(
        self, interaction: discord.Interaction
    ) -> None:
        # A season whose placements are confirmed refuses the enable before this handler is
        # reached (issue #220), so there is never a running season to catch up on: its
        # forecast channels are checked, and its phases armed, when placements are confirmed.
        # The switch is weather's own change on the queue: this asks for it and does nothing
        # else.
        await self.bot.change_queue.ask(
            module_on("weather"),
            {},
            interaction=interaction,
            what=describe(interaction),
        )

    # ── Weather disable ─────────────────────────────────────────────────

    async def _disable_weather(
        self, interaction: discord.Interaction
    ) -> None:
        # The switch-off is weather's own change on the queue: it lowers the flag
        # and writes its audit entry in one save, then removes the season's weather jobs in a
        # step of its own. This asks for it and does nothing else.
        await self.bot.change_queue.ask(
            module_off("weather"),
            {},
            interaction=interaction,
            what=describe(interaction),
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
        enabled = {"attendance"} if attendance_on else set()
        cascade_attendance = "attendance" in taken_down_with("results", enabled)

        if active_season is not None or cascade_attendance:
            await interaction.response.send_message(
                _results_disable_warning(
                    season_active=active_season is not None, attendance=cascade_attendance
                ),
                view=_ConfirmDisableResultsView(
                    self,
                    interaction.user.id,
                    cascade_attendance=cascade_attendance,
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
        # 1. Guard: the dependency table's needs must be enabled first
        if "results" in needs("attendance") and not await self.bot.module_service.is_results_enabled():
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

        # The switch-off is a change on the queue: where the window is open it closes it first, as
        # jobs, and its switch-off turns the module off, forgets the channel and audits it, then
        # clears the channel's overwrites in a job of its own, so that they go only with the module.
        # The command acknowledges it at once and does nothing else.
        signup_cfg = await self.bot.signup_module_service.get_config()
        window = (
            signup_cfg.signup_button_message_id if signup_cfg and signup_cfg.signups_open else None
        )
        asked = await self.bot.change_queue.ask(
            module_off("signup"),
            {"window": window},
            interaction=interaction,
            what=_SIGNUP_OFF,
            refusal_what=describe(interaction),
        )
        if asked is None:
            return
