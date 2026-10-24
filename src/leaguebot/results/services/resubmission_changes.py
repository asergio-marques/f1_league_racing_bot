"""`results.resubmission.start`: a round's results resubmitted, started on the change queue
(#439, 6b, C51).

A league manager's press of **🔄 Resubmit Initial Results** on the penalty review's prompt asks
this change with the press's interaction, answered privately as under way. It runs on the round's
own lane (``channel:<submission channel>``), the conversation it begins. Its jobs, in order:

1. **`mark`** flags the round's submission channel `resubmitting` and records who started it (a
   restart reads both), and writes the one line the log channel keeps of the press, naming the
   penalties and pardons the press discards: once they are gone it is the only record they were
   staged. The line is queued in the save, so a log channel that refuses it holds nothing up.
   **It cannot be discarded** (#439, slice 8): dropped, the resubmission would run unrecorded, its
   pastes refused and its review left with no prompt until the bot restarts, so a Discard on it is
   refused (a change stopped at its check on it included) and only a Retry moves it.
2. **`delete_prompt`** and **`delete_approval`** take the review's prompt and any approval message
   down, so that nobody can approve the results being replaced or start a second resubmission
   (#402); a message already gone completes the job.
3. **`post_announcement`** posts the announcement with its Cancel button and saves its id with the
   job's mark (a restart takes the button down by it).
4. **`first_prompt`** reads the first session the resubmission expects and plans the
   **`post_prompt`** that asks for it.

**The pastes that follow** are `results.resubmission.turn`s on the same lane
(`submission_turn_change`), each held as a draft. The last hands **`results.resubmission.swap`**
to the league lane, which replaces the round's results with the drafts, deletes them, and asks the
review again, naming the announcement so that its Cancel button comes down with the prompt's
posting; with every session cancelled it asks the round's cancelled sessions posted and the
channel closed instead. **Cancel** asks the review back as cancelled (`ask_resubmission_cancel`).

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
from leaguebot.core.models.change import FollowOn, PlannedStep, StepKind, StepResult, Verdict
from leaguebot.core.services.change_queue import (
    ChangeQueue,
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services import season_service
from leaguebot.results.services import result_submission_service as submissions
from leaguebot.results.services import review_open_change
from leaguebot.results.services.review_posting import DELETE_MESSAGE, posting_steps
from leaguebot.results.services.submission_changes import ALL_CANCELLED_KIND
from leaguebot.results.services.submission_turn_change import SWAP_KIND
from leaguebot.results.services.submission_steps import (
    POST_PROMPT,
    post_choice_step,
    post_prompt_step,
)

KIND = "results.resubmission.start"
SWAP = "swap"
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

__all__ = [
    "KIND", "ask_resubmission_cancel", "ask_resubmission_start",
    "resubmission_start_change", "resubmission_swap_change",
]


@dataclass(frozen=True)
class ReviewRef:
    """What the Cancel button needs of the review it belongs to, to refuse a press that is not a
    league manager's and to name the review in the log: the penalty review's state is in memory
    and not the announcement's to hold."""

    bot: Any
    db_path: str
    round_id: int
    round_number: int
    division_name: str
    queue: ChangeQueue


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
        "channel_id": channel_id, "text": ANNOUNCEMENT, "round_id": int(payload["round_id"]),
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


def resubmission_start_change(*, queue: ChangeQueue) -> ChangeType:
    """The `results.resubmission.start` change type, for the builder to register; *queue* is
    handed to the Cancel button it posts, which asks the review back of it.

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
        # A resubmission starts from nothing, whatever an earlier attempt left.
        await db.execute(
            "DELETE FROM resubmission_drafts WHERE round_id = ?", (int(payload["round_id"]),)
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

    async def would_run_unrecorded(ctx: StepContext) -> str:
        called = await season_service.round_called(ctx.db_path, int(ctx.payload["round_id"]))
        return (
            f"{called}'s resubmission would run unrecorded: its pastes would be refused and its "
            "review left with no prompt until the bot restarts"
        )

    async def describe_mark(_ctx: StepContext) -> str:
        return "flagging the round's submission as being resubmitted"

    async def describe_announcement(_ctx: StepContext) -> str:
        return "posting the resubmission's announcement"

    async def announcement_view(ctx: StepContext) -> discord.ui.View:
        payload = ctx.step_payload
        return submissions.ResubmissionCancelView(
            ReviewRef(
                ctx.bot, ctx.db_path, int(payload["round_id"]), int(payload["round_number"]),
                str(payload["division_name"]), queue,
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
            MARK: Step(
                MARK, StepKind.SAVE, mark, describe=describe_mark,
                undiscardable=would_run_unrecorded,
            ),
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
        writes=("round_submission_channels", "resubmission_drafts"),
    )


def resubmission_swap_change() -> ChangeType:
    """The `results.resubmission.swap` change type, for the builder to register, on the league
    lane: the last resubmitted paste, handed over by the round's lane.

    The payload is ``round_id``, ``channel_id``, ``division_id``, ``season_id``, ``round_number``,
    ``division_name``, ``cancel_message_id`` (the announcement, None where unrecorded),
    ``all_cancelled`` and the last paste's ``mark``. Its one job, ``swap``, is one save: the
    round's results replaced by the drafts (`replace_round_results_on`, which clears the
    resubmission's flag), the drafts deleted, and the next request asked in the same save. With a
    session held the review is asked again, publishing the new results as "Provisional Results
    (amended)"; with every session cancelled the cancelled sessions are asked posted and the
    channel closed (`results.submission.all_cancelled`), told that it follows a resubmission, so
    that the round's standings are withdrawn with it, and the tables of the earlier sessions the
    resubmission does not enter, which the new rows could not carry, are taken down.
    """

    async def check(ctx: CheckContext) -> Verdict:
        async with get_connection(ctx.db_path) as db:
            row = await (
                await db.execute(
                    "SELECT resubmitting FROM round_submission_channels WHERE round_id = ?",
                    (int(ctx.payload["round_id"]),),
                )
            ).fetchone()
        if row is None or not row["resubmitting"]:
            return Verdict.not_due("the resubmission has ended")
        return Verdict.go()

    async def swap(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.payload
        round_id = int(payload["round_id"])
        held = await submissions.resubmission_drafts_on(db, round_id)
        unplaced = await submissions.replace_round_results_on(
            db, round_id, int(payload["division_id"]), int(payload["season_id"]), held
        )
        await db.execute("DELETE FROM resubmission_drafts WHERE round_id = ?", (round_id,))
        number = payload["round_number"]
        if payload["all_cancelled"]:
            order = [session.session_type for session in held]
            entered = await (
                await db.execute(
                    "SELECT id, session_type FROM session_results WHERE round_id = ?", (round_id,)
                )
            ).fetchall()
            sessions = sorted(
                ({"session_id": int(each["id"]), "session_type": each["session_type"]}
                 for each in entered),
                key=lambda each: [value.value for value in order].index(each["session_type"]),
            )
            follow_on = FollowOn(
                ALL_CANCELLED_KIND,
                {
                    "round_id": round_id, "channel_id": int(payload["channel_id"]),
                    "round_number": number, "sessions": sessions,
                    "resubmission": True, "old_tables": unplaced,
                },
                f"posting round {number}'s cancelled sessions",
            )
        else:
            asked: dict[str, Any] = {
                "round_id": round_id,
                "round_number": number,
                "division_name": payload["division_name"],
                "label": "Provisional Results (amended)",
                "publish": True,
                "cancel_message_id": payload.get("cancel_message_id"),
            }
            if payload.get("mark") is not None:
                asked["mark"] = payload["mark"]
            follow_on = FollowOn(
                review_open_change.KIND, asked, f"the penalty review of round {number}"
            )
        return StepResult(result={"swapped": True}, follow_ons=(follow_on,))

    async def describe_swap(_ctx: StepContext) -> str:
        return "replacing the round's results with the resubmission"

    return ChangeType(
        kind=SWAP_KIND,
        opening=(PlannedStep(SWAP),),
        steps={SWAP: Step(SWAP, StepKind.SAVE, swap, describe=describe_swap)},
        check=check,
        key=lambda payload: f"{SWAP_KIND}:{payload['round_id']}",
        doing=lambda payload: f"Replacing round {payload['round_number']}'s results",
        outcome=lambda _ctx: "",
        turn_channel=lambda payload: int(payload["channel_id"]),
        writes=(
            "session_results", "qualifying_session_results", "race_session_results",
            "round_submission_channels", "resubmission_drafts",
        ),
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


async def ask_resubmission_cancel(
    interaction: discord.Interaction, ref: ReviewRef, *, what: str
) -> bool:
    """Ask the queue for round *ref*'s penalty review back, the resubmission cancelled, as the
    member who pressed **Cancel**; whether it was asked.

    The round's results were never touched and are already posted, so nothing is published; the
    review's jobs clear the resubmission's flag, take the Cancel button off the announcement
    (named here by the id the start saved) and put the prompt back (`review_open_change`).
    """
    async with get_connection(ref.db_path) as db:
        row = await (
            await db.execute(
                "SELECT resubmit_prompt_message_id FROM round_submission_channels "
                "WHERE round_id = ?",
                (ref.round_id,),
            )
        ).fetchone()
    announcement = None if row is None else row["resubmit_prompt_message_id"]
    if announcement is None and interaction.message is not None:
        announcement = interaction.message.id
    asked = await ref.queue.ask(
        review_open_change.KIND,
        {
            "round_id": ref.round_id,
            "round_number": ref.round_number,
            "division_name": ref.division_name,
            "publish": False,
            "returning": "cancelled",
            "cancel_message_id": announcement,
            "while_resubmitting": True,
        },
        interaction=interaction,
        what=f"cancelling round {ref.round_number}'s resubmission",
        refusal_what=what,
    )
    return asked is not None
