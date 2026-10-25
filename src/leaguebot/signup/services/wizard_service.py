"""WizardService — what the signup wizard keeps of a driver's signup channel and its words.

The wizard itself runs as changes on the change queue (`wizard_changes`, `wizard_answers`,
`review_changes`, `closing_steps`), each answer a turn on the driver's lane. What is left here is
what those changes and the commands outside signup still reach: the channel's making, its lock
and its move to a driver's new account, the prompts and the review panel's text, the views a
question carries, and the restart's recovery of the timers.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import discord

from leaguebot.core.utils.in_a_job import posts_in_a_job
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import StepFailedOnDiscord
from leaguebot.core.models.driver_profile import DriverState
from leaguebot.signup.models.signup_module import SignupRecord, SignupWizardRecord, WizardState
from leaguebot.core.utils.input_validator import parse_nationality, parse_time
from leaguebot.results.utils.results_formatter import render_lap_time
from leaguebot.signup.services.closing_steps import (
    DELETE_TIMER,
    INACTIVITY_TIMER,
    ask_channel_delete,
    ask_expiry,
)

if TYPE_CHECKING:
    from leaguebot.core.services.change_queue import ChangeQueue
    from leaguebot.core.utils.league_bot import LeagueBot

    from leaguebot.core.services.scheduler_service import SchedulerService
    from leaguebot.core.services.output_router import OutputRouter

log = logging.getLogger(__name__)

#: Who the review panel may notify: the people it mentions, and never a group (#362). It
#: quotes a driver's free-text answers, which refuse a group mention where they are typed;
#: this withholds the notification as well, the bot holding the permission to mention
#: everybody where the driver does not.
_REVIEW_PANEL_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True)

# The ids a wizard's two timers had as APScheduler jobs, before they became the scheduler
# service's `signup_inactivity` and `signup_channel_delete` timers. Nothing arms a job under
# them; the closing of a window and the switching off of signup still cancel by them.
_INACTIVITY_JOB_PREFIX = "wizard_inactivity"
_CHANNEL_DELETE_JOB_PREFIX = "wizard_channel_delete"


def inactivity_job_id(discord_user_id: str) -> str:
    """A wizard's inactivity job, named by its account: one league, one wizard per account."""
    return f"{_INACTIVITY_JOB_PREFIX}_{discord_user_id}"


def channel_delete_job_id(discord_user_id: str) -> str:
    """A held signup channel's deletion job, named by the account it belongs to."""
    return f"{_CHANNEL_DELETE_JOB_PREFIX}_{discord_user_id}"


#: What a button press is told where the wizard it belongs to is gone, and where the step it
#: answers has been answered.
_NO_WIZARD = "Wizard session not found."
_STEP_ANSWERED = "That step has already been answered."
_TEAM_PICKED = "That team has already been picked."


def _wizard_gone_or_answered(wizard: SignupWizardRecord | None) -> str:
    """Why a button press on a step that is not the wizard's current one did nothing."""
    return _NO_WIZARD if wizard is None else _STEP_ANSWERED


def build_step_view(
    queue: "ChangeQueue",
    state: WizardState,
    discord_user_id: str,
    team_names: list[str] | None = None,
) -> discord.ui.View:
    """The view that answers the wizard's question at *state*, for the driver *discord_user_id*."""
    from leaguebot.signup.services.signup_views import (  # lazy import — avoid circular
        WithdrawButtonView,
        PlatformButtonView,
        DriverTypeButtonView,
        PreferredTeamsButtonView,
        NoPreferenceTeammateView,
        NoNotesButtonView,
    )
    if state == WizardState.COLLECTING_PLATFORM:
        return PlatformButtonView(discord_user_id, queue)
    if state == WizardState.COLLECTING_DRIVER_TYPE:
        return DriverTypeButtonView(discord_user_id, queue)
    if state == WizardState.COLLECTING_PREFERRED_TEAMS:
        return PreferredTeamsButtonView(discord_user_id, queue, team_names or [])
    if state == WizardState.COLLECTING_PREFERRED_TEAMMATE:
        return NoPreferenceTeammateView(discord_user_id, queue)
    if state == WizardState.COLLECTING_NOTES:
        return NoNotesButtonView(discord_user_id, queue)
    return WithdrawButtonView(discord_user_id, queue)


# ---------------------------------------------------------------------------
# WizardService
# ---------------------------------------------------------------------------


#: The answer a manager may ask a driver to correct, by the key the review's buttons carry, and
#: the wizard step that re-collects it. `_commit_correction` reads it backwards to name what was
#: corrected.
_CORRECTION_PARAMETER_STATES: dict[str, WizardState] = {
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

#: Each correctable answer by the label its review button shows, which the log names it by:
#: "Correction submitted: Lap Times". The cog's `CorrectionParameterView._PARAMETERS` carries the
#: same labels, and its choice line, "Correction requested: Lap Times", writes them the same way.
_CORRECTION_LABELS: dict[str, str] = {
    "nationality":          "Nationality",
    "platform":             "Platform",
    "platform_id":          "Platform ID",
    "availability":         "Availability",
    "driver_type":          "Driver Type",
    "preferred_teams":      "Preferred Teams",
    "preferred_teammate":   "Preferred Teammate",
    "lap_times":            "Lap Times",
    "notes":                "Notes",
}

@dataclass(frozen=True)
class HoldOutcome:
    """What `WizardService.trigger_channel_hold` did, for a caller to name in its own line.

    *channel_id* is None where there was nothing to hold (no wizard, or no text channel).
    *posted* says the closing notice went out; *locked* that the driver's typing was taken away;
    *reason* names the kind of Discord fault that refused the notice, when it was refused.
    """

    channel_id: int | None
    posted: bool = False
    locked: bool = False
    reason: str | None = None


class WizardService:
    """Manages the full lifecycle of a driver's signup wizard session.

    Dependency wiring:
    - ``set_bot(bot)`` gives this service access to Discord guild objects, driver_service
      and signup_module_service. ``__main__.py`` calls it as soon as the service is built, before
      the gateway opens: a press on a signup button can arrive while ``on_ready`` is still
      recovering, and must find the bot already bound (#228).
    """

    def __init__(
        self,
        db_path: str,
        scheduler_service: "SchedulerService",
        output_router: "OutputRouter",
        *,
        change_queue: "ChangeQueue",
    ) -> None:
        self._db_path = db_path
        self._scheduler = scheduler_service
        # The one way a held channel's deletion is armed (the `signup_channel_delete` timer, a
        # one-off with no lateness limit), for a lock the season's end asks.
        self._arm_deletion = scheduler_service.arm_once
        self._output_router = output_router
        # Handed the change queue when it is built, for the views it makes and the changes it asks
        # (docs/design/architecture.md, "Services stay attached to the bot, and are built in one place").
        self._change_queue = change_queue

        # Late-bound after bot is ready; set via set_bot().
        self._bot: "LeagueBot | None" = None

    def set_bot(self, bot: "LeagueBot") -> None:
        """Bind the bot instance for Discord API and service access."""
        self._bot = bot


    # ------------------------------------------------------------------
    # Convenience accessors (safe after set_bot is called)
    # ------------------------------------------------------------------

    @property
    def _signup_svc(self):
        assert self._bot is not None, "WizardService.set_bot() not called"
        return self._bot.signup_module_service

    # ------------------------------------------------------------------
    # Channel helpers
    # ------------------------------------------------------------------

    @staticmethod
    @posts_in_a_job
    async def _create_wizard_channel(
        guild: discord.Guild,
        member: discord.Member,
        signup_channel_id: int | None,
        interaction_role_id: int | None,
        league_admin_role_id: int | None = None,
    ) -> discord.TextChannel:
        """Create a private ``<username>-signup`` channel for the driver.

        Visible to the driver, the bot, and both tiers of the league. The admin review panel
        is posted into this channel, so a league admin who does not also hold the interaction
        role would otherwise be unable to see the signup they are entitled to approve
        (issue #116).
        """
        safe_name = re.sub(r"[^a-z0-9_-]", "-", member.name.lower())
        channel_name = f"{safe_name}-signup"

        overwrites: dict[Any, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        for role_id in (interaction_role_id, league_admin_role_id):
            if not role_id:
                continue
            role = guild.get_role(role_id)
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

    @staticmethod
    async def _revoke_driver_write(
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


    # ------------------------------------------------------------------
    # Inactivity / channel-delete job helpers
    # ------------------------------------------------------------------








    # ------------------------------------------------------------------
    # A held channel follows the driver's current account (issue #243)
    # ------------------------------------------------------------------

    async def move_held_channel(
        self, from_account: str, to_account: str, guild: discord.Guild
    ) -> list[str]:
        """Move the driver's held signup channel, and its deletion, to their new current account.

        After an approval or a rejection the channel stays open, read-only, for 24 hours. A
        reassign cannot happen while a signup is still in progress, so any channel the
        replaced account holds is such a held one. It moves to the new account, and its
        deletion is kept at the moment it was already due.

        Where the new account holds a held channel of its own, that one is kept and the
        replaced account's is deleted, by the `signup.channel.delete` change asked for it on the
        command's lane: its final message has been posted, and the driver can keep only one
        (decided 2026-09-18).

        **The order is what survives a crash part-way.** The new deletion timer is armed first
        (through `arm_once`, as the closing's `arm_deletion` arms the hold), then the record
        re-keyed, then the old timer removed. At every point one job can find
        the record — a job whose record has moved simply finds none — and the channel is
        never left with nothing to delete it.

        Returns what Discord would not allow, for the command to report.
        """
        svc = self._signup_svc
        scheduler = self._scheduler
        problems: list[str] = []
        held = await svc.get_wizard(from_account)
        if held is None or held.signup_channel_id is None:
            return problems
        theirs = await svc.get_wizard(to_account)

        if theirs is not None and theirs.signup_channel_id is not None:
            # The replaced account's channel is deleted by the change asked for it, on the
            # command's lane; its timer goes once the change is saved, so that the channel is
            # never left with nothing to delete it.
            await ask_channel_delete(self._change_queue, self._db_path, from_account, own_lane=False)
            scheduler.disarm(DELETE_TIMER, from_account)
            return problems

        fire_at = (
            scheduler.timer_due(DELETE_TIMER, from_account)
            or (datetime.now(timezone.utc) + timedelta(hours=24))
        )
        scheduler.arm_once(DELETE_TIMER, to_account, fire_at)
        # An abandoned draft on the new account (the transient state of a signup never begun)
        # goes in the same save as the move, which it would otherwise collide with.
        await svc.rekey_wizard(from_account, to_account)
        scheduler.disarm(DELETE_TIMER, from_account)

        channel = as_text_channel(guild.get_channel(held.signup_channel_id))
        if channel is None:
            return problems
        try:
            new_member = guild.get_member(int(to_account))
            if new_member is not None:
                await self._revoke_driver_write(channel, new_member)
            old_member = guild.get_member(int(from_account))
            if old_member is not None:
                await channel.set_permissions(
                    old_member, overwrite=None, reason="Driver's current account changed"
                )
        except discord.HTTPException as exc:
            problems.append(f"the signup channel's permissions could not be moved: {exc}")
        return problems

    @posts_in_a_job
    async def trigger_channel_hold(
        self,
        discord_user_id: str,
        guild: discord.Guild,
        terminal_message: str,
        *,
        lock: bool = True,
        raise_on_failure: bool = False,
    ) -> HoldOutcome:
        """Post the terminal notice, then hold the channel: revoke the driver's write access and
        schedule the channel's deletion +24 h (T033).

        FR-026, SC-003.

        **The notice is posted first**, so a driver is never locked out of a channel that says
        nothing. No wizard, or a channel that is not a text channel, leaves nothing done and gives
        `HoldOutcome(channel_id=None)`. Where Discord refuses the notice:

        - with *raise_on_failure* (the change queue's notice job) `StepFailedOnDiscord` is raised
          from the refusal, having locked and armed nothing, so a Retry finds the channel as it
          was;
        - by default (withdrawal, approval, rejection and expiry) the driver's typing is locked and
          **no deletion is armed**: the channel stays readable for a manager to delete by hand, and
          the outcome says so for the caller's log line.

        *lock=False* posts the notice alone, leaving the lock and the deletion to `lock_signup_channel`.
        """
        wizard = await self._signup_svc.get_wizard(discord_user_id)
        if wizard is None or wizard.signup_channel_id is None:
            return HoldOutcome(channel_id=None)

        channel = guild.get_channel(wizard.signup_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return HoldOutcome(channel_id=None)

        try:
            await channel.send(terminal_message)
        except discord.HTTPException as exc:
            if raise_on_failure:
                raise StepFailedOnDiscord(
                    f"the closing notice could not be posted in channel {channel.id}"
                ) from exc
            log.warning("trigger_channel_hold: failed to post terminal message in %s", channel.id)
            reason = type(exc).__name__
            if lock:
                await self._lock_channel(discord_user_id, guild, channel, arm_deletion=False)
            return HoldOutcome(channel.id, posted=False, locked=lock, reason=reason)

        if lock:
            await self._lock_channel(discord_user_id, guild, channel, arm_deletion=True)
        return HoldOutcome(channel.id, posted=True, locked=lock)

    async def _lock_channel(
        self,
        discord_user_id: str,
        guild: discord.Guild,
        channel: discord.TextChannel,
        *,
        arm_deletion: bool,
    ) -> None:
        """Revoke the driver's write access and, when asked, arm the deletion 24 hours on."""
        member = guild.get_member(int(discord_user_id))
        if member is not None:
            await self._revoke_driver_write(channel, member)
        if arm_deletion:
            self._arm_deletion(
                DELETE_TIMER, discord_user_id, datetime.now(timezone.utc) + timedelta(hours=24)
            )

    async def lock_signup_channel(self, discord_user_id: str, guild: discord.Guild) -> None:
        """Lock a driver's signup channel and arm its deletion 24 hours on, posting nothing.

        The change queue's `close_signup` job, after its `signup_notice` job. No wizard, or no
        channel, is nothing to lock. A permission write Discord refuses raises
        `StepFailedOnDiscord` from the refusal.
        """
        wizard = await self._signup_svc.get_wizard(discord_user_id)
        if wizard is None or wizard.signup_channel_id is None:
            return
        channel = guild.get_channel(wizard.signup_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            await self._lock_channel(discord_user_id, guild, channel, arm_deletion=True)
        except discord.HTTPException as exc:
            raise StepFailedOnDiscord(
                f"the signup channel {channel.id} could not be locked"
            ) from exc


    async def recover_wizards(self) -> None:
        """Called in on_ready: re-arm inactivity jobs for non-UNENGAGED wizards.

        For each active wizard, if the inactivity deadline (last_activity_at
        + 24 h) is still in the future, re-arm the signup's timer with the
        correct fire time.  If it has already passed, ask the queue, as the bot, for the signup's
        expiry (`signup.end`), before the queue starts; the change's check drops it where the
        driver has since moved on.

        A signup whose channel was deleted while the bot was down is ended first
        (:meth:`recover_gone_channels`), and neither its expiry nor its correction's lapse is
        then asked.

        SC-005.
        """
        gone = await self.recover_gone_channels()
        await self.recover_correction_timeouts(skip=gone)

        wizards = await self._signup_svc.get_all_active_wizards_all_servers()
        now = datetime.now(timezone.utc)
        for wizard in wizards:
            if wizard.wizard_state == WizardState.UNENGAGED:
                continue
            if wizard.discord_user_id in gone:
                continue
            last_str = wizard.last_activity_at
            if last_str:
                last = datetime.fromisoformat(last_str)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                fire_at = last + timedelta(hours=24)
            else:
                fire_at = now  # No activity recorded — expire immediately

            discord_user_id = wizard.discord_user_id
            if fire_at > now:
                self._scheduler.arm_once(INACTIVITY_TIMER, discord_user_id, fire_at)
            else:
                await ask_expiry(self._change_queue, self._db_path, discord_user_id)

    async def recover_gone_channels(self) -> set[str]:
        """Called first in recover_wizards: end the signup of every driver whose signup channel
        was deleted while the bot was down (#439, slice 8).

        Every driver whose signup is under way (`IN_PROGRESS_STATES`) and whose wizard names a
        channel is looked at. A channel the cache lacks is confirmed with `fetch_channel`: not
        found, or found as no text channel, is gone, and the restart asks the queue, as the bot
        and before it starts, for the ending of that signup (`signup.end`, reason `CHANNEL_GONE`,
        found at a restart), whose check drops it where the driver has since moved on. Any other
        failure of the lookup is logged and the channel treated as standing: a sweep cannot stop
        the queue, and the deletion listener or the next restart tries again. With no league
        server in the cache nothing is ended, and a warning says so.

        Gives the accounts it asked an ending of, which the rest of the recovery skips.
        """
        from leaguebot.core.utils.league_server import league_guild
        from leaguebot.core.utils.log_lines import name_of_member
        from leaguebot.signup.services.signup_views import IN_PROGRESS_STATES
        from leaguebot.signup.services.wizard_changes import RESTART, ask_channel_gone

        asked: set[str] = set()
        assert self._bot is not None, "WizardService.set_bot() not called"
        placeholders = ", ".join("?" for _ in IN_PROGRESS_STATES)
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT d.discord_user_id, w.signup_channel_id "
                "FROM driver_profiles d JOIN signup_wizard_records w "
                "  ON w.discord_user_id = d.discord_user_id "
                f"WHERE d.current_state IN ({placeholders}) "
                "  AND w.signup_channel_id IS NOT NULL",
                tuple(state.value for state in IN_PROGRESS_STATES),
            )
            rows = await cursor.fetchall()
        if not rows:
            return asked
        guild = await league_guild(self._bot)
        if guild is None:
            log.warning(
                "recover_gone_channels: the league's server is not in the cache, so no signup "
                "channel was looked for"
            )
            return asked
        for row in rows:
            user_id = row["discord_user_id"]
            channel_id = int(row["signup_channel_id"])
            try:
                if guild.get_channel(channel_id) is not None:
                    continue
                try:
                    fetched = await guild.fetch_channel(channel_id)
                except discord.NotFound:
                    fetched = None
                if isinstance(fetched, discord.TextChannel):
                    continue
                await ask_channel_gone(
                    self._change_queue,
                    user_id=user_id,
                    channel_id=channel_id,
                    named=await name_of_member(self._bot, int(user_id)),
                    found=RESTART,
                )
                asked.add(user_id)
            except discord.HTTPException:
                log.warning(
                    "recover_gone_channels: signup channel %s of %s could not be looked for; "
                    "treated as standing", channel_id, user_id, exc_info=True,
                )
            except Exception:
                log.exception(
                    "recover_gone_channels: could not ask the ending of %s's signup", user_id,
                )
        return asked

    async def recover_correction_timeouts(self, *, skip: frozenset[str] | set[str] = frozenset()) -> None:
        """Called from recover_wizards: release every driver awaiting a correction parameter,
        save those in *skip*, whose signup the restart has just ended for a channel gone.

        The five-minute selection window is a one-off timer, so a restart discards it and nothing
        re-creates it — which left the driver parked for good, shown in no listing and refused by
        their own Sign Up button (issue #129). The restart asks the queue, as the bot and before
        it starts, for the lapse of each such driver (`signup.correction.lapse`, with *restart*
        set, which words the notice).

        A restart releases them **unconditionally** rather than re-arming whatever time was
        left. The state exists only while a manager is mid-decision, and a manager whose
        bot has restarted is no longer mid-decision (decided 2026-09-15). That is also why
        nothing here reads a stored deadline, and why a driver stranded by the old
        behaviour — whose record predates any of this — is released on the same path.

        These drivers cannot come from :meth:`recover_wizards`' own iteration: the wizard
        record is UNENGAGED for the whole window, and
        ``get_all_active_wizards_all_servers`` filters that out in SQL. The driver profile
        is the only record that the window is open, so it is what is read here.
        """
        from leaguebot.signup.services.review_changes import ask_lapse

        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT discord_user_id FROM driver_profiles WHERE current_state = ?",
                (DriverState.AWAITING_CORRECTION_PARAMETER.value,),
            )
            rows = await cursor.fetchall()

        for row in rows:
            if row["discord_user_id"] in skip:
                continue
            try:
                await ask_lapse(self._change_queue, row["discord_user_id"], restart=True)
            except Exception:
                # One driver's refused ask must not strand the rest.
                log.exception(
                    "recover_correction_timeouts: could not ask the lapse of %s",
                    row["discord_user_id"],
                )

    async def get_wizard_by_channel(
        self, channel_id: int
    ):  # -> SignupWizardRecord | None
        """Look up an active wizard record by channel ID.

        Thin delegation to signup_module_service; exposed here so cogs only
        need a reference to wizard_service.
        """
        return await self._signup_svc.get_wizard_by_channel(channel_id)

    # ------------------------------------------------------------------
    # Validation helpers (implemented in T020–T021)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_lap_time(raw: str) -> str | None:
        """A lap time in the one form the bot reads times in, ``M:SS.mmm``, or None (#362).

        Strict, as the results paste is, by the shared parser: always a dot and exactly three
        digits after it. `1:23:456` and `1:23.4` were once read here and guessed at; they are
        refused, and the driver asked again, as the prompt has always shown `1:23.456`.
        """
        ms = parse_time(raw)
        return None if ms is None else render_lap_time(ms)

    @staticmethod
    def _validate_nationality(raw: str) -> str | None:
        """Accept nationality adjective, country name, or 'other'; return canonical Title-Case string."""
        return parse_nationality(raw)

    #: The wizard steps answered by pressing a button, where typing is not an answer.
    _BUTTON_ONLY_STATES = frozenset({
        WizardState.COLLECTING_PLATFORM,
        WizardState.COLLECTING_DRIVER_TYPE,
        WizardState.COLLECTING_PREFERRED_TEAMS,
    })




    def _build_step_view(
        self,
        state: WizardState,
        discord_user_id: str,
        team_names: list[str] | None = None,
    ) -> discord.ui.View:
        """Return the appropriate view for the given wizard state."""
        return build_step_view(self._change_queue, state, discord_user_id, team_names)





















    # ------------------------------------------------------------------
    # Prompt builder helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _prompt_for_state(
        state: WizardState,
        snapshot,  # ConfigSnapshot
        wizard: SignupWizardRecord | None = None,
        track_name_map: dict[str, str] | None = None,
    ) -> str:

        if state == WizardState.COLLECTING_NATIONALITY:
            return (
                "**Step 1 — Nationality**\n"
                "Enter your nationality (e.g. `British`) or country name (e.g. `United Kingdom`), or type `other`."
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
                track_name = (track_name_map or {}).get(track_id, track_id)
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
        slot_labels: dict[str, str] | None = None,
        track_name_map: dict[str, str] | None = None,
    ) -> str:
        # slot_labels is keyed by durable slot ID ("Mon_19_00"), which is a storage
        # form no league should ever read. An answer with no label is a time that no
        # longer exists, so it is named as such rather than printed raw.
        if record.availability_slot_ids:
            availability_str = ", ".join(
                (slot_labels or {}).get(slot_id, "Unknown slot")
                for slot_id in record.availability_slot_ids
            )
        else:
            availability_str = "None"
        teams_str = (
            ", ".join(record.preferred_teams)
            if record.preferred_teams else "No Preference"
        )
        lap_lines = (
            "\n".join(
                f"  • {(track_name_map or {}).get(tid, tid)}: `{t}`"
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
            "\nPlease wait for an admin to validate your signup.\n"
        )

    # ------------------------------------------------------------------
    # Internal correction-timeout helpers (T036, T038)
    # ------------------------------------------------------------------




