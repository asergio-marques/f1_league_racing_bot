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

The payload is ``round_id``, ``division_id``, ``channel_id`` (the lane), ``appeals`` (whether the
item is staged for the appeals stage), ``prompt_message_id`` (the prompt it was staged against),
``item`` (what was staged, as plain data), ``reply`` (what the manager is told once it is staged),
and ``log`` (the line its staging leaves in the log channel: ``what`` and ``detail``).
"""
from __future__ import annotations

from typing import Any

import aiosqlite

from leaguebot.core.models.change import PlannedStep, StepKind, StepResult, Verdict
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.results.services.penalty_service import StagedPenalty
from leaguebot.results.services import penalty_wizard
from leaguebot.results.services.penalty_wizard import (
    PenaltyReviewState,
    StagedPardon,
    _appeals_review_moved_on,
    _review_moved_on,
    held_review,
)
from leaguebot.results.services.review_posting import _delete_message

__all__ = ["KIND", "review_stage_change"]

KIND = "results.review.stage"

STAGE = "stage"
WITHDRAW_APPROVAL = "withdraw_approval"
REDRAW = "redraw"

#: What an item staged is, as the payload names it.
PENALTY = "penalty"
PARDON = "pardon"


def lane_of(payload: dict[str, Any]) -> str:
    """The round's lane: the submission channel's."""
    return f"channel:{int(payload['channel_id'])}"


def _review(payload: dict[str, Any]) -> PenaltyReviewState | None:
    return held_review(int(payload["round_id"]), appeals=bool(payload.get("appeals")))


def _apply(state: PenaltyReviewState, payload: dict[str, Any]) -> None:
    """Put the item on the review's staged lists."""
    item = payload["item"]
    if item["op"] == PENALTY:
        penalty = StagedPenalty.from_payload(item["penalty"])
        (state.staged_appeals if payload.get("appeals") else state.staged).append(penalty)
    elif item["op"] == PARDON:
        state.staged_pardons.append(StagedPardon.from_payload(item["pardon"]))
    else:
        raise ValueError(f"unknown item staged: {item['op']!r}")


async def _stage(ctx: StepContext) -> StepResult:
    """Apply the item to the review, and plan the withdrawal of the approval message standing and
    the prompt's redraw, in that order. The one line the staging leaves is written here, where it
    has happened."""
    state = _review(ctx.payload)
    if state is None:
        return StepResult(result={"dropped": True})
    _apply(state, ctx.payload)
    then: list[PlannedStep] = []
    approval, state.approval_message_id = state.approval_message_id, None
    if approval is not None:
        then.append(PlannedStep(WITHDRAW_APPROVAL, {
            "channel_id": state.submission_channel_id, "message_id": approval,
            "what": "approval message",
        }))
    then.append(PlannedStep(REDRAW, {}))
    log = ctx.payload["log"]
    line = f"{ctx.named} | {log['what']} | Success" + "".join(
        f"\n  {detail}" for detail in log["detail"]
    )
    return StepResult(result={"staged": True}, then=tuple(then), lines=(line,))


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


async def _describe_stage(ctx: StepContext) -> str:
    return "staging the item in the penalty review"


async def _describe_redraw(ctx: StepContext) -> str:
    return f"redrawing the penalty review prompt in <#{ctx.payload['channel_id']}>"


async def _describe_withdraw(ctx: StepContext) -> str:
    return f"withdrawing the approval message in <#{ctx.step_payload['channel_id']}>"


def review_stage_change() -> ChangeType:
    """The change that stages an item in a round's penalty review; see the module."""

    async def check(ctx: CheckContext) -> Verdict:
        state = _review(ctx.payload)
        if state is None:
            return Verdict.not_due("The review this was staged in is no longer held.")
        refusal = await (
            _appeals_review_moved_on(state) if ctx.payload.get("appeals")
            else _review_moved_on(state)
        )
        if refusal is not None:
            return Verdict.refuse(refusal)
        return Verdict.go()

    async def conversation_open(db: aiosqlite.Connection, payload: dict[str, Any]) -> bool:
        row = await (
            await db.execute(
                "SELECT 1 FROM round_submission_channels WHERE round_id = ? AND channel_id = ? "
                "AND closed = 0",
                (int(payload["round_id"]), int(payload["channel_id"])),
            )
        ).fetchone()
        return row is not None

    def outcome(ctx: OutcomeContext) -> str:
        return str(ctx.payload["reply"])

    steps: dict[str, Step] = {
        STAGE: Step(STAGE, StepKind.ACT, _stage, describe=_describe_stage),
        WITHDRAW_APPROVAL: Step(
            WITHDRAW_APPROVAL, StepKind.DELETE, _delete_message, describe=_describe_withdraw,
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

