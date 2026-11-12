"""appeals_review_views.py — Stage two of the penalty review: the appeals review's message and buttons.

Once a round's reports are approved, the submission channel carries the appeals review: its prompt
(:func:`_render_appeals_prompt_content`, kept current by :func:`_refresh_appeals_prompt`) and the
persistent :class:`AppealsReviewView` under it, with its clear confirmation.  Stage one's views are
in *penalty_review_views.py*; the press plumbing both share is in *penalty_wizard.py*.
"""
from __future__ import annotations

import logging

import discord

from leaguebot.core.utils.in_a_job import posts_in_a_job
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import StepFailedOnDiscord
from leaguebot.core.services.queue.change_queue import ChangeQueue
from leaguebot.core.services.settings.channel_registry_service import as_text_channel
from leaguebot.core.services.drivers.driver_service import current_account_map_for_division
from leaguebot.core.utils.league_server import LeagueView
from leaguebot.core.utils.log_lines import record_abandoned, refuse
from leaguebot.results.services.review.penalty_wizard import (
    _CID_AR_ADD,
    _CID_AR_APPROVE,
    _CID_AR_CONFIRM,
    _ask_stage,
    _button,
    _clear_confirmation,
    _record_press,
    _require_appeals_current,
    _require_lm,
    _take_down_confirmation,
)
from leaguebot.results.services.review.penalty_review_views import _SessionSelectView, _add_remove_buttons
from leaguebot.results.services.review.review_state import (
    PenaltyReviewState,
    _pen_label,
    _shown,
    _staged_named,
)

log = logging.getLogger(__name__)


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


@posts_in_a_job
async def _refresh_appeals_prompt(state: PenaltyReviewState) -> None:
    """Edit the existing appeals prompt message to reflect current staged_appeals. As
    `_refresh_prompt`, a message gone is passed over and a refused edit raises."""
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
    except discord.NotFound as exc:
        log.warning("_refresh_appeals_prompt: failed to edit appeals prompt: %s", exc)
    except discord.HTTPException as exc:
        raise StepFailedOnDiscord("the appeals prompt could not be edited") from exc


async def _ask_appeals_approved(
    interaction: discord.Interaction,
    state: PenaltyReviewState,
    *,
    queue: ChangeQueue,
    what: str,
) -> None:
    """Ask the change queue to approve the review's appeals as it stands (#439).

    The staged corrections travel in the change's payload as plain data, so what was staged
    survives a stop once it has been asked for. An amendment's appeals stage shares the screens
    and is a change of its own kind (`results.amendment.appeals.approve`).

    The queue checks the review is current, that no approval of it is in hand and that no
    amendment holds the division, and refuses in the manager's reply where one fails, naming
    *what* in the log.
    """
    if state.is_amendment:
        from leaguebot.results.services.round_amendment.amendment_stage_changes import APPEALS_KIND

        await queue.ask(
            APPEALS_KIND,
            {
                "round_id": state.round_id,
                "division_id": state.division_id,
                "round_number": state.round_number,
                "division_name": state.division_name,
                "session_types": [st.value for st in state.session_types_present],
                "staged": [correction.to_payload() for correction in state.staged_appeals],
                "pardons": [pardon.to_payload() for pardon in state.staged_pardons],
                "appeals_prompt_message_id": state.appeals_prompt_message_id,
            },
            interaction=interaction,
            what=what,
        )
        return
    from leaguebot.results.services.review.appeals_approval_change import KIND

    await queue.ask(
        KIND,
        {
            "round_id": state.round_id,
            "division_id": state.division_id,
            "round_number": state.round_number,
            "division_name": state.division_name,
            "staged": [correction.to_payload() for correction in state.staged_appeals],
            "appeals_prompt_message_id": state.appeals_prompt_message_id,
        },
        interaction=interaction,
        what=what,
    )


# ---------------------------------------------------------------------------
# Appeals review view (T008 — minimal stub; expanded in T018)
# ---------------------------------------------------------------------------

class AppealsReviewView(LeagueView):
    """Persistent appeals review prompt view.

    Mirrors the :class:`PenaltyReviewView` structure:
    - ➕ Add Correction — opens ``_SessionSelectView`` in appeals mode
    - No Changes / Confirm — finalises without corrections (with confirmation when staged)
    - ✅ Approve — applies staged corrections and asks the queue for ``results.appeals.approve``

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
                await refuse(
                    interaction,
                    "⚠️ The bot was restarted. Please wait for the appeals prompt to refresh.",
                    what=_button(f"Remove #{idx + 1}", None, "appeals"),
                )
                return
            if not await _require_lm(
                interaction, self.state, what=_button(f"Remove #{idx + 1}", self.state, "appeals")
            ):
                return
            if not await _require_appeals_current(
                interaction, self.state, what=_button(f"Remove #{idx + 1}", self.state, "appeals")
            ):
                return
            if idx < len(self.state.staged_appeals):
                removed = self.state.staged_appeals[idx]
                pl = _pen_label(removed)
                shown = await _shown(self.state, removed.driver_user_id)
                what = _button(f"Remove #{idx + 1}", self.state, "appeals")
                await _ask_stage(
                    self.state.queue,
                    interaction,
                    self.state,
                    {"op": "remove", "list": "appeals", "entry": removed.to_payload()},
                    appeals=True,
                    doing="Removing a staged correction from the appeals review",
                    reply=f"\U0001f5d1\ufe0f Removed: <@{shown}> | {pl}",
                    what=what,
                    log_what=what,
                    log_detail=[f"removed: {pl} for <@{shown}>"],
                )
            else:
                await refuse(
                    interaction,
                    "⚠️ That entry no longer exists (the list may have changed).",
                    what=_button(f"Remove #{idx + 1}", self.state, "appeals"),
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
            await refuse(
                interaction,
                "⚠️ The bot was restarted. Please wait for the appeals prompt to refresh.",
                what=_button(button.label, None, "appeals"),
            )
            return
        if not await _require_lm(
            interaction, self.state, what=_button(button.label, self.state, "appeals")
        ):
            return
        if not await _require_appeals_current(
            interaction, self.state, what=_button(button.label, self.state, "appeals")
        ):
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
            await refuse(
                interaction,
                "⚠️ The bot was restarted. Please wait for the appeals prompt to refresh.",
                what=_button(button.label, None, "appeals"),
            )
            return
        if not await _require_lm(
            interaction, self.state, what=_button(button.label, self.state, "appeals")
        ):
            return
        if not await _require_appeals_current(
            interaction, self.state, what=_button(button.label, self.state, "appeals")
        ):
            return
        if not self.state.staged_appeals:
            # No corrections — finalise directly
            await _ask_appeals_approved(
                interaction,
                self.state,
                queue=self.state.queue,
                what=_button(button.label, self.state, "appeals"),
            )
        else:
            # Ask for explicit confirmation before clearing
            view = _AppealsConfirmClearView(state=self.state, posted=interaction)
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
            await refuse(
                interaction,
                "⚠️ The bot was restarted. Please wait for the appeals prompt to refresh.",
                what=_button(button.label, None, "appeals"),
            )
            return
        if not await _require_lm(
            interaction, self.state, what=_button(button.label, self.state, "appeals")
        ):
            return
        if not await _require_appeals_current(
            interaction, self.state, what=_button(button.label, self.state, "appeals")
        ):
            return
        if not self.state.staged_appeals:
            await refuse(
                interaction,
                "⚠️ No corrections are staged. "
                "Use **No Changes / Confirm** to finalise without corrections.",
                what=_button(button.label, self.state, "appeals"),
            )
            return
        await _ask_appeals_approved(
            interaction,
            self.state,
            queue=self.state.queue,
            what=_button(button.label, self.state, "appeals"),
        )


class _AppealsConfirmClearView(LeagueView):
    """Two-button confirmation for clearing the staged appeals corrections list.

    Going back and leaving it to lapse are each recorded (#482), as the penalty review's are.
    """

    _KEPT = (
        "Nothing was cleared; the staged list stands. "
        "Press “No Changes / Confirm” again to clear it."
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
            what=_clear_confirmation(self.state, "appeals"),
            lapsed=True,
            detail=self._KEPT,
        )

    @discord.ui.button(
        label="Yes, clear and proceed with no corrections",
        style=discord.ButtonStyle.danger,
    )
    async def confirm_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await _require_lm(
            interaction, self.state, what=_button(button.label, self.state, "appeals")
        ):
            return
        if not await _require_appeals_current(
            interaction, self.state, what=_button(button.label, self.state, "appeals")
        ):
            return
        # Stopped before the approval, which can run past the minute (see the penalty clear).
        self.stop()
        cleared = await _staged_named(self.state, self.state.staged_appeals)
        self.state.staged_appeals.clear()
        await _record_press(
            self.state,
            interaction,
            _button(button.label, self.state, "appeals"),
            f"cleared: {cleared}",
        )
        await _ask_appeals_approved(
            interaction,
            self.state,
            queue=self.state.queue,
            what=_button(button.label, self.state, "appeals"),
        )

    @discord.ui.button(
        label="No, go back",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await record_abandoned(
            self.state.bot,
            interaction.user,
            what=_clear_confirmation(self.state, "appeals"),
            lapsed=False,
            detail=self._KEPT,
        )
        self.stop()
