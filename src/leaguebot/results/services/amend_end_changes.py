"""`results.amend.cancel`: ending an amendment its manager cancelled, on the change queue (#439, 6b,
C62).

The **Cancel Amendment** button asks the queue for this change as the member who pressed it,
acknowledged privately as under way. It is the league's, whole (a command's jobs go on one lane):
the amendment is not one conversation's to end from within, since it writes the round, so its
channel's deletion stops the league's lane where Discord refuses it, and not the amendment's.

**The check** is judged at the press and again when the change comes up to run. A closed or gone
amendment is too late to cancel; an amendment whose stage one is being recorded, or a stage of
which is being approved, on the queue (`stage_in_hand`, stopped included) is left alone until that
is done (results specification :491, "Leave it while stuck").

**The jobs.** `names` resolves the standings names a full tie is settled by, outside any save;
`revert` is one save: the round put back from the amendment's snapshot with its standings
(`revert_abandoned_amendment_on`, a no-op where stage one never wrote), the drafts dropped, the
amendment's row closed (which ends the conversation, so a paste still waiting on its lane is found
no longer due) and the cancel written in the standard form; and `delete_channel` takes the channel
down. The row stays, closed, as it does for every amendment that ended: restart recovery and the next
amendment of the round find it so, and the channel's deletion does not forget it.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import datetime
from typing import Any

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import PlannedStep, StepKind, StepResult, Verdict
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.utils.log_lines import abandoned_line
from leaguebot.results.services import result_submission_service as submissions
from leaguebot.results.services.amend_changes import COMMAND
from leaguebot.results.services.amend_views import CANCEL_KIND
from leaguebot.results.services.review_posting import (
    DELETE_CHANNEL,
    NAMES,
    display_names,
    posting_steps,
)

KIND = CANCEL_KIND
REVERT = "revert"

#: Why a cancel of an amendment that has ended is refused.
TOO_LATE = (
    "ℹ️ Too late to cancel — the amendment is already being committed, or is no longer open."
)
#: Why a cancel is refused while a stage of the amendment is on the queue.
STAGE_IN_HAND = (
    "⏳ A stage of this amendment is being approved, so it cannot be cancelled now. If its "
    "approval is stopped, press Retry or Discard on its notice in the log channel."
)

__all__ = ["KIND", "REVERT", "STAGE_IN_HAND", "TOO_LATE", "amend_cancel_change"]


def amend_cancel_change(*, now: Callable[[], datetime]) -> ChangeType:
    """The `results.amend.cancel` change type, for the builder to register.

    *now* is the queue's clock, read when the amendment's row is closed. The payload is
    ``amend_id``, ``round_id``, ``division_id`` and ``channel_id`` of the amendment.
    """

    async def check(ctx: CheckContext) -> Verdict:
        async with get_connection(ctx.db_path) as db:
            row = await (
                await db.execute(
                    "SELECT round_id, closed_at FROM round_amend_channels WHERE id = ?",
                    (int(ctx.payload["amend_id"]),),
                )
            ).fetchone()
        if row is None or row["closed_at"] is not None:
            return Verdict.refuse(TOO_LATE)
        if await submissions.stage_in_hand(ctx.db_path, int(row["round_id"])):
            return Verdict.refuse(STAGE_IN_HAND)
        return Verdict.go()

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        return (
            PlannedStep(NAMES, {"division_id": int(payload["division_id"])}),
            PlannedStep(REVERT),
        )

    async def revert(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.payload
        amend_id, round_id = int(payload["amend_id"]), int(payload["round_id"])
        division_id, channel_id = int(payload["division_id"]), int(payload["channel_id"])
        row = await (
            await db.execute(
                "SELECT r.round_number, d.name AS division_name "
                "FROM rounds r JOIN divisions d ON d.id = r.division_id WHERE r.id = ?",
                (round_id,),
            )
        ).fetchone()
        assert row is not None  # the check found the amendment standing
        reverted = await submissions.revert_abandoned_amendment_on(
            db, round_id, display_names(ctx, division_id)
        )
        await db.execute("DELETE FROM amend_drafts WHERE amend_id = ?", (amend_id,))
        await db.execute(
            "UPDATE round_amend_channels SET closed_at = ? WHERE id = ? AND closed_at IS NULL",
            (now().isoformat(), amend_id),
        )
        what = f"`/{COMMAND}` of round {row['round_number']} ({row['division_name']})"
        detail = (
            submissions.ROUND_PUT_BACK if reverted
            else "Nothing was written. " + submissions.AMENDMENT_RE_RUN
        )
        return StepResult(
            result={"reverted": reverted},
            lines=(abandoned_line(ctx.named, what, lapsed=False, detail=detail),),
            then=(PlannedStep(DELETE_CHANNEL, {
                "channel_id": channel_id, "round_id": round_id,
                "what": "amendment channel", "reason": "Amendment cancelled",
            }),),
        )

    async def describe_revert(_ctx: StepContext) -> str:
        return "putting the amended round back"

    def outcome(ctx: OutcomeContext) -> str:
        lines: list[str] = []
        for view in ctx.steps:
            result = view.result or {}
            if view.name == REVERT:
                if "discarded" in result:
                    return (
                        "⚠️ The amendment was not cancelled, and nothing was put back: press "
                        "Cancel Amendment again."
                    )
                if view.done:
                    lines.append(
                        "↩️ Amendment cancelled. "
                        + (submissions.ROUND_PUT_BACK if result.get("reverted")
                           else "Nothing was written.")
                    )
            elif view.name == DELETE_CHANNEL and "discarded" in result:
                lines.append(
                    f"⚠️ The amendment channel <#{view.payload.get('channel_id')}> was not "
                    "deleted: delete it by hand."
                )
        return "\n".join(lines)

    return ChangeType(
        kind=KIND,
        opening=(),
        plan=plan,
        steps={
            NAMES: posting_steps()[NAMES],
            REVERT: Step(REVERT, StepKind.SAVE, revert, describe=describe_revert),
            DELETE_CHANNEL: dataclasses.replace(posting_steps()[DELETE_CHANNEL], record=None),
        },
        check=check,
        key=lambda payload: f"{KIND}:{payload['amend_id']}",
        doing=lambda _payload: "Cancelling an amendment of a round's results",
        outcome=outcome,
    )
