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
_CID_AV_MAKE_CHANGES  = "pw_av_make_changes"
_CID_AV_APPROVE       = "pw_av_approve"


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
    prompt_message_id: int | None = None
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

    if state.session_types_present:
        lines.append("**Sessions present:**")
        async with get_connection(state.db_path) as db:
            for stype in state.session_types_present:
                cursor = await db.execute(
                    """
                    SELECT dsr.driver_user_id, dsr.finishing_position, dsr.outcome
                    FROM session_results sr
                    JOIN driver_session_results dsr ON dsr.session_result_id = sr.id
                    WHERE sr.round_id = ? AND sr.session_type = ? AND sr.status = 'ACTIVE'
                      AND dsr.is_superseded = 0
                    ORDER BY dsr.finishing_position
                    """,
                    (state.round_id, stype.value),
                )
                rows = await cursor.fetchall()
        label = stype.value.replace("_", " ").title()
        driver_strs = [
            f"P{r['finishing_position']} <@{r['driver_user_id']}>"
            + (f" [{r['outcome']}]" if r["outcome"] not in ("CLASSIFIED", "FINISHED") else "")
            for r in rows
        ]
        lines.append(
            f"  • **{label}**: {', '.join(driver_strs) if driver_strs else '—'}"
        )
        lines.append("")

    if state.staged:
        lines.append(f"**Staged Penalties ({len(state.staged)}):**")
        for i, sp in enumerate(state.staged, 1):
            pl = _pen_label(sp)
            sl = sp.session_type.value.replace("_", " ").title()
            lines.append(
                f"  {i}. <@{sp.driver_user_id}> | {sl} | **{pl}**  ← Remove #{i} below"
            )
    else:
        lines.append("**Staged Penalties:** *(none — click Add Penalty to stage one)*")

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


# ---------------------------------------------------------------------------
# Session selector (ephemeral, non-persistent)
# ---------------------------------------------------------------------------

class _SessionSelectView(discord.ui.View):
    """One button per non-cancelled session type in the round."""

    def __init__(self, state: PenaltyReviewState) -> None:
        super().__init__(timeout=120)
        self.state = state
        for stype in state.session_types_present:
            label = stype.value.replace("_", " ").title()
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)
            btn.callback = self._make_cb(stype)
            self.add_item(btn)

    def _make_cb(self, stype: SessionType):
        async def cb(interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(
                AddPenaltyModal(state=self.state, session_type=stype)
            )
            self.stop()
        return cb


# ---------------------------------------------------------------------------
# Add Penalty modal (T015, T017)
# ---------------------------------------------------------------------------

class AddPenaltyModal(discord.ui.Modal, title="Add Penalty"):
    """Two-field modal for specifying driver @mention/ID and penalty value."""

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

    def __init__(self, state: PenaltyReviewState, session_type: SessionType) -> None:
        super().__init__()
        self.state = state
        self.session_type = session_type

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
                SELECT dsr.id, dsr.total_time
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

        # Validate the penalty value (T017)
        result = validate_penalty_input(
            driver_user_id=driver_user_id,
            session_type=self.session_type,
            penalty_value=self.penalty_input.value,
            current_time_ms=current_time_ms,
        )
        if isinstance(result, str):
            await interaction.followup.send(f"❌ {result}", ephemeral=True)
            return

        # Stage the penalty and refresh the prompt (T017)
        self.state.staged.append(result)
        await _refresh_prompt(self.state)

        pl = _pen_label(result)
        sl = self.session_type.value.replace("_", " ").title()
        await interaction.followup.send(
            f"✅ Staged: <@{driver_user_id}> | {sl} | **{pl}**",
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
        view = _SessionSelectView(state=self.state)
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
        await interaction.response.defer(ephemeral=True)
        await _show_approval_step(interaction, self.state)


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
        # T025: wire to finalize_round
        from services.result_submission_service import finalize_round
        await finalize_round(interaction, self.state)
