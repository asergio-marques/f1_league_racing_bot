"""Posting a round's approval question on the round's lane (#439, slice 6b, C56).

No Penalties / Confirm, and clearing the staged penalties and confirming, end the penalty review's
staging with a question: the approval message, whose Approve commits the review as it stands. It is
a change, `results.review.confirm`, asked with the press and run on the round's lane (the
submission channel's, `channel:<id>`), behind any staging asked before it. The manager is told it
is under way at once.

Its one job, `post_approval`, posts the message and holds its id on the review, and its record
saves the id on `round_submission_channels.approval_message_id`, where a later staging reads it to
withdraw the message (`review_stage_change`). A message Discord refuses stops the round's lane alone
at that job, with Retry and Discard; no id is saved.

**The check** is the review's own (`_review_moved_on`, as a staging's), and that the submission
channel can still be reached: where it cannot, the press is refused, the manager told the
approval question could not be posted, and nothing is started (results :433). It is asked again as
the change comes up to run.

An amendment's review is confirmed alike, in its amend channel, its message kept in memory alone.

The payload is ``round_id``, ``division_id``, ``channel_id`` (the lane), ``amendment``, ``doing`` and ``reply``
(what the manager is told while it is under way and once it is posted), and ``log`` (the line the
press leaves in the log channel, ``what`` and ``detail``; None where the staging asked just before
it wrote the press's line).
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
from leaguebot.results.services import penalty_wizard
from leaguebot.results.services.penalty_wizard import (
    approval_unreachable,
    held_review,
)
from leaguebot.results.services.review_stage_change import (
    approval_on_the_row,
    conversation_open,
    lane_of,
)

__all__ = ["KIND", "review_confirm_change"]

KIND = "results.review.confirm"

POST_APPROVAL = "post_approval"


async def _post_approval(ctx: StepContext) -> StepResult:
    """Post the approval message. An approval standing is taken down first, as the review's
    replaces it: the state's, or the submission row's where the state holds none."""
    state = held_review(int(ctx.payload["round_id"]), appeals=False)
    if state is None:
        return StepResult(result={"dropped": True})
    if state.approval_message_id is None and not ctx.payload.get("amendment"):
        state.approval_message_id = await approval_on_the_row(ctx.db_path, state.round_id)
    message_id = await penalty_wizard._show_approval_step(None, state)
    if message_id is None:
        return StepResult(result={"unreachable": True})
    log = ctx.payload.get("log")
    lines: tuple[str, ...] = ()
    if log is not None:
        lines = (f"{ctx.named} | {log['what']} | Success" + "".join(
            f"\n  {detail}" for detail in log["detail"]
        ),)
    return StepResult(result={"message_id": message_id}, lines=lines)


async def _save_the_id(
    db: aiosqlite.Connection, ctx: StepContext, result: StepResult
) -> None:
    """The record of the post: the submission row names the approval message."""
    message_id = result.result.get("message_id")
    if message_id is None or ctx.payload.get("amendment"):
        return
    await db.execute(
        "UPDATE round_submission_channels SET approval_message_id = ? WHERE round_id = ?",
        (int(message_id), int(ctx.payload["round_id"])),
    )


async def _describe_post(ctx: StepContext) -> str:
    return f"posting the approval question in <#{ctx.payload['channel_id']}>"


def review_confirm_change() -> ChangeType:
    """The change that posts a round's approval question; see the module."""

    async def check(ctx: CheckContext) -> Verdict:
        state = held_review(int(ctx.payload["round_id"]), appeals=False)
        if state is None:
            return Verdict.not_due("The review this was confirmed in is no longer held.")
        refusal = await penalty_wizard._review_moved_on(state)
        if refusal is not None:
            return Verdict.refuse(refusal)
        unreachable = approval_unreachable(state)
        if unreachable is not None:
            return Verdict.refuse(unreachable)
        return Verdict.go()

    def outcome(ctx: OutcomeContext) -> str:
        return str(ctx.payload["reply"])

    steps: dict[str, Step] = {
        POST_APPROVAL: Step(
            POST_APPROVAL, StepKind.ACT, _post_approval, describe=_describe_post,
            record=_save_the_id,
        ),
    }
    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(POST_APPROVAL),),
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
