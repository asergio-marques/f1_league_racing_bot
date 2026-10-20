"""`results.review.lock` and `.lock.clear`: a message typed in a locked review channel, on the
round's lane (#439, slice 6b, C57).

While a round's penalty review is open its submission channel is locked: the manager uses the
buttons, and anything typed there is deleted with a warning. `ResultsCog.on_message` asks
`results.review.lock` as the bot's, on the round's own lane (``channel:<submission channel>``), for
each such message. Its jobs delete the member's message (`delete_message`, the shared deletion: a
message already gone completes), post the warning (`post_warning`, mentioning the member alone and
carrying no `delete_after`), whose record writes the warning's row (`review_lock_warnings`), and
arm the timer that takes the warning down (`arm_clear`, a job of its own after the save of the
row: a timer is never armed inside a record).

Eight seconds on, the timer's callback (`lock_clear_timer`, registered by the builder) reads the
row again and asks `results.review.lock.clear`, again as the bot's and on the round's lane: its one
job, `delete_warning`, deletes the warning and its record deletes the row. With the row gone the
callback asks nothing. A refusal of either deletion, or of the warning, stops the round's lane
alone, to be tried again on the schedule or discarded; while it is stopped, what is typed in the
channel stands until it clears (results spec :377).

**Why a clear is its own change.** The warning's life is the timer's, not the lock's: a restart,
a stop or a Discard of the lock change must not leave a warning on the channel that nothing will
take down, so the row is what the timer reads, and the clear is asked afresh.

The lock change's payload is ``channel_id``, ``message_id`` (the typed message), ``author_id`` and
``display_name``. The clear's is ``channel_id`` and ``message_id`` (the warning).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import ChangeOrigin, PlannedStep, StepKind, StepResult, Verdict
from leaguebot.core.services.change_queue import (
    ChangeQueue,
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.results.services.review_posting import DELETE_MESSAGE, delete_message_step
from leaguebot.results.services.submission_steps import post_step

KIND = "results.review.lock"
CLEAR_KIND = "results.review.lock.clear"

#: The one-off timer that takes a warning down, keyed by the warning's message id.
TIMER = "review_lock_clear"

POST_WARNING = "post_warning"
ARM_CLEAR = "arm_clear"
DELETE_WARNING = "delete_warning"

#: How long the warning stands.
WARNING_LIFE = timedelta(seconds=8)

_WRITES = ("review_lock_warnings",)


def _warning_text(display_name: str) -> str:
    return (
        f"❌ **{display_name}** this channel is locked during penalty review."
        " Please use the buttons above."
    )


def _lane(payload: dict[str, Any]) -> str:
    return f"channel:{int(payload['channel_id'])}"


async def _in_review(db: aiosqlite.Connection, channel_id: int) -> bool:
    """Whether *channel_id* is a round's open submission channel in penalty review (the guard's
    rule: not closed, not being resubmitted, the round not final)."""
    row = await (
        await db.execute(
            """
            SELECT 1 FROM round_submission_channels rsc JOIN rounds r ON r.id = rsc.round_id
            WHERE rsc.channel_id = ? AND rsc.closed = 0 AND rsc.in_penalty_review = 1
              AND rsc.resubmitting = 0 AND r.status != 'FINAL'
            """,
            (channel_id,),
        )
    ).fetchone()
    return row is not None


async def _conversation_open(db: aiosqlite.Connection, payload: dict[str, Any]) -> bool:
    row = await (
        await db.execute(
            "SELECT 1 FROM round_submission_channels WHERE channel_id = ?",
            (int(payload["channel_id"]),),
        )
    ).fetchone()
    return row is not None


async def _check_in_review(ctx: CheckContext) -> Verdict:
    async with get_connection(ctx.db_path) as db:
        if await _in_review(db, int(ctx.payload["channel_id"])):
            return Verdict.go()
    return Verdict.not_due("the channel is no longer in penalty review")


async def _check_go(_ctx: CheckContext) -> Verdict:
    return Verdict.go()


async def _record_warning(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
    await db.execute(
        "INSERT OR REPLACE INTO review_lock_warnings (message_id, channel_id, due_at) "
        "VALUES (?, ?, ?)",
        (
            int(result.result["message_id"]),
            int(ctx.step_payload["channel_id"]),
            str(result.result["due_at"]),
        ),
    )


async def _record_warning_cleared(
    db: aiosqlite.Connection, ctx: StepContext, _result: StepResult
) -> None:
    await db.execute(
        "DELETE FROM review_lock_warnings WHERE message_id = ?", (int(ctx.step_payload["message_id"]),)
    )


def lock_change(*, now: Callable[[], datetime], arm_once: Callable[[str, int, datetime], None]) -> ChangeType:
    """The `results.review.lock` change type, for the builder to register.

    *now* is the queue's clock, which dates the warning's eight seconds; *arm_once* is the
    scheduler service's, handed here so that no job looks the scheduler up on the bot.
    """
    posting = post_step(
        POST_WARNING, describing="posting the warning that the review channel is locked",
        record=_record_warning,
    )

    async def post_warning(ctx: StepContext) -> StepResult:
        posted = await posting.run(ctx)
        due_at = now() + WARNING_LIFE
        message_id = int(posted.result["message_id"])
        return StepResult(
            result={"message_id": message_id, "due_at": due_at.isoformat()},
            then=(PlannedStep(ARM_CLEAR, {"message_id": message_id, "due_at": due_at.isoformat()}),),
        )

    async def arm_clear(ctx: StepContext) -> StepResult:
        payload = ctx.step_payload
        arm_once(TIMER, int(payload["message_id"]), datetime.fromisoformat(str(payload["due_at"])))
        return StepResult(result={"armed": True})

    async def describe_arm(_ctx: StepContext) -> str:
        return "arming the timer that takes the lock warning down"

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        channel_id = int(payload["channel_id"])
        return (
            PlannedStep(DELETE_MESSAGE, {
                "channel_id": channel_id, "message_id": int(payload["message_id"]),
                "what": "a message typed in a locked review channel",
            }),
            PlannedStep(POST_WARNING, {
                "channel_id": channel_id, "text": _warning_text(str(payload["display_name"])),
                "mention_user_ids": [int(payload["author_id"])],
            }),
        )

    steps = {
        DELETE_MESSAGE: delete_message_step(),
        POST_WARNING: Step(
            POST_WARNING, StepKind.ACT, post_warning, describe=posting.describe,
            record=_record_warning,
        ),
        ARM_CLEAR: Step(ARM_CLEAR, StepKind.ACT, arm_clear, describe=describe_arm),
    }
    return ChangeType(
        kind=KIND,
        opening=(),
        plan=plan,
        steps=steps,
        check=_check_in_review,
        key=lambda payload: f"{KIND}:{payload['message_id']}",
        doing=lambda _payload: "Locking a message typed in a review channel",
        outcome=lambda _ctx: "",
        repeatable=True,
        lane=_lane,
        conversation_open=_conversation_open,
        writes=_WRITES,
    )


def lock_clear_change() -> ChangeType:
    """The `results.review.lock.clear` change type, for the builder to register."""

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        return (
            PlannedStep(DELETE_WARNING, {
                "channel_id": int(payload["channel_id"]), "message_id": int(payload["message_id"]),
                "what": "the review channel's lock warning",
            }),
        )

    return ChangeType(
        kind=CLEAR_KIND,
        opening=(),
        plan=plan,
        steps={DELETE_WARNING: delete_message_step(DELETE_WARNING, record=_record_warning_cleared)},
        check=_check_go,
        key=lambda payload: f"{CLEAR_KIND}:{payload['message_id']}",
        doing=lambda _payload: "Taking down a review channel's lock warning",
        outcome=lambda _ctx: "",
        lane=_lane,
        writes=_WRITES,
    )


async def ask_lock(queue: ChangeQueue, message: Any) -> None:
    """Ask `results.review.lock` for *message*, a member's, typed in a locked review channel."""
    await queue.ask(
        KIND,
        {
            "channel_id": message.channel.id,
            "message_id": message.id,
            "author_id": message.author.id,
            "display_name": message.author.display_name,
        },
        what="a message typed in a locked review channel",
        origin=ChangeOrigin.BOT,
    )


def lock_clear_timer(
    db_path: str, queue_of: Callable[[], ChangeQueue]
) -> Callable[[int | str], Awaitable[None]]:
    """The callback the builder registers for the `review_lock_clear` timer.

    It is handed the warning's message id, reads the warning's row again and asks the clear as the
    bot's, or asks nothing where the row is gone. *queue_of* gives the queue when the timer fires,
    which a restart has rebuilt since the timer was armed.
    """

    async def fired(key: int | str) -> None:
        message_id = int(key)
        async with get_connection(db_path) as db:
            row = await (
                await db.execute(
                    "SELECT channel_id FROM review_lock_warnings WHERE message_id = ?",
                    (message_id,),
                )
            ).fetchone()
        if row is None:
            return
        await queue_of().ask(
            CLEAR_KIND,
            {"channel_id": int(row["channel_id"]), "message_id": message_id},
            what="taking down a review channel's lock warning",
            origin=ChangeOrigin.BOT,
        )

    return fired


__all__ = [
    "ARM_CLEAR", "CLEAR_KIND", "DELETE_WARNING", "KIND", "POST_WARNING", "TIMER", "ask_lock",
    "lock_change", "lock_clear_change", "lock_clear_timer",
]
