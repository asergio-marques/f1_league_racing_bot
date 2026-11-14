"""WizardService — what the signup wizard keeps of a driver's signup channel and its words.

The wizard itself runs as changes on the change queue (`wizard_changes`, `wizard_answers`,
`review_changes`, `closing_steps`), each answer a turn on the driver's lane. What is left here is
what those changes and the commands outside signup still reach: the channel's making and its lock,
the prompts and the review panel's text, the views a question carries, and the restart's sweep
of the drivers parked awaiting a correction (`recover_correction_timeouts`). A held channel's move to a driver's new account is `held_channel_moves`'s.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import discord

from leaguebot.core.utils.in_a_job import posts_in_a_job
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.driver_profile import DriverState
from leaguebot.signup.models.signup_module import SignupRecord, SignupWizardRecord, WizardState
from leaguebot.core.utils.input_validator import parse_nationality, parse_time, render_lap_time
from leaguebot.signup.services.closing_steps import recover_correction_timeouts

if TYPE_CHECKING:
    from leaguebot.core.services.queue.change_queue import ChangeQueue
    from leaguebot.core.utils.league_bot import LeagueBot

    from leaguebot.signup.services.signup_module_service import SignupModuleService

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
    *,
    signup: "SignupModuleService",
) -> discord.ui.View:
    """The view that answers the wizard's question at *state*, for the driver *discord_user_id*,
    handed the change queue and *signup*, the signup service it finds the wizard through."""
    from leaguebot.signup.services.signup_views import (  # lazy import — avoid circular
        WithdrawButtonView,
        PlatformButtonView,
        DriverTypeButtonView,
        PreferredTeamsButtonView,
        NoPreferenceTeammateView,
        NoNotesButtonView,
    )
    if state == WizardState.COLLECTING_PLATFORM:
        return PlatformButtonView(discord_user_id, queue, signup=signup)
    if state == WizardState.COLLECTING_DRIVER_TYPE:
        return DriverTypeButtonView(discord_user_id, queue, signup=signup)
    if state == WizardState.COLLECTING_PREFERRED_TEAMS:
        return PreferredTeamsButtonView(discord_user_id, queue, team_names or [], signup=signup)
    if state == WizardState.COLLECTING_PREFERRED_TEAMMATE:
        return NoPreferenceTeammateView(discord_user_id, queue, signup=signup)
    if state == WizardState.COLLECTING_NOTES:
        return NoNotesButtonView(discord_user_id, queue, signup=signup)
    return WithdrawButtonView(discord_user_id, queue, signup=signup)


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

class WizardService:
    """Manages the full lifecycle of a driver's signup wizard session.

    Dependency wiring: the service is given the bot in its constructor, for the Discord guild
    objects and the driver and signup module services it reaches through it. It is complete once
    built, so a press on a signup button arriving while ``start_up`` is still recovering finds
    the bot already there (#228).
    """

    def __init__(
        self,
        db_path: str,
        signup_module_service: "SignupModuleService",
        *,
        change_queue: "ChangeQueue",
        bot: "LeagueBot",
    ) -> None:
        self._db_path = db_path
        # Handed the signup module service it reads wizards through, rather than looking it up on
        # the bot, and passed on to the step views it makes.
        self._signup = signup_module_service
        # Handed the change queue when it is built, for the views it makes and the changes it asks
        # (docs/design/architecture.md, "Services stay attached to the bot, and are built in one place").
        self._change_queue = change_queue

        self._bot = bot

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

    async def recover_correction_timeouts(self, *, skip: frozenset[str] | set[str] = frozenset()) -> None:
        """Release every driver awaiting a correction parameter, save those in *skip* (the
        restart's sweep, `closing_steps.recover_correction_timeouts`, which this hands its own
        database and queue), whose signup the restart has just ended for a channel gone.

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
        await recover_correction_timeouts(self._db_path, self._change_queue, skip=skip)

    async def get_wizard_by_channel(
        self, channel_id: int
    ):  # -> SignupWizardRecord | None
        """Look up an active wizard record by channel ID.

        Thin delegation to signup_module_service; exposed here so cogs only
        need a reference to wizard_service.
        """
        return await self._signup.get_wizard_by_channel(channel_id)

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
        return build_step_view(
            self._change_queue, state, discord_user_id, team_names, signup=self._signup
        )





















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




