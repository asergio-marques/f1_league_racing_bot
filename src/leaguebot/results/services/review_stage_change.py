"""Staging a round's penalty review, carried out on the round's lane (#439, slice 6b, C54).

A round's penalty review runs in its submission channel, a conversation with a lane of its own
(`channel:<id>`). What a league manager stages there (a penalty, a pardon, an appeal correction)
is a change, `results.review.stage`, asked with the form or press that staged it. The manager is
told it is under way at once; when its turn comes the item is applied to the review held in memory
(decision 21: the staging stays in memory, and a restart still loses what was staged), the approval
message standing is withdrawn by a job of its own, and the prompt is redrawn by another. A redraw
Discord refuses stops the round's lane alone, with the item already staged, and Retry draws it
again without staging it twice.

**What the lane applies the item to.** The review's state is held by its views and forms, and a
change on the queue carries plain data only, so the form or press that asks hands the review it
stands for to `hold_review` first (`penalty_wizard`), keyed by round and stage. The change reads the
review it finds there when it runs. A restart finds none, as it finds no staged item: the change is
then no longer due, which keeps defect 1 of #439 fixed (a prompt put back after a restart is a new
one, and an old press staged nothing against it).

**The check** is the review's own: while the round is at the stage the review is of, and its prompt
is the one the channel records (`_review_moved_on`, `_appeals_review_moved_on`). It is asked again
as the change comes up to run, so a staging that waited behind a stop and found the prompt replaced
is dropped, and a stop is not left behind it.

**What is staged** (C54, C55) is a penalty, a pardon or a correction added; an entry removed (a
penalty, a pardon or a correction, named by the entry itself and not its place, which a removal
before it may have moved); the staged penalties cleared; or none of these, for Make Changes, which
only brings the review back to staging. Whichever it is, the approval message standing is withdrawn
and the prompt redrawn after it. The approval message is the state's, or the submission row's where
the state holds none (C56, `round_submission_channels.approval_message_id`, which the confirmation
saves with its post): the withdrawal's record clears the row. An amendment's review keeps its
approval message in memory alone, as its channel is not the submission's.

The payload is ``round_id``, ``division_id``, ``channel_id`` (the lane), ``amendment`` (whether the review is an amendment's,
held in its amend channel), ``appeals`` (whether the
item is staged for the appeals stage), ``prompt_message_id`` (the prompt it was staged against),
``item`` (what was staged, as plain data), ``reply`` (what the manager is told once it is staged),
and ``log`` (the line its staging leaves in the log channel: ``what`` and ``detail``).
"""
from __future__ import annotations

from typing import Any

import aiosqlite

import discord

from leaguebot.core.models.change import (
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.db.database import get_connection
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.results.services.penalty_service import StagedPenalty
from leaguebot.results.services import penalty_wizard
from leaguebot.results.services.penalty_wizard import (
    PenaltyReviewState,
    StagedPardon,
    held_review,
)
from leaguebot.results.services.results_post_service import _delete_posting

__all__ = ["KIND", "review_stage_change"]

KIND = "results.review.stage"

STAGE = "stage"
WITHDRAW_APPROVAL = "withdraw_approval"
REDRAW = "redraw"

#: What an item staged is, as the payload names it.
PENALTY = "penalty"
PARDON = "pardon"
REMOVE = "remove"
CLEAR = "clear"
MAKE_CHANGES = "make_changes"

#: The lists a removal names, and where the review holds each.
STAGED = "staged"
PARDONS = "pardons"
APPEALS = "appeals"

NO_LONGER_THERE = "\u26a0\ufe0f That entry no longer exists (the list may have changed)."


def lane_of(payload: dict[str, Any]) -> str:
    """The round's lane: the submission channel's."""
    return f"channel:{int(payload['channel_id'])}"


def _review(payload: dict[str, Any]) -> PenaltyReviewState | None:
    return held_review(int(payload["round_id"]), appeals=bool(payload.get("appeals")))


def _list_of(state: PenaltyReviewState, name: str) -> list[Any]:
    """The staged list a removal names."""
    if name == STAGED:
        return state.staged
    if name == APPEALS:
        return state.staged_appeals
    if name == PARDONS:
        return state.staged_pardons
    raise ValueError(f"unknown list: {name!r}")


def _place_of(state: PenaltyReviewState, item: dict[str, Any]) -> int | None:
    """Where the entry a removal names stands in its list now, or None where it no longer does."""
    for place, entry in enumerate(_list_of(state, item["list"])):
        if entry.to_payload() == item["entry"]:
            return place
    return None


def _apply(state: PenaltyReviewState, payload: dict[str, Any]) -> None:
    """Put the item on the review's staged lists, or take it off them."""
    item = payload["item"]
    if item["op"] == PENALTY:
        penalty = StagedPenalty.from_payload(item["penalty"])
        (state.staged_appeals if payload.get("appeals") else state.staged).append(penalty)
    elif item["op"] == PARDON:
        state.staged_pardons.append(StagedPardon.from_payload(item["pardon"]))
    elif item["op"] == REMOVE:
        place = _place_of(state, item)
        if place is not None:
            _list_of(state, item["list"]).pop(place)
    elif item["op"] == CLEAR:
        state.staged.clear()
    elif item["op"] != MAKE_CHANGES:
        raise ValueError(f"unknown item staged: {item['op']!r}")


async def approval_on_the_row(db_path: str, round_id: int) -> int | None:
    """The approval message the submission's row names, or None."""
    async with get_connection(db_path) as db:
        row = await (
            await db.execute(
                "SELECT approval_message_id FROM round_submission_channels WHERE round_id = ?",
                (round_id,),
            )
        ).fetchone()
    return None if row is None or row["approval_message_id"] is None else int(
        row["approval_message_id"]
    )


async def _stage(ctx: StepContext) -> StepResult:
    """Apply the item to the review, and plan the withdrawal of the approval message standing and
    the prompt's redraw, in that order. The one line the staging leaves is written here, where it
    has happened."""
    state = _review(ctx.payload)
    if state is None:
        return StepResult(result={"dropped": True})
    _apply(state, ctx.payload)
    then: list[PlannedStep] = []
    approval = None
    if not ctx.payload.get("appeals"):
        # The appeals stage has no approval message of its own: the report stage's is not its to
        # withdraw. An amendment's is the state's alone: the row is the submission's.
        approval, state.approval_message_id = state.approval_message_id, None
        if approval is None and not ctx.payload.get("amendment"):
            approval = await approval_on_the_row(ctx.db_path, state.round_id)
    if approval is not None:
        then.append(PlannedStep(WITHDRAW_APPROVAL, {
            "round_id": state.round_id, "channel_id": state.submission_channel_id,
            "message_id": approval, "what": "approval message",
        }))
    then.append(PlannedStep(REDRAW, {}))
    log = ctx.payload["log"]
    line = f"{ctx.named} | {log['what']} | Success" + "".join(
        f"\n  {detail}" for detail in log["detail"]
    )
    return StepResult(result={"staged": True}, then=tuple(then), lines=(line,))


async def _withdraw(ctx: StepContext) -> StepResult:
    """Delete the approval message, found in the review's channel as the review's own redraw finds
    it. A message already gone, or its channel, completes the job; a deletion Discord refuses
    raises, for the queue to stop the round's lane at it."""
    payload = ctx.step_payload
    channel = as_text_channel(ctx.bot.get_channel(int(payload["channel_id"])))
    if channel is None:
        return StepResult(result={"gone": True})
    message_id = int(payload["message_id"])
    failures: list[discord.HTTPException] = []
    left = await _delete_posting(
        channel, message_id, [message_id], label=payload.get("what", "message"),
        failures=failures,
    )
    if left:
        raise StepFailedOnDiscord(
            "the approval message could not be deleted",
            result={"link": channel.get_partial_message(message_id).jump_url},
        ) from (failures[0] if failures else None)
    return StepResult(result={"deleted": True})


async def _redraw(ctx: StepContext) -> StepResult:
    """Edit the review's prompt to show what is staged, as the review's own refresh does (it
    raises, to stop the round's lane at this job, where Discord refuses the edit)."""
    state = _review(ctx.payload)
    if state is None:
        return StepResult(result={"gone": True})
    if ctx.payload.get("appeals"):
        await penalty_wizard._refresh_appeals_prompt(state)
    else:
        await penalty_wizard._refresh_prompt(state)
    return StepResult(result={"drawn": True})


async def _clear_approval_on_the_row(
    db: aiosqlite.Connection, ctx: StepContext, result: StepResult
) -> None:
    """The record of the withdrawal: the row no longer names the message taken down."""
    await db.execute(
        "UPDATE round_submission_channels SET approval_message_id = NULL "
        "WHERE round_id = ? AND approval_message_id = ?",
        (int(ctx.step_payload["round_id"]), int(ctx.step_payload["message_id"])),
    )


async def _describe_stage(ctx: StepContext) -> str:
    return "staging the item in the penalty review"


async def _describe_redraw(ctx: StepContext) -> str:
    return f"redrawing the penalty review prompt in <#{ctx.payload['channel_id']}>"


async def _describe_withdraw(ctx: StepContext) -> str:
    return f"withdrawing the approval message in <#{ctx.step_payload['channel_id']}>"


async def conversation_open(db: aiosqlite.Connection, payload: dict[str, Any]) -> bool:
    """Whether the conversation the review is held in has not ended since: the round's submission
    row, or the amendment's where the review is an amendment's, names this channel and is not
    closed. A round with no row records nothing that ended it (a test's database has none)."""
    if payload.get("amendment"):
        query = "SELECT channel_id, closed_at IS NOT NULL AS closed FROM round_amend_channels"
    else:
        query = "SELECT channel_id, closed FROM round_submission_channels"
    row = await (
        await db.execute(f"{query} WHERE round_id = ?", (int(payload["round_id"]),))
    ).fetchone()
    return row is None or (
        int(row["channel_id"]) == int(payload["channel_id"]) and not row["closed"]
    )


def review_stage_change() -> ChangeType:
    """The change that stages an item in a round's penalty review; see the module."""

    async def check(ctx: CheckContext) -> Verdict:
        state = _review(ctx.payload)
        if state is None:
            return Verdict.not_due("The review this was staged in is no longer held.")
        refusal = await (
            penalty_wizard._appeals_review_moved_on(state) if ctx.payload.get("appeals")
            else penalty_wizard._review_moved_on(state)
        )
        if refusal is not None:
            return Verdict.refuse(refusal)
        item = ctx.payload["item"]
        if item["op"] == REMOVE and _place_of(state, item) is None:
            return Verdict.refuse(NO_LONGER_THERE)
        return Verdict.go()

    def outcome(ctx: OutcomeContext) -> str:
        return str(ctx.payload["reply"])

    steps: dict[str, Step] = {
        STAGE: Step(STAGE, StepKind.ACT, _stage, describe=_describe_stage),
        WITHDRAW_APPROVAL: Step(
            WITHDRAW_APPROVAL, StepKind.DELETE, _withdraw, describe=_describe_withdraw,
            record=_clear_approval_on_the_row,
        ),
        REDRAW: Step(REDRAW, StepKind.EDIT, _redraw, describe=_describe_redraw),
    }
    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(STAGE),),
        steps=steps,
        check=check,
        key=lambda payload: f"{KIND}:{payload['round_id']}",
        doing=lambda payload: str(payload["doing"]),
        outcome=outcome,
        repeatable=True,
        lane=lane_of,
        conversation_open=conversation_open,
        writes=("round_submission_channels",),
    )

