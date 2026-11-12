"""penalty_wizard.py — The press plumbing both stages of the penalty review share.

The custom IDs of the review's buttons, the league-manager gate, the line a press records in the
log, the labels and confirmations the two stages' buttons use, the guards that refuse a press on a
review that has moved on, the asks that put a stage's change on the queue, and the take-down of
the review's messages.  What a review holds is in *review_state.py*; stage one's views are in
*penalty_review_views.py* and stage two's in *appeals_review_views.py*.

Access: league managers and the league admin (`core.utils.channel_guard.is_league_manager`:
the interaction role, or the league admin role, which carries it).
"""
from __future__ import annotations

import logging
from typing import Any

import discord

from leaguebot.core.utils.in_a_job import posts_in_a_job
from leaguebot.core.services.posting.owned_channel_handler import delete_in_owned_channel
from leaguebot.core.services.queue.change_queue import ChangeQueue
from leaguebot.core.services.settings.channel_registry_service import as_text_channel
from leaguebot.core.utils.channel_guard import is_league_manager
from leaguebot.core.utils.log_lines import refuse
from leaguebot.core.utils.member_names import interaction_member
from leaguebot.results.services.review.review_state import (
    PenaltyReviewState,
    ReadServerConfig,
    _appeals_review_moved_on,
    _review_moved_on,
    hold_review,
)

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
# Authorization helpers
# ---------------------------------------------------------------------------

async def _is_league_manager(
    interaction: discord.Interaction,
    server_config: ReadServerConfig,
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
    config = await server_config()
    if config is None:
        return False
    return is_league_manager(config, interaction.user)


def _button(label: str | None, state: PenaltyReviewState | None, review: str = "penalty") -> str:
    """Name a button of a review as the log channel should read it (#482).

    "the “Remove #2” button of the penalty review of round 3 (Division 1)". A press made after a
    restart has no state, so no review to name: the button alone. A button without a label,
    which none of the review's has, is "a button".
    """
    named = "a button" if label is None else f"the “{label}” button"
    if state is None:
        return named
    return f"{named} of the {review} review of round {state.round_number} ({state.division_name})"


async def _record_press(
    state: PenaltyReviewState, interaction: discord.Interaction, what: str, *detail: str
) -> None:
    """Write the one line a review press leaves in the log channel (#482), in the success form.

    "Alex (<@77>) | the “Remove #2” button of the penalty review of round 3 (Division 1) |
    Success", with what was staged, removed or cleared beneath it. Every press that changes a
    review writes one, and only that one: the approval's own line is the record of an approval.
    Written through the output router the review's state is handed.
    """
    lines = [f"{interaction_member(interaction)} | {what} | Success"]
    lines += [f"  {line}" for line in detail]
    await state.router.post_log("\n".join(lines))


def _clear_confirmation(state: PenaltyReviewState, review: str) -> str:
    """Name a review's clear confirmation as the log channel should read it (#482).

    "the clear confirmation of the penalty review of round 3 (Division 1)".
    """
    return (
        f"the clear confirmation of the {review} review of round {state.round_number} "
        f"({state.division_name})"
    )


async def _take_down_confirmation(posted: discord.Interaction | None) -> None:
    """Take a lapsed confirmation's buttons down, through the interaction that posted it (#482).

    Never through a message object's ``edit``: the confirmation is an ephemeral reply, which
    only the interaction that sent it can edit. Left as it is where the interaction is not to
    hand or Discord will not have it; the lapse is recorded either way.
    """
    if posted is None:
        return
    try:
        await posted.edit_original_response(view=None)
    except discord.HTTPException:
        log.warning("could not take down a lapsed clear confirmation", exc_info=True)


async def _require_lm(
    interaction: discord.Interaction,
    state: PenaltyReviewState,
    *,
    what: str,
) -> bool:
    """If the actor is not a league manager, refuse and return False.

    The refusal is recorded in the log channel as *what* refused, so *what* is the caller's to
    name — the button or form that was pressed, with the review it belongs to (`_button`). The
    reply is the member's alone.
    """
    if not await _is_league_manager(interaction, state.server_config):
        await refuse(
            interaction,
            "⛔ Only league managers can interact with the penalty review.",
            what=what,
        )
        return False
    return True


async def _require_current(
    interaction: discord.Interaction,
    state: PenaltyReviewState,
    *,
    what: str,
) -> bool:
    """If the review has moved on, refuse, say why and return False; see :func:`_review_moved_on`.

    The refusal is recorded as *what* refused, named by the caller as `_require_lm`'s is.
    """
    refusal = await _review_moved_on(state)
    if refusal is not None:
        await refuse(interaction, refusal, what=what)
        return False
    return True


async def _require_appeals_current(
    interaction: discord.Interaction,
    state: PenaltyReviewState,
    *,
    what: str,
) -> bool:
    """If the appeals review has moved on, refuse, say why and return False (#482).

    The refusal is recorded as *what* refused, named by the caller as `_require_lm`'s is.
    """
    refusal = await _appeals_review_moved_on(state)
    if refusal is not None:
        await refuse(interaction, refusal, what=what)
        return False
    return True


async def _ask_stage(
    queue: ChangeQueue,
    interaction: discord.Interaction,
    state: "PenaltyReviewState",
    item: dict[str, Any],
    *,
    appeals: bool,
    doing: str,
    reply: str,
    what: str,
    log_what: str,
    log_detail: list[str],
) -> None:
    """Ask the round's lane, through *queue* (which the callback that stages reads, once), to stage
    *item* in the review (#439, C54, C55), the member told it is under way and then *reply* once it
    is staged. *log_what* and *log_detail* are the line the staging
    leaves in the log channel, written when it has happened."""
    from leaguebot.results.services.review.review_stage_change import KIND

    hold_review(state, appeals=appeals)
    await queue.ask(
        KIND,
        {
            "round_id": state.round_id,
            "division_id": state.division_id,
            "channel_id": state.submission_channel_id,
            "amendment": state.is_amendment,
            "appeals": appeals,
            "prompt_message_id": (
                state.appeals_prompt_message_id if appeals else state.prompt_message_id
            ),
            "item": item,
            "doing": doing,
            "reply": reply,
            "log": {"what": log_what, "detail": log_detail},
        },
        interaction=interaction,
        what=what,
    )


async def _ask_confirm(
    queue: ChangeQueue,
    interaction: discord.Interaction | None,
    state: "PenaltyReviewState",
    *,
    actor: Any = None,
    what: str,
    log_what: str | None = None,
    log_detail: list[str] | None = None,
) -> None:
    """Ask the round's lane, through *queue*, to post the approval question (#439, C56), the member
    told it is under way. *actor* stands for the member where the press was answered by a staging
    asked just before (clearing), whose line is the press's. *log_what* and *log_detail* are the
    line the post leaves in the log channel, none where there are none."""
    from leaguebot.results.services.review.review_confirm_change import KIND

    hold_review(state, appeals=False)
    await queue.ask(
        KIND,
        {
            "round_id": state.round_id,
            "division_id": state.division_id,
            "channel_id": state.submission_channel_id,
            "amendment": state.is_amendment,
            "doing": "Posting the approval question in the penalty review",
            "reply": f"\u2705 The approval question is posted in <#{state.submission_channel_id}>.",
            "log": (
                None if log_what is None
                else {"what": log_what, "detail": log_detail or []}
            ),
        },
        interaction=interaction,
        actor=actor,
        what=what,
    )


# ---------------------------------------------------------------------------
# Prompt refresh helper
# ---------------------------------------------------------------------------

@posts_in_a_job
async def _delete_review_message(state: PenaltyReviewState, message_id: int | None) -> None:
    """Delete one of the review's own messages from its channel, where it is still there."""
    if message_id is None:
        return
    ch = as_text_channel(state.bot.get_channel(state.submission_channel_id))
    if ch is None:
        return
    try:
        message = await ch.fetch_message(message_id)
        await delete_in_owned_channel(message)
    except discord.NotFound:
        pass  # Already gone; a refusal raises, for the job to stop the round's lane on


async def _take_down_approval(state: PenaltyReviewState) -> None:
    """Withdraw the review's approval message, where one is up (#402).

    The message confirms the review as it stood when it was posted — "no penalties staged,
    approve to finalise as submitted" — while its Approve commits the review as it stands when
    pressed. Left up once the review changed, it approved what it did not show: a penalty staged
    after **Make Changes** was applied by an Approve that said there were none. So anything that
    changes the review withdraws it, as does anything that ends the stage it belongs to, and its
    buttons refuse on any message but the one recorded here.
    """
    # Cleared once the delete returns: a refusal raises for the job to stop the lane on, and an
    # amendment keeps the id in memory alone, so a Retry must still find the message.
    await _delete_review_message(state, state.approval_message_id)
    state.approval_message_id = None
