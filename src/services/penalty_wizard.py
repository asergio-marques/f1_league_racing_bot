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
from models.round import RoundStatus
from services.channel_registry_service import as_text_channel
from services.driver_service import accounts_of_in_division, current_account_map_for_division
from services.penalty_service import StagedPenalty, validate_penalty_input
from utils.channel_guard import is_league_manager
from utils.input_validator import STEWARD_TEXT, parse_user, parse_user_id
from utils.league_bot import LeagueBot
from utils.league_server import CallbackButton, LeagueModal, LeagueView

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
# Attendance pardon staging dataclass
# ---------------------------------------------------------------------------

@dataclass
class StagedPardon:
    """In-memory representation of a staged attendance pardon (033-attendance-tracking)."""
    driver_user_id: int       # Discord user ID (display / audit)
    driver_profile_id: int    # FK — driver_profiles.id
    attendance_id: int        # FK — driver_round_attendance.id
    pardon_type: str          # 'NO_RSVP' | 'ABSENT' | 'NO_SHOW'
    justification: str
    grantor_id: int           # Discord user ID of staging admin
    #: When a pardon read back from the round was granted, so that an amendment writing it out
    #: again keeps the time as well as the grantor (#345). None for one staged now.
    granted_at: str | None = None


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
    bot: LeagueBot
    staged: list[StagedPenalty] = field(default_factory=list)
    staged_appeals: list[StagedPenalty] = field(default_factory=list)
    staged_pardons: list[StagedPardon] = field(default_factory=list)
    prompt_message_id: int | None = None
    appeals_prompt_message_id: int | None = None
    #: The approval message **No Penalties / Confirm** posted, while it stands (#402). Its buttons
    #: act only while they sit on this message; see :func:`_take_down_approval`.
    approval_message_id: int | None = None
    round_number: int = 0
    division_name: str = ""
    #: True where this review is an **amendment replaying a settled round**, not the round's
    #: first pass through review (#345).
    #:
    #: The stages are the same and the views are the same; what differs is what approving one
    #: means. A first pass moves the round on — report review sets `AWAITING_APPEAL_VERDICTS`,
    #: appeal review sets `FINAL`, refreshes the division's status and may wind the season
    #: down. An amended round is *already* FINAL, so replaying those transitions would move a
    #: settled round backwards and forwards through states it has long since left, and could
    #: finish a division or wind down a season a second time.
    #:
    #: Read by the finalisers, which hand an amendment's stages to functions of their own.
    is_amendment: bool = False
    #: Set once an amendment's report stage has been approved (#345). ``apply_penalties`` adds
    #: to the penalty columns, so approving the stage a second time would add every report
    #: again; a first pass is guarded by ``staged_penalties``, which an amendment does not own.
    reports_approved: bool = False
    #: Set while a first pass's reports are being approved (#402). The approval draws every
    #: graphic the round posts before it moves the round on, which on the Pi is a long time, and
    #: until the round moves on nothing in the database says the review is closing. This does.
    approving: bool = False


# ---------------------------------------------------------------------------
# Authorization helpers
# ---------------------------------------------------------------------------

async def _is_league_manager(
    interaction: discord.Interaction,
    db_path: str,
    bot: LeagueBot,
) -> bool:
    """Return True if the interacting member holds the league manager tier.

    This asked for the interaction role and nothing else, so a league admin who did not also
    hold that role was refused all thirteen buttons of the penalty and appeals reviews — the
    same defect the commands had (issue #116), on the half of the bot that is entirely
    button-driven and where it was therefore never noticed.

    Asking `is_league_manager` is what makes the buttons and the commands agree: the higher
    tier carries the lower, so the admin role passes here without anybody needing both.
    """
    if not isinstance(interaction.user, discord.Member):
        return False
    config = await bot.config_service.get_server_config()
    if config is None:
        return False
    return is_league_manager(config, interaction.user)


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


async def _shown(state: PenaltyReviewState, driver_user_id: int) -> int:
    """The account a message names *driver_user_id*'s driver by: the one they use now.

    A staged penalty carries the account its result row stands under, which is what applies
    it; the driver may have moved on from that account since (issue #243).
    """
    async with get_connection(state.db_path) as db:
        current_of = await current_account_map_for_division(db, state.division_id)
    return current_of.get(driver_user_id, driver_user_id)


#: What a review answers while its reports are being approved (#402).
_BEING_APPROVED = (
    "⏳ This round's reports are being approved. Its appeals review is posted below once they are."
)


async def _review_moved_on(state: PenaltyReviewState, *, pardons: bool = False) -> str | None:
    """Why this review can no longer be acted on, or None while it can (#402).

    Every control of the review acts on *state*, which is held in memory and outlives the stage
    it was built for. The prompt and the approval message stayed on screen, and worked, through a
    resubmission, through the appeals stage and while an approval was still being applied — so a
    manager could approve results already being replaced, approve the reports a second time, or
    be told a penalty was removed when it had been applied. Every control asks this first.

    A first pass is current while all four hold:

    - its reports are not already being approved (``approving``, set by the finaliser);
    - the round still awaits its report verdicts. Approved, it waits on appeals, and a round
      that has ended has nothing left to review. This is what closes its pardons (FR-011): they
      are written when the reports are approved;
    - no resubmission is collecting, which replaces the results this review is of;
    - its prompt is the one the channel records. Every review posts a prompt of its own and
      records it, so a review whose prompt has been replaced — by a cancelled or completed
      resubmission, or by restart recovery — is an old one, whatever its state still holds.

    An amendment's round is FINAL throughout, so none of that applies to it. Its reports close
    once approved; its **pardons** do not, because an amendment writes them when its appeals are
    approved, and until then this review is where they are changed (#345). The controls that
    stage or remove a pardon pass *pardons*.

    **One race is left open.** A resubmission records itself only after logging what it
    discards, so an approval pressed in that moment passes the check. The window is one log post
    wide, and both buttons would have to be pressed inside it.
    """
    if state.is_amendment:
        if pardons or not state.reports_approved:
            return None
        return (
            "❌ This amendment's reports are already approved, so they can no longer be changed "
            "here. Its appeals are reviewed below."
        )
    if state.approving:
        return _BEING_APPROVED
    async with get_connection(state.db_path) as db:
        cursor = await db.execute(
            """
            SELECT r.status, rsc.resubmitting, rsc.prompt_message_id
            FROM rounds r
            JOIN round_submission_channels rsc ON rsc.round_id = r.id AND rsc.closed = 0
            WHERE r.id = ?
            """,
            (state.round_id,),
        )
        row = await cursor.fetchone()
    if row is not None and row["status"] == RoundStatus.AWAITING_APPEAL_VERDICTS.value:
        return (
            "❌ This round's post-race penalties have already been approved, and its pardons "
            "with them. Its appeals are reviewed below; a decision already applied is changed "
            "with `/round results amend` once the round is final."
        )
    if row is None or row["status"] != RoundStatus.AWAITING_REPORT_VERDICTS.value:
        return "❌ This round's penalty review is over, so nothing here can be changed."
    if row["resubmitting"]:
        return (
            "❌ This round's results are being resubmitted. The penalty review is posted again "
            "once every session is in, or when the resubmission is cancelled."
        )
    if row["prompt_message_id"] != state.prompt_message_id:
        return (
            "❌ This penalty review has been replaced by a newer one in this channel. "
            "Use that one."
        )
    return None


async def _require_current(
    interaction: discord.Interaction,
    state: PenaltyReviewState,
    *,
    pardons: bool = False,
) -> bool:
    """If the review has moved on, say why and return False; see :func:`_review_moved_on`."""
    refusal = await _review_moved_on(state, pardons=pardons)
    if refusal is not None:
        await interaction.response.send_message(refusal, ephemeral=True)
        return False
    return True


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


# ---------------------------------------------------------------------------
# Prompt refresh helper
# ---------------------------------------------------------------------------

async def _delete_review_message(state: PenaltyReviewState, message_id: int | None) -> None:
    """Delete one of the review's own messages from its channel, where it is still there."""
    if message_id is None:
        return
    ch = as_text_channel(state.bot.get_channel(state.submission_channel_id))
    if ch is None:
        return
    try:
        message = await ch.fetch_message(message_id)
        await message.delete()
    except (discord.NotFound, discord.HTTPException):
        pass  # Already gone; nothing depends on it


async def _take_down_approval(state: PenaltyReviewState) -> None:
    """Withdraw the review's approval message, where one is up (#402).

    The message confirms the review as it stood when it was posted — "no penalties staged,
    approve to finalise as submitted" — while its Approve commits the review as it stands when
    pressed. Left up once the review changed, it approved what it did not show: a penalty staged
    after **Make Changes** was applied by an Approve that said there were none. So anything that
    changes the review withdraws it, as does anything that ends the stage it belongs to, and its
    buttons refuse on any message but the one recorded here.
    """
    message_id, state.approval_message_id = state.approval_message_id, None
    await _delete_review_message(state, message_id)


async def _take_down_report_stage(state: PenaltyReviewState) -> None:
    """Take a review's report-stage controls down once its reports are approved (#402).

    Left up, they were what a manager pressed to approve the reports a second time, or to be
    told a penalty was removed that had been applied. The controls refuse regardless; this takes
    them out of reach.

    A first pass's prompt goes with its approval message: its pardons are granted with the
    reports, so nothing on it is left to do. An amendment's prompt stays, because its pardons are
    changed there until its appeals are approved (#345), and only the approval message goes.

    **Never raises.** It runs after the round has moved on and before the next stage is opened,
    and a failure to tidy the channel must not cost the manager the appeals prompt.
    """
    try:
        await _take_down_approval(state)
        if not state.is_amendment:
            await _delete_review_message(state, state.prompt_message_id)
    except Exception:  # noqa: BLE001 — the controls refuse whether or not they came down
        log.exception("could not take down the report stage of round %s", state.round_id)


async def _refresh_prompt(state: PenaltyReviewState) -> None:
    """Edit the existing prompt message to reflect the current staged list.

    Every change to the review comes through here, and **Make Changes** does too, so this is
    where an approval message confirming the review as it stood is withdrawn (#402).
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
    if state.is_amendment:
        # Nothing has been posted during an amendment, so the first pass's line would be false.
        lines[2] = (
            "**Stage 3 of 3 — Appeals.** The appeals the amended sessions already carry are "
            "listed below. Approving commits the amendment and rebuilds the division's "
            "channels in round order."
        )
    if state.staged_appeals:
        async with get_connection(state.db_path) as db:
            current_of = await current_account_map_for_division(db, state.division_id)
        lines.append(f"**Staged Corrections ({len(state.staged_appeals)}):**")
        for i, sp in enumerate(state.staged_appeals, 1):
            pl = _pen_label(sp)
            sl = sp.session_type.value.replace("_", " ").title()
            shown = current_of.get(sp.driver_user_id, sp.driver_user_id)
            lines.append(
                f"  {i}. <@{shown}> | {sl} | **{pl}**  ← Remove #{i} below"
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
    ch = as_text_channel(state.bot.get_channel(state.submission_channel_id))
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

        # The review may have moved on while the form was open (#402). A correction is staged on
        # the appeals review, which is the stage the round has moved on *to*.
        if not self.use_appeals_staging:
            refusal = await _review_moved_on(self.state)
            if refusal is not None:
                await interaction.followup.send(refusal, ephemeral=True)
                return

        # Both texts are published in the verdict, so neither may mention a group or carry an
        # emoji (#204).
        for label, text_input in (
            ("description", self.description_input),
            ("justification", self.justification_input),
        ):
            refusal = STEWARD_TEXT.check(label, text_input.value).refusal
            if refusal is not None:
                await interaction.followup.send(f"❌ {refusal}", ephemeral=True)
                return

        # Resolve driver user ID from @mention or raw integer
        raw = self.driver_input.value.strip()
        driver_user_id = parse_user(raw)
        if driver_user_id is None:
            await interaction.followup.send(
                "❌ Could not parse driver. Use a @mention or a Discord user ID.",
                ephemeral=True,
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
            await interaction.followup.send(
                f"❌ <@{shown_user_id}> was not found in the **{sl}** results.",
                ephemeral=True,
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
            f"\u2705 Staged {action}: <@{shown_user_id}> | {sl} | **{pl}**",
            ephemeral=True,
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
        await interaction.response.defer(ephemeral=True)

        # --- The review may have moved on while the form was open (FR-011, #402) ---
        refusal = await _review_moved_on(self.state, pardons=True)
        if refusal is not None:
            await interaction.followup.send(refusal, ephemeral=True)
            return

        # --- Parse driver user ID ---
        raw_id = self.driver_id_input.value.strip()
        parsed_id = parse_user_id(raw_id)
        if parsed_id is None:
            await interaction.followup.send(
                "❌ Invalid Discord User ID — must be a numeric ID.", ephemeral=True
            )
            return
        driver_user_id = parsed_id

        # --- Validate pardon type ---
        pardon_type = self.pardon_type_input.value.strip().upper()
        if pardon_type not in _VALID_PARDON_TYPES:
            await interaction.followup.send(
                f"❌ Invalid pardon type `{pardon_type}`. Must be one of: NO_RSVP, ABSENT, NO_SHOW.",
                ephemeral=True,
            )
            return

        justification = self.justification_input.value.strip()
        # Refused as a penalty's texts are (#204). The log channel it reaches notifies nobody and
        # draws nothing; this keeps one rule for every text a steward types.
        refusal = STEWARD_TEXT.check("justification", justification).refusal
        if refusal is not None:
            await interaction.followup.send(f"❌ {refusal}", ephemeral=True)
            return

        from db.database import get_connection
        from services.driver_service import current_account_of, resolve_driver_profile_id

        async with get_connection(self.state.db_path) as db:
            # --- Resolve driver profile ID ---
            profile_id = await resolve_driver_profile_id(driver_user_id, db)
            if profile_id is None:
                await interaction.followup.send(
                    f"❌ No driver profile found for user ID `{driver_user_id}` in this server.",
                    ephemeral=True,
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
            #     until finalize_penalty_review, so we query results here instead.
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
        if pardon_type == "ABSENT":
            if rsvp_status not in {"NO_RSVP", "TENTATIVE", "DECLINED"}:
                await interaction.followup.send(
                    f"❌ ABSENT pardon rejected: <@{driver_user_id}> has RSVP status "
                    f"`{rsvp_status}` — ABSENT requires NO_RSVP, TENTATIVE, or DECLINED status.",
                    ephemeral=True,
                )
                return
            if attended_in_results:
                await interaction.followup.send(
                    f"❌ ABSENT pardon rejected: <@{driver_user_id}> is present in session results.",
                    ephemeral=True,
                )
                return
        if pardon_type == "NO_SHOW":
            if rsvp_status != "ACCEPTED":
                await interaction.followup.send(
                    f"❌ NO_SHOW pardon rejected: <@{driver_user_id}> has RSVP status "
                    f"`{rsvp_status}` — NO_SHOW requires ACCEPTED status.",
                    ephemeral=True,
                )
                return
            if attended_in_results:
                await interaction.followup.send(
                    f"❌ NO_SHOW pardon rejected: <@{driver_user_id}> is present in session results.",
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
        await self.state.bot.output_router.post_log(
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

class _ConfirmClearView(LeagueView):
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
        if not await _require_current(interaction, self.state):
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
    """Post an :class:`ApprovalView` message to the submission channel.

    One at a time (#402): pressing **No Penalties / Confirm** again replaces the message rather
    than leaving two approvals standing, and the one posted is recorded so that its buttons can
    tell it from any other.
    """
    if state.staged:
        lines = ["**Review and approve the following penalties:**", ""]
        for i, sp in enumerate(state.staged, 1):
            pl = _pen_label(sp)
            sl = sp.session_type.value.replace("_", " ").title()
            lines.append(f"{i}. <@{await _shown(state, sp.driver_user_id)}> | {sl} | **{pl}**")
        content = "\n".join(lines)
    else:
        content = (
            "✅ **No penalties staged.** "
            "Approve to finalize the round with results as submitted."
        )

    ch = as_text_channel(state.bot.get_channel(state.submission_channel_id))
    if ch is not None:
        await _take_down_approval(state)
        view = ApprovalView(state=state)
        msg = await ch.send(content, view=view)
        state.approval_message_id = msg.id
        state.bot.add_view(view, message_id=msg.id)


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
            # manager cancels and runs `/round results amend` afresh.
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
                await interaction.response.send_message(
                    "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.",
                    ephemeral=True,
                )
                return
            if not await _require_lm(interaction, self.state):
                return
            if not await _require_current(interaction, self.state):
                return
            if idx < len(self.state.staged):
                removed = self.state.staged.pop(idx)
                await interaction.response.defer(ephemeral=True)
                await _refresh_prompt(self.state)
                pl = _pen_label(removed)
                await interaction.followup.send(
                    f"🗑️ Removed: <@{await _shown(self.state, removed.driver_user_id)}> | {pl}",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "⚠️ That entry no longer exists (the list may have changed).",
                    ephemeral=True,
                )
        return cb

    def _make_pardon_remove_cb(self, idx: int):
        async def cb(interaction: discord.Interaction) -> None:
            if self.state is None:
                await interaction.response.send_message(
                    "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.",
                    ephemeral=True,
                )
                return
            if not await _require_lm(interaction, self.state):
                return
            # Once a first pass's reports are approved its pardons are granted, and removing
            # one would change nothing the round carries (#356, #402).
            if not await _require_current(interaction, self.state, pardons=True):
                return
            if idx < len(self.state.staged_pardons):
                removed = self.state.staged_pardons.pop(idx)
                await interaction.response.defer(ephemeral=True)
                await _refresh_prompt(self.state)
                await interaction.followup.send(
                    f"🗑️ Removed pardon: <@{await _shown(self.state, removed.driver_user_id)}> "
                    f"| {removed.pardon_type}",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "⚠️ That pardon no longer exists (the list may have changed).",
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
        if not await _require_current(interaction, self.state):
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
        if not await _require_current(interaction, self.state):
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
        if not await _require_current(interaction, self.state):
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
        if not await _require_current(interaction, self.state, pardons=True):
            return
        await interaction.response.send_modal(AddPardonModal(state=self.state))


# ---------------------------------------------------------------------------
# Approval view (T020, T025)
# ---------------------------------------------------------------------------

async def _require_approval_message(
    interaction: discord.Interaction,
    state: PenaltyReviewState,
) -> bool:
    """Refuse, and return False, unless *interaction* is on the review's approval message (#402).

    Withdrawing a message takes it off the channel, but a client already showing it can still
    press it; and before this was recorded, an approval message outlived a resubmission, a
    second one posted beside it, and the review it confirmed changing under it.
    """
    message = interaction.message
    if message is not None and message.id == state.approval_message_id:
        return True
    await interaction.response.send_message(
        "❌ This approval message was withdrawn when the review changed or moved on, so it can "
        "no longer be used.",
        ephemeral=True,
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
            await interaction.response.send_message(
                "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh.",
                ephemeral=True,
            )
            return
        if not await _require_lm(interaction, self.state):
            return
        if not await _require_approval_message(interaction, self.state):
            return
        if not await _require_current(interaction, self.state):
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
        if not await _require_approval_message(interaction, self.state):
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

class AppealsReviewView(LeagueView):
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

        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue
            if item.custom_id == _CID_AR_ADD:
                item.disabled = state is None or len(state.session_types_present) == 0
            elif item.custom_id == _CID_AR_APPROVE:
                item.disabled = state is None or len(state.staged_appeals) == 0

        # Dynamic Remove buttons — one per staged correction
        if state is not None:
            _add_remove_buttons(
                self,
                [
                    (f"Remove #{idx + 1}", f"ar_remove_{idx}", self._make_remove_cb(idx))
                    for idx in range(len(state.staged_appeals))
                ],
            )

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
                    f"\U0001f5d1\ufe0f Removed: <@{await _shown(self.state, removed.driver_user_id)}> | {pl}",
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


class _AppealsConfirmClearView(LeagueView):
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

