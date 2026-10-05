"""Approving an amendment's report stage and its appeals stage, carried out on the change queue
(#439, slice 2).

`/results rounds amend` writes the corrected classification at once (stage one), then asks its
manager to approve the reports and, last, the appeals. The two approvals ran inside the buttons
that asked for them, claiming the amendment's deadline in memory and undoing the amendment on any
failure. They are now changes of their own, which the queue saves and resumes:

- **`results.amendment.reports.approve`**: `apply`, one save (the superseded announcements noted,
  the amended sessions' verdict records cleared and the approved reports written back with the
  points recalculated, `round_amend_channels.reports_approved_at` set while the amendment is still
  unclaimed, the deadline kept), the take-down of the stage's prompt and approval message, the
  amendment's appeals prompt, and `close`, writing `AMEND_STAGE_2 | Recorded`. Nothing is
  published: an amendment abandoned part-way has nothing on Discord to take back.
- **`results.amendment.appeals.approve`**: `names`, then `apply`, one save (the upheld appeals and
  the points, the round's pardons, the former drivers, the cascade of standings snapshots, the
  attendance recalculation, the release of the snapshot and the deadline, and the amend row's
  `closed_at`), then the division's rebuild between the batch notices, the superseded
  announcements and banners taken down, the amendment channel deleted, and `close`, writing
  `RESULT_AMENDED`.

**Nothing is undone because a stage's job failed.** A job that fails stops the queue and is
retried ("Retry like any job"). While a stage's job is in hand, stopped included, the amendment
neither lapses nor can be cancelled, restart recovery leaves it alone, and a Retry approves it
even past its half-hour. Once a league admin discards it, the next sweep undoes an amendment past
its half-hour ("Leave it while stuck"). One change at a time already stops two stages running
together, so the stage claim of the old code (`_claim_amendment`) is not used on this path.

**The release and everything the rebuild writes are saved together**, so a stop after the release
leaves no standings or attendance owed. Once that save is made the amendment can no longer be
abandoned.

**The rebuild is the division's, in the order a league reads it**: every round's results, then
every round's standings (each a new message), the attendance sheet and the sanctions it owes, then
each round's heading over its report verdicts and its appeal verdicts, from the amended round on.
A round's superseded announcements and the banner heading them are taken down only where every
verdict replacing them was posted (a verdict deleted from a channel is in no channel at all), and a
banner that also heads an attendance sanction card is kept. Ones left standing are named, with a
link, for deletion by hand.

**The payloads** are `round_id`, `division_id`, `session_types` (the sessions amended, as their
values), `staged` (the reports of the report stage, the corrections of the appeals stage, each as
plain data), `pardons`, and for the report stage `prompt_message_id` and `approval_message_id`, for
the appeals stage `appeals_prompt_message_id`; `round_number` and `division_name` too, where the
review that asks knows them, which name the round in the acknowledgement. The time every writer
stamps is the queue's clock, handed in, never `datetime.now`.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

import aiosqlite

from leaguebot.core.db.database import get_connection
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
    unfinished,
)
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.services.driver_service import current_account_map_for_division
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services import review_posting, review_verdicts
from leaguebot.results.services.attendance_hook import AttendanceAfterReview
from leaguebot.results.services.penalty_service import (
    StagedPenalty,
    apply_penalties_on,
    load_staged_from_records,
    reports_only,
)
from leaguebot.results.services.penalty_wizard import PenaltyReviewState, StagedPardon, _pen_label
from leaguebot.results.services.report_approval_change import _latest_round
from leaguebot.results.services.result_submission_service import (
    _AMENDMENT_NOT_OPEN,
    _apply_staged_appeals_on,
    _clear_round_verdict_records_on,
    _recompute_former_drivers_after_amendment_on,
    _recompute_session_points_on,
    _release_amendment_on,
    _remember_superseded_announcements_on,
    send_appeals_prompt,
)
from leaguebot.results.services.results_post_service import _delete_posting, _parse_ids
from leaguebot.results.services.review_posting import (
    DELETE_BATCH_NOTICE,
    DELETE_CHANNEL,
    DELETE_MESSAGE,
    NAMES,
    POST_BATCH_NOTICE,
    display_names,
    plan_division_posts,
    posting_steps,
)
from leaguebot.results.services.standings_service import cascade_recompute_from_round_on
from leaguebot.results.services.verdict_announcement_service import (
    _banners_heading_sanctions_on,
    _banners_of_on,
    _forget_banners_on,
)
from leaguebot.results.services.verdict_records import VERDICT_TABLES, select_verdicts

log = logging.getLogger(__name__)

__all__ = ["APPEALS_KIND", "REPORTS_KIND", "amendment_stage_changes"]

REPORTS_KIND = "results.amendment.reports.approve"
APPEALS_KIND = "results.amendment.appeals.approve"

_APPLY = "apply"
_CLOSE = "close"
POST_AMENDMENT_APPEALS_PROMPT = review_verdicts.POST_APPEALS_PROMPT
TAKE_DOWN_VERDICT = "take_down_verdict"
TAKE_DOWN_BANNER = "take_down_banner"

_REBUILD_NOTICE = "\U0001f3a8 Rebuilding the division's results, standings and verdicts — one moment."
_REPORTS_APPROVED = (
    "ℹ️ This amendment's reports are already approved; its appeals follow below."
)
_NOTHING_CHANGED_LAPSED = (
    "Nothing was changed, and the amendment's half-hour has passed, so it will be undone in the "
    "next few minutes. Run `/results rounds amend` again."
)
_VERDICT_COLUMNS = (
    "v.id AS id, v.race_result_id, v.qual_result_id, v.penalty_type, v.time_seconds, "
    "v.description, v.justification, r.driver_user_id AS driver_user_id, "
    "r.team_instance_id AS team_instance_id, sr.session_type AS session_type"
)


def _discarded(result: dict[str, Any] | None) -> bool:
    return "discarded" in (result or {})


def _unapplied(view: Any) -> bool:
    result = (view.result if view is not None else None) or {}
    return view is None or "discarded" in result or bool(result.get("dropped"))


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _sessions_text(values: list[str]) -> str:
    return ", ".join(values)


def _past(expires_at: Any, now: datetime) -> bool:
    """Whether the amendment's deadline *expires_at* has passed by the handed clock. A deadline
    that cannot be read counts as passed: the amendment is not one to act on."""
    try:
        deadline = datetime.fromisoformat(expires_at)
    except (TypeError, ValueError):
        return True
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=now.tzinfo)
    return deadline <= now


async def _deadline_passed(db: aiosqlite.Connection, round_id: int, now: datetime) -> bool:
    """Whether the half-hour of the amendment of *round_id* has passed. Read on the connection
    handed, so a stage's save and its reply agree on the same row."""
    row = await (
        await db.execute(
            "SELECT expires_at FROM round_amend_channels WHERE round_id = ?", (round_id,)
        )
    ).fetchone()
    return row is None or row["expires_at"] is None or _past(row["expires_at"], now)


async def _open_refusal(
    db_path: str, round_id: int, now: datetime, *, reports_stage: bool
) -> str | None:
    """Why the amendment of *round_id* cannot be acted on now, or None while it is open: its row
    stands with its classification stage done and its deadline not past by the handed clock. The
    report stage is refused once approved; the appeals stage until it is."""
    async with get_connection(db_path) as db:
        row = await (
            await db.execute(
                "SELECT pre_amendment_state, expires_at, reports_approved_at "
                "FROM round_amend_channels WHERE round_id = ? AND closed_at IS NULL",
                (round_id,),
            )
        ).fetchone()
    if row is None or row["pre_amendment_state"] is None or row["expires_at"] is None:
        return _AMENDMENT_NOT_OPEN
    if reports_stage and row["reports_approved_at"] is not None:
        return _REPORTS_APPROVED
    if not reports_stage and row["reports_approved_at"] is None:
        return _AMENDMENT_NOT_OPEN
    return _AMENDMENT_NOT_OPEN if _past(row["expires_at"], now) else None


async def _amend_channel_of(db: aiosqlite.Connection, round_id: int) -> int:
    row = await (
        await db.execute(
            "SELECT channel_id FROM round_amend_channels WHERE round_id = ?", (round_id,)
        )
    ).fetchone()
    if row is None:
        raise LookupError(f"round {round_id} has no amendment to approve")
    return int(row["channel_id"])


async def _round_names(db: aiosqlite.Connection, round_id: int) -> tuple[int, str, int | None]:
    row = await (
        await db.execute(
            "SELECT r.round_number, d.name AS division_name, s.season_number "
            "FROM rounds r JOIN divisions d ON d.id = r.division_id "
            "JOIN seasons s ON s.id = d.season_id WHERE r.id = ?",
            (round_id,),
        )
    ).fetchone()
    if row is None:
        raise LookupError(f"round {round_id} is not in the database")
    return int(row["round_number"]), str(row["division_name"]), row["season_number"]


async def _applied_text(
    db: aiosqlite.Connection, division_id: int, staged: list[StagedPenalty]
) -> str:
    current_of = await current_account_map_for_division(db, division_id)
    return ", ".join(
        f"{_pen_label(sp)} for <@{current_of.get(sp.driver_user_id, sp.driver_user_id)}> "
        f"in {sp.session_type.value}"
        for sp in staged
    )


def _doing(stage: str) -> Callable[[dict[str, Any]], str]:
    def doing(payload: dict[str, Any]) -> str:
        if payload.get("round_number") is None:
            return f"Approving an amendment's {stage}"
        return (
            f"Approving the {stage} of round {payload['round_number']}'s amendment "
            f"({payload.get('division_name', 'its division')})"
        )

    return doing


def amendment_stage_changes(
    *, attendance: AttendanceAfterReview, now: Callable[[], datetime]
) -> tuple[ChangeType, ChangeType]:
    """The two change types of an amendment's stages; see the module. *attendance* is the hook the
    builder hands every change type of the review, and *now* the queue's clock."""

    async def _in_hand(ctx: CheckContext, kind: str, round_id: int) -> bool:
        others = await unfinished(ctx.db_path, [kind], excluding=ctx.change_id)
        return any(p.get("round_id") == round_id for p in others)

    async def _closed_unapplied(db: aiosqlite.Connection, ctx: StepContext) -> dict[str, Any]:
        """What `close` keeps where the stage's `apply` was discarded: whether the amendment's
        half-hour has passed by now, which decides whether the manager may press Approve again or
        the next sweep undoes the amendment."""
        return {
            "closed": True,
            "lapsed": await _deadline_passed(db, int(ctx.payload["round_id"]), now()),
        }

    def _lapsed(ctx: OutcomeContext) -> bool:
        closed = next((view for view in ctx.steps if view.name == _CLOSE), None)
        return bool(((closed.result if closed is not None else None) or {}).get("lapsed"))

    # ── The report stage ──────────────────────────────────────────────────

    async def check_reports(ctx: CheckContext) -> Verdict:
        round_id = int(ctx.payload["round_id"])
        refusal = await _open_refusal(ctx.db_path, round_id, now(), reports_stage=True)
        if refusal is None and await _in_hand(ctx, REPORTS_KIND, round_id):
            refusal = _AMENDMENT_NOT_OPEN
        return Verdict.go() if refusal is None else Verdict.refuse(refusal)

    async def apply_reports(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.payload
        round_id, division_id = int(payload["round_id"]), int(payload["division_id"])
        session_types = [SessionType(value) for value in payload["session_types"]]
        staged = [StagedPenalty.from_payload(p) for p in payload["staged"]]
        when = now()
        channel_id = await _amend_channel_of(db, round_id)
        round_number, division_name, _season = await _round_names(db, round_id)

        # Only an amendment still unclaimed (`expires_at IS NOT NULL`) can be approved: a stage
        # approved after the sweep or Cancel took the amendment writes nothing, and the deadline
        # is kept, never extended.
        claimed = await db.execute(
            "UPDATE round_amend_channels SET reports_approved_at = ? "
            "WHERE round_id = ? AND expires_at IS NOT NULL AND reports_approved_at IS NULL",
            (when.isoformat(), round_id),
        )
        if claimed.rowcount != 1:
            raise RuntimeError(f"the amendment of round {round_id} is no longer open")
        await _remember_superseded_announcements_on(db, round_id, session_types)
        await _clear_round_verdict_records_on(db, round_id, session_types)
        if staged:
            await apply_penalties_on(db, round_id, division_id, staged, ctx.actor_id or 0, now=when)
            # Raises where a session cannot be scored: the save is rolled back whole.
            await _recompute_session_points_on(db, round_id)
        applied = await _applied_text(db, division_id, staged) if staged else ""

        # The half-hour covers both steps and is never extended: a reports approval landing after
        # it (stuck on the queue, then retried) is recorded, and nothing carries on from it. The
        # amendment is undone at the next sweep, and the manager runs the command again.
        late = await _deadline_passed(db, round_id, when)
        then: list[PlannedStep] = []
        taken_down = () if late else (
            ("prompt_message_id", "report review prompt"),
            ("approval_message_id", "approval message"),
        )
        for key, what in taken_down:
            if payload.get(key) is not None:
                then.append(PlannedStep(DELETE_MESSAGE, {
                    "channel_id": channel_id, "message_id": int(payload[key]), "what": what,
                }))
        if not late:
            then.append(PlannedStep(POST_AMENDMENT_APPEALS_PROMPT, {
                "round_id": round_id, "division_id": division_id, "channel_id": channel_id,
            }))
        return StepResult(
            result={
                "round_number": round_number, "division_name": division_name,
                "reports": len(staged), "applied": applied,
                "sessions": list(payload["session_types"]), "late": late,
            },
            then=tuple(then),
        )

    async def close_reports(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        applied = next((view for view in ctx.steps if view.name == _APPLY), None)
        if _unapplied(applied):
            return StepResult(result=await _closed_unapplied(db, ctx))
        assert applied is not None
        result = applied.result or {}
        text = (
            f"  applied: {result['applied']}\n" if result.get("applied") else ""
        )
        line = (
            f"{ctx.named} | AMEND_STAGE_2 | Recorded\n"
            f"  round: {result['round_number']} ({result['division_name']}), "
            f"sessions: {_sessions_text(result['sessions'])}\n"
            f"  reports: {result['reports'] or 'none'}\n"
            f"{text}"
            "  Nothing is published until the appeal stage is approved."
            + ("\n  The half-hour had passed: the amendment is undone at the next sweep."
               if result.get("late") else "")
        )
        return StepResult(result={"closed": True}, lines=(line,))

    def outcome_reports(ctx: OutcomeContext) -> str:
        views = {view.name: view for view in ctx.steps}
        applied = views.get(_APPLY)
        if _unapplied(applied):
            if _lapsed(ctx):
                return _NOTHING_CHANGED_LAPSED
            return (
                "Nothing was changed. Press Approve again on the amendment's report stage, "
                "before it lapses."
            )
        assert applied is not None
        result = applied.result or {}
        reports = int(result.get("reports", 0))
        what = f"{_plural(reports, 'report', 'reports')} kept" if reports else "no reports"
        named = f"Round {result.get('round_number', '?')}'s amendment reports are approved " \
            f"({result.get('division_name', '?')}): {what}."
        if result.get("late"):
            return (
                f"⚠️ {named}\nThe amendment's half-hour has passed, so it will be undone in the "
                "next few minutes. Run `/results rounds amend` again."
            )
        reply = f"✅ {named}"
        prompt = views.get(POST_AMENDMENT_APPEALS_PROMPT)
        if prompt is not None and _discarded(prompt.result):
            return (
                f"{reply}\n⚠️ The appeals stage's prompt could not be posted in the amendment "
                "channel, so the amendment cannot go on: cancel it, or let it lapse, then run "
                "`/results rounds amend` again."
            )
        return f"{reply} Its appeals stage is posted below."

    # ── The appeals stage ─────────────────────────────────────────────────

    async def check_appeals(ctx: CheckContext) -> Verdict:
        round_id = int(ctx.payload["round_id"])
        refusal = await _open_refusal(ctx.db_path, round_id, now(), reports_stage=False)
        if refusal is None and await _in_hand(ctx, APPEALS_KIND, round_id):
            refusal = _AMENDMENT_NOT_OPEN
        return Verdict.go() if refusal is None else Verdict.refuse(refusal)

    async def names_not_discarded(ctx: StepContext) -> bool:
        return not any(view.name == NAMES and _discarded(view.result) for view in ctx.steps)

    async def apply_appeals(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.payload
        round_id, division_id = int(payload["round_id"]), int(payload["division_id"])
        staged = [StagedPenalty.from_payload(p) for p in payload["staged"]]
        pardons = [StagedPardon.from_payload(p) for p in payload["pardons"]]
        when = now()
        channel_id = await _amend_channel_of(db, round_id)
        round_number, division_name, season_number = await _round_names(db, round_id)

        # The upheld appeals and the points they move (raising where a session cannot be scored:
        # the save is rolled back whole), then the round's pardons, the former drivers (the one
        # path that can take a flag down, read from the snapshot the release below destroys), the
        # cascade of standings and the attendance rebuilt from the corrected results.
        await _apply_staged_appeals_on(db, round_id, division_id, staged, ctx.actor_id or 0, now=when)
        await attendance.rewrite_pardons_on(db, round_id, pardons, when)
        await _recompute_former_drivers_after_amendment_on(db, round_id)
        await cascade_recompute_from_round_on(db, division_id, round_id, display_names(ctx))
        await attendance.recalculate_on(db, round_id, division_id)
        # The commitment: from this save nothing reverts the round, and what the rebuild below
        # shows is already in the database.
        await _release_amendment_on(db, round_id)
        await db.execute(
            "UPDATE round_submission_channels SET closed = 1 WHERE round_id = ?", (round_id,)
        )
        await db.execute(
            "UPDATE round_amend_channels SET closed_at = ? WHERE round_id = ? AND channel_id = ?",
            (when.isoformat(), round_id, channel_id),
        )
        applied = await _applied_text(db, division_id, staged) if staged else ""

        then = await _plan_rebuild(db, round_id, division_id, division_name, channel_id)
        return StepResult(
            result={
                "round_number": round_number, "division_name": division_name,
                "season_number": season_number, "applied": applied,
                "sessions": list(payload["session_types"]), "corrections": len(staged),
            },
            then=tuple(then),
        )

    async def close_appeals(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        applied = next((view for view in ctx.steps if view.name == _APPLY), None)
        if _unapplied(applied):
            return StepResult(result=await _closed_unapplied(db, ctx))
        assert applied is not None
        result = applied.result or {}
        left = _left(ctx)
        hint = [line for line in left if line.startswith("Repair the cause")]
        summary = (
            f"{ctx.named} | RESULT_AMENDED | {'Incomplete' if left else 'Success'}\n"
            f"  season: {result.get('season_number')}, division: {result['division_name']!r}\n"
            f"  round: {result['round_number']}, sessions: {_sessions_text(result['sessions'])}"
        )
        if result.get("applied"):
            summary += f"\n  appeals applied: {result['applied']}"
        if left:
            summary += "".join(f"\n  {line}" for line in left if line not in hint)
            summary += "".join(f"\n  {line}" for line in hint)
        return StepResult(result={"closed": True}, lines=(summary,))

    def outcome_appeals(ctx: OutcomeContext) -> str:
        applied = next((view for view in ctx.steps if view.name == _APPLY), None)
        if _unapplied(applied):
            if _lapsed(ctx):
                return _NOTHING_CHANGED_LAPSED
            return "Nothing was changed. Press Approve again on the amendment's appeals stage."
        left = _left(ctx)
        if left:
            return "\n".join(
                ["⚠️ The amendment is applied, but some of it could not be posted:", *left]
            )
        return "✅ Amendment applied, and the division's channels rebuilt in round order."

    # ── The rebuild's own jobs ────────────────────────────────────────────

    async def describe_appeals_prompt(ctx: StepContext) -> str:
        return f"posting the amendment's appeals prompt in <#{ctx.step_payload['channel_id']}>"

    async def post_appeals_prompt(ctx: StepContext) -> StepResult:
        """Open the amendment's last stage in its channel: the corrections as the round's records
        hold them, the pardons as the report stage left them."""
        payload = ctx.step_payload
        guild = await review_posting._league_guild(ctx.bot)
        channel = review_posting._channel(guild, int(payload["channel_id"]), "amendment")
        # A try that posted the prompt and failed to save the job's mark left it standing.
        kept = (ctx.kept or {}).get("message_id")
        if kept is not None:
            await _delete_posting(
                channel, int(kept), [int(kept)], label="amendment appeals prompt", failures=[]
            )
        session_types = [SessionType(value) for value in ctx.payload["session_types"]]
        reports, corrections, _stored = await load_staged_from_records(
            ctx.db_path, int(payload["round_id"]), session_types=session_types
        )
        async with get_connection(ctx.db_path) as db:
            round_number, division_name, _season = await _round_names(db, int(payload["round_id"]))
        state = PenaltyReviewState(
            round_id=int(payload["round_id"]),
            division_id=int(payload["division_id"]),
            submission_channel_id=channel.id,
            session_types_present=session_types,
            db_path=ctx.db_path,
            bot=ctx.bot,
            staged=reports,
            staged_appeals=corrections,
            staged_pardons=[StagedPardon.from_payload(p) for p in ctx.payload["pardons"]],
            round_number=round_number,
            division_name=division_name,
            is_amendment=True,
        )
        message = await send_appeals_prompt(ctx.bot, channel, state)
        return StepResult(result={"message_id": message.id, "channel_id": channel.id})

    async def replacements_posted(ctx: StepContext) -> bool:
        """A round's old announcements and banner come down only once every verdict replacing
        them stands: a verdict deleted from a channel is in no channel at all."""
        round_id = ctx.step_payload["round_id"]
        return all(
            view.done and not _discarded(view.result)
            for view in ctx.steps
            if view.name == review_verdicts.ANNOUNCE_VERDICT and view.payload["round_id"] == round_id
        )

    async def describe_take_down(ctx: StepContext) -> str:
        payload = ctx.step_payload
        what = "verdict banner" if ctx.step_name == TAKE_DOWN_BANNER else "verdict announcement"
        return f"deleting a superseded {what} in <#{payload['channel_id']}>"

    async def take_down_verdict(ctx: StepContext) -> StepResult:
        payload = ctx.step_payload
        guild = await review_posting._league_guild(ctx.bot)
        channel = as_text_channel(guild.get_channel(int(payload["channel_id"])))
        if channel is None:
            return StepResult(result={"gone": True})
        anchor = int(payload["anchor"])
        failures: list[Any] = []
        left = await _delete_posting(
            channel, anchor, payload.get("chunks") or [anchor], label="verdict",
            failures=failures,
        )
        if left:
            raise StepFailedOnDiscord(
                "the superseded verdict announcement could not be deleted",
                result={"link": payload.get("link")},
            ) from (failures[0] if failures else None)
        return StepResult(result={"deleted": True})

    async def take_down_banner(ctx: StepContext) -> StepResult:
        payload = ctx.step_payload
        guild = await review_posting._league_guild(ctx.bot)
        channel = as_text_channel(guild.get_channel(int(payload["channel_id"])))
        if channel is None:
            return StepResult(result={"gone": True})
        message_id = int(payload["message_id"])
        failures: list[Any] = []
        left = await _delete_posting(
            channel, message_id, [message_id], label="verdict banner", failures=failures
        )
        if left:
            raise StepFailedOnDiscord(
                "the superseded verdict banner could not be deleted",
                result={"link": payload.get("link")},
            ) from (failures[0] if failures else None)
        return StepResult(result={"deleted": True})

    async def forget_banner(
        db: aiosqlite.Connection, ctx: StepContext, _result: StepResult
    ) -> None:
        await _forget_banners_on(db, [int(ctx.step_payload["message_id"])])

    steps: dict[str, Step] = {
        **posting_steps(),
        **review_verdicts.verdict_steps(now, attendance),
        POST_AMENDMENT_APPEALS_PROMPT: Step(
            POST_AMENDMENT_APPEALS_PROMPT, StepKind.ACT, post_appeals_prompt,
            describe=describe_appeals_prompt,
        ),
        TAKE_DOWN_VERDICT: Step(
            TAKE_DOWN_VERDICT, StepKind.DELETE, take_down_verdict, still_due=replacements_posted,
            describe=describe_take_down,
        ),
        TAKE_DOWN_BANNER: Step(
            TAKE_DOWN_BANNER, StepKind.DELETE, take_down_banner, still_due=replacements_posted,
            describe=describe_take_down, record=forget_banner,
        ),
    }

    async def describe_reports(_ctx: StepContext) -> str:
        return "approving the amendment's reports"

    async def describe_appeals(_ctx: StepContext) -> str:
        return "approving the amendment's appeals"

    async def describe_close(_ctx: StepContext) -> str:
        return "recording the amendment's approval"

    # The report stage publishes and moves nothing: only the jobs that take its own screens down
    # and open the next stage.
    reports_steps = {
        DELETE_MESSAGE: steps[DELETE_MESSAGE],
        POST_AMENDMENT_APPEALS_PROMPT: steps[POST_AMENDMENT_APPEALS_PROMPT],
        _APPLY: Step(_APPLY, StepKind.SAVE, apply_reports, describe=describe_reports),
        _CLOSE: Step(_CLOSE, StepKind.SAVE, close_reports, describe=describe_close),
    }
    appeals_steps = {
        **steps,
        _APPLY: Step(
            _APPLY, StepKind.SAVE, apply_appeals, still_due=names_not_discarded,
            describe=describe_appeals,
        ),
        _CLOSE: Step(_CLOSE, StepKind.SAVE, close_appeals, describe=describe_close),
    }
    reports = ChangeType(
        kind=REPORTS_KIND,
        opening=(PlannedStep(_APPLY), PlannedStep(_CLOSE)),
        steps=reports_steps,
        check=check_reports,
        key=lambda payload: f"{REPORTS_KIND}:{payload['round_id']}",
        doing=_doing("reports"),
        outcome=outcome_reports,
    )
    appeals = ChangeType(
        kind=APPEALS_KIND,
        opening=(PlannedStep(NAMES), PlannedStep(_APPLY), PlannedStep(_CLOSE)),
        steps=appeals_steps,
        check=check_appeals,
        key=lambda payload: f"{APPEALS_KIND}:{payload['round_id']}",
        doing=_doing("appeals"),
        outcome=outcome_appeals,
    )
    return reports, appeals


# ---------------------------------------------------------------------------
# What the rebuild plans, and what a Discard of it leaves
# ---------------------------------------------------------------------------


def _left(ctx: OutcomeContext) -> list[str]:
    """What was not done, one line each, in the order the jobs stood: the posting and verdict
    jobs a league admin discarded, the announcements left standing, and the channel."""
    lines = [*review_posting.not_done(ctx), *review_verdicts.not_done(ctx)]
    # `not_done` ends the posting lines with the commands that finish them; the standing
    # announcements go before it, so that the commands close the list.
    standing: list[str] = []
    for view in ctx.steps:
        if view.name not in (TAKE_DOWN_VERDICT, TAKE_DOWN_BANNER):
            continue
        result = view.result or {}
        link = result.get("link") or view.payload.get("link")
        what = "banner" if view.name == TAKE_DOWN_BANNER else "announcement"
        if _discarded(result):
            standing.append(
                f"⚠️ A superseded verdict {what} could not be deleted ({link}): delete it by hand."
            )
        elif result.get("dropped"):
            standing.append(
                f"⚠️ A superseded verdict {what} was left standing, its replacements not having "
                f"all gone out ({link}): delete it by hand once the verdicts are posted."
            )
    repair = [line for line in lines if line.startswith("Repair the cause")]
    body = [line for line in lines if line not in repair]
    return [*body, *standing, *repair]


async def _plan_rebuild(
    db: aiosqlite.Connection, round_id: int, division_id: int, division_name: str,
    channel_id: int,
) -> list[PlannedStep]:
    """The jobs that rebuild the division's channels once the amendment is saved, in the order a
    league reads them (see the module), read on the save's own connection."""
    server = await (await db.execute("SELECT server_id FROM server_configs")).fetchone()
    guild_id = None if server is None else server["server_id"]

    def link(channel: Any, message_id: int) -> str | None:
        if guild_id is None or not channel:
            return None
        return f"https://discord.com/channels/{guild_id}/{channel}/{message_id}"

    rounds = await (
        await db.execute(
            "SELECT id, round_number FROM rounds WHERE division_id = ? AND status != 'CANCELLED' "
            "AND round_number >= (SELECT round_number FROM rounds WHERE id = ?) "
            "ORDER BY round_number",
            (division_id, round_id),
        )
    ).fetchall()

    planned: list[PlannedStep] = [
        PlannedStep(POST_BATCH_NOTICE, {"channel_id": channel_id, "text": _REBUILD_NOTICE}),
        *await plan_division_posts(db, division_id),
        *review_verdicts.plan_attendance(
            await _latest_round(db, round_id, division_id), division_id, division_name
        ),
    ]
    takes_down: list[PlannedStep] = []
    for rnd in rounds:
        this_round = int(rnd["id"])
        appeals = sorted(
            await select_verdicts(db, "appeal_records", _VERDICT_COLUMNS, round_id=this_round),
            key=lambda r: r["id"],
        )
        penalties = reports_only(
            sorted(
                await select_verdicts(db, "penalty_records", _VERDICT_COLUMNS, round_id=this_round),
                key=lambda r: r["id"],
            ),
            appeals,
        )
        planned.extend(
            review_verdicts.plan_round_verdicts(
                this_round, int(rnd["round_number"]), penalties, appeals
            )
        )

        # The announcements these replace: those the rows still hold, and for the amended round
        # those its report stage noted before it cleared their records.
        old: list[dict[str, Any]] = []
        for table in VERDICT_TABLES:
            old.extend(await select_verdicts(
                db, table,
                "v.announcement_message_id AS anchor, v.announcement_message_ids AS chunks, "
                "v.announcement_channel_id AS channel_id, r.driver_user_id AS driver_user_id",
                round_id=this_round, where=" AND v.announcement_message_id IS NOT NULL",
            ))
        if this_round == round_id:
            noted = await (
                await db.execute(
                    "SELECT superseded_announcements FROM round_amend_channels WHERE round_id = ?",
                    (round_id,),
                )
            ).fetchone()
            if noted is not None and noted["superseded_announcements"]:
                old.extend(json.loads(noted["superseded_announcements"]))
        for entry in old:
            if not entry.get("channel_id"):
                continue
            takes_down.append(PlannedStep(TAKE_DOWN_VERDICT, {
                "round_id": this_round, "channel_id": int(entry["channel_id"]),
                "anchor": int(entry["anchor"]), "chunks": _parse_ids(entry.get("chunks")),
                "driver_user_id": entry.get("driver_user_id"),
                "link": link(entry["channel_id"], int(entry["anchor"])),
            }))

        # The banner heading them goes with them, unless it also heads a sanction card.
        banners = await _banners_of_on(db, this_round)
        kept = await _banners_heading_sanctions_on(db, [message_id for _c, message_id in banners])
        for banner_channel, message_id in banners:
            if message_id in kept:
                continue
            takes_down.append(PlannedStep(TAKE_DOWN_BANNER, {
                "round_id": this_round, "channel_id": int(banner_channel),
                "message_id": int(message_id), "link": link(banner_channel, int(message_id)),
            }))

    planned.append(PlannedStep(DELETE_BATCH_NOTICE, {"channel_id": channel_id}))
    planned.extend(takes_down)
    planned.append(PlannedStep(DELETE_CHANNEL, {
        "channel_id": channel_id, "round_id": round_id, "what": "amendment channel",
        "reason": "Amendment applied",
    }))
    return planned
