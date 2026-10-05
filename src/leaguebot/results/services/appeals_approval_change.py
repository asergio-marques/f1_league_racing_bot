"""Approving a round's appeals, carried out on the change queue (#439, slice 2).

`results.appeals.approve` is stage two of a round's penalty review: the corrections the manager
staged are applied, the round becomes final, its tables are posted again under "Final Results",
each correction is announced, and the submission channel is deleted. It used to run inside the
button that asked for it, committing several times, posting between the commits and keeping what
it was doing in memory: a stop part-way left a final round with a dead review that was posted
again at every restart and appeals never announced, and a fresh prompt after a restart forgot
what had been applied, so the same corrections could be staged and applied twice. It is now a list
of jobs the queue saves and resumes:

1. **`names`**, which resolves the drivers' display names from Discord, so that the save after it
   awaits nothing but its connection.
2. **`apply`**, **one save for everything the approval writes**: the corrections and the appeal
   records, the points (a session that cannot be scored **raises**), the standings snapshots from
   this round on, the round made final with its former drivers marked, the division's status
   refreshed, and the round's submission row closed with the amendment record's `closed_at`. The
   round is final and its row closed in the same save as the corrections, so a stop after it
   leaves no review to restore. Where the division is now done it asks for the season's wind-down
   as a change of its own. A failure rolls the lot back: nothing is changed and the review is
   still open. It plans every job after it.
3. **`post_batch_notice`**, the posting jobs under "Final Results" for this round and the standings
   of every later round, and **`delete_batch_notice`** (`review_posting`).
4. **The verdicts** (`review_verdicts`): the heading, then one `announce_verdict` for each
   correction.
5. **`delete_message`** for the appeals prompt, then **`delete_channel`** for the submission
   channel, last, since every job above posts or deletes in it.
6. **`close`**, **an opening job**, so that it runs last whatever was discarded before it. It
   writes `APPEALS_REVIEW_APPROVED | Success`, or `| Incomplete` naming every job a league admin
   discarded.

**The check** is that no other approval of the round's appeals is in hand (`unfinished`, a stopped
one included), refused with the wording the review's controls use, that the round still awaits its
appeal verdicts and the prompt is the current one (`appeals_stage_refusal`), and that no amendment
holds the division. Once the first approval is done the round's status refuses any later one, so
the corrections are applied once.

The payload is ``round_id``, ``division_id``, ``staged`` (the corrections, each as plain data,
`to_payload`) and ``appeals_prompt_message_id``, with ``round_number`` and ``division_name`` where
the review that asks knows them, which name the round in the acknowledgement. What is staged
travels in it, so a stop loses none of it once it has been asked for. The time every writer stamps
is the queue's clock, handed in, never `datetime.now`.
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
from leaguebot.core.services.driver_service import current_account_map_for_division
from leaguebot.core.services import season_lifecycle_service, season_service
from leaguebot.core.services.season_service import set_round_status_on
from leaguebot.results.services import review_posting, review_verdicts
from leaguebot.results.services.attendance_hook import AttendanceAfterReview
from leaguebot.results.services.penalty_service import StagedPenalty
from leaguebot.results.services.penalty_wizard import (
    _APPEALS_BEING_APPROVED,
    _pen_label,
    appeals_stage_refusal,
)
from leaguebot.results.services.result_submission_service import (
    _apply_staged_appeals_on,
    _snapshot_staged_drivers_on,
    held_by_amendment,
    recompute_former_drivers_for_round,
)
from leaguebot.results.services.review_posting import (
    DELETE_CHANNEL,
    DELETE_MESSAGE,
    NAMES,
    display_names,
    plan_posts,
    posting_steps,
)
from leaguebot.results.services.standings_service import cascade_recompute_from_round_on

__all__ = ["KIND", "appeals_approval_change"]

KIND = "results.appeals.approve"
APPEALS_OPEN = "results.appeals.open"

_APPLY = "apply"
_CLOSE = "close"


def _discarded(result: dict[str, Any] | None) -> bool:
    return "discarded" in (result or {})


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def appeals_approval_change(
    *, attendance: AttendanceAfterReview, now: Callable[[], datetime]
) -> ChangeType:
    """The change that approves a round's appeals; see the module. *attendance* is the hook the
    builder hands every change type of the review (the verdict jobs share the sanction jobs it
    serves, which an appeals approval never plans), and *now* the queue's clock."""

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        round_id = int(payload["round_id"])
        in_hand = await unfinished(ctx.db_path, [KIND], excluding=ctx.change_id)
        if any(p.get("round_id") == round_id for p in in_hand):
            return Verdict.refuse(_APPEALS_BEING_APPROVED, "The round's appeals are being approved.")
        # The round must still be AWAITING_APPEAL_VERDICTS, which the first approval's save
        # ends, so a round already made final is never approved twice (defect 1).
        refusal = await appeals_stage_refusal(
            ctx.db_path, round_id, payload.get("appeals_prompt_message_id")
        )
        if refusal is None:
            refusal = await held_by_amendment(
                ctx.db_path, round_id, int(payload["division_id"]),
                then="Approve the appeals again then.",
            )
        if refusal is not None:
            return Verdict.refuse(refusal)
        return Verdict.go()

    async def names_not_discarded(ctx: StepContext) -> bool:
        """The save is due only where the display names it reads were resolved: a discarded
        `names` leaves nothing changed."""
        return not any(view.name == NAMES and _discarded(view.result) for view in ctx.steps)

    async def apply(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.payload
        round_id, division_id = int(payload["round_id"]), int(payload["division_id"])
        staged = [StagedPenalty.from_payload(p) for p in payload["staged"]]
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
        channel_id = int(row["channel_id"])

        before = await _snapshot_staged_drivers_on(db, round_id, division_id, staged)
        # The corrections, the points they move (raising where a session cannot be scored: the
        # save is rolled back whole) and one appeal record for each.
        records = await _apply_staged_appeals_on(
            db, round_id, division_id, staged, actor_id, now=when
        )
        await cascade_recompute_from_round_on(db, division_id, round_id, display_names(ctx))
        # Appeal verdicts are in; the results stand. Guarded against a round that has ended
        # (#167): a settled round is never raised to FINAL by a view that outlived its
        # cancellation, and a round already final is never moved on twice (#345).
        if not await set_round_status_on(db, round_id, RoundStatus.FINAL):
            raise RuntimeError(f"round {round_id} has ended, so its appeals cannot be approved")
        # The round's results are final as of the line above, so this is the moment its drivers
        # become former drivers (#216), in the same save: a round that is FINAL with nobody
        # marked is a season-end deletion of a profile its results point at.
        await recompute_former_drivers_for_round(db, round_id)
        # Approving the last round's appeals is what ends a division, and a division ending is
        # what lets `/season complete` run (#154).
        division_done = await season_service.refresh_division_status_on(db, division_id)
        await db.execute(
            "UPDATE round_submission_channels SET closed = 1 WHERE round_id = ?", (round_id,)
        )
        await db.execute(
            "UPDATE round_amend_channels SET closed_at = ? WHERE round_id = ? AND channel_id = ?",
            (when.isoformat(), round_id, channel_id),
        )
        after = await _snapshot_staged_drivers_on(db, round_id, division_id, staged)

        current_of = await current_account_map_for_division(db, division_id)
        applied = ", ".join(
            f"{_pen_label(sp)} for <@{current_of.get(sp.driver_user_id, sp.driver_user_id)}> "
            f"in {sp.session_type.value}"
            for sp in staged
        )
        old_val = json.dumps({
            "status": RoundStatus.AWAITING_APPEAL_VERDICTS.value, "affected_drivers": before,
        })
        new_val = json.dumps({
            "status": RoundStatus.FINAL.value,
            "affected_drivers": after,
            "actor_id": ctx.actor_id,
            "corrections": len(staged),
        })
        body = (
            f"  round: {round_number} ({division_name})\n"
            + (f"  corrections: {len(staged)}\n" if staged else "  corrections: none\n")
            + (f"  applied: {applied}\n" if staged else "")
            + f"  old={old_val}\n  new={new_val}"
        )

        then: list[PlannedStep] = []
        then.extend(
            await plan_posts(
                db, round_id, label="Final Results", later_rounds=True, notice=True
            )
        )
        then.extend(
            review_verdicts.plan_verdicts(round_id, round_number, "appeal_records", records)
        )
        if payload.get("appeals_prompt_message_id") is not None:
            then.append(PlannedStep(DELETE_MESSAGE, {
                "channel_id": channel_id, "message_id": int(payload["appeals_prompt_message_id"]),
                "what": "appeals review prompt",
            }))
        then.append(PlannedStep(DELETE_CHANNEL, {
            "channel_id": channel_id, "round_id": round_id, "what": "submission channel",
        }))
        follow_ons = (
            (FollowOn(
                season_lifecycle_service.wind_down_change().kind, {},
                f"Winding the season down after {ctx.what}",
            ),)
            if division_done else ()
        )
        return StepResult(
            result={
                "round_number": round_number, "division_name": division_name,
                "corrections": len(staged), "body": body,
            },
            then=tuple(then),
            follow_ons=follow_ons,
        )

    async def close(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        applied = next((view for view in ctx.steps if view.name == _APPLY), None)
        if (
            applied is None or _discarded(applied.result)
            or bool((applied.result or {}).get("dropped"))
        ):
            # Nothing was changed and the review stands, its prompt dead beside nothing: the
            # discard's own line records it, and the appeals prompt is asked for again, as the
            # bot, so that a fresh one replaces the dead one ("Discard reopens the review").
            reopen = FollowOn(
                APPEALS_OPEN,
                {
                    "round_id": int(ctx.payload["round_id"]),
                    "division_id": int(ctx.payload["division_id"]),
                    "old_prompt_id": ctx.payload.get("appeals_prompt_message_id"),
                },
                what="the appeals review, opened again after its approval was discarded",
            )
            return StepResult(result={"closed": True, "reopened": True}, follow_ons=(reopen,))
        left = [*review_posting.not_done(ctx), *review_verdicts.not_done(ctx)]
        outcome = "Incomplete" if left else "Success"
        line = (
            f"{ctx.named} | APPEALS_REVIEW_APPROVED | {outcome}\n"
            + str((applied.result or {}).get("body", ""))
            + "".join(f"\n  {text}" for text in left)
        )
        return StepResult(result={"closed": True}, lines=(line,))

    async def describe_apply(_ctx: StepContext) -> str:
        return "approving the round's appeals"

    async def describe_close(_ctx: StepContext) -> str:
        return "recording that the round's appeals are approved"

    def outcome(ctx: OutcomeContext) -> str:
        applied = next((view for view in ctx.steps if view.name == _APPLY), None)
        if (
            applied is None or _discarded(applied.result)
            or (applied.result or {}).get("dropped")
        ):
            return (
                "Nothing was changed. The appeals review is posted again: press Approve again "
                "on it."
            )
        result = applied.result or {}
        corrections = int(result.get("corrections", 0))
        what = (
            f"{_plural(corrections, 'correction', 'corrections')} applied"
            if corrections else "no corrections were staged"
        )
        reply = (
            f"✅ Round {result.get('round_number', '?')}'s appeals are approved "
            f"({result.get('division_name', '?')}): {what}. The round is final."
        )
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

    def doing(payload: dict[str, Any]) -> str:
        if payload.get("round_number") is None:
            return "Approving the round's appeals"
        return (
            f"Approving round {payload['round_number']}'s appeals "
            f"({payload.get('division_name', 'its division')})"
        )

    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(NAMES), PlannedStep(_APPLY), PlannedStep(_CLOSE)),
        steps=steps,
        check=check,
        key=lambda payload: f"{KIND}:{payload['round_id']}",
        doing=doing,
        outcome=outcome,
    )
