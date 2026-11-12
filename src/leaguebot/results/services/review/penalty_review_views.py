"""penalty_review_views.py — Stage one of the penalty review: the reports review's message, buttons and forms.

Activated by the change `results.review.open` (*review_open_change.py*) once all sessions for a
round have been submitted or cancelled.  The submission channel stays open and a
:class:`PenaltyReviewView` prompt (:func:`_render_prompt_content`, kept current by
:func:`_refresh_prompt`) is posted there, with the forms that stage a penalty or a pardon, the
session selector, the clear confirmation, and the approval step with its :class:`ApprovalView`.
Stage two's views are in *appeals_review_views.py*; the press plumbing both share is in
*penalty_wizard.py*.
"""
from __future__ import annotations

import logging

import discord

from leaguebot.core.utils.in_a_job import posts_in_a_job
from leaguebot.core.db.database import get_connection
from leaguebot.results.models.points_config import SessionType
from leaguebot.core.models.change import StepFailedOnDiscord
from leaguebot.core.services.queue.change_queue import ChangeQueue
from leaguebot.core.services.settings.channel_registry_service import as_text_channel
from leaguebot.core.services.drivers.driver_service import accounts_of_in_division, current_account_map_for_division
from leaguebot.results.services.review.penalty_service import validate_penalty_input
from leaguebot.core.utils.input_validator import STEWARD_TEXT, parse_user, parse_user_id
from leaguebot.core.utils.interaction_errors import describe_form
from leaguebot.core.utils.league_server import CallbackButton, LeagueModal, LeagueView
from leaguebot.core.utils.log_lines import record_abandoned, refuse
from leaguebot.results.services.review.penalty_wizard import (
    _CID_ADD,
    _CID_APPROVE,
    _CID_AV_APPROVE,
    _CID_AV_MAKE_CHANGES,
    _CID_CONFIRM,
    _CID_PARDON,
    _CID_RESUBMIT,
    _ask_confirm,
    _ask_stage,
    _button,
    _clear_confirmation,
    _require_current,
    _require_lm,
    _take_down_approval,
    _take_down_confirmation,
)
from leaguebot.results.services.review.review_state import (
    PenaltyReviewState,
    StagedPardon,
    _appeals_review_moved_on,
    _pen_label,
    _review_moved_on,
    _shown,
    _staged_named,
)

log = logging.getLogger(__name__)


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
    if state.is_amendment:
        # Drawn here rather than prefixed by the caller, so that every refresh keeps it (#345).
        lines[2] = (
            "**Stage 2 of 3 — Reports.** The decisions the amended sessions already carry are "
            "listed below. Change what the corrected classification changes and leave the "
            "rest: approving keeps them exactly as they stand. Nothing is published until the "
            "appeals are approved."
        )


    async with get_connection(state.db_path) as db:
        # A result keeps the account it was recorded under; the prompt names the driver by
        # the account they use now (issue #243).
        current_of = await current_account_map_for_division(db, state.division_id)
        cursor = await db.execute(
            """
            SELECT DISTINCT driver_user_id, NULL AS test_display_name
            FROM (
                SELECT rsr.driver_user_id
                FROM race_session_results rsr
                JOIN session_results sr ON sr.id = rsr.session_result_id
                WHERE sr.round_id = ? AND sr.status = 'ACTIVE'
                UNION ALL
                SELECT qsr.driver_user_id
                FROM qualifying_session_results qsr
                JOIN session_results sr ON sr.id = qsr.session_result_id
                WHERE sr.round_id = ? AND sr.status = 'ACTIVE'
            )
            """,
            (state.round_id, state.round_id),
        )
        pre_attendees = await cursor.fetchall()
        # Fetch test display names separately. One driver under two accounts is one attendee.
        all_uids = list(dict.fromkeys(
            current_of.get(int(r["driver_user_id"]), int(r["driver_user_id"]))
            for r in pre_attendees
        ))
        test_name_map: dict[int, str | None] = {}
        if all_uids:
            ph2 = ",".join("?" * len(all_uids))
            cursor = await db.execute(
                f"SELECT discord_user_id, test_display_name FROM driver_profiles "
                f"WHERE discord_user_id IN ({ph2})",
                [str(uid) for uid in all_uids],
            )
            for row in await cursor.fetchall():
                test_name_map[int(row["discord_user_id"])] = row["test_display_name"]

        import dataclasses as _dc
        attendee_rows = [
            type("_Row", (), {"driver_user_id": uid,
                              "test_display_name": test_name_map.get(uid)})()  # noqa
            for uid in all_uids
        ]

        # Bulk-fetch test display names for staged penalty/pardon drivers.
        staged_user_ids = {current_of.get(sp.driver_user_id, sp.driver_user_id) for sp in state.staged}
        staged_user_ids.update(
            current_of.get(sp.driver_user_id, sp.driver_user_id) for sp in state.staged_pardons
        )
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
        user_id = current_of.get(user_id, user_id)
        display = name if name is not None else test_names.get(user_id)
        return f"<@{user_id}>" + (f" ({display})" if display else "")

    attendee_mentions = [
        _mention(r.driver_user_id, r.test_display_name)
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
        for i, pardon in enumerate(state.staged_pardons, 1):
            lines.append(
                f"  • {_mention(pardon.driver_user_id)} — **{pardon.pardon_type}** "
                f"*(justification logged)*  ← Remove Pardon #{i} below"
            )

    return "\n".join(lines)


@posts_in_a_job
async def _refresh_prompt(state: PenaltyReviewState) -> None:
    """Edit the existing prompt message to reflect the current staged list.

    Every change to the review comes through here, and **Make Changes** does too, so this is
    where an approval message confirming the review as it stood is withdrawn (#402). A staging
    on the queue has withdrawn it as a job of its own already, and finds none here.

    The edit is a job of the staging's change (#439): a message gone is logged and passed over,
    and an edit Discord refuses raises `StepFailedOnDiscord`, for the queue to stop the round's
    lane on and try again.
    """
    await _take_down_approval(state)
    if state.prompt_message_id is None:
        return
    ch = as_text_channel(state.bot.get_channel(state.submission_channel_id))
    if ch is None:
        return
    try:
        msg = await ch.fetch_message(state.prompt_message_id)
        content = await _render_prompt_content(state)
        new_view = PenaltyReviewView(state)
        await msg.edit(content=content, view=new_view)
        state.bot.add_view(new_view, message_id=state.prompt_message_id)
    except discord.NotFound as exc:
        log.warning("_refresh_prompt: failed to edit prompt: %s", exc)
    except discord.HTTPException as exc:
        raise StepFailedOnDiscord("the prompt could not be edited") from exc


# ---------------------------------------------------------------------------
# Session selector (ephemeral, non-persistent)
# ---------------------------------------------------------------------------

class _SessionSelectView(LeagueView):
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
            btn = CallbackButton(
                label=label,
                style=discord.ButtonStyle.primary,
                on_press=self._make_cb(stype),
            )
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

class AddPenaltyModal(LeagueModal, title="Add Penalty"):
    """Four-field modal: driver, penalty value, description, and justification."""

    driver_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Driver (@mention or user ID)",
        placeholder="@DriverName  or  123456789",
        required=True,
        max_length=40,
    )
    penalty_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Penalty value",
        placeholder="+5s  or  -3s  or  DSQ  or  NFA (no further action)",
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
        # Not deferred for the queue to answer the form, as it says it is under way (#439, C54);
        # a refusal below defers first, so that it is answered as it always was.

        # The review may have moved on while the form was open (#402), whichever it stages on:
        # the penalty review, or the appeals review the round moved on to (#482).
        refusal = (
            await _appeals_review_moved_on(self.state)
            if self.use_appeals_staging
            else await _review_moved_on(self.state)
        )
        if refusal is not None:
            await interaction.response.defer(ephemeral=True)
            await refuse(interaction, refusal, what=describe_form(self))
            return

        # Both texts are published in the verdict, so neither may mention a group or carry an
        # emoji (#204).
        for label, text_input in (
            ("description", self.description_input),
            ("justification", self.justification_input),
        ):
            refusal = STEWARD_TEXT.check(label, text_input.value).refusal
            if refusal is not None:
                await refuse(
                    interaction, f"❌ {refusal}", what=describe_form(self)
                )
                return

        # Resolve driver user ID from @mention or raw integer
        raw = self.driver_input.value.strip()
        driver_user_id = parse_user(raw)
        if driver_user_id is None:
            await refuse(
                interaction, "❌ Could not parse driver. Use a @mention or a Discord user ID.", what=describe_form(self)
            )
            return

        # Verify the driver is in the selected session's results (T017 check c).
        #
        # Any account the driver has held names them, and the result may stand under one
        # they have since left (issue #243). The row is found by every account of theirs,
        # and the penalty is staged under the account the row holds — which is the one it
        # is applied by — while every message names the account they use now.
        async with get_connection(self.state.db_path) as db:
            accounts = await accounts_of_in_division(db, self.state.division_id, driver_user_id)
            shown_user_id = int(
                (await current_account_map_for_division(db, self.state.division_id)).get(
                    driver_user_id, driver_user_id
                )
            )
        in_accounts = ",".join("?" for _ in accounts)
        current_time_ms: int | None = None
        current_time_penalty_s: int = 0
        driver_found = False

        if not self.session_type.is_qualifying:
            async with get_connection(self.state.db_path) as db:
                cursor = await db.execute(
                    f"""
                    SELECT rsr.driver_user_id,
                           rsr.base_time_ms, rsr.ingame_time_penalties_ms,
                           rsr.postrace_time_penalties_ms, rsr.appeal_time_penalties_ms
                    FROM session_results sr
                    JOIN race_session_results rsr ON rsr.session_result_id = sr.id
                    WHERE sr.round_id = ? AND sr.session_type = ? AND sr.status = 'ACTIVE'
                      AND rsr.driver_user_id IN ({in_accounts})
                    """,
                    (self.state.round_id, self.session_type.value, *accounts),
                )
                rsr_row = await cursor.fetchone()
                if rsr_row is not None:
                    driver_found = True
                    driver_user_id = int(rsr_row["driver_user_id"])
                    base_ms = rsr_row["base_time_ms"]
                    ingame_ms = rsr_row["ingame_time_penalties_ms"] or 0
                    postrace_ms = rsr_row["postrace_time_penalties_ms"] or 0
                    appeal_ms = rsr_row["appeal_time_penalties_ms"] or 0
                    if base_ms is not None:
                        current_time_ms = base_ms + ingame_ms + postrace_ms + appeal_ms
                    current_time_penalty_s = (ingame_ms + postrace_ms + appeal_ms) // 1000
        else:
            async with get_connection(self.state.db_path) as db:
                cursor = await db.execute(
                    f"""
                    SELECT qsr.driver_user_id FROM session_results sr
                    JOIN qualifying_session_results qsr ON qsr.session_result_id = sr.id
                    WHERE sr.round_id = ? AND sr.session_type = ? AND sr.status = 'ACTIVE'
                      AND qsr.driver_user_id IN ({in_accounts})
                    """,
                    (self.state.round_id, self.session_type.value, *accounts),
                )
                qsr_row = await cursor.fetchone()
                driver_found = qsr_row is not None
                if qsr_row is not None:
                    driver_user_id = int(qsr_row["driver_user_id"])

        if not driver_found:
            sl = self.session_type.value.replace("_", " ").title()
            await refuse(
                interaction, f"❌ <@{shown_user_id}> was not found in the **{sl}** results.", what=describe_form(self)
            )
            return

        # current_time_ms and current_time_penalty_s already computed above.

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
            await refuse(
                interaction, f"❌ {result}", what=describe_form(self)
            )
            return

        # Stage the penalty (with description and justification) and refresh.
        result.description = self.description_input.value.strip()
        result.justification = self.justification_input.value.strip()

        pl = _pen_label(result)
        sl = self.session_type.value.replace("_", " ").title()
        action = "Correction" if self.use_appeals_staging else "Penalty"
        review = "appeals" if self.use_appeals_staging else "penalty"
        await _ask_stage(
            self.state.queue,
            interaction,
            self.state,
            {"op": "penalty", "penalty": result.to_payload()},
            appeals=self.use_appeals_staging,
            doing=f"Staging a {action.lower()} in the {review} review",
            reply=f"\u2705 Staged {action}: <@{shown_user_id}> | {sl} | **{pl}**",
            what=describe_form(self),
            log_what=describe_form(self),
            log_detail=[
                f"review: {review} review of round {self.state.round_number} "
                f"({self.state.division_name})",
                f"staged: {action} {pl} for <@{shown_user_id}> in {sl}",
            ],
        )


# ---------------------------------------------------------------------------
# Add Pardon modal (033-attendance-tracking T007)
# ---------------------------------------------------------------------------

_VALID_PARDON_TYPES = {"NO_RSVP", "ABSENT", "NO_SHOW"}


class AddPardonModal(LeagueModal, title="Attendance Pardon"):
    """Three-field modal for staging an attendance pardon during penalty review."""

    driver_id_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Driver Discord User ID",
        placeholder="123456789012345678",
        required=True,
        max_length=25,
    )
    pardon_type_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Pardon Type",
        placeholder="NO_RSVP / ABSENT / NO_SHOW",
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
        # Not deferred for the queue to answer the form, as it says it is under way (#439, C54);
        # a refusal below defers first, so that it is answered as it always was.

        # --- The review may have moved on while the form was open (FR-011, #402) ---
        refusal = await _review_moved_on(self.state)
        if refusal is not None:
            await interaction.response.defer(ephemeral=True)
            await refuse(
                interaction, refusal, what=describe_form(self)
            )
            return

        # --- Parse driver user ID ---
        raw_id = self.driver_id_input.value.strip()
        parsed_id = parse_user_id(raw_id)
        if parsed_id is None:
            await refuse(
                interaction, "❌ Invalid Discord User ID — must be a numeric ID.", what=describe_form(self)
            )
            return
        driver_user_id = parsed_id

        # --- Validate pardon type ---
        pardon_type = self.pardon_type_input.value.strip().upper()
        if pardon_type not in _VALID_PARDON_TYPES:
            await refuse(
                interaction, f"❌ Invalid pardon type `{pardon_type}`. Must be one of: NO_RSVP, ABSENT, NO_SHOW.", what=describe_form(self)
            )
            return

        justification = self.justification_input.value.strip()
        # Refused as a penalty's texts are (#204). The log channel it reaches notifies nobody and
        # draws nothing; this keeps one rule for every text a steward types.
        refusal = STEWARD_TEXT.check("justification", justification).refusal
        if refusal is not None:
            await refuse(
                interaction, f"❌ {refusal}", what=describe_form(self)
            )
            return

        from leaguebot.core.db.database import get_connection
        from leaguebot.core.services.drivers.driver_service import current_account_of, resolve_driver_profile_id

        async with get_connection(self.state.db_path) as db:
            # --- Resolve driver profile ID ---
            profile_id = await resolve_driver_profile_id(driver_user_id, db)
            if profile_id is None:
                await refuse(
                    interaction, f"❌ No driver profile found for user ID `{driver_user_id}` in this server.", what=describe_form(self)
                )
                return
            # Any account the driver has held names them; the pardon is staged, and every
            # message names them, by the one they use now (issue #243). It is matched to
            # the round through the profile, so the account it names changes nothing else.
            driver_user_id = int(await current_account_of(db, driver_user_id))

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
            #     until the report approval's save, so we query results here instead.
            #     A driver is considered attended if they appear in ANY active
            #     session result for this round. ---
            cursor = await db.execute(
                """
                SELECT 1 FROM (
                    SELECT rsr.driver_profile_id
                    FROM race_session_results rsr
                    JOIN session_results sr ON sr.id = rsr.session_result_id
                    WHERE sr.round_id = ? AND sr.status = 'ACTIVE'
                      AND rsr.driver_profile_id = ?
                    UNION ALL
                    SELECT qsr.driver_profile_id
                    FROM qualifying_session_results qsr
                    JOIN session_results sr ON sr.id = qsr.session_result_id
                    WHERE sr.round_id = ? AND sr.status = 'ACTIVE'
                      AND qsr.driver_profile_id = ?
                ) LIMIT 1
                """,
                (self.state.round_id, profile_id,
                 self.state.round_id, profile_id),
            )
            attended_in_results = (await cursor.fetchone()) is not None

        if dra_row is None:
            await refuse(
                interaction, f"❌ No attendance row found for <@{driver_user_id}> in this round. "
                "Ensure results have been submitted first.", what=describe_form(self)
            )
            return

        attendance_id = dra_row["id"]
        rsvp_status = dra_row["rsvp_status"]

        # --- Validate pardon type against driver state (FR-007) ---
        if pardon_type == "NO_RSVP" and rsvp_status != "NO_RSVP":
            await refuse(
                interaction, f"❌ NO_RSVP pardon rejected: <@{driver_user_id}> has RSVP status "
                f"`{rsvp_status}` — they did RSVP, so NO_RSVP pardon is not applicable.", what=describe_form(self)
            )
            return
        if pardon_type == "ABSENT":
            if rsvp_status not in {"NO_RSVP", "TENTATIVE", "DECLINED"}:
                await refuse(
                    interaction, f"❌ ABSENT pardon rejected: <@{driver_user_id}> has RSVP status "
                    f"`{rsvp_status}` — ABSENT requires NO_RSVP, TENTATIVE, or DECLINED status.", what=describe_form(self)
                )
                return
            if attended_in_results:
                await refuse(
                    interaction, f"❌ ABSENT pardon rejected: <@{driver_user_id}> is present in session results.", what=describe_form(self)
                )
                return
        if pardon_type == "NO_SHOW":
            if rsvp_status != "ACCEPTED":
                await refuse(
                    interaction, f"❌ NO_SHOW pardon rejected: <@{driver_user_id}> has RSVP status "
                    f"`{rsvp_status}` — NO_SHOW requires ACCEPTED status.", what=describe_form(self)
                )
                return
            if attended_in_results:
                await refuse(
                    interaction, f"❌ NO_SHOW pardon rejected: <@{driver_user_id}> is present in session results.", what=describe_form(self)
                )
                return

        # --- Check for duplicate in staged_pardons (FR-008) ---
        duplicate = any(
            p.attendance_id == attendance_id and p.pardon_type == pardon_type
            for p in self.state.staged_pardons
        )
        if duplicate:
            await refuse(
                interaction, f"❌ A `{pardon_type}` pardon for <@{driver_user_id}> is already staged.", what=describe_form(self)
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
        # --- Ask the round's lane to stage it (FR-010: the justification goes to the log) ---
        await _ask_stage(
            self.state.queue,
            interaction,
            self.state,
            {"op": "pardon", "pardon": pardon.to_payload()},
            appeals=False,
            doing="Staging an attendance pardon in the penalty review",
            reply=f"\u2705 Pardon staged: <@{driver_user_id}> \u2014 **{pardon_type}**",
            what=describe_form(self),
            log_what="ATTENDANCE_PARDON_STAGED",
            log_detail=[
                f"pardon: {pardon_type}",
                f"driver: <@{driver_user_id}> | round: {self.state.round_number} "
                f"({self.state.division_name})",
                f"justification: {justification}",
            ],
        )


# ---------------------------------------------------------------------------
# Confirm-clear view (used by No Penalties / Confirm when list is non-empty)
# ---------------------------------------------------------------------------

class _ConfirmClearView(LeagueView):
    """Two-button confirmation for clearing the staged penalty list.

    Cancelling it and leaving it to lapse are each recorded (#482), the lapse naming the member
    who was asked (*posted* is the press that asked, whose reply the buttons come down from)
    and the cancel the member who pressed. Neither changes the list.
    """

    _KEPT = (
        "Nothing was cleared; the staged list stands. "
        "Press “No Penalties / Confirm” again to clear it."
    )

    def __init__(
        self, state: PenaltyReviewState, *, posted: discord.Interaction | None = None
    ) -> None:
        super().__init__(timeout=60)
        self.state = state
        self._posted = posted

    async def on_timeout(self) -> None:
        """Nobody answered: the buttons come down and the lapse is recorded."""
        await _take_down_confirmation(self._posted)
        await record_abandoned(
            self.state.bot,
            None if self._posted is None else self._posted.user,
            what=_clear_confirmation(self.state, "penalty"),
            lapsed=True,
            detail=self._KEPT,
        )

    @discord.ui.button(label="Yes, clear and proceed with no penalties", style=discord.ButtonStyle.danger)
    async def confirm_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await _require_lm(
            interaction, self.state, what=_button(button.label, self.state)
        ):
            return
        if not await _require_current(
            interaction, self.state, what=_button(button.label, self.state)
        ):
            return
        # Stopped before the work, not after it: the approval that follows can run past the
        # minute, and a view still listening then would record a lapse for a list that has gone.
        self.stop()
        what = _button(button.label, self.state)
        if approval_unreachable(self.state) is not None:
            # Refused before anything is cleared: the list stands.
            await _approval_not_posted(interaction, self.state, what=what)
            return
        cleared = await _staged_named(self.state, self.state.staged)
        queue = self.state.queue
        # Staged before the question is posted, not after: the staging withdraws any approval
        # question standing, and would withdraw the one about to be posted. Both are on the
        # round's lane, in this order; the press is answered by the first.
        await _ask_stage(
            queue,
            interaction,
            self.state,
            {"op": "clear"},
            appeals=False,
            doing="Clearing the staged penalties of the penalty review",
            reply="🗑️ Cleared the staged penalties; the approval question follows.",
            what=what,
            log_what=what,
            log_detail=[f"cleared: {cleared}"],
        )
        await _ask_confirm(queue, None, self.state, actor=interaction.user, what=what)

    @discord.ui.button(label="Cancel — keep penalties", style=discord.ButtonStyle.secondary)
    async def cancel_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await _require_lm(
            interaction, self.state, what=_button(button.label, self.state)
        ):
            return
        await interaction.response.send_message(
            "↩️ Staged penalties kept intact.", ephemeral=True
        )
        await record_abandoned(
            self.state.bot,
            interaction.user,
            what=_clear_confirmation(self.state, "penalty"),
            lapsed=False,
            detail=self._KEPT,
        )
        self.stop()


# ---------------------------------------------------------------------------
# Approval step helper (T019, T020)
# ---------------------------------------------------------------------------

@posts_in_a_job
async def _show_approval_step(
    interaction: discord.Interaction | None, state: PenaltyReviewState
) -> int | None:
    """Post an :class:`ApprovalView` message to the submission channel: the job of
    `results.review.confirm` (#439, C56).

    Returns the message's id, or None where it was not posted. *interaction* is the press that
    asked, none for the change's job. Where the submission channel cannot
    be reached nothing is posted and nothing is said here: the change's check refuses the press
    beforehand and tells the member (`_approval_not_posted`). A message Discord refuses raises,
    for the queue to stop the round's lane at the job.

    One at a time (#402): pressing **No Penalties / Confirm** again replaces the message rather
    than leaving two approvals standing, and the one posted is recorded so that its buttons can
    tell it from any other.

    **It lists what its Approve commits, the attendance pardons included** (#403). It is reached
    only with no penalty staged — the prompt's own Approve takes a staged list at once — so a
    review holding only pardons, a first pass's or an amendment's, was approved on a message
    saying nothing was staged. A pardon's justification stays in the log channel, as on the
    prompt.
    """
    pardons = [
        f"  • <@{await _shown(state, p.driver_user_id)}> — **{p.pardon_type}**"
        for p in state.staged_pardons
    ]
    pardon_heading = f"**Staged Attendance Pardons ({len(pardons)}):**"
    if state.staged:
        lines = ["**Review and approve the following penalties:**", ""]
        for i, sp in enumerate(state.staged, 1):
            pl = _pen_label(sp)
            sl = sp.session_type.value.replace("_", " ").title()
            lines.append(f"{i}. <@{await _shown(state, sp.driver_user_id)}> | {sl} | **{pl}**")
        if pardons:
            lines += ["", pardon_heading, *pardons]
    elif pardons:
        lines = [
            "✅ **No penalties staged.**",
            "",
            pardon_heading,
            *pardons,
            "",
            "Approve to move on to the appeals with no penalty applied and these pardons granted.",
        ]
    else:
        lines = [
            "✅ **No penalties staged.** "
            "Approve to move on to the appeals with no penalty applied."
        ]
    content = "\n".join(lines)

    ch = as_text_channel(state.bot.get_channel(state.submission_channel_id))
    if ch is not None:
        await _take_down_approval(state)
        view = ApprovalView(state=state)
        msg = await ch.send(content, view=view)
        state.approval_message_id = msg.id
        state.bot.add_view(view, message_id=msg.id)
        return msg.id
    return None


def approval_unreachable(state: PenaltyReviewState) -> str | None:
    """What the member is told where the review's submission channel cannot be reached for the
    approval question, or None where it can."""
    if as_text_channel(state.bot.get_channel(state.submission_channel_id)) is not None:
        return None
    return (
        f"\u26a0\ufe0f The approval question could not be posted: "
        f"<#{state.submission_channel_id}> could not be reached."
    )


async def _approval_not_posted(
    interaction: discord.Interaction, state: PenaltyReviewState, *, what: str
) -> None:
    """Tell the member the approval question could not be posted, and record the refusal.

    Not a fault in the bot: the submission channel is gone or out of reach, as the amend
    review's is when it cannot be posted to. The review stands as it was.
    """
    await refuse(interaction, approval_unreachable(state) or "", what=what)


# ---------------------------------------------------------------------------
# Main persistent view (T010, T011, T018)
# ---------------------------------------------------------------------------

def _add_remove_buttons(view: discord.ui.View, buttons: list[tuple]) -> None:
    """Add a view's Remove buttons in rows 1 to 4, into whatever room those rows have left.

    Each is ``(label, custom_id, callback)``. Placing them by arithmetic alone — five to a row
    from row 1 — ignored the static buttons already sitting there: the penalty review keeps its
    Attendance Pardon button on row 1, so a fifth staged penalty overflowed the row and the
    view raised ``ValueError`` as it was built. A first pass needed five penalties to reach it;
    an amendment reaches it by showing back five reports the round already carries (#345).

    Discord allows twenty-five components to a message, so a list longer than the room left is
    cut short with a warning rather than failing the whole prompt: the entries beyond it are
    still listed and still applied, and removing an earlier one brings theirs into view.
    """
    used = [0] * 5
    for item in view.children:
        if item.row is not None:
            used[item.row] += item.width
    slots = [row for row in range(1, 5) for _ in range(5 - used[row])]
    if len(buttons) > len(slots):
        log.warning(
            "_add_remove_buttons: %d Remove buttons, room for %d; the rest are not shown",
            len(buttons), len(slots),
        )
    for (label, custom_id, callback), row in zip(buttons, slots):
        btn = CallbackButton(
            label=label,
            style=discord.ButtonStyle.danger,
            custom_id=custom_id,
            row=row,
            on_press=callback,
        )
        view.add_item(btn)


async def _ask_reports_approved(
    interaction: discord.Interaction,
    state: PenaltyReviewState,
    *,
    queue: ChangeQueue,
    what: str,
) -> None:
    """Ask the change queue to approve the review's reports as it stands (#439).

    The staged penalties and pardons travel in the change's payload as plain data, so what was
    staged survives a stop once it has been asked for. An amendment's report stage shares the
    screens and is a change of its own kind (`results.amendment.reports.approve`).

    The queue checks the review is current, that no approval of it is in hand and that no
    amendment holds the division, and refuses in the manager's reply where one fails, naming
    *what* in the log.
    """
    if state.is_amendment:
        from leaguebot.results.services.round_amendment.amendment_stage_changes import REPORTS_KIND

        await queue.ask(
            REPORTS_KIND,
            {
                "round_id": state.round_id,
                "division_id": state.division_id,
                "round_number": state.round_number,
                "division_name": state.division_name,
                "session_types": [st.value for st in state.session_types_present],
                "staged": [penalty.to_payload() for penalty in state.staged],
                "pardons": [pardon.to_payload() for pardon in state.staged_pardons],
                "prompt_message_id": state.prompt_message_id,
                "approval_message_id": state.approval_message_id,
            },
            interaction=interaction,
            what=what,
        )
        return
    from leaguebot.results.services.review.report_approval_change import KIND

    await queue.ask(
        KIND,
        {
            "round_id": state.round_id,
            "division_id": state.division_id,
            "round_number": state.round_number,
            "division_name": state.division_name,
            "staged": [penalty.to_payload() for penalty in state.staged],
            "pardons": [pardon.to_payload() for pardon in state.staged_pardons],
            "prompt_message_id": state.prompt_message_id,
            "approval_message_id": state.approval_message_id,
        },
        interaction=interaction,
        what=what,
    )


class PenaltyReviewView(LeagueView):
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
        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue
            if item.custom_id == _CID_ADD:
                item.disabled = state is None or len(state.session_types_present) == 0
            elif item.custom_id == _CID_APPROVE:
                item.disabled = state is None or len(state.staged) == 0

        if state is not None and state.is_amendment:
            # **No resubmission in an amendment** (#345). It replaces every session of the round
            # and sends it back through a first-pass review — against a round already FINAL. An
            # amendment's classification is corrected in its first stage; to start again, the
            # manager cancels and runs `/results rounds amend` afresh.
            for item in list(self.children):
                if getattr(item, "custom_id", None) == _CID_RESUBMIT:
                    self.remove_item(item)

        # Dynamic Remove buttons — one per staged entry (T018), then one per staged pardon: a
        # first pass's until its reports are approved (#356), an amendment's reopened from the
        # round (#345).
        if state is not None:
            buttons = [
                (f"Remove #{idx + 1}", f"pw_remove_{idx}", self._make_remove_cb(idx))
                for idx in range(len(state.staged))
            ]
            buttons += [
                (f"Remove Pardon #{idx + 1}", f"pw_pardon_remove_{idx}",
                 self._make_pardon_remove_cb(idx))
                for idx in range(len(state.staged_pardons))
            ]
            _add_remove_buttons(self, buttons)

    def _make_remove_cb(self, idx: int):
        async def cb(interaction: discord.Interaction) -> None:
            if self.state is None:
                await refuse(
                    interaction,
                    "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.",
                    what=_button(f"Remove #{idx + 1}", None),
                )
                return
            if not await _require_lm(
                interaction, self.state, what=_button(f"Remove #{idx + 1}", self.state)
            ):
                return
            if not await _require_current(
                interaction, self.state, what=_button(f"Remove #{idx + 1}", self.state)
            ):
                return
            if idx < len(self.state.staged):
                removed = self.state.staged[idx]
                pl = _pen_label(removed)
                shown = await _shown(self.state, removed.driver_user_id)
                what = _button(f"Remove #{idx + 1}", self.state)
                await _ask_stage(
                    self.state.queue,
                    interaction,
                    self.state,
                    {"op": "remove", "list": "staged", "entry": removed.to_payload()},
                    appeals=False,
                    doing="Removing a staged penalty from the penalty review",
                    reply=f"🗑️ Removed: <@{shown}> | {pl}",
                    what=what,
                    log_what=what,
                    log_detail=[f"removed: {pl} for <@{shown}>"],
                )
            else:
                await refuse(
                    interaction,
                    "⚠️ That entry no longer exists (the list may have changed).",
                    what=_button(f"Remove #{idx + 1}", self.state),
                )
        return cb

    def _make_pardon_remove_cb(self, idx: int):
        async def cb(interaction: discord.Interaction) -> None:
            if self.state is None:
                await refuse(
                    interaction,
                    "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.",
                    what=_button(f"Remove Pardon #{idx + 1}", None),
                )
                return
            if not await _require_lm(
                interaction, self.state, what=_button(f"Remove Pardon #{idx + 1}", self.state)
            ):
                return
            # Once a first pass's reports are approved its pardons are granted, and removing
            # one would change nothing the round carries (#356, #402).
            if not await _require_current(
                interaction, self.state, what=_button(f"Remove Pardon #{idx + 1}", self.state)
            ):
                return
            if idx < len(self.state.staged_pardons):
                removed = self.state.staged_pardons[idx]
                shown = await _shown(self.state, removed.driver_user_id)
                what = _button(f"Remove Pardon #{idx + 1}", self.state)
                await _ask_stage(
                    self.state.queue,
                    interaction,
                    self.state,
                    {"op": "remove", "list": "pardons", "entry": removed.to_payload()},
                    appeals=False,
                    doing="Removing a staged pardon from the penalty review",
                    reply=f"🗑️ Removed pardon: <@{shown}> | {removed.pardon_type}",
                    what=what,
                    log_what=what,
                    log_detail=[f"removed: {removed.pardon_type} pardon for <@{shown}>"],
                )
            else:
                await refuse(
                    interaction,
                    "⚠️ That pardon no longer exists (the list may have changed).",
                    what=_button(f"Remove Pardon #{idx + 1}", self.state),
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
            await refuse(
                interaction,
                "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.",
                what=_button(button.label, None),
            )
            return
        if not await _require_lm(
            interaction, self.state, what=_button(button.label, self.state)
        ):
            return
        if not await _require_current(
            interaction, self.state, what=_button(button.label, self.state)
        ):
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
            await refuse(
                interaction,
                "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.",
                what=_button(button.label, None),
            )
            return
        if not await _require_lm(
            interaction, self.state, what=_button(button.label, self.state)
        ):
            return
        if not await _require_current(
            interaction, self.state, what=_button(button.label, self.state)
        ):
            return
        if not self.state.staged:
            # No penalties — advance directly to approval step (T019); the question is posted by
            # a change on the round's lane, which refuses the press where the channel is out of reach.
            what = _button("No Penalties / Confirm", self.state)
            await _ask_confirm(
                self.state.queue,
                interaction,
                self.state,
                what=what,
                log_what=what,
                log_detail=["posted the approval question: no penalties staged"],
            )
        else:
            # Ask for explicit confirmation before clearing (T019)
            view = _ConfirmClearView(state=self.state, posted=interaction)
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
            await refuse(
                interaction,
                "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.",
                what=_button(button.label, None),
            )
            return
        if not await _require_lm(
            interaction, self.state, what=_button(button.label, self.state)
        ):
            return
        if not self.state.staged:
            await refuse(
                interaction,
                "⚠️ No penalties are staged. "
                "Use **No Penalties / Confirm** to finalize without penalties.",
                what=_button(button.label, self.state),
            )
            return
        await _ask_reports_approved(
            interaction,
            self.state,
            queue=self.state.queue,
            what=_button(button.label, self.state),
        )

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
            await refuse(
                interaction,
                "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.",
                what=_button(button.label, None),
            )
            return
        if not await _require_lm(
            interaction, self.state, what=_button(button.label, self.state)
        ):
            return
        if not await _require_current(
            interaction, self.state, what=_button(button.label, self.state)
        ):
            return
        from leaguebot.results.services.submission.resubmission_changes import ask_resubmission_start

        await ask_resubmission_start(
            interaction,
            self.state,
            queue=self.state.queue,
            what=_button(button.label, self.state),
        )

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
            await refuse(
                interaction,
                "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.",
                what=_button(button.label, None),
            )
            return
        if not await _require_lm(
            interaction, self.state, what=_button(button.label, self.state)
        ):
            return
        if not await _require_current(
            interaction, self.state, what=_button(button.label, self.state)
        ):
            return
        await interaction.response.send_modal(AddPardonModal(state=self.state))


# ---------------------------------------------------------------------------
# Approval view (T020, T025)
# ---------------------------------------------------------------------------

async def _require_approval_message(
    interaction: discord.Interaction,
    state: PenaltyReviewState,
    *,
    what: str,
) -> bool:
    """Refuse, and return False, unless *interaction* is on the review's approval message (#402).

    The refusal is recorded as *what* refused, named by the caller as `_require_lm`'s is.

    Withdrawing a message takes it off the channel, but a client already showing it can still
    press it; and before this was recorded, an approval message outlived a resubmission, a
    second one posted beside it, and the review it confirmed changing under it.
    """
    message = interaction.message
    if message is not None and message.id == state.approval_message_id:
        return True
    await refuse(
        interaction,
        "❌ This approval message was withdrawn when the review changed or moved on, so it can "
        "no longer be used.",
        what=what,
    )
    return False


class ApprovalView(LeagueView):
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
            await refuse(
                interaction,
                "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.",
                what=_button(button.label, None),
            )
            return
        if not await _require_lm(
            interaction, self.state, what=_button(button.label, self.state)
        ):
            return
        if not await _require_approval_message(
            interaction, self.state, what=_button(button.label, self.state)
        ):
            return
        if not await _require_current(
            interaction, self.state, what=_button(button.label, self.state)
        ):
            return
        what = _button(button.label, self.state)
        await _ask_stage(
            self.state.queue,
            interaction,
            self.state,
            {"op": "make_changes"},
            appeals=False,
            doing="Returning the penalty review to staging",
            reply="↩️ Returned to penalty staging. The staged list is intact.",
            what=what,
            log_what=what,
            log_detail=["withdrew the approval question; the staged list is intact"],
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
            await refuse(
                interaction,
                "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.",
                what=_button(button.label, None),
            )
            return
        if not await _require_lm(
            interaction, self.state, what=_button(button.label, self.state)
        ):
            return
        if not await _require_approval_message(
            interaction, self.state, what=_button(button.label, self.state)
        ):
            return
        # Immutability guard: reject finalize on archived season
        from leaguebot.core.services.seasons.season_service import SeasonImmutableError
        from leaguebot.core.db.database import get_connection as _gc
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
            await refuse(
                interaction,
                "❌ This season is archived (COMPLETED) and cannot be modified.",
                what=_button(button.label, self.state),
            )
            return
        await _ask_reports_approved(
            interaction,
            self.state,
            queue=self.state.queue,
            what=_button(button.label, self.state),
        )
