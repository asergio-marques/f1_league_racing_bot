"""WizardService — signup wizard state machine and lifecycle management.

Handles the per-driver signup channel lifecycle, sequential parameter
collection, inactivity timeouts, correction cycles, and admin review.

All Discord channel mutations (create/delete/hold) are coordinated here.
APScheduler jobs delegate back via module-level callables using the
_GLOBAL_WIZARD_SERVICE sentinel (same pattern as SchedulerService).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import discord
from apscheduler.triggers.date import DateTrigger

from models.driver_profile import DriverState
from models.signup_module import SignupRecord, SignupWizardRecord, WizardState

if TYPE_CHECKING:
    from discord.ext.commands import Bot

    from services.scheduler_service import SchedulerService
    from utils.output_router import OutputRouter

log = logging.getLogger(__name__)

# Module-level sentinel so APScheduler picklable jobs can reach the service.
_GLOBAL_WIZARD_SERVICE: "WizardService | None" = None

# APScheduler job-ID prefixes.
_INACTIVITY_JOB_PREFIX = "wizard_inactivity"
_CHANNEL_DELETE_JOB_PREFIX = "wizard_channel_delete"


# ---------------------------------------------------------------------------
# Module-level APScheduler callables (must be picklable — no closures)
# ---------------------------------------------------------------------------


async def _wizard_inactivity_job(server_id: int, discord_user_id: str) -> None:
    """APScheduler callable for wizard inactivity timeout."""
    if _GLOBAL_WIZARD_SERVICE is None:
        log.warning(
            "_wizard_inactivity_job fired but _GLOBAL_WIZARD_SERVICE is None "
            "(server_id=%s, user=%s) — skipping",
            server_id,
            discord_user_id,
        )
        return
    await _GLOBAL_WIZARD_SERVICE.handle_inactivity_timeout(server_id, discord_user_id)


async def _wizard_channel_delete_job(server_id: int, discord_user_id: str) -> None:
    """APScheduler callable for post-hold channel deletion."""
    if _GLOBAL_WIZARD_SERVICE is None:
        log.warning(
            "_wizard_channel_delete_job fired but _GLOBAL_WIZARD_SERVICE is None "
            "(server_id=%s, user=%s) — skipping",
            server_id,
            discord_user_id,
        )
        return
    await _GLOBAL_WIZARD_SERVICE._execute_channel_delete(server_id, discord_user_id)


# ---------------------------------------------------------------------------
# WizardService
# ---------------------------------------------------------------------------


class WizardService:
    """Manages the full lifecycle of a driver's signup wizard session.

    Dependency wiring:
    - ``set_bot(bot)`` must be called in ``on_ready`` (after services are bound
      to bot) to give this service access to Discord guild objects, driver_service,
      and signup_module_service.
    """

    def __init__(
        self,
        db_path: str,
        scheduler_service: "SchedulerService",
        output_router: "OutputRouter",
    ) -> None:
        self._db_path = db_path
        self._scheduler = scheduler_service
        self._output_router = output_router

        # Late-bound after bot is ready; set via set_bot().
        self._bot: "Bot | None" = None

        # In-memory asyncio task references for correction-parameter timeouts,
        # keyed by (server_id, discord_user_id).
        self._correction_tasks: dict[tuple[int, str], asyncio.Task] = {}

        # Register as the global singleton so APScheduler jobs can reach us.
        global _GLOBAL_WIZARD_SERVICE
        _GLOBAL_WIZARD_SERVICE = self

    def set_bot(self, bot: "Bot") -> None:
        """Bind the bot instance for Discord API and service access."""
        self._bot = bot

    # ------------------------------------------------------------------
    # Convenience accessors (safe after set_bot is called)
    # ------------------------------------------------------------------

    @property
    def _driver_service(self):
        assert self._bot is not None, "WizardService.set_bot() not called"
        return self._bot.driver_service  # type: ignore[attr-defined]

    @property
    def _signup_svc(self):
        assert self._bot is not None, "WizardService.set_bot() not called"
        return self._bot.signup_module_service  # type: ignore[attr-defined]

    def _get_guild(self, server_id: int) -> discord.Guild | None:
        assert self._bot is not None, "WizardService.set_bot() not called"
        return self._bot.get_guild(server_id)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Channel helpers
    # ------------------------------------------------------------------

    async def _create_wizard_channel(
        self,
        guild: discord.Guild,
        member: discord.Member,
        signup_channel_id: int | None,
        interaction_role_id: int | None,
    ) -> discord.TextChannel:
        """Create a private ``<username>-signup`` channel for the driver."""
        safe_name = re.sub(r"[^a-z0-9_-]", "-", member.name.lower())
        channel_name = f"{safe_name}-signup"

        overwrites: dict[Any, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        if interaction_role_id:
            role = guild.get_role(interaction_role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True
                )

        category = None
        if signup_channel_id:
            signup_ch = guild.get_channel(signup_channel_id)
            if signup_ch and isinstance(signup_ch, discord.TextChannel):
                category = signup_ch.category

        channel = await guild.create_text_channel(
            channel_name,
            overwrites=overwrites,
            category=category,
            reason=f"Signup wizard for {member}",
        )
        return channel

    async def _revoke_driver_write(
        self,
        channel: discord.TextChannel,
        member: discord.Member,
    ) -> None:
        """Revoke driver's send_messages permission (channel hold)."""
        await channel.set_permissions(
            member,
            view_channel=True,
            send_messages=False,
            reason="Signup wizard channel hold",
        )

    async def _grant_driver_write(
        self,
        channel: discord.TextChannel,
        member: discord.Member,
    ) -> None:
        """Restore driver's send_messages permission after a button-only step."""
        await channel.set_permissions(
            member,
            view_channel=True,
            send_messages=True,
            reason="Signup wizard button step answered",
        )

    async def _execute_channel_delete(
        self, server_id: int, discord_user_id: str
    ) -> None:
        """Delete the wizard channel and clean up the wizard record."""
        svc = self._signup_svc
        wizard = await svc.get_wizard(server_id, discord_user_id)
        if wizard is None or wizard.signup_channel_id is None:
            return
        guild = self._get_guild(server_id)
        if guild is not None:
            channel = guild.get_channel(wizard.signup_channel_id)
            if channel is not None:
                try:
                    await channel.delete(reason="Signup wizard channel hold expired")
                except discord.HTTPException as exc:
                    log.warning(
                        "_execute_channel_delete: could not delete channel %s: %s",
                        wizard.signup_channel_id,
                        exc,
                    )
        await svc.delete_wizard(server_id, discord_user_id)

    # ------------------------------------------------------------------
    # Inactivity / channel-delete job helpers
    # ------------------------------------------------------------------

    def _inactivity_job_id(self, server_id: int, discord_user_id: str) -> str:
        return f"{_INACTIVITY_JOB_PREFIX}_{server_id}_{discord_user_id}"

    def _channel_delete_job_id(self, server_id: int, discord_user_id: str) -> str:
        return f"{_CHANNEL_DELETE_JOB_PREFIX}_{server_id}_{discord_user_id}"

    async def _arm_inactivity_job(
        self,
        server_id: int,
        discord_user_id: str,
        fire_at: datetime,
    ) -> None:
        """Schedule (or reschedule) the 24-h inactivity APScheduler job."""
        job_id = self._inactivity_job_id(server_id, discord_user_id)
        if fire_at.tzinfo is None:
            fire_at = fire_at.replace(tzinfo=timezone.utc)
        self._scheduler._scheduler.add_job(
            _wizard_inactivity_job,
            trigger=DateTrigger(run_date=fire_at, timezone="UTC"),
            id=job_id,
            replace_existing=True,
            name=f"Wizard inactivity {server_id}/{discord_user_id}",
            kwargs={
                "server_id": server_id,
                "discord_user_id": discord_user_id,
            },
        )
        log.debug("Armed inactivity job %s → %s", job_id, fire_at.isoformat())

    async def _cancel_inactivity_job(
        self, server_id: int, discord_user_id: str
    ) -> None:
        """Remove the inactivity APScheduler job if it exists."""
        job_id = self._inactivity_job_id(server_id, discord_user_id)
        try:
            self._scheduler._scheduler.remove_job(job_id)
            log.debug("Cancelled inactivity job %s", job_id)
        except Exception:
            pass  # Job already fired or never existed

    async def _arm_channel_delete_job(
        self,
        server_id: int,
        discord_user_id: str,
        fire_at: datetime,
    ) -> None:
        """Schedule the 24-h post-hold channel deletion APScheduler job."""
        job_id = self._channel_delete_job_id(server_id, discord_user_id)
        if fire_at.tzinfo is None:
            fire_at = fire_at.replace(tzinfo=timezone.utc)
        self._scheduler._scheduler.add_job(
            _wizard_channel_delete_job,
            trigger=DateTrigger(run_date=fire_at, timezone="UTC"),
            id=job_id,
            replace_existing=True,
            name=f"Wizard channel delete {server_id}/{discord_user_id}",
            kwargs={
                "server_id": server_id,
                "discord_user_id": discord_user_id,
            },
        )
        log.debug("Armed channel-delete job %s → %s", job_id, fire_at.isoformat())

    async def _cancel_channel_delete_job(
        self, server_id: int, discord_user_id: str
    ) -> None:
        """Remove the channel-delete APScheduler job if it exists."""
        job_id = self._channel_delete_job_id(server_id, discord_user_id)
        try:
            self._scheduler._scheduler.remove_job(job_id)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public API stubs (implementations in T020–T049)
    # ------------------------------------------------------------------

    async def start_wizard(
        self,
        interaction: discord.Interaction,
        server_id: int,
    ) -> discord.TextChannel | None:
        """Create the driver's private signup channel and start collection.

        Returns the newly created channel, or None if the module is not
        configured (caller is responsible for sending an error response).
        """
        assert self._bot is not None
        guild = interaction.guild
        member = interaction.user
        assert guild is not None
        assert isinstance(member, discord.Member)

        discord_user_id = str(member.id)

        # T049: delete any existing wizard channel if present
        existing = await self._signup_svc.get_wizard(server_id, discord_user_id)
        if existing and existing.signup_channel_id is not None:
            old_ch = guild.get_channel(existing.signup_channel_id)
            if old_ch is not None:
                try:
                    await old_ch.delete(reason="Driver re-started signup wizard")
                except discord.HTTPException:
                    pass
            # Cancel any old jobs
            await self._cancel_inactivity_job(server_id, discord_user_id)
            await self._cancel_channel_delete_job(server_id, discord_user_id)
            ckey = (server_id, discord_user_id)
            if ckey in self._correction_tasks:
                self._correction_tasks.pop(ckey).cancel()

        # Load configs
        signup_cfg = await self._signup_svc.get_config(server_id)
        server_cfg = await self._bot.config_service.get_server_config(server_id)  # type: ignore[attr-defined]
        if signup_cfg is None:
            return None

        # Create channel
        channel = await self._create_wizard_channel(
            guild,
            member,
            signup_cfg.signup_channel_id,
            server_cfg.interaction_role_id if server_cfg else None,
        )

        # Capture config snapshot
        snapshot = await self._signup_svc.capture_config_snapshot(server_id)

        # Fetch non-reserve default team names for step 6 buttons
        default_teams = await self._bot.team_service.get_default_teams(server_id)  # type: ignore[attr-defined]
        snapshot.team_names = [t.name for t in default_teams if not t.is_reserve]

        # Determine first wizard state (skip nationality if not required)
        first_state = (
            WizardState.COLLECTING_NATIONALITY
            if snapshot.nationality_required
            else WizardState.COLLECTING_PLATFORM
        )
        now_iso = datetime.now(timezone.utc).isoformat()

        # Upsert wizard record
        wizard = SignupWizardRecord(
            id=-1,
            server_id=server_id,
            discord_user_id=discord_user_id,
            wizard_state=first_state,
            signup_channel_id=channel.id,
            config_snapshot=snapshot,
            draft_answers={
                "discord_username": member.name,
                "server_display_name": member.display_name,
            },
            current_lap_track_index=0,
            last_activity_at=now_iso,
        )
        await self._signup_svc.save_wizard(wizard)

        # Transition driver to PENDING_SIGNUP_COMPLETION
        await self._driver_service.transition(
            server_id, discord_user_id, DriverState.PENDING_SIGNUP_COMPLETION
        )

        # Arm inactivity job (+24 h)
        fire_at = datetime.now(timezone.utc) + timedelta(hours=24)
        await self._arm_inactivity_job(server_id, discord_user_id, fire_at)

        # Revoke write if first step is button-only, then post first prompt
        if first_state in WizardService._BUTTON_ONLY_STATES:
            await self._revoke_driver_write(channel, member)
        await channel.send(
            f"Welcome to the signup wizard, {member.mention}!\n"
            + self._prompt_for_state(first_state, snapshot),
            view=self._build_step_view(first_state, server_id, discord_user_id, snapshot.team_names),
        )

        return channel

    async def handle_message(
        self,
        wizard,  # SignupWizardRecord
        message: discord.Message,
    ) -> None:
        """Route an incoming message to the correct per-step handler."""
        _HANDLERS = {
            WizardState.COLLECTING_NATIONALITY:        self._handle_nationality,
            WizardState.COLLECTING_PLATFORM:           self._handle_platform,
            WizardState.COLLECTING_PLATFORM_ID:        self._handle_platform_id,
            WizardState.COLLECTING_AVAILABILITY:       self._handle_availability,
            WizardState.COLLECTING_DRIVER_TYPE:        self._handle_driver_type,
            WizardState.COLLECTING_PREFERRED_TEAMS:    self._handle_preferred_teams,
            WizardState.COLLECTING_PREFERRED_TEAMMATE: self._handle_preferred_teammate,
            WizardState.COLLECTING_LAP_TIME:           self._handle_lap_time,
            WizardState.COLLECTING_NOTES:              self._handle_notes,
        }
        handler = _HANDLERS.get(wizard.wizard_state)
        if handler is None:
            return  # UNENGAGED or unknown state — ignore
        await handler(wizard, message)

    async def commit_wizard(
        self,
        server_id: int,
        discord_user_id: str,
        guild: discord.Guild,
    ) -> None:
        """Commit draft answers to SignupRecord and post admin review panel."""
        wizard = await self._signup_svc.get_wizard(server_id, discord_user_id)
        if wizard is None:
            return

        d = wizard.draft_answers
        record = SignupRecord(
            id=-1,
            server_id=server_id,
            discord_user_id=discord_user_id,
            discord_username=d.get("discord_username"),
            server_display_name=d.get("server_display_name"),
            nationality=d.get("nationality"),
            platform=d.get("platform"),
            platform_id=d.get("platform_id"),
            availability_slot_ids=d.get("availability_slot_ids", []),
            driver_type=d.get("driver_type"),
            preferred_teams=d.get("preferred_teams", []),
            preferred_teammate=d.get("preferred_teammate"),
            lap_times=d.get("lap_times", {}),
            notes=d.get("notes"),
            signup_channel_id=wizard.signup_channel_id,
        )
        await self._signup_svc.save_record(record)

        # Transition driver
        await self._driver_service.transition(
            server_id, discord_user_id, DriverState.PENDING_ADMIN_APPROVAL
        )

        # Set wizard state to UNENGAGED
        wizard.wizard_state = WizardState.UNENGAGED
        wizard.draft_answers = {}
        await self._signup_svc.save_wizard(wizard)

        # Revoke driver write permission (FR-024)
        if wizard.signup_channel_id:
            channel = guild.get_channel(wizard.signup_channel_id)
            member = guild.get_member(int(discord_user_id))
            if channel and isinstance(channel, discord.TextChannel) and member:
                await self._revoke_driver_write(channel, member)

        # Cancel inactivity job (driver is now waiting for admin review)
        await self._cancel_inactivity_job(server_id, discord_user_id)

        # Post admin review panel in channel
        if wizard.signup_channel_id:
            channel = guild.get_channel(wizard.signup_channel_id)
            if channel and isinstance(channel, discord.TextChannel):
                from cogs.admin_review_cog import AdminReviewView  # type: ignore[import]
                slot_labels = {
                    s.slot_sequence_id: s.display_label
                    for s in (wizard.config_snapshot.slots if wizard.config_snapshot else [])
                }
                panel_text = self._format_review_panel(record, slot_labels)
                await channel.send(
                    panel_text,
                    view=AdminReviewView(server_id, discord_user_id, self._bot),  # type: ignore[arg-type]
                )

    async def withdraw(
        self,
        server_id: int,
        discord_user_id: str,
        guild: discord.Guild,
    ) -> None:
        """Voluntarily withdraw from the signup wizard (T040).

        Cancels pending jobs/tasks, transitions driver to NOT_SIGNED_UP,
        posts cancellation notice, and schedules channel deletion.
        FR-033, FR-036.
        """
        # Cancel asyncio correction task if any
        ckey = (server_id, discord_user_id)
        if ckey in self._correction_tasks:
            self._correction_tasks.pop(ckey).cancel()

        # Cancel inactivity APScheduler job
        await self._cancel_inactivity_job(server_id, discord_user_id)

        # Transition driver to NOT_SIGNED_UP
        try:
            await self._driver_service.transition(
                server_id, discord_user_id, DriverState.NOT_SIGNED_UP
            )
        except Exception:
            log.warning("withdraw: driver transition failed for %s/%s", server_id, discord_user_id)

        # Post cancellation notice and hold channel
        await self._trigger_channel_hold(
            server_id, discord_user_id, guild,
            "❌ You have cancelled your signup. "
            "This channel will be automatically deleted in 24 hours.",
        )

    async def approve_signup(
        self,
        server_id: int,
        discord_user_id: str,
        guild: discord.Guild,
        actor: discord.Member,
    ) -> None:
        """Admin approves the signup (T032).

        Grants the signed-up role, transitions driver to UNASSIGNED,
        and holds the channel for 24 hours before deletion.
        FR-040.
        """
        signup_cfg = await self._signup_svc.get_config(server_id)
        if signup_cfg is None:
            return

        # Grant signed-up role
        member = guild.get_member(int(discord_user_id))
        if member is not None and signup_cfg.signed_up_role_id:
            role = guild.get_role(signup_cfg.signed_up_role_id)
            if role is not None:
                try:
                    await member.add_roles(role, reason="Signup approved")
                except discord.HTTPException:
                    log.warning("approve_signup: could not add signed-up role for %s", discord_user_id)

        # Compute and persist total_lap_ms before transitioning state
        signup_record = await self._signup_svc.get_record(server_id, discord_user_id)
        if signup_record is not None and signup_record.lap_times:
            await self._bot.placement_service.store_total_lap_ms(  # type: ignore[attr-defined]
                server_id, discord_user_id, signup_record.lap_times
            )

        # Transition driver to UNASSIGNED
        await self._driver_service.transition(
            server_id, discord_user_id, DriverState.UNASSIGNED
        )

        await self._cancel_inactivity_job(server_id, discord_user_id)

        await self._trigger_channel_hold(
            server_id, discord_user_id, guild,
            f"✅ Your signup has been approved by **{actor.display_name}**! "
            "You are now an Unassigned driver. "
            "This channel will be automatically deleted in 24 hours.",
        )

    async def reject_signup(
        self,
        server_id: int,
        discord_user_id: str,
        guild: discord.Guild,
        actor: discord.Member,
        reason: str = "",
    ) -> None:
        """Admin rejects the signup (T042).

        Posts rejection notice, transitions driver to NOT_SIGNED_UP,
        and holds the channel for 24 hours before deletion.
        FR-041.
        """
        await self._cancel_inactivity_job(server_id, discord_user_id)

        ckey = (server_id, discord_user_id)
        if ckey in self._correction_tasks:
            self._correction_tasks.pop(ckey).cancel()

        try:
            await self._driver_service.transition(
                server_id, discord_user_id, DriverState.NOT_SIGNED_UP
            )
        except Exception:
            log.warning("reject_signup: driver transition failed for %s/%s", server_id, discord_user_id)

        await self._trigger_channel_hold(
            server_id, discord_user_id, guild,
            f"<@{discord_user_id}> ❌ Your signup has been rejected by **{actor.display_name}**."
            + (f"\n**Reason:** {reason}" if reason else "")
            + "\nThis channel will be automatically deleted in 24 hours.",
        )

    async def request_changes(
        self,
        server_id: int,
        discord_user_id: str,
        guild: discord.Guild,
        actor: discord.Member,
        reason: str = "",
    ) -> None:
        """Admin requests correction (T036).

        Transitions driver to AWAITING_CORRECTION_PARAMETER, posts
        CorrectionParameterView, and arms a 5-minute asyncio timeout.
        FR-042, FR-043.
        """
        wizard = await self._signup_svc.get_wizard(server_id, discord_user_id)
        if wizard is None:
            return

        await self._driver_service.transition(
            server_id, discord_user_id, DriverState.AWAITING_CORRECTION_PARAMETER
        )

        # Post CorrectionParameterView in the wizard channel
        channel = (
            guild.get_channel(wizard.signup_channel_id)
            if wizard.signup_channel_id else None
        )
        # Save reason so it appears only when the driver is prompted to re-submit
        if reason:
            wizard.draft_answers["_correction_reason"] = reason
            await self._signup_svc.save_wizard(wizard)

        if isinstance(channel, discord.TextChannel):
            driver_member = guild.get_member(int(discord_user_id))
            mention = driver_member.mention if driver_member else f"<@{discord_user_id}>"
            from cogs.admin_review_cog import CorrectionParameterView  # type: ignore[import]
            await channel.send(
                f"{mention} **{actor.display_name}** has requested a correction.\n"
                "Please select the parameter to correct (5-minute window):",
                view=CorrectionParameterView(server_id, discord_user_id, self._bot),  # type: ignore[arg-type]
            )

        # Arm 5-minute correction selection timeout
        ckey = (server_id, discord_user_id)
        if ckey in self._correction_tasks:
            self._correction_tasks[ckey].cancel()
        self._correction_tasks[ckey] = asyncio.create_task(
            self._correction_timeout_after_delay(server_id, discord_user_id)
        )

    async def select_correction_parameter(
        self,
        server_id: int,
        discord_user_id: str,
        parameter: str,
        guild: discord.Guild,
    ) -> None:
        """Admin selects the parameter to re-collect (T037).

        Cancels the 5-minute asyncio timeout, transitions driver to
        PENDING_DRIVER_CORRECTION, sets WizardState to the target step,
        and posts the re-collection prompt.
        FR-044.
        """
        _PARAM_STATE_MAP: dict[str, WizardState] = {
            "nationality":          WizardState.COLLECTING_NATIONALITY,
            "platform":             WizardState.COLLECTING_PLATFORM,
            "platform_id":          WizardState.COLLECTING_PLATFORM_ID,
            "availability":         WizardState.COLLECTING_AVAILABILITY,
            "driver_type":          WizardState.COLLECTING_DRIVER_TYPE,
            "preferred_teams":      WizardState.COLLECTING_PREFERRED_TEAMS,
            "preferred_teammate":   WizardState.COLLECTING_PREFERRED_TEAMMATE,
            "lap_times":            WizardState.COLLECTING_LAP_TIME,
            "notes":                WizardState.COLLECTING_NOTES,
        }
        target_state = _PARAM_STATE_MAP.get(parameter)
        if target_state is None:
            log.warning("select_correction_parameter: unknown parameter %r", parameter)
            return

        # Cancel the 5-minute selection timeout
        ckey = (server_id, discord_user_id)
        if ckey in self._correction_tasks:
            self._correction_tasks.pop(ckey).cancel()

        wizard = await self._signup_svc.get_wizard(server_id, discord_user_id)
        if wizard is None:
            return

        # Transition driver to PENDING_DRIVER_CORRECTION
        await self._driver_service.transition(
            server_id, discord_user_id, DriverState.PENDING_DRIVER_CORRECTION
        )

        # Configure wizard for single-field re-collection
        correction_reason = wizard.draft_answers.pop("_correction_reason", "")
        wizard.wizard_state = target_state
        wizard.draft_answers["_is_correction"] = True
        if target_state == WizardState.COLLECTING_LAP_TIME:
            wizard.current_lap_track_index = 0
        if target_state == WizardState.COLLECTING_PREFERRED_TEAMS:
            wizard.draft_answers.pop("_pref_teams_step", None)
            wizard.draft_answers["preferred_teams"] = []
        wizard.last_activity_at = datetime.now(timezone.utc).isoformat()
        await self._signup_svc.save_wizard(wizard)

        # Arm inactivity job for PENDING_DRIVER_CORRECTION (T045)
        fire_at = datetime.now(timezone.utc) + timedelta(hours=24)
        await self._arm_inactivity_job(server_id, discord_user_id, fire_at)

        # Post re-collection prompt with step-appropriate view
        channel = (
            guild.get_channel(wizard.signup_channel_id)
            if wizard.signup_channel_id else None
        )
        if isinstance(channel, discord.TextChannel):
            member = guild.get_member(int(discord_user_id))
            mention = member.mention if member else f"<@{discord_user_id}>"
            # Correct write permissions for the target step type
            if target_state in WizardService._BUTTON_ONLY_STATES and member:
                await self._revoke_driver_write(channel, member)
            elif member:
                await self._grant_driver_write(channel, member)
            team_names = wizard.config_snapshot.team_names if wizard.config_snapshot else []
            reason_line = f"\n**Reason:** {correction_reason}" if correction_reason else ""
            await channel.send(
                f"{mention} Please re-submit your **{parameter.replace('_', ' ')}**:{reason_line}\n"
                + self._prompt_for_state(target_state, wizard.config_snapshot, wizard),
                view=self._build_step_view(target_state, server_id, discord_user_id, team_names),
            )

    async def _trigger_channel_hold(
        self,
        server_id: int,
        discord_user_id: str,
        guild: discord.Guild,
        terminal_message: str,
    ) -> None:
        """Revoke driver write access, post terminal message, and schedule
        channel deletion +24 h (T033).

        FR-026, SC-003.
        """
        wizard = await self._signup_svc.get_wizard(server_id, discord_user_id)
        if wizard is None or wizard.signup_channel_id is None:
            return

        channel = guild.get_channel(wizard.signup_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        # Revoke driver write access
        member = guild.get_member(int(discord_user_id))
        if member is not None:
            await self._revoke_driver_write(channel, member)

        # Post terminal notice
        try:
            await channel.send(terminal_message)
        except discord.HTTPException:
            log.warning("_trigger_channel_hold: failed to post terminal message in %s", channel.id)

        # Schedule channel deletion (+24 h)
        fire_at = datetime.now(timezone.utc) + timedelta(hours=24)
        await self._arm_channel_delete_job(server_id, discord_user_id, fire_at)

    async def handle_inactivity_timeout(
        self, server_id: int, discord_user_id: str
    ) -> None:
        """APScheduler callback: 24-h inactivity deadline reached (T043-T045).

        Cancels pending tasks/jobs, transitions driver to NOT_SIGNED_UP,
        and holds the channel.
        FR-047, FR-048.
        """
        # Cancel asyncio correction task if any
        ckey = (server_id, discord_user_id)
        if ckey in self._correction_tasks:
            self._correction_tasks.pop(ckey).cancel()

        guild = self._get_guild(server_id)
        if guild is None:
            log.warning(
                "handle_inactivity_timeout: guild %s not found; skipping channel hold",
                server_id,
            )

        try:
            await self._driver_service.transition(
                server_id, discord_user_id, DriverState.NOT_SIGNED_UP
            )
        except Exception:
            log.warning(
                "handle_inactivity_timeout: transition failed for %s/%s",
                server_id, discord_user_id,
            )

        if guild is not None:
            await self._trigger_channel_hold(
                server_id, discord_user_id, guild,
                "⏰ Your signup session has expired due to inactivity. "
                "This channel will be automatically deleted in 24 hours.",
            )

    async def handle_member_remove(
        self, server_id: int, discord_user_id: str, guild: discord.Guild
    ) -> None:
        """Member left the server while in a wizard session (T047).

        Cancels all pending jobs/tasks, transitions driver to NOT_SIGNED_UP,
        and deletes the channel immediately (no 24-hour hold).
        FR-027.
        """
        wizard = await self._signup_svc.get_wizard(server_id, discord_user_id)
        if wizard is None or wizard.wizard_state == WizardState.UNENGAGED:
            return

        # Cancel all jobs and tasks
        ckey = (server_id, discord_user_id)
        if ckey in self._correction_tasks:
            self._correction_tasks.pop(ckey).cancel()
        await self._cancel_inactivity_job(server_id, discord_user_id)
        await self._cancel_channel_delete_job(server_id, discord_user_id)

        # Transition driver to NOT_SIGNED_UP
        try:
            await self._driver_service.transition(
                server_id, discord_user_id, DriverState.NOT_SIGNED_UP
            )
        except Exception:
            log.warning(
                "handle_member_remove: transition failed for %s/%s",
                server_id, discord_user_id,
            )

        # Delete channel immediately (no hold)
        if wizard.signup_channel_id is not None:
            channel = guild.get_channel(wizard.signup_channel_id)
            if channel is not None:
                try:
                    await channel.delete(reason="Driver left the server")
                except discord.HTTPException:
                    log.warning(
                        "handle_member_remove: could not delete channel %s",
                        wizard.signup_channel_id,
                    )

        await self._signup_svc.delete_wizard(server_id, discord_user_id)
        raise NotImplementedError

    async def recover_wizards(self) -> None:
        """Called in on_ready: re-arm inactivity jobs for non-UNENGAGED wizards.

        For each active wizard, if the inactivity deadline (last_activity_at
        + 24 h) is still in the future, re-arm the APScheduler job with the
        correct fire time.  If it has already passed, call
        handle_inactivity_timeout() immediately in a background task.

        SC-005.
        """
        wizards = await self._signup_svc.get_all_active_wizards_all_servers()
        now = datetime.now(timezone.utc)
        for wizard in wizards:
            if wizard.wizard_state == WizardState.UNENGAGED:
                continue
            last_str = wizard.last_activity_at
            if last_str:
                last = datetime.fromisoformat(last_str)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                fire_at = last + timedelta(hours=24)
            else:
                fire_at = now  # No activity recorded — expire immediately

            server_id = wizard.server_id
            discord_user_id = wizard.discord_user_id
            if fire_at > now:
                await self._arm_inactivity_job(server_id, discord_user_id, fire_at)
            else:
                asyncio.create_task(
                    self.handle_inactivity_timeout(server_id, discord_user_id)
                )

    async def get_wizard_by_channel(
        self, server_id: int, channel_id: int
    ):  # -> SignupWizardRecord | None
        """Look up an active wizard record by channel ID.

        Thin delegation to signup_module_service; exposed here so cogs only
        need a reference to wizard_service.
        """
        return await self._signup_svc.get_wizard_by_channel(server_id, channel_id)

    # ------------------------------------------------------------------
    # Validation helpers (implemented in T020–T021)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_lap_time(raw: str) -> str | None:
        """Normalise a lap-time string to ``M:ss.mmm``."""
        raw = raw.strip()
        # Accept M:ss.mmm or M:ss:mmm (separator can be . or :)
        m = re.fullmatch(r"(\d+):(\d{2})[.:]([0-9]+)", raw)
        if not m:
            return None
        minutes = int(m.group(1))
        seconds = int(m.group(2))
        if seconds >= 60:
            return None
        ms_raw = m.group(3)
        length = len(ms_raw)
        if length < 3:
            ms_val = ms_raw.ljust(3, "0")
        elif length > 3:
            # Half-up rounding to 3 digits
            divisor = 10 ** (length - 3)
            ms_int = int(ms_raw)
            ms_rounded = (ms_int + divisor // 2) // divisor
            if ms_rounded >= 1000:
                seconds += 1
                ms_rounded -= 1000
                if seconds >= 60:
                    minutes += 1
                    seconds -= 60
            ms_val = f"{ms_rounded:03d}"
        else:
            ms_val = ms_raw
        return f"{minutes}:{seconds:02d}.{ms_val}"

    @staticmethod
    def _validate_nationality(raw: str) -> str | None:
        """Accept 2-letter ISO code or 'other'; return normalised lowercase."""
        stripped = raw.strip().lower()
        if stripped == "other":
            return "other"
        if re.fullmatch(r"[a-z]{2}", stripped):
            return stripped
        return None

    _PLATFORMS = ["Steam", "EA", "Xbox", "PlayStation"]
    _DRIVER_TYPES = ["Full-Time Driver", "Reserve Driver"]
    _BUTTON_ONLY_STATES = frozenset({
        WizardState.COLLECTING_PLATFORM,
        WizardState.COLLECTING_DRIVER_TYPE,
        WizardState.COLLECTING_PREFERRED_TEAMS,
    })

    async def _advance_wizard(
        self,
        wizard: SignupWizardRecord,
        message: discord.Message,
    ) -> None:
        """Determine and move to the next collection state; commit when done."""
        assert isinstance(message.channel, discord.TextChannel)
        assert message.guild is not None
        await self._advance_wizard_in_channel(wizard, message.channel, message.guild)

    async def _advance_wizard_in_channel(
        self,
        wizard: SignupWizardRecord,
        channel: discord.TextChannel,
        guild: discord.Guild,
    ) -> None:
        """Core wizard advancement — move to the next state within a known channel."""
        snapshot = wizard.config_snapshot
        assert snapshot is not None

        state = wizard.wizard_state
        _SEQUENCE = [
            WizardState.COLLECTING_NATIONALITY,
            WizardState.COLLECTING_PLATFORM,
            WizardState.COLLECTING_PLATFORM_ID,
            WizardState.COLLECTING_AVAILABILITY,
            WizardState.COLLECTING_DRIVER_TYPE,
            WizardState.COLLECTING_PREFERRED_TEAMS,
            WizardState.COLLECTING_PREFERRED_TEAMMATE,
            WizardState.COLLECTING_LAP_TIME,
            WizardState.COLLECTING_NOTES,
        ]

        idx = _SEQUENCE.index(state)

        def _peek_next(i: int) -> WizardState | None:
            for j in range(i + 1, len(_SEQUENCE)):
                ns = _SEQUENCE[j]
                if ns == WizardState.COLLECTING_NATIONALITY and not snapshot.nationality_required:
                    continue
                if ns == WizardState.COLLECTING_PREFERRED_TEAMS:
                    if wizard.draft_answers.get("driver_type") == "Reserve Driver":
                        continue
                if ns == WizardState.COLLECTING_LAP_TIME:
                    if wizard.current_lap_track_index < len(snapshot.selected_track_ids):
                        return ns  # still tracks to collect
                    else:
                        continue  # skip lap time entirely
                return ns
            return None

        # Exiting a button-only state: restore send_messages for next text step
        if state in WizardService._BUTTON_ONLY_STATES:
            member = guild.get_member(int(wizard.discord_user_id))
            if member:
                await self._grant_driver_write(channel, member)

        # For COLLECTING_LAP_TIME: check if more tracks remain
        if state == WizardState.COLLECTING_LAP_TIME:
            track_ids = snapshot.selected_track_ids
            next_idx = wizard.current_lap_track_index  # already advanced by handler
            if next_idx < len(track_ids):
                # More tracks: stay in LAP_TIME state but for next track
                wizard.last_activity_at = datetime.now(timezone.utc).isoformat()
                await self._signup_svc.save_wizard(wizard)
                await channel.send(
                    self._prompt_for_state(state, snapshot, wizard),
                    view=self._build_step_view(state, wizard.server_id, wizard.discord_user_id, snapshot.team_names),
                )
                await self._reset_inactivity_job(wizard.server_id, wizard.discord_user_id)
                return

        # T039: correction mode — commit correction instead of advancing
        if wizard.draft_answers.pop("_is_correction", False):
            await self._commit_correction(wizard, guild)
            return

        next_state = _peek_next(idx)
        if next_state is None:
            # All steps complete — persist final draft answers before commit reads them back
            await self._signup_svc.save_wizard(wizard)
            await self.commit_wizard(wizard.server_id, wizard.discord_user_id, guild)
            return

        wizard.wizard_state = next_state
        wizard.last_activity_at = datetime.now(timezone.utc).isoformat()
        await self._signup_svc.save_wizard(wizard)
        await self._reset_inactivity_job(wizard.server_id, wizard.discord_user_id)

        # Entering a button-only state: revoke send_messages
        if next_state in WizardService._BUTTON_ONLY_STATES:
            member = guild.get_member(int(wizard.discord_user_id))
            if member:
                await self._revoke_driver_write(channel, member)

        await channel.send(
            self._prompt_for_state(next_state, snapshot, wizard),
            view=self._build_step_view(next_state, wizard.server_id, wizard.discord_user_id, snapshot.team_names),
        )

    async def _reset_inactivity_job(self, server_id: int, discord_user_id: str) -> None:
        fire_at = datetime.now(timezone.utc) + timedelta(hours=24)
        await self._arm_inactivity_job(server_id, discord_user_id, fire_at)

    def _build_step_view(
        self,
        state: WizardState,
        server_id: int,
        discord_user_id: str,
        team_names: list[str] | None = None,
    ) -> discord.ui.View:
        """Return the appropriate view for the given wizard state."""
        from cogs.signup_cog import (  # lazy import — avoid circular
            WithdrawButtonView,
            PlatformButtonView,
            DriverTypeButtonView,
            PreferredTeamsButtonView,
            NoPreferenceTeammateView,
            NoNotesButtonView,
        )
        if state == WizardState.COLLECTING_PLATFORM:
            return PlatformButtonView(server_id, discord_user_id, self._bot)
        if state == WizardState.COLLECTING_DRIVER_TYPE:
            return DriverTypeButtonView(server_id, discord_user_id, self._bot)
        if state == WizardState.COLLECTING_PREFERRED_TEAMS:
            return PreferredTeamsButtonView(server_id, discord_user_id, self._bot, team_names or [])
        if state == WizardState.COLLECTING_PREFERRED_TEAMMATE:
            return NoPreferenceTeammateView(server_id, discord_user_id, self._bot)
        if state == WizardState.COLLECTING_NOTES:
            return NoNotesButtonView(server_id, discord_user_id, self._bot)
        return WithdrawButtonView(server_id, discord_user_id, self._bot)

    async def handle_platform_button(
        self,
        server_id: int,
        discord_user_id: str,
        platform: str,
        guild: discord.Guild,
    ) -> None:
        """Handle a platform button press in Step 2."""
        wizard = await self._signup_svc.get_wizard(server_id, discord_user_id)
        if wizard is None or wizard.wizard_state != WizardState.COLLECTING_PLATFORM:
            return
        wizard.draft_answers["platform"] = platform
        if wizard.signup_channel_id is None:
            return
        channel = guild.get_channel(wizard.signup_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        await self._advance_wizard_in_channel(wizard, channel, guild)

    async def handle_driver_type_button(
        self,
        server_id: int,
        discord_user_id: str,
        driver_type: str,
        guild: discord.Guild,
    ) -> None:
        """Handle a driver-type button press in Step 5."""
        wizard = await self._signup_svc.get_wizard(server_id, discord_user_id)
        if wizard is None or wizard.wizard_state != WizardState.COLLECTING_DRIVER_TYPE:
            return
        wizard.draft_answers["driver_type"] = driver_type
        if wizard.signup_channel_id is None:
            return
        channel = guild.get_channel(wizard.signup_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        await self._advance_wizard_in_channel(wizard, channel, guild)

    async def handle_preferred_teams_button(
        self,
        server_id: int,
        discord_user_id: str,
        team_name: str | None,
        guild: discord.Guild,
    ) -> None:
        """Handle a team button or No Preference press in Step 6 (up to 3 sub-steps)."""
        wizard = await self._signup_svc.get_wizard(server_id, discord_user_id)
        if wizard is None or wizard.wizard_state != WizardState.COLLECTING_PREFERRED_TEAMS:
            return
        if wizard.signup_channel_id is None:
            return
        channel = guild.get_channel(wizard.signup_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        current_step: int = wizard.draft_answers.get("_pref_teams_step", 0)
        current_picks: list[str] = list(wizard.draft_answers.get("preferred_teams") or [])

        if team_name is None:
            # No Preference — finalise with however many picks accumulated so far
            wizard.draft_answers["preferred_teams"] = current_picks
            wizard.draft_answers.pop("_pref_teams_step", None)
            await self._advance_wizard_in_channel(wizard, channel, guild)
            return

        # Record this pick
        current_picks.append(team_name)
        wizard.draft_answers["preferred_teams"] = current_picks
        next_step = current_step + 1

        snapshot = wizard.config_snapshot
        team_names: list[str] = snapshot.team_names if snapshot else []
        remaining = [t for t in team_names if t not in current_picks]

        if next_step >= 3 or not remaining:
            # Done — all 3 picks taken or no teams left
            wizard.draft_answers.pop("_pref_teams_step", None)
            await self._advance_wizard_in_channel(wizard, channel, guild)
            return

        # More sub-steps to go — save and send next sub-step prompt
        wizard.draft_answers["_pref_teams_step"] = next_step
        wizard.last_activity_at = datetime.now(timezone.utc).isoformat()
        await self._signup_svc.save_wizard(wizard)
        await self._reset_inactivity_job(server_id, discord_user_id)

        _ORDINALS = ["1st", "2nd", "3rd"]
        ordinal = _ORDINALS[next_step]
        picks_str = ", ".join(current_picks)
        prompt = (
            f"**Step 6 — {ordinal} Preferred Team** *(so far: {picks_str})*\n"
            f"Select your {ordinal} preferred team, or press **No Preference** to finish."
        )
        from cogs.signup_cog import PreferredTeamsButtonView  # lazy import
        await channel.send(
            prompt,
            view=PreferredTeamsButtonView(
                server_id, discord_user_id, self._bot, team_names, excluded=current_picks
            ),
        )

    async def handle_no_preference_teammate(
        self,
        server_id: int,
        discord_user_id: str,
        guild: discord.Guild,
    ) -> None:
        """Handle the No Preference button press in Step 7."""
        wizard = await self._signup_svc.get_wizard(server_id, discord_user_id)
        if wizard is None or wizard.wizard_state != WizardState.COLLECTING_PREFERRED_TEAMMATE:
            return
        wizard.draft_answers["preferred_teammate"] = None
        if wizard.signup_channel_id is None:
            return
        channel = guild.get_channel(wizard.signup_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        await self._advance_wizard_in_channel(wizard, channel, guild)

    async def _handle_nationality(
        self, wizard: SignupWizardRecord, message: discord.Message
    ) -> None:
        val = self._validate_nationality(message.content)
        if val is None:
            await message.channel.send(
                "❌ Invalid nationality. Please enter a 2-letter country code (e.g. `gb`, `us`) "
                "or `other`."
            )
            return
        wizard.draft_answers["nationality"] = val
        await self._advance_wizard(wizard, message)

    async def _handle_platform(
        self, wizard: SignupWizardRecord, message: discord.Message
    ) -> None:
        raw = message.content.strip()
        # Case-insensitive match
        match = next(
            (p for p in self._PLATFORMS if p.lower() == raw.lower()), None
        )
        if match is None:
            opts = " / ".join(self._PLATFORMS)
            await message.channel.send(
                f"❌ Invalid platform. Please choose one of: {opts}"
            )
            return
        wizard.draft_answers["platform"] = match
        await self._advance_wizard(wizard, message)

    async def _handle_platform_id(
        self, wizard: SignupWizardRecord, message: discord.Message
    ) -> None:
        raw = message.content.strip()
        if not raw:
            await message.channel.send("❌ Platform ID cannot be empty.")
            return
        wizard.draft_answers["platform_id"] = raw
        await self._advance_wizard(wizard, message)

    async def _handle_availability(
        self, wizard: SignupWizardRecord, message: discord.Message
    ) -> None:
        snapshot = wizard.config_snapshot
        assert snapshot is not None
        raw = message.content.strip()
        parts = re.split(r"[,\s]+", raw)
        try:
            selected_ids = [int(p) for p in parts if p]
        except ValueError:
            await message.channel.send("❌ Please enter slot IDs as numbers (e.g. `1 3`).")
            return
        valid_ids = {s.slot_sequence_id for s in snapshot.slots}
        bad = [str(i) for i in selected_ids if i not in valid_ids]
        if bad:
            await message.channel.send(
                f"❌ Unknown slot ID(s): {', '.join(bad)}. "
                f"Valid IDs: {', '.join(str(i) for i in sorted(valid_ids))}"
            )
            return
        if not selected_ids:
            await message.channel.send("❌ Please select at least one time slot.")
            return
        wizard.draft_answers["availability_slot_ids"] = selected_ids
        await self._advance_wizard(wizard, message)

    async def _handle_driver_type(
        self, wizard: SignupWizardRecord, message: discord.Message
    ) -> None:
        raw = message.content.strip()
        match = next(
            (t for t in self._DRIVER_TYPES if t.lower() == raw.lower()), None
        )
        if match is None:
            opts = " / ".join(self._DRIVER_TYPES)
            await message.channel.send(
                f"❌ Please choose one of: {opts}"
            )
            return
        wizard.draft_answers["driver_type"] = match
        await self._advance_wizard(wizard, message)

    async def _handle_preferred_teams(
        self, wizard: SignupWizardRecord, message: discord.Message
    ) -> None:
        assert self._bot is not None
        raw = message.content.strip()
        if raw.lower() == "no preference":
            wizard.draft_answers["preferred_teams"] = []
            await self._advance_wizard(wizard, message)
            return
        # Load non-reserve teams
        teams = await self._bot.team_service.get_default_teams(  # type: ignore[attr-defined]
            wizard.server_id
        )
        non_reserve = [t.name for t in teams if not t.is_reserve]
        # Parse comma/newline-separated list
        parts = [p.strip() for p in re.split(r"[,\n]+", raw) if p.strip()]
        if len(parts) > 3:
            await message.channel.send(
                "❌ Please select up to 3 teams."
            )
            return
        bad = [p for p in parts if p not in non_reserve]
        if bad:
            bad_list = ", ".join(f"`{b}`" for b in bad)
            valid = ", ".join(f"`{t}`" for t in non_reserve)
            await message.channel.send(
                f"❌ Unknown team(s): {bad_list}.\nValid teams: {valid}"
            )
            return
        wizard.draft_answers["preferred_teams"] = parts
        await self._advance_wizard(wizard, message)

    async def _handle_preferred_teammate(
        self, wizard: SignupWizardRecord, message: discord.Message
    ) -> None:
        raw = message.content.strip()
        wizard.draft_answers["preferred_teammate"] = (
            None if raw.lower() == "no preference" else raw
        )
        await self._advance_wizard(wizard, message)

    async def _handle_lap_time(
        self, wizard: SignupWizardRecord, message: discord.Message
    ) -> None:
        snapshot = wizard.config_snapshot
        assert snapshot is not None
        idx = wizard.current_lap_track_index
        if idx >= len(snapshot.selected_track_ids):
            # Shouldn't happen, but skip safely
            await self._advance_wizard(wizard, message)
            return

        # Check image requirement
        if snapshot.time_image_required and not message.attachments:
            await message.channel.send(
                "❌ A screenshot of your lap time is required. "
                "Please attach an image along with your time."
            )
            return

        normalised = self._normalise_lap_time(message.content)
        if normalised is None:
            label = (
                "Time Trial" if snapshot.time_type == "TIME_TRIAL" else "Short Qualification"
            )
            await message.channel.send(
                f"❌ Invalid {label} time. Use format `M:ss.mmm` (e.g. `1:23.456`)."
            )
            return

        track_id = snapshot.selected_track_ids[idx]
        lap_times: dict = wizard.draft_answers.get("lap_times", {})
        lap_times[track_id] = normalised
        wizard.draft_answers["lap_times"] = lap_times
        wizard.current_lap_track_index = idx + 1
        await self._advance_wizard(wizard, message)

    async def _handle_notes(
        self, wizard: SignupWizardRecord, message: discord.Message
    ) -> None:
        raw = message.content.strip()
        if raw.lower() == "no notes":
            wizard.draft_answers["notes"] = None
        elif len(raw) > 50:
            await message.channel.send(
                "❌ Notes must be 50 characters or fewer."
            )
            return
        else:
            wizard.draft_answers["notes"] = raw
        await self._advance_wizard(wizard, message)

    async def handle_no_notes(
        self,
        server_id: int,
        discord_user_id: str,
        guild: discord.Guild,
    ) -> None:
        """Handle the 'No Notes' button press in Step 9."""
        wizard = await self._signup_svc.get_wizard(server_id, discord_user_id)
        if wizard is None or wizard.wizard_state != WizardState.COLLECTING_NOTES:
            return

        is_correction = wizard.draft_answers.pop("_is_correction", False)
        wizard.draft_answers["notes"] = None

        if is_correction:
            await self._commit_correction(wizard, guild)
        else:
            await self._signup_svc.save_wizard(wizard)
            await self.commit_wizard(server_id, discord_user_id, guild)

    # ------------------------------------------------------------------
    # Prompt builder helpers
    # ------------------------------------------------------------------

    def _prompt_for_state(
        self,
        state: WizardState,
        snapshot,  # ConfigSnapshot
        wizard: SignupWizardRecord | None = None,
    ) -> str:
        from models.track import TRACK_IDS  # type: ignore[import-not-found]

        if state == WizardState.COLLECTING_NATIONALITY:
            return (
                "**Step 1 — Nationality**\n"
                "Enter your 2-letter country code (e.g. `gb`, `us`) or `other`."
            )
        if state == WizardState.COLLECTING_PLATFORM:
            return "**Step 2 — Platform**\nSelect your platform using the buttons below."
        if state == WizardState.COLLECTING_PLATFORM_ID:
            return "**Step 3 — Platform ID**\nEnter your platform username / gamertag."
        if state == WizardState.COLLECTING_AVAILABILITY:
            slot_lines = "\n".join(
                f"  `{s.slot_sequence_id}` — {s.display_label}" for s in snapshot.slots
            )
            return (
                "**Step 4 — Availability**\n"
                "Available slots:\n"
                + slot_lines
                + "\nEnter the IDs of the slots you can attend (e.g. `1 3 5`)."
            )
        if state == WizardState.COLLECTING_DRIVER_TYPE:
            return "**Step 5 — Driver Type**\nSelect your driver type using the buttons below."
        if state == WizardState.COLLECTING_PREFERRED_TEAMS:
            return (
                "**Step 6 — 1st Preferred Team**\n"
                "Select your 1st preferred team, or press **No Preference** to skip."
            )
        if state == WizardState.COLLECTING_PREFERRED_TEAMMATE:
            return (
                "**Step 7 — Preferred Teammate**\n"
                "Enter your preferred teammate's username, or press **No Preference**."
            )
        if state == WizardState.COLLECTING_LAP_TIME:
            idx = wizard.current_lap_track_index if wizard else 0
            if snapshot.selected_track_ids and idx < len(snapshot.selected_track_ids):
                track_id = snapshot.selected_track_ids[idx]
                track_name = TRACK_IDS.get(track_id, track_id)
                label = (
                    "Time Trial"
                    if snapshot.time_type == "TIME_TRIAL"
                    else "Short Qualification"
                )
                img_note = " **(attach screenshot)**" if snapshot.time_image_required else ""
                total = len(snapshot.selected_track_ids)
                return (
                    f"**Step 8 — {label} ({idx + 1}/{total}: {track_name})**\n"
                    f"Enter your time in `M:ss.mmm` format (e.g. `1:23.456`).{img_note}"
                )
        if state == WizardState.COLLECTING_NOTES:
            return (
                "**Step 9 — Additional Notes**\n"
                "Press **No Notes** or type any extra notes (max 50 chars)."
            )
        return "Ready for next step."

    # ------------------------------------------------------------------
    # Admin review panel format helper
    # ------------------------------------------------------------------

    @staticmethod
    def _format_review_panel(
        record: SignupRecord,
        slot_labels: dict[int, str] | None = None,
    ) -> str:
        from models.track import TRACK_IDS  # type: ignore[import-not-found]

        if slot_labels:
            availability_str = ", ".join(
                slot_labels.get(i, f"#{i}") for i in record.availability_slot_ids
            ) if record.availability_slot_ids else "None"
        else:
            availability_str = (
                ", ".join(f"#{i}" for i in record.availability_slot_ids)
                if record.availability_slot_ids else "None"
            )
        teams_str = (
            ", ".join(record.preferred_teams)
            if record.preferred_teams else "No Preference"
        )
        lap_lines = (
            "\n".join(
                f"  • {TRACK_IDS.get(tid, tid)}: `{t}`"
                for tid, t in record.lap_times.items()
            )
            if record.lap_times else "  (none required)"
        )
        return (
            "**📋 Signup Review**\n"
            f"**Driver:** <@{record.discord_user_id}> ({record.discord_username})\n"
            f"**Display name:** {record.server_display_name}\n"
            f"**Nationality:** {record.nationality or 'Not collected'}\n"
            f"**Platform:** {record.platform}\n"
            f"**Platform ID:** {record.platform_id}\n"
            f"**Availability:** {availability_str}\n"
            f"**Driver type:** {record.driver_type}\n"
            f"**Preferred teams:** {teams_str}\n"
            f"**Preferred teammate:** {record.preferred_teammate or 'No Preference'}\n"
            f"**Lap times:**\n{lap_lines}\n"
            f"**Notes:** {record.notes or 'None'}\n"
        )

    # ------------------------------------------------------------------
    # Internal correction-timeout helpers (T036, T038)
    # ------------------------------------------------------------------

    async def _correction_timeout_after_delay(
        self, server_id: int, discord_user_id: str
    ) -> None:
        """Wait 5 minutes then fire the correction timeout callback."""
        await asyncio.sleep(300)  # 5 minutes
        await self._correction_timeout_callback(server_id, discord_user_id)

    async def _correction_timeout_callback(
        self, server_id: int, discord_user_id: str
    ) -> None:
        """Auto-revert AWAITING_CORRECTION_PARAMETER → PENDING_ADMIN_APPROVAL (T038).

        Clears the task entry, transitions driver back, and re-posts the
        admin review panel with AdminReviewView.
        FR-043.
        """
        # Clear the task entry
        ckey = (server_id, discord_user_id)
        self._correction_tasks.pop(ckey, None)

        # Transition driver back to PENDING_ADMIN_APPROVAL
        try:
            await self._driver_service.transition(
                server_id, discord_user_id, DriverState.PENDING_ADMIN_APPROVAL
            )
        except Exception:
            log.warning(
                "_correction_timeout_callback: transition failed for %s/%s",
                server_id, discord_user_id,
            )
            return

        # Set wizard state back to UNENGAGED
        wizard = await self._signup_svc.get_wizard(server_id, discord_user_id)
        guild = self._get_guild(server_id)
        if wizard is None or guild is None:
            return

        wizard.wizard_state = WizardState.UNENGAGED
        wizard.draft_answers.pop("_is_correction", None)
        await self._signup_svc.save_wizard(wizard)

        # Re-post admin review panel
        if wizard.signup_channel_id is not None:
            channel = guild.get_channel(wizard.signup_channel_id)
            if isinstance(channel, discord.TextChannel):
                record = await self._signup_svc.get_record(server_id, discord_user_id)
                if record is not None:
                    from cogs.admin_review_cog import AdminReviewView  # type: ignore[import]
                    slot_labels = {
                        s.slot_sequence_id: s.display_label
                        for s in (wizard.config_snapshot.slots if wizard.config_snapshot else [])
                    }
                    await channel.send(
                        "⏰ Parameter selection timed out. Here is the review panel again:",
                    )
                    await channel.send(
                        self._format_review_panel(record, slot_labels),
                        view=AdminReviewView(server_id, discord_user_id, self._bot),  # type: ignore[arg-type]
                    )

    async def _commit_correction(
        self,
        wizard: SignupWizardRecord,
        guild: discord.Guild,
    ) -> None:
        """Commit a single-field correction and return driver to PENDING_ADMIN_APPROVAL.

        Used by _advance_wizard_in_channel when the _is_correction flag is set in
        draft_answers.  Updates the existing SignupRecord with the corrected
        field(s), transitions driver state, and posts a fresh AdminReviewView.
        """
        server_id = wizard.server_id
        discord_user_id = wizard.discord_user_id

        # Load existing signup record and apply corrections from draft_answers
        record = await self._signup_svc.get_record(server_id, discord_user_id)
        if record is None:
            log.warning("_commit_correction: no SignupRecord found for %s/%s", server_id, discord_user_id)
            return

        d = wizard.draft_answers
        if "nationality"         in d: record.nationality        = d["nationality"]
        if "platform"            in d: record.platform           = d["platform"]
        if "platform_id"         in d: record.platform_id        = d["platform_id"]
        if "availability_slot_ids" in d: record.availability_slot_ids = d["availability_slot_ids"]
        if "driver_type"         in d: record.driver_type        = d["driver_type"]
        if "preferred_teams"     in d: record.preferred_teams    = d["preferred_teams"]
        if "preferred_teammate"  in d: record.preferred_teammate = d["preferred_teammate"]
        if "lap_times"           in d: record.lap_times          = d["lap_times"]
        if "notes"               in d: record.notes              = d["notes"]

        await self._signup_svc.save_record(record)

        # Cancel PDC inactivity job
        await self._cancel_inactivity_job(server_id, discord_user_id)

        # Transition driver back to PENDING_ADMIN_APPROVAL
        await self._driver_service.transition(
            server_id, discord_user_id, DriverState.PENDING_ADMIN_APPROVAL
        )

        # Clear correction state from wizard record
        wizard.wizard_state = WizardState.UNENGAGED
        wizard.draft_answers = {}
        await self._signup_svc.save_wizard(wizard)

        # Post fresh admin review panel
        if wizard.signup_channel_id is not None:
            channel = guild.get_channel(wizard.signup_channel_id)
            if isinstance(channel, discord.TextChannel):
                from cogs.admin_review_cog import AdminReviewView  # type: ignore[import]
                slot_labels = {
                    s.slot_sequence_id: s.display_label
                    for s in (wizard.config_snapshot.slots if wizard.config_snapshot else [])
                }
                await channel.send(
                    self._format_review_panel(record, slot_labels),
                    view=AdminReviewView(server_id, discord_user_id, self._bot),  # type: ignore[arg-type]
                )
