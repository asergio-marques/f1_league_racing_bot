"""Approving a round's reports, carried out on the change queue (#439, slice 2).

`results.reports.approve` is stage one of a round's penalty review: the manager's staged penalties
and pardons are applied, the round's tables are posted again under "Post-Race Penalty Results",
each penalty is announced, and the round moves on to its appeals. It used to run inside the button
that asked for it, committing five to fifteen times, posting between the commits and keeping what
it was doing in memory; a stop part-way left penalties applied and never announced, attendance
never recorded, or penalties marked applied that never were. It is now a list of jobs the queue
saves and resumes:

1. **`names`**, which resolves the drivers' display names from Discord, so that the save after it
   awaits nothing but its connection.
2. **`apply`**, **one save for everything the approval writes**: the penalties and their records,
   the points (a session that cannot be scored **raises**, where it used to be logged to the host
   and the round published with its old points), the round's attendance through the hook (so a
   fault in it fails the whole approval), the standings snapshots of this round and every later
   one, and the round's status. A failure rolls the lot back: nothing is changed and the review is
   still open. It plans every job after it.
3. **`post_batch_notice`**, the posting jobs under "Post-Race Penalty Results" for this round and
   the standings of every later round, and **`delete_batch_notice`** (`review_posting`).
4. **`delete_message`** for the review's prompt and its approval message, once the new posts stand.
5. **The verdicts** (`review_verdicts`): the heading, then one `announce_verdict` for each penalty.
6. **`post_appeals_prompt`**, opening the appeals stage, its `record` saving the prompt's id.
7. **The attendance sheet and the sanctions** (`review_verdicts`), read **at the latest round the
   cascade reached**, not the round approved: each round's stored total is the driver's total as
   at that round, so approving round 3 while round 4 is already final leaves the division's
   current standing on round 4, and it is the current standing a sheet must show and a threshold
   must be read from (decided with #238).
8. **`close`**, **an opening job**, so that it runs last whatever was discarded before it. It
   writes `PENALTY_REVIEW_APPROVED | Success`, or `| Incomplete` naming every job a league admin
   discarded. Where `names` or `apply` was discarded nothing was changed, and it asks for
   `results.review.open` as the bot, so that a fresh prompt replaces the dead one and the manager
   may press Approve again at once.

**The check** is the one every control of the review asks (`review_stage_refusal`: the round awaits
its report verdicts, no resubmission is collecting, the prompt is the current one), that no
amendment holds the division, and that no other approval of the round is in hand (`unfinished`,
a stopped one included), which is refused with the wording the review's controls use.

The payload is ``round_id``, ``division_id``, ``staged`` and ``pardons`` (each as plain data,
`to_payload`), ``prompt_message_id`` and ``approval_message_id``. What is staged travels in it, so a
stop loses none of it once it has been asked for. The time every writer stamps is the queue's
clock, handed in, never `datetime.now`.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

import aiosqlite

from leaguebot.core.models.change import (
    FollowOn,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.round import RoundStatus
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
    unfinished,
)
from leaguebot.core.services.season_service import set_round_status_on
from leaguebot.results.services import review_posting, review_verdicts
from leaguebot.results.services.attendance_hook import AttendanceAfterReview
from leaguebot.results.services.penalty_service import StagedPenalty, apply_penalties_on
from leaguebot.results.services.penalty_wizard import (
    _BEING_APPROVED,
    StagedPardon,
    review_stage_refusal,
)
from leaguebot.results.services.result_submission_service import (
    _recompute_session_points_on,
    _snapshot_staged_drivers_on,
    held_by_amendment,
)
from leaguebot.results.services.review_posting import (
    DELETE_MESSAGE,
    NAMES,
    display_names,
    plan_posts,
    posting_steps,
)
from leaguebot.results.services.standings_service import cascade_recompute_from_round_on

__all__ = ["KIND", "report_approval_change"]

KIND = "results.reports.approve"
REVIEW_OPEN = "results.review.open"

_APPLY = "apply"
_CLOSE = "close"


def _discarded(result: dict[str, Any] | None) -> bool:
    return "discarded" in (result or {})


async def _latest_round(db: aiosqlite.Connection, round_id: int, division_id: int) -> int:
    """The last round the attendance cascade from *round_id* reaches: the division's latest round
    after it that is awaiting appeals or final, or *round_id* where there is none."""
    row = await (
        await db.execute(
            "SELECT id FROM rounds WHERE division_id = ? "
            "AND status IN ('AWAITING_APPEAL_VERDICTS', 'FINAL') "
            "AND round_number > (SELECT round_number FROM rounds WHERE id = ?) "
            "ORDER BY round_number DESC LIMIT 1",
            (division_id, round_id),
        )
    ).fetchone()
    return round_id if row is None else int(row["id"])


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def report_approval_change(
    *, attendance: AttendanceAfterReview, now: Callable[[], datetime]
) -> ChangeType:
    """The change that approves a round's reports; see the module. *attendance* is the hook the
    builder hands every change type of the review, and *now* the queue's clock."""

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        round_id = int(payload["round_id"])
        in_hand = await unfinished(ctx.db_path, [KIND], excluding=ctx.change_id)
        if any(p.get("round_id") == round_id for p in in_hand):
            return Verdict.refuse(_BEING_APPROVED, "The round's reports are being approved.")
        refusal = await review_stage_refusal(
            ctx.db_path, round_id, payload.get("prompt_message_id")
        )
        if refusal is None:
            refusal = await held_by_amendment(
                ctx.db_path, round_id, int(payload["division_id"]),
                then="Approve the reports again then.",
            )
        if refusal is not None:
            return Verdict.refuse(refusal)
        return Verdict.go()

    async def names_not_discarded(ctx: StepContext) -> bool:
        """The save is due only where the display names it reads were resolved: a discarded
        `names` leaves nothing changed, and the review is opened again."""
        return not any(view.name == NAMES and _discarded(view.result) for view in ctx.steps)

    async def apply(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.payload
        round_id, division_id = int(payload["round_id"]), int(payload["division_id"])
        staged = [StagedPenalty.from_payload(p) for p in payload["staged"]]
        pardons = [StagedPardon.from_payload(p) for p in payload["pardons"]]
        when = now()
        actor_id = ctx.actor_id or 0

        row = await (
            await db.execute(
                "SELECT r.round_number, d.name AS division_name, rsc.channel_id "
                "FROM rounds r JOIN divisions d ON d.id = r.division_id "
                "LEFT JOIN round_submission_channels rsc ON rsc.round_id = r.id "
                "WHERE r.id = ?",
                (round_id,),
            )
        ).fetchone()
        if row is None or row["channel_id"] is None:
            raise LookupError(f"round {round_id} has no submission channel to approve in")
        round_number, division_name = int(row["round_number"]), str(row["division_name"])

        before = await _snapshot_staged_drivers_on(db, round_id, division_id, staged)
        records: list[dict[str, Any]] = []
        if staged:
            records = await apply_penalties_on(
                db, round_id, division_id, staged, actor_id, now=when
            )
            # Raises where a session cannot be scored: the save is rolled back whole.
            await _recompute_session_points_on(db, round_id)
        # The round's attendance, the pardons and the points carried forward are the hook's, in
        # this save: a fault in them fails the approval whole ("Whole approval fails").
        await attendance.record_on(db, round_id, division_id, pardons, when)
        await cascade_recompute_from_round_on(db, division_id, round_id, display_names(ctx))
        # Report verdicts are in; the round now waits on appeals. Guarded against a round that
        # has ended (#167): a settled round is never dragged back into an awaiting state.
        if not await set_round_status_on(db, round_id, RoundStatus.AWAITING_APPEAL_VERDICTS):
            raise RuntimeError(f"round {round_id} has ended, so its reports cannot be approved")
        after = await _snapshot_staged_drivers_on(db, round_id, division_id, staged)
        latest = await _latest_round(db, round_id, division_id)

        penalty_log = [
            {k: v for k, v in p.to_payload().items()
             if k not in ("decided_by", "decided_at")}
            for p in staged
        ]
        old_val = json.dumps(
            {"status": RoundStatus.AWAITING_REPORT_VERDICTS.value, "affected_drivers": before}
        )
        new_val = json.dumps({
            "status": RoundStatus.AWAITING_APPEAL_VERDICTS.value,
            "affected_drivers": after,
            "penalties": penalty_log,
            "actor_id": ctx.actor_id,
        })
        body = (
            f"  round: {round_number} ({division_name})\n"
            + (f"  penalties: {len(staged)}\n" if staged else "  penalties: none\n")
            + f"  old={old_val}\n  new={new_val}"
        )

        channel_id = int(row["channel_id"])
        then: list[PlannedStep] = []
        then.extend(
            await plan_posts(
                db, round_id, label="Post-Race Penalty Results", later_rounds=True, notice=True
            )
        )
        if payload.get("prompt_message_id") is not None:
            then.append(PlannedStep(DELETE_MESSAGE, {
                "channel_id": channel_id, "message_id": int(payload["prompt_message_id"]),
                "what": "penalty review prompt",
            }))
        if payload.get("approval_message_id") is not None:
            then.append(PlannedStep(DELETE_MESSAGE, {
                "channel_id": channel_id, "message_id": int(payload["approval_message_id"]),
                "what": "penalty approval message",
            }))
        then.extend(
            review_verdicts.plan_verdicts(round_id, round_number, "penalty_records", records)
        )
        then.append(PlannedStep(
            review_verdicts.POST_APPEALS_PROMPT,
            {"round_id": round_id, "division_id": division_id},
        ))
        then.extend(review_verdicts.plan_attendance(latest, division_id, division_name))
        return StepResult(
            result={
                "round_number": round_number, "division_name": division_name,
                "penalties": len(staged), "pardons": len(pardons), "body": body,
            },
            then=tuple(then),
        )

    async def close(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        applied = next((view for view in ctx.steps if view.name == _APPLY), None)
        unapplied = (
            applied is None or _discarded(applied.result) or bool((applied.result or {}).get("dropped"))
        )
        if unapplied:
            # Nothing was changed and the review stands, its prompt dead beside nothing: ask for
            # it to open again, as the bot, so that a fresh prompt replaces the dead one.
            row = await (
                await db.execute(
                    "SELECT results_posted FROM round_submission_channels WHERE round_id = ?",
                    (int(ctx.payload["round_id"]),),
                )
            ).fetchone()
            reopen = FollowOn(
                REVIEW_OPEN,
                {
                    "round_id": int(ctx.payload["round_id"]),
                    "label": "Provisional Results",
                    "publish": not (row is not None and row["results_posted"]),
                    "old_prompt_id": ctx.payload.get("prompt_message_id"),
                },
                what="the penalty review, opened again after its approval was discarded",
            )
            return StepResult(result={"closed": True, "reopened": True}, follow_ons=(reopen,))
        assert applied is not None
        left = [*review_posting.not_done(ctx), *review_verdicts.not_done(ctx)]
        outcome = "Incomplete" if left else "Success"
        line = (
            f"{ctx.named} | PENALTY_REVIEW_APPROVED | {outcome}\n"
            + str((applied.result or {}).get("body", ""))
            + "".join(f"\n  {text}" for text in left)
        )
        return StepResult(result={"closed": True}, lines=(line,))

    async def describe_apply(_ctx: StepContext) -> str:
        return "approving the round's reports"

    async def describe_close(_ctx: StepContext) -> str:
        return "recording that the round's reports are approved"

    def outcome(ctx: OutcomeContext) -> str:
        views = {view.name: view for view in ctx.steps}
        applied = views.get(_APPLY)
        if applied is None or _discarded(applied.result) or (applied.result or {}).get("dropped"):
            return (
                "Nothing was changed. The penalty review is posted again: press Approve again "
                "on it."
            )
        result = applied.result or {}
        penalties, pardons = int(result.get("penalties", 0)), int(result.get("pardons", 0))
        done: list[str] = []
        if penalties:
            done.append(f"{_plural(penalties, 'penalty', 'penalties')} applied")
        if pardons:
            done.append(f"{_plural(pardons, 'pardon', 'pardons')} granted")
        what = " and ".join(done) if done else "nothing was staged"
        reply = (
            f"✅ Round {result.get('round_number', '?')}'s reports are approved "
            f"({result.get('division_name', '?')}): {what}."
        )
        prompt = views.get(review_verdicts.POST_APPEALS_PROMPT)
        if prompt is None or not _discarded(prompt.result):
            reply += " Its appeals review is posted below."
        left = [*review_posting.not_done(ctx), *review_verdicts.not_done(ctx)]
        return "\n".join([reply, *left])

    steps: dict[str, Step] = {
        **posting_steps(),
        **review_verdicts.verdict_steps(now, attendance),
        _APPLY: Step(
            _APPLY, StepKind.SAVE, apply, still_due=names_not_discarded, describe=describe_apply,
        ),
        _CLOSE: Step(_CLOSE, StepKind.SAVE, close, describe=describe_close),
    }
    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(NAMES), PlannedStep(_APPLY), PlannedStep(_CLOSE)),
        steps=steps,
        check=check,
        key=lambda payload: f"{KIND}:{payload['round_id']}",
        doing=lambda _payload: "Approving the round's reports",
        outcome=outcome,
    )
