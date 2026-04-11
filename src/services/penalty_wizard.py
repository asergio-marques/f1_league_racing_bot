"""penalty_wizard.py — Post-submission inline penalty review wizard.

Activated by ``enter_penalty_state`` in *result_submission_service.py* once all
sessions for a round have been submitted or cancelled.  The submission channel
stays open and a :class:`PenaltyReviewView` prompt is posted there.

Access: league managers only (users whose guild roles include the
``interaction_role_id`` from :class:`ServerConfig`).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import discord

from db.database import get_connection
from models.points_config import SessionType
from services.penalty_service import StagedPenalty, validate_penalty_input, _time_to_ms

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom-ID constants — must be globally unique for restart-persistence.
# ---------------------------------------------------------------------------
_CID_ADD              = "pw_add"
_CID_CONFIRM          = "pw_confirm"
_CID_APPROVE          = "pw_approve"
_CID_RESUBMIT         = "pw_resubmit"
_CID_PARDON           = "att_pardon"
_CID_AV_MAKE_CHANGES  = "pw_av_make_changes"
_CID_AV_APPROVE       = "pw_av_approve"
_CID_AR_APPROVE       = "ar_approve"
_CID_AR_ADD           = "ar_add"
_CID_AR_CONFIRM       = "ar_confirm"
_CID_AR_MAKE_CHANGES  = "ar_make_changes"


# ---------------------------------------------------------------------------
# Time-penalty parsing helper
# ---------------------------------------------------------------------------

def _parse_penalty_seconds(raw: str | None) -> int:
    """Parse a ``time_penalties`` TEXT value to integer seconds.

    Accepts formats stored by the submission validator: ``"SS.mmm"``,
    ``"M:SS.mmm"``, ``"H:MM:SS.mmm"``, ``"N/A"``, or ``None``.
    Returns 0 for ``None``, ``"N/A"``, or any unparseable value.
    """
    if not raw or raw.strip().upper() == "N/A":
        return 0
    s = raw.strip().lstrip("+")
    try:
        parts = s.split(":")
        if len(parts) == 1:
            return int(float(parts[0]))
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(float(parts[1]))
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(float(parts[2]))
    except (ValueError, IndexError):
        return 0
    return 0


# ---------------------------------------------------------------------------
# Attendance pardon staging dataclass
# ---------------------------------------------------------------------------

@dataclass
class StagedPardon:
    """In-memory representation of a staged attendance pardon (033-attendance-tracking)."""
    driver_user_id: int       # Discord user ID (display / audit)
    driver_profile_id: int    # FK — driver_profiles.id
    attendance_id: int        # FK — driver_round_attendance.id
    pardon_type: str          # 'NO_RSVP' | 'NO_RSVP_ABSENT' | 'RSVP_ABSENT'
    justification: str
    grantor_id: int           # Discord user ID of staging admin


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class PenaltyReviewState:
    """Mutable state threaded through every step of the penalty review wizard."""
    round_id: int
    division_id: int
    submission_channel_id: int
    session_types_present: list[SessionType]
    db_path: str
    bot: Any
    staged: list[StagedPenalty] = field(default_factory=list)
    staged_appeals: list[StagedPenalty] = field(default_factory=list)
    staged_pardons: list[StagedPardon] = field(default_factory=list)
    prompt_message_id: int | None = None
    appeals_prompt_message_id: int | None = None
    round_number: int = 0
    division_name: str = ""


# ---------------------------------------------------------------------------
# Authorization helpers
# ---------------------------------------------------------------------------

async def _is_league_manager(
    interaction: discord.Interaction,
    db_path: str,
    bot: Any,
) -> bool:
    """Return True if the interacting member has the league-manager role."""
    if not isinstance(interaction.user, discord.Member):
        return False
    config = await bot.config_service.get_server_config(interaction.guild_id)
    if config is None:
        return False
    role_ids = {r.id for r in interaction.user.roles}
    return config.interaction_role_id in role_ids


async def _require_lm(
    interaction: discord.Interaction,
    state: PenaltyReviewState,
) -> bool:
    """If the actor is not a league manager, respond with an error and return False."""
    if not await _is_league_manager(interaction, state.db_path, state.bot):
        await interaction.response.send_message(
            "⛔ Only league managers can interact with the penalty review.",
            ephemeral=True,
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _pen_label(sp: StagedPenalty) -> str:
    """Format a staged penalty as a human-readable string."""
    if sp.penalty_type == "DSQ":
        return "DSQ"
    assert sp.penalty_seconds is not None
    return f"+{sp.penalty_seconds}s" if sp.penalty_seconds > 0 else f"{sp.penalty_seconds}s"


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

async def _render_prompt_content(state: PenaltyReviewState) -> str:
    """Build the text content for the penalty review prompt message."""
    lines: list[str] = [
        f"🏁 **Penalty Review — Round {state.round_number} | {state.division_name}**",
        "",
        "Interim results and standings have been posted. "
        "Review them and apply any penalties below before approving.",
        "",
    ]

    # Collect all user IDs that may need test_display_name resolution.
    staged_user_ids = {sp.driver_user_id for sp in state.staged}
    staged_user_ids.update(sp.driver_user_id for sp in state.staged_pardons)

    async with get_connection(state.db_path) as db:
        cursor = await db.execute(
            """
            SELECT DISTINCT dsr.driver_user_id, dp.test_display_name
            FROM driver_session_results dsr
            JOIN session_results sr ON sr.id = dsr.session_result_id
            JOIN driver_profiles dp ON dp.discord_user_id = dsr.driver_user_id
            WHERE sr.round_id = ? AND sr.status = 'ACTIVE' AND dsr.is_superseded = 0
            """,
            (state.round_id,),
        )
        attendee_rows = await cursor.fetchall()

        # Bulk-fetch test display names for staged penalty/pardon drivers.
        test_names: dict[int, str | None] = {}
        if staged_user_ids:
            placeholders = ",".join("?" * len(staged_user_ids))
            cursor = await db.execute(
                f"SELECT discord_user_id, test_display_name FROM driver_profiles "
                f"WHERE discord_user_id IN ({placeholders})",
                list(staged_user_ids),
            )
            for row in await cursor.fetchall():
                test_names[int(row["discord_user_id"])] = row["test_display_name"]

    def _mention(user_id: int, name: str | None = None) -> str:
        display = name if name is not None else test_names.get(user_id)
        return f"<@{user_id}>" + (f" ({display})" if display else "")

    attendee_mentions = [
        _mention(r["driver_user_id"], r["test_display_name"])
        for r in attendee_rows
    ]
    if attendee_mentions:
        lines.append(f"**Preliminary Attendees ({len(attendee_mentions)}):**\n- {'\n- '.join(attendee_mentions)}")
    else:
        lines.append("**Preliminary Attendees:** *(none — no session results found)*")
    lines.append("")

    if state.staged:
        lines.append(f"**Staged Penalties ({len(state.staged)}):**")
        for i, sp in enumerate(state.staged, 1):
            pl = _pen_label(sp)
            sl = sp.session_type.value.replace("_", " ").title()
            lines.append(
                f"  {i}. {_mention(sp.driver_user_id)} | {sl} | **{pl}**  ← Remove #{i} below"
            )
    else:
        lines.append("**Staged Penalties:** *(none — click Add Penalty to stage one)*")

    if state.staged_pardons:
        lines.append("")
        lines.append(f"**Staged Attendance Pardons ({len(state.staged_pardons)}):**")
        for sp in state.staged_pardons:
            lines.append(
                f"  • {_mention(sp.driver_user_id)} — **{sp.pardon_type}** *(justification logged)*"
            )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prompt refresh helper
# ---------------------------------------------------------------------------

async def _refresh_prompt(state: PenaltyReviewState) -> None:
    """Edit the existing prompt message to reflect the current staged list."""
    if state.prompt_message_id is None:
        return
    ch = state.bot.get_channel(state.submission_channel_id)
    if ch is None:
        return
    try:
        msg = await ch.fetch_message(state.prompt_message_id)
        content = await _render_prompt_content(state)
        new_view = PenaltyReviewView(state)
        await msg.edit(content=content, view=new_view)
        state.bot.add_view(new_view, message_id=state.prompt_message_id)
    except (discord.NotFound, discord.HTTPException) as exc:
        log.warning("_refresh_prompt: failed to edit prompt: %s", exc)


async def _render_appeals_prompt_content(state: PenaltyReviewState) -> str:
    """Build the text content for the appeals review prompt message."""
    lines: list[str] = [
        f"⚖️ **Appeals Review — Round {state.round_number} | {state.division_name}**",
        "",
        "Post-Race Penalty Results have been posted. "
        "Review and add any appeal corrections before approving to finalise.",
        "",
    ]
    if state.staged_appeals:
        lines.append(f"**Staged Corrections ({len(state.staged_appeals)}):**")
        for i, sp in enumerate(state.staged_appeals, 1):
            pl = _pen_label(sp)
            sl = sp.session_type.value.replace("_", " ").title()
            lines.append(
                f"  {i}. <@{sp.driver_user_id}> | {sl} | **{pl}**  ← Remove #{i} below"
            )
    else:
        lines.append(
            "**Staged Corrections:** *(none — click Add Correction to stage one)*"
        )
    return "\n".join(lines)


async def _refresh_appeals_prompt(state: PenaltyReviewState) -> None:
    """Edit the existing appeals prompt message to reflect current staged_appeals."""
    if state.appeals_prompt_message_id is None:
        return
    ch = state.bot.get_channel(state.submission_channel_id)
    if ch is None:
        return
    try:
        msg = await ch.fetch_message(state.appeals_prompt_message_id)
        content = await _render_appeals_prompt_content(state)
        new_view = AppealsReviewView(state)
        await msg.edit(content=content, view=new_view)
        state.bot.add_view(new_view, message_id=state.appeals_prompt_message_id)
    except (discord.NotFound, discord.HTTPException) as exc:
        log.warning("_refresh_appeals_prompt: failed to edit appeals prompt: %s", exc)


# ---------------------------------------------------------------------------
# Session selector (ephemeral, non-persistent)
# ---------------------------------------------------------------------------

class _SessionSelectView(discord.ui.View):
    """One button per non-cancelled session type in the round."""

    def __init__(
        self,
        state: PenaltyReviewState,
        source_interaction: discord.Interaction,
        *,
        use_appeals_staging: bool = False,
    ) -> None:
        super().__init__(timeout=120)
        self.state = state
        self.source_interaction = source_interaction
        self.use_appeals_staging = use_appeals_staging
        for stype in state.session_types_present:
            label = stype.value.replace("_", " ").title()
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)
            btn.callback = self._make_cb(stype)
            self.add_item(btn)

    def _make_cb(self, stype: SessionType):
        async def cb(interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(
                AddPenaltyModal(
                    state=self.state,
                    session_type=stype,
                    use_appeals_staging=self.use_appeals_staging,
                )
            )
            self.stop()
            try:
                await self.source_interaction.delete_original_response()
            except discord.HTTPException:
                pass
        return cb

    async def on_timeout(self) -> None:
        """User closed the selector without picking — delete the ephemeral."""
        try:
            await self.source_interaction.delete_original_response()
        except discord.HTTPException:
            pass


# ---------------------------------------------------------------------------
# Add Penalty modal (T015, T017)
# ---------------------------------------------------------------------------

class AddPenaltyModal(discord.ui.Modal, title="Add Penalty"):
    """Four-field modal: driver, penalty value, description, and justification."""

    driver_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Driver (@mention or user ID)",
        placeholder="@DriverName  or  123456789",
        required=True,
        max_length=40,
    )
    penalty_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Penalty value",
        placeholder="+5s  or  -3s  or  DSQ",
        required=True,
        max_length=10,
    )
    description_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Penalty description",
        placeholder="Brief description of the incident",
        required=True,
        max_length=200,
        style=discord.TextStyle.paragraph,
    )
    justification_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Justification",
        placeholder="Why this penalty was applied",
        required=True,
        max_length=200,
        style=discord.TextStyle.paragraph,
    )

    def __init__(
        self,
        state: PenaltyReviewState,
        session_type: SessionType,
        *,
        use_appeals_staging: bool = False,
    ) -> None:
        session_label = session_type.value.replace("_", " ").title()
        title_prefix = "Add Correction" if use_appeals_staging else "Add Penalty"
        super().__init__(title=f"{title_prefix} — {session_label}")
        self.state = state
        self.session_type = session_type
        self.use_appeals_staging = use_appeals_staging

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        # Resolve driver user ID from @mention or raw integer
        raw = self.driver_input.value.strip()
        driver_user_id: int | None = None
        if raw.startswith("<@") and raw.endswith(">"):
            try:
                driver_user_id = int(raw.strip("<@!>"))
            except ValueError:
                pass
        else:
            try:
                driver_user_id = int(raw)
            except ValueError:
                pass

        if driver_user_id is None:
            await interaction.followup.send(
                "❌ Could not parse driver. Use a @mention or a Discord user ID.",
                ephemeral=True,
            )
            return

        # Verify the driver is in the selected session's results (T017 check c)
        async with get_connection(self.state.db_path) as db:
            cursor = await db.execute(
                """
                SELECT dsr.id, dsr.total_time, dsr.time_penalties, dsr.post_race_time_penalties
                FROM session_results sr
                JOIN driver_session_results dsr ON dsr.session_result_id = sr.id
                WHERE sr.round_id = ? AND sr.session_type = ? AND sr.status = 'ACTIVE'
                  AND dsr.driver_user_id = ? AND dsr.is_superseded = 0
                """,
                (self.state.round_id, self.session_type.value, driver_user_id),
            )
            dr_row = await cursor.fetchone()

        if dr_row is None:
            sl = self.session_type.value.replace("_", " ").title()
            await interaction.followup.send(
                f"❌ <@{driver_user_id}> was not found in the **{sl}** results.",
                ephemeral=True,
            )
            return

        # Compute current_time_ms to validate negative penalty magnitude (T006)
        current_time_ms: int | None = None
        if dr_row["total_time"]:
            current_time_ms = _time_to_ms(dr_row["total_time"])
        # Total removable budget = original race time penalty + any wizard-applied penalty.
        # post_race_time_penalties is NULL (Python None) when no wizard penalty has been
        # applied yet; treat it as 0 so the negative-penalty guard always runs.
        _base_pen_s: int = _parse_penalty_seconds(dr_row["time_penalties"])
        _post_pen_raw = dr_row["post_race_time_penalties"]
        _post_pen_s: int = int(_post_pen_raw) if _post_pen_raw is not None else 0
        current_time_penalty_s: int = _base_pen_s + _post_pen_s

        # Adjust for TIME penalties already staged in this wizard session for the
        # same driver+session — a second negative penalty must not exceed whatever
        # headroom remains after previously staged reductions.
        staging_list = (
            self.state.staged_appeals if self.use_appeals_staging else self.state.staged
        )
        staged_adjustment = sum(
            sp.penalty_seconds
            for sp in staging_list
            if sp.driver_user_id == driver_user_id
            and sp.session_type == self.session_type
            and sp.penalty_type == "TIME"
            and sp.penalty_seconds is not None
        )
        current_time_penalty_s += staged_adjustment

        # Validate the penalty value (T017)
        result = validate_penalty_input(
            driver_user_id=driver_user_id,
            session_type=self.session_type,
            penalty_value=self.penalty_input.value,
            current_time_ms=current_time_ms,
            current_time_penalty_s=current_time_penalty_s,
        )
        if isinstance(result, str):
            await interaction.followup.send(f"❌ {result}", ephemeral=True)
            return

        # Stage the penalty (with description and justification) and refresh.
        result.description = self.description_input.value.strip()
        result.justification = self.justification_input.value.strip()
        staging_list.append(result)
        if self.use_appeals_staging:
            await _refresh_appeals_prompt(self.state)
        else:
            await _refresh_prompt(self.state)

        pl = _pen_label(result)
        sl = self.session_type.value.replace("_", " ").title()
        action = "Correction" if self.use_appeals_staging else "Penalty"
        await interaction.followup.send(
            f"\u2705 Staged {action}: <@{driver_user_id}> | {sl} | **{pl}**",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Add Pardon modal (033-attendance-tracking T007)
# ---------------------------------------------------------------------------

_VALID_PARDON_TYPES = {"NO_RSVP", "NO_RSVP_ABSENT", "RSVP_ABSENT"}


class AddPardonModal(discord.ui.Modal, title="Attendance Pardon"):
    """Three-field modal for staging an attendance pardon during penalty review."""

    driver_id_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Driver Discord User ID",
        placeholder="123456789012345678",
        required=True,
        max_length=25,
    )
    pardon_type_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Pardon Type",
        placeholder="NO_RSVP / NO_RSVP_ABSENT / RSVP_ABSENT",
        required=True,
        max_length=15,
    )
    justification_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Justification",
        placeholder="Reason for granting this pardon",
        required=True,
        max_length=300,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, state: PenaltyReviewState) -> None:
        super().__init__()
        self.state = state

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        server_id: int = interaction.guild_id  # type: ignore[assignment]

        # --- Parse driver user ID ---
        raw_id = self.driver_id_input.value.strip()
        try:
            driver_user_id = int(raw_id)
        except ValueError:
            await interaction.followup.send(
                "❌ Invalid Discord User ID — must be a numeric ID.", ephemeral=True
            )
            return

        # --- Validate pardon type ---
        pardon_type = self.pardon_type_input.value.strip().upper()
        if pardon_type not in _VALID_PARDON_TYPES:
            await interaction.followup.send(
                f"❌ Invalid pardon type `{pardon_type}`. Must be one of: NO_RSVP, NO_RSVP_ABSENT, RSVP_ABSENT.",
                ephemeral=True,
            )
            return

        justification = self.justification_input.value.strip()

        from db.database import get_connection
        from services.driver_service import resolve_driver_profile_id

        async with get_connection(self.state.db_path) as db:
            # --- Check round is not already finalized (FR-011) ---
            cursor = await db.execute(
                "SELECT result_status FROM rounds WHERE id = ?",
                (self.state.round_id,),
            )
            round_row = await cursor.fetchone()
            if round_row and round_row["result_status"] == "POST_RACE_PENALTY":
                await interaction.followup.send(
                    "❌ Post-race penalties have already been finalized for this round. "
                    "No further attendance pardons may be applied.",
                    ephemeral=True,
                )
                return

            # --- Resolve driver profile ID ---
            profile_id = await resolve_driver_profile_id(server_id, driver_user_id, db)
            if profile_id is None:
                await interaction.followup.send(
                    f"❌ No driver profile found for user ID `{driver_user_id}` in this server.",
                    ephemeral=True,
                )
                return

            # --- Fetch DRA row ---
            cursor = await db.execute(
                """
                SELECT id, rsvp_status
                FROM driver_round_attendance
                WHERE round_id = ? AND division_id = ? AND driver_profile_id = ?
                """,
                (self.state.round_id, self.state.division_id, profile_id),
            )
            dra_row = await cursor.fetchone()

            # --- Determine attendance from session results directly (FR-007).
            #     The pre-computed attended flag on the DRA row is not populated
            #     until finalize_penalty_review, so we query results here instead.
            #     A driver is considered attended if they appear in ANY active
            #     session result for this round. ---
            cursor = await db.execute(
                """
                SELECT 1
                FROM driver_session_results dsr
                JOIN session_results sr ON sr.id = dsr.session_result_id
                WHERE sr.round_id = ? AND sr.status = 'ACTIVE'
                  AND dsr.driver_profile_id = ? AND dsr.is_superseded = 0
                LIMIT 1
                """,
                (self.state.round_id, profile_id),
            )
            attended_in_results = (await cursor.fetchone()) is not None

        if dra_row is None:
            await interaction.followup.send(
                f"❌ No attendance row found for <@{driver_user_id}> in this round. "
                "Ensure results have been submitted first.",
                ephemeral=True,
            )
            return

        attendance_id = dra_row["id"]
        rsvp_status = dra_row["rsvp_status"]

        # --- Validate pardon type against driver state (FR-007) ---
        if pardon_type == "NO_RSVP" and rsvp_status != "NO_RSVP":
            await interaction.followup.send(
                f"❌ NO_RSVP pardon rejected: <@{driver_user_id}> has RSVP status "
                f"`{rsvp_status}` — they did RSVP, so NO_RSVP pardon is not applicable.",
                ephemeral=True,
            )
            return
        if pardon_type == "NO_RSVP_ABSENT":
            if rsvp_status != "NO_RSVP":
                await interaction.followup.send(
                    f"❌ NO_RSVP_ABSENT pardon rejected: <@{driver_user_id}> has RSVP status "
                    f"`{rsvp_status}` — NO_RSVP_ABSENT requires NO_RSVP status.",
                    ephemeral=True,
                )
                return
            if attended_in_results:
                await interaction.followup.send(
                    f"❌ NO_RSVP_ABSENT pardon rejected: <@{driver_user_id}> is present in session results.",
                    ephemeral=True,
                )
                return
        if pardon_type == "RSVP_ABSENT":
            if rsvp_status not in {"ACCEPTED", "TENTATIVE", "DECLINED"}:
                await interaction.followup.send(
                    f"❌ RSVP_ABSENT pardon rejected: <@{driver_user_id}> has RSVP status "
                    f"`{rsvp_status}` — RSVP_ABSENT requires ACCEPTED, TENTATIVE, or DECLINED status.",
                    ephemeral=True,
                )
                return
            if attended_in_results:
                await interaction.followup.send(
                    f"❌ RSVP_ABSENT pardon rejected: <@{driver_user_id}> is present in session results.",
                    ephemeral=True,
                )
                return

        # --- Check for duplicate in staged_pardons (FR-008) ---
        duplicate = any(
            p.attendance_id == attendance_id and p.pardon_type == pardon_type
            for p in self.state.staged_pardons
        )
        if duplicate:
            await interaction.followup.send(
                f"❌ A `{pardon_type}` pardon for <@{driver_user_id}> is already staged.",
                ephemeral=True,
            )
            return

        # --- Stage the pardon ---
        pardon = StagedPardon(
            driver_user_id=driver_user_id,
            driver_profile_id=profile_id,
            attendance_id=attendance_id,
            pardon_type=pardon_type,
            justification=justification,
            grantor_id=interaction.user.id,
        )
        self.state.staged_pardons.append(pardon)

        # --- Log justification to calc-log channel only (FR-010) ---
        await self.state.bot.output_router.post_log(  # type: ignore[attr-defined]
            server_id,
            f"ATTENDANCE_PARDON_STAGED | <@{interaction.user.id}> granted {pardon_type} pardon\n"
            f"  driver: <@{driver_user_id}> | round: {self.state.round_number} "
            f"({self.state.division_name})\n"
            f"  justification: {justification}",
        )

        await _refresh_prompt(self.state)
        await interaction.followup.send(
            f"✅ Pardon staged: <@{driver_user_id}> — **{pardon_type}**",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Confirm-clear view (used by No Penalties / Confirm when list is non-empty)
# ---------------------------------------------------------------------------

class _ConfirmClearView(discord.ui.View):
    """Two-button confirmation for clearing the staged penalty list."""

    def __init__(self, state: PenaltyReviewState) -> None:
        super().__init__(timeout=60)
        self.state = state

    @discord.ui.button(label="Yes, clear and proceed with no penalties", style=discord.ButtonStyle.danger)
    async def confirm_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await _require_lm(interaction, self.state):
            return
        self.state.staged.clear()
        await interaction.response.defer(ephemeral=True)
        await _show_approval_step(interaction, self.state)
        self.stop()

    @discord.ui.button(label="Cancel — keep penalties", style=discord.ButtonStyle.secondary)
    async def cancel_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await _require_lm(interaction, self.state):
            return
        await interaction.response.send_message(
            "↩️ Staged penalties kept intact.", ephemeral=True
        )
        self.stop()


# ---------------------------------------------------------------------------
# Approval step helper (T019, T020)
# ---------------------------------------------------------------------------

async def _show_approval_step(
    interaction: discord.Interaction,
    state: PenaltyReviewState,
) -> None:
    """Post an :class:`ApprovalView` message to the submission channel."""
    if state.staged:
        lines = ["**Review and approve the following penalties:**", ""]
        for i, sp in enumerate(state.staged, 1):
            pl = _pen_label(sp)
            sl = sp.session_type.value.replace("_", " ").title()
            lines.append(f"{i}. <@{sp.driver_user_id}> | {sl} | **{pl}**")
        content = "\n".join(lines)
    else:
        content = (
            "✅ **No penalties staged.** "
            "Approve to finalize the round with results as submitted."
        )

    ch = state.bot.get_channel(state.submission_channel_id)
    if ch is not None:
        view = ApprovalView(state=state)
        msg = await ch.send(content, view=view)
        state.bot.add_view(view, message_id=msg.id)


# ---------------------------------------------------------------------------
# Main persistent view (T010, T011, T018)
# ---------------------------------------------------------------------------

class PenaltyReviewView(discord.ui.View):
    """Persistent penalty review prompt view.

    The three static buttons (Add Penalty, No Penalties / Confirm, Approve)
    carry stable ``custom_id`` values so that Discord.py can route clicks
    back to this class after a bot restart.

    Dynamic Remove buttons are added per staged entry at construction time.
    They are registered per-message (``bot.add_view(view, message_id=msg.id)``)
    and are intentionally NOT covered by the global restart registration — the
    T030 recovery re-posts the prompt with an empty staged list, so there are
    no dangling Remove buttons to handle after restart.

    Registered globally on startup via ``bot.add_view(PenaltyReviewView(state=None))``
    to catch any leftover Add/Confirm/Approve clicks on old messages after restart.
    """

    def __init__(self, state: PenaltyReviewState | None = None) -> None:
        super().__init__(timeout=None)
        self.state = state

        # Configure static button states
        _no_state = state is None
        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue
            if item.custom_id == _CID_ADD:
                item.disabled = _no_state or len(state.session_types_present) == 0  # type: ignore[union-attr]
            elif item.custom_id == _CID_APPROVE:
                item.disabled = _no_state or len(state.staged) == 0  # type: ignore[union-attr]

        # Dynamic Remove buttons — one per staged entry (T018)
        if state is not None:
            for idx, sp in enumerate(state.staged):
                row_num = min(1 + idx // 5, 4)
                btn = discord.ui.Button(
                    label=f"Remove #{idx + 1}",
                    style=discord.ButtonStyle.danger,
                    custom_id=f"pw_remove_{idx}",
                    row=row_num,
                )
                btn.callback = self._make_remove_cb(idx)
                self.add_item(btn)

    def _make_remove_cb(self, idx: int):
        async def cb(interaction: discord.Interaction) -> None:
            if self.state is None:
                await interaction.response.send_message(
                    "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.",
                    ephemeral=True,
                )
                return
            if not await _require_lm(interaction, self.state):
                return
            if idx < len(self.state.staged):
                removed = self.state.staged.pop(idx)
                await interaction.response.defer(ephemeral=True)
                await _refresh_prompt(self.state)
                pl = _pen_label(removed)
                await interaction.followup.send(
                    f"🗑️ Removed: <@{removed.driver_user_id}> | {pl}", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "⚠️ That entry no longer exists (the list may have changed).",
                    ephemeral=True,
                )
        return cb

    @discord.ui.button(
        label="➕ Add Penalty",
        style=discord.ButtonStyle.primary,
        custom_id=_CID_ADD,
        row=0,
    )
    async def add_penalty_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.state is None:
            await interaction.response.send_message(
                "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.",
                ephemeral=True,
            )
            return
        if not await _require_lm(interaction, self.state):
            return
        view = _SessionSelectView(state=self.state, source_interaction=interaction)
        await interaction.response.send_message(
            "Select which session to penalise:", view=view, ephemeral=True
        )

    @discord.ui.button(
        label="No Penalties / Confirm",
        style=discord.ButtonStyle.secondary,
        custom_id=_CID_CONFIRM,
        row=0,
    )
    async def no_penalties_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.state is None:
            await interaction.response.send_message(
                "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.",
                ephemeral=True,
            )
            return
        if not await _require_lm(interaction, self.state):
            return
        if not self.state.staged:
            # No penalties — advance directly to approval step (T019)
            await interaction.response.defer(ephemeral=True)
            await _show_approval_step(interaction, self.state)
        else:
            # Ask for explicit confirmation before clearing (T019)
            view = _ConfirmClearView(state=self.state)
            await interaction.response.send_message(
                f"⚠️ You have **{len(self.state.staged)}** staged penalty(ies). "
                "Clicking **Yes, clear and proceed** will discard all of them and finalize "
                "the round without any penalties.",
                view=view,
                ephemeral=True,
            )

    @discord.ui.button(
        label="✅ Approve",
        style=discord.ButtonStyle.success,
        custom_id=_CID_APPROVE,
        row=0,
    )
    async def approve_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.state is None:
            await interaction.response.send_message(
                "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.",
                ephemeral=True,
            )
            return
        if not await _require_lm(interaction, self.state):
            return
        if not self.state.staged:
            await interaction.response.send_message(
                "⚠️ No penalties are staged. "
                "Use **No Penalties / Confirm** to finalize without penalties.",
                ephemeral=True,
            )
            return
        from services.result_submission_service import finalize_penalty_review
        await finalize_penalty_review(interaction, self.state)

    @discord.ui.button(
        label="🔄 Resubmit Initial Results",
        style=discord.ButtonStyle.danger,
        custom_id=_CID_RESUBMIT,
        row=0,
    )
    async def resubmit_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.state is None:
            await interaction.response.send_message(
                "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.",
                ephemeral=True,
            )
            return
        if not await _require_lm(interaction, self.state):
            return
        await interaction.response.defer(ephemeral=True)
        from services.result_submission_service import enter_resubmit_flow
        await enter_resubmit_flow(interaction, self.state)

    @discord.ui.button(
        label="🏳️ Attendance Pardon",
        style=discord.ButtonStyle.secondary,
        custom_id=_CID_PARDON,
        row=1,
    )
    async def pardon_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.state is None:
            await interaction.response.send_message(
                "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.",
                ephemeral=True,
            )
            return
        if not await _require_lm(interaction, self.state):
            return
        await interaction.response.send_modal(AddPardonModal(state=self.state))


# ---------------------------------------------------------------------------
# Approval view (T020, T025)
# ---------------------------------------------------------------------------

class ApprovalView(discord.ui.View):
    """Two-button approval step: Make Changes or final Approve."""

    def __init__(self, state: PenaltyReviewState | None = None) -> None:
        super().__init__(timeout=None)
        self.state = state

    @discord.ui.button(
        label="✏️ Make Changes",
        style=discord.ButtonStyle.secondary,
        custom_id=_CID_AV_MAKE_CHANGES,
    )
    async def make_changes_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.state is None:
            await interaction.response.send_message(
                "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.",
                ephemeral=True,
            )
            return
        if not await _require_lm(interaction, self.state):
            return
        await interaction.response.defer(ephemeral=True)
        await _refresh_prompt(self.state)
        await interaction.followup.send(
            "↩️ Returned to penalty staging. The staged list is intact.", ephemeral=True
        )

    @discord.ui.button(
        label="✅ Approve",
        style=discord.ButtonStyle.success,
        custom_id=_CID_AV_APPROVE,
    )
    async def approve_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.state is None:
            await interaction.response.send_message(
                "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.",
                ephemeral=True,
            )
            return
        if not await _require_lm(interaction, self.state):
            return
        # Immutability guard: reject finalize on archived season
        from services.season_service import SeasonImmutableError
        from db.database import get_connection as _gc
        try:
            async with _gc(self.state.db_path) as _db:
                _cur = await _db.execute(
                    """
                    SELECT s.status AS season_status
                    FROM rounds r
                    JOIN divisions d ON d.id = r.division_id
                    JOIN seasons s ON s.id = d.season_id
                    WHERE r.id = ?
                    """,
                    (self.state.round_id,),
                )
                _row = await _cur.fetchone()
            if _row and _row["season_status"] == "COMPLETED":
                raise SeasonImmutableError(
                    f"Round {self.state.round_id} belongs to an archived season."
                )
        except SeasonImmutableError:
            await interaction.response.send_message(
                "❌ This season is archived (COMPLETED) and cannot be modified.",
                ephemeral=True,
            )
            return
        # T007: wire to finalize_penalty_review
        from services.result_submission_service import finalize_penalty_review
        await finalize_penalty_review(interaction, self.state)


# ---------------------------------------------------------------------------
# Appeals review view (T008 — minimal stub; expanded in T018)
# ---------------------------------------------------------------------------

class AppealsReviewView(discord.ui.View):
    """Persistent appeals review prompt view.

    Mirrors the :class:`PenaltyReviewView` structure:
    - ➕ Add Correction — opens ``_SessionSelectView`` in appeals mode
    - No Changes / Confirm — finalises without corrections (with confirmation when staged)
    - ✅ Approve — applies staged corrections and calls ``finalize_appeals_review``

    Dynamic Remove buttons are added per staged_appeals entry at construction time.
    """

    def __init__(self, state: PenaltyReviewState | None = None) -> None:
        super().__init__(timeout=None)
        self.state = state

        _no_state = state is None
        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue
            if item.custom_id == _CID_AR_ADD:
                item.disabled = _no_state or len(state.session_types_present) == 0  # type: ignore[union-attr]
            elif item.custom_id == _CID_AR_APPROVE:
                item.disabled = _no_state or len(state.staged_appeals) == 0  # type: ignore[union-attr]

        # Dynamic Remove buttons — one per staged correction
        if state is not None:
            for idx, sp in enumerate(state.staged_appeals):
                row_num = min(1 + idx // 5, 4)
                btn = discord.ui.Button(
                    label=f"Remove #{idx + 1}",
                    style=discord.ButtonStyle.danger,
                    custom_id=f"ar_remove_{idx}",
                    row=row_num,
                )
                btn.callback = self._make_remove_cb(idx)
                self.add_item(btn)

    def _make_remove_cb(self, idx: int):
        async def cb(interaction: discord.Interaction) -> None:
            if self.state is None:
                await interaction.response.send_message(
                    "⚠️ The bot was restarted. Please wait for the appeals prompt to refresh.",
                    ephemeral=True,
                )
                return
            if not await _require_lm(interaction, self.state):
                return
            if idx < len(self.state.staged_appeals):
                removed = self.state.staged_appeals.pop(idx)
                await interaction.response.defer(ephemeral=True)
                await _refresh_appeals_prompt(self.state)
                pl = _pen_label(removed)
                await interaction.followup.send(
                    f"\U0001f5d1\ufe0f Removed: <@{removed.driver_user_id}> | {pl}",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "⚠️ That entry no longer exists (the list may have changed).",
                    ephemeral=True,
                )
        return cb

    @discord.ui.button(
        label="➕ Add Correction",
        style=discord.ButtonStyle.primary,
        custom_id=_CID_AR_ADD,
        row=0,
    )
    async def add_correction_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.state is None:
            await interaction.response.send_message(
                "⚠️ The bot was restarted. Please wait for the appeals prompt to refresh.",
                ephemeral=True,
            )
            return
        if not await _require_lm(interaction, self.state):
            return
        view = _SessionSelectView(
            state=self.state,
            source_interaction=interaction,
            use_appeals_staging=True,
        )
        await interaction.response.send_message(
            "Select which session to apply a correction to:", view=view, ephemeral=True
        )

    @discord.ui.button(
        label="No Changes / Confirm",
        style=discord.ButtonStyle.secondary,
        custom_id=_CID_AR_CONFIRM,
        row=0,
    )
    async def no_changes_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.state is None:
            await interaction.response.send_message(
                "⚠️ The bot was restarted. Please wait for the appeals prompt to refresh.",
                ephemeral=True,
            )
            return
        if not await _require_lm(interaction, self.state):
            return
        if not self.state.staged_appeals:
            # No corrections — finalise directly
            from services.result_submission_service import finalize_appeals_review
            await finalize_appeals_review(interaction, self.state)
        else:
            # Ask for explicit confirmation before clearing
            view = _AppealsConfirmClearView(state=self.state)
            await interaction.response.send_message(
                f"⚠️ You have **{len(self.state.staged_appeals)}** staged correction(s). "
                "Clicking **Yes, clear and proceed** will discard all of them and finalise "
                "the round without any appeal corrections.",
                view=view,
                ephemeral=True,
            )

    @discord.ui.button(
        label="✅ Approve",
        style=discord.ButtonStyle.success,
        custom_id=_CID_AR_APPROVE,
        row=0,
    )
    async def approve_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.state is None:
            await interaction.response.send_message(
                "⚠️ The bot was restarted. Please wait for the appeals prompt to refresh.",
                ephemeral=True,
            )
            return
        if not await _require_lm(interaction, self.state):
            return
        if not self.state.staged_appeals:
            await interaction.response.send_message(
                "⚠️ No corrections are staged. "
                "Use **No Changes / Confirm** to finalise without corrections.",
                ephemeral=True,
            )
            return
        from services.result_submission_service import finalize_appeals_review
        await finalize_appeals_review(interaction, self.state)


class _AppealsConfirmClearView(discord.ui.View):
    """Two-button confirmation for clearing the staged appeals corrections list."""

    def __init__(self, state: PenaltyReviewState) -> None:
        super().__init__(timeout=60)
        self.state = state

    @discord.ui.button(
        label="Yes, clear and proceed with no corrections",
        style=discord.ButtonStyle.danger,
    )
    async def confirm_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await _require_lm(interaction, self.state):
            return
        self.state.staged_appeals.clear()
        from services.result_submission_service import finalize_appeals_review
        await finalize_appeals_review(interaction, self.state)
        self.stop()

    @discord.ui.button(
        label="No, go back",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        self.stop()

