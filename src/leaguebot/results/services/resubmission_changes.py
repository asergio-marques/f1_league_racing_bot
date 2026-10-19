"""`results.resubmission.start`: a round's results resubmitted, started on the change queue
(#439, 6b, C51).

A league manager's press of **🔄 Resubmit Initial Results** on the penalty review's prompt asks
this change with the press's interaction, answered privately as under way. It runs on the round's
own lane (``channel:<submission channel>``), the conversation it begins. Its jobs, in order:

1. **`mark`** flags the round's submission channel `resubmitting` and records who started it (a
   restart reads both), and writes the one line the log channel keeps of the press, naming the
   penalties and pardons the press discards: once they are gone it is the only record they were
   staged. The line is queued in the save, so a log channel that refuses it holds nothing up.
2. **`delete_prompt`** and **`delete_approval`** take the review's prompt and any approval message
   down, so that nobody can approve the results being replaced or start a second resubmission
   (#402); a message already gone completes the job.
3. **`post_announcement`** posts the announcement with its Cancel button and saves its id with the
   job's mark (a restart takes the button down by it).
4. **`first_prompt`** reads the first session the resubmission expects and plans the
   **`post_prompt`** that asks for it.

The round's results stand meanwhile: nothing is deleted or replaced until every session has been
entered again. **A channel that cannot be found refuses the press** before anything is discarded:
with nowhere to collect in, the review is all the round has. The press clears the review's staged
penalties, pardons and approval message in memory once the queue has accepted the request.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import PlannedStep, StepKind, StepResult, Verdict
from leaguebot.core.services.change_queue import (
    ChangeQueue,
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.results.services import result_submission_service as submissions
from leaguebot.results.services.review_posting import DELETE_MESSAGE, posting_steps
from leaguebot.results.services.submission_steps import (
    POST_PROMPT,
    post_choice_step,
    post_prompt_step,
)

KIND = "results.resubmission.start"
MARK = "mark"
DELETE_PROMPT = "delete_prompt"
DELETE_APPROVAL = "delete_approval"
POST_ANNOUNCEMENT = "post_announcement"
FIRST_PROMPT = "first_prompt"

ANNOUNCEMENT = (
    "⚠️ **Results resubmission started.** "
    "The results already submitted stand until every session has been entered again. "
    "Press **Cancel** to stop and keep them."
)

__all__ = ["KIND", "ask_resubmission_start", "resubmission_start_change"]


@dataclass(frozen=True)
class ReviewRef:
    """What the Cancel button needs of the review it belongs to, to refuse a press that is not a
    league manager's and to name the review in the log: the penalty review's state is in memory
    and not the announcement's to hold."""

    bot: Any
    db_path: str
    round_number: int
    division_name: str


def _lane(payload: dict[str, Any]) -> str:
    return f"channel:{int(payload['channel_id'])}"


async def _conversation_open(db: aiosqlite.Connection, payload: dict[str, Any]) -> bool:
    row = await (
        await db.execute(
            "SELECT 1 FROM round_submission_channels WHERE round_id = ? AND channel_id = ? "
            "AND closed = 0",
            (int(payload["round_id"]), int(payload["channel_id"])),
        )
    ).fetchone()
    return row is not None


async def _check(ctx: CheckContext) -> Verdict:
    payload = ctx.payload
    if ctx.bot.get_channel(int(payload["channel_id"])) is None:
        return Verdict.refuse(
            "❌ The submission channel could not be found, so the results cannot be resubmitted."
        )
    async with get_connection(ctx.db_path) as db:
        row = await (
            await db.execute(
                "SELECT resubmitting FROM round_submission_channels "
                "WHERE round_id = ? AND channel_id = ? AND closed = 0",
                (int(payload["round_id"]), int(payload["channel_id"])),
            )
        ).fetchone()
    if row is None:
        return Verdict.refuse("⚠️ The round's submission is no longer open.")
    if row["resubmitting"]:
        return Verdict.refuse("⚠️ This round's results are already being resubmitted.")
    return Verdict.go()


def _plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
    channel_id = int(payload["channel_id"])
    steps = [PlannedStep(MARK, dict(payload))]
    if payload.get("prompt_message_id") is not None:
        steps.append(PlannedStep(DELETE_PROMPT, {
            "channel_id": channel_id, "message_id": int(payload["prompt_message_id"]),
            "what": "penalty review prompt",
        }))
    if payload.get("approval_message_id") is not None:
        steps.append(PlannedStep(DELETE_APPROVAL, {
            "channel_id": channel_id, "message_id": int(payload["approval_message_id"]),
            "what": "approval message",
        }))
    steps.append(PlannedStep(POST_ANNOUNCEMENT, {
        "channel_id": channel_id, "text": ANNOUNCEMENT,
        "round_number": payload["round_number"], "division_name": payload["division_name"],
    }))
    steps.append(PlannedStep(FIRST_PROMPT, {
        "channel_id": channel_id, "round_id": int(payload["round_id"]),
    }))
    return tuple(steps)


def _started_line(named: str, payload: dict[str, Any]) -> str:
    return (
        f"{named} | RESULTS_RESUBMISSION | Started\n"
        f"  round_id: {payload['round_id']} ({payload['division_name']})\n"
        f"  discarded_count: {len(payload['discarded'])}\n"
        f"  discarded: {json.dumps(payload['discarded'])}\n"
        f"  discarded_pardons_count: {len(payload['discarded_pardons'])}\n"
        f"  discarded_pardons: {json.dumps(payload['discarded_pardons'])}"
    )


def resubmission_start_change() -> ChangeType:
    """The `results.resubmission.start` change type, for the builder to register.

    The payload is ``round_id``, ``division_id``, ``round_number``, ``division_name``,
    ``channel_id`` (the submission channel), ``started_by``, ``prompt_message_id`` and
    ``approval_message_id`` (each None where none stands) and what the press discards, ``discarded`` penalties and ``discarded_pardons``.
    """

    async def mark(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.step_payload
        await db.execute(
            "UPDATE round_submission_channels SET resubmitting = 1, resubmit_started_by = ? "
            "WHERE round_id = ?",
            (int(payload["started_by"]), int(payload["round_id"])),
        )
        return StepResult(result={"marked": True}, lines=(_started_line(ctx.named, payload),))

    async def first_prompt(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.step_payload
        first = await submissions.expected_resubmitted_session_on(db, int(payload["round_id"]))
        if first is None:
            return StepResult(result={"prompted": False})
        return StepResult(
            result={"prompted": True},
            then=(PlannedStep(POST_PROMPT, {
                "channel_id": int(payload["channel_id"]), "round_id": int(payload["round_id"]),
                "session_type": first.value, "override_fl": True,
            }),),
        )

    async def describe_first_prompt(_ctx: StepContext) -> str:
        return "working out the first session to ask for"

    async def describe_mark(_ctx: StepContext) -> str:
        return "flagging the round's submission as being resubmitted"

    async def describe_announcement(_ctx: StepContext) -> str:
        return "posting the resubmission's announcement"

    async def announcement_view(ctx: StepContext) -> discord.ui.View:
        payload = ctx.step_payload
        return submissions.ResubmissionCancelView(
            ReviewRef(
                ctx.bot, ctx.db_path, int(payload["round_number"]), str(payload["division_name"])
            )
        )

    async def record_announcement(
        db: aiosqlite.Connection, ctx: StepContext, result: StepResult
    ) -> None:
        """Only the restart sweep reads it, to take the button down."""
        await db.execute(
            "UPDATE round_submission_channels SET resubmit_prompt_message_id = ? "
            "WHERE round_id = ?",
            (int(result.result["message_id"]), int(ctx.payload["round_id"])),
        )

    deletion = posting_steps()[DELETE_MESSAGE]
    announcement = dataclasses.replace(
        post_choice_step(
            POST_ANNOUNCEMENT, announcement_view, describing="posting the resubmission's announcement"
        ),
        record=record_announcement,
    )

    def outcome(_ctx: OutcomeContext) -> str:
        return "🔄 Resubmission started. Please re-enter the session results in the submission channel."

    return ChangeType(
        kind=KIND,
        opening=(),
        plan=_plan,
        steps={
            MARK: Step(MARK, StepKind.SAVE, mark, describe=describe_mark),
            DELETE_PROMPT: dataclasses.replace(deletion, name=DELETE_PROMPT),
            DELETE_APPROVAL: dataclasses.replace(deletion, name=DELETE_APPROVAL),
            POST_ANNOUNCEMENT: announcement,
            FIRST_PROMPT: Step(FIRST_PROMPT, StepKind.SAVE, first_prompt, describe=describe_first_prompt),
            POST_PROMPT: post_prompt_step(),
        },
        check=_check,
        key=lambda payload: f"{KIND}:{payload['round_id']}",
        doing=lambda payload: f"Resubmitting round {payload['round_number']}'s results",
        outcome=outcome,
        lane=_lane,
        conversation_open=_conversation_open,
        writes=("round_submission_channels",),
    )


async def ask_resubmission_start(
    interaction: discord.Interaction, state: Any, *, queue: ChangeQueue, what: str
) -> None:
    """Ask the queue to start the resubmission of *state*'s round, as the member who pressed.

    Once the queue has accepted it the review's staged penalties and pardons and its approval
    message are forgotten, as the change discards them; a request the queue refuses at once
    leaves the review as it was.
    """
    payload = {
        "round_id": state.round_id,
        "division_id": state.division_id,
        "round_number": state.round_number,
        "division_name": state.division_name,
        "channel_id": state.submission_channel_id,
        "started_by": interaction.user.id,
        "prompt_message_id": state.prompt_message_id,
        "approval_message_id": state.approval_message_id,
        "discarded": [
            {
                "driver_user_id": penalty.driver_user_id,
                "session_type": penalty.session_type.value,
                "penalty_type": penalty.penalty_type,
                "penalty_seconds": penalty.penalty_seconds,
            }
            for penalty in state.staged
        ],
        # The justification is logged as it was staged: the log channel is the one place it
        # may appear.
        "discarded_pardons": [
            {
                "driver_user_id": pardon.driver_user_id,
                "pardon_type": pardon.pardon_type,
                "justification": pardon.justification,
            }
            for pardon in state.staged_pardons
        ],
    }
    asked = await queue.ask(
        KIND, payload, interaction=interaction,
        what=f"resubmitting round {state.round_number}'s results", refusal_what=what,
    )
    if asked is None:
        return
    state.staged.clear()
    state.staged_pardons.clear()
    state.approval_message_id = None
