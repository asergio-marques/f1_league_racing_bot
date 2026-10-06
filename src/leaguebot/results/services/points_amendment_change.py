"""Approving a change to the season's points, carried out on the change queue (#439, slice 3).

`results.points_amendment.approve` is ✅ Approve on `/results amend review`: the season's staged
points replace its own, every raced session is scored again, every division's standings are
recomputed, and every round is posted afresh. It used to run inside the button that pressed it,
committing the points and then recalculating, reposting and recalculating attendance outside that
commit with every repost failure caught and logged to the host alone, and it never asked at the
press whether amendment mode was still on, so a panel left open could approve a second time and
empty the season's points table (#507).

**The check, at the press and again when the change runs** (`check`), refuses in this order: the
module is off; no live season, or not the season the changes were staged for; another approval of
the season already in hand (the job it waits on is named); amendment mode off; a round amendment
open in the season; the working copy out of order; the result unable to be published. A refusal at
the press answers it at once; one found when the change runs updates its acknowledgement and the
queue goes on.

**Everything the approval writes is one job and the posts follow it** (architecture.md, "All or
nothing in one step"), so that a fault leaves the season as it stood and a job that fails stops the
queue for its retry, never half an approval.

**The jobs.** `plan_names` plans one `names` job for each division of the season, which resolves the
drivers' display names the standings are ordered on, so that the save awaits nothing but its
connection. `apply` is **one save for everything the approval writes**: the staged points replace
the season's own (`season_points_service.install_staged_points_on`), every raced session is scored
again, every division's standings are recomputed, and attendance is recalculated through the hook
at each division's latest approved round. A fault in any of it rolls the whole save back, the season
keeps its points, the staged changes and amendment mode, and the queue stops at it to retry. The save
then plans the posts (`review_posting.plan_division_posts`, each replacement posted before the old
message is deleted), and each division's attendance sheet and sanctions (`review_verdicts`), where
each driver's sanction is a job of its own that stops the queue. `close` writes the line.

**The save checks again that it may.** Amendment mode off, or the working copy out of order, refuses
the save having written nothing (`StagedPointsNotApprovable`), where the check at run was passed and
something wrote the database in between. It is a backstop for #507: nothing the bot offers reaches
that window, the staging commands being refused while the approval is in hand.

**A Discard** of `plan_names`, a `names` job or `apply` leaves nothing changed, and the reply says
to run `/results amend review` again; there is no review to reopen, the panel being the admin's
own. A later job discarded leaves the approval in force, named under `Incomplete`.

Two things outside the queue leave the approval alone while it is in hand, stopped included
(architecture.md, "How a change is carried out"): the commands that stage points changes
(`staging_refusal`) and `/results rounds amend` (`review_changes`). The module imports neither
`review_changes`, whose docstring forbids a change type importing it, nor the cog.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    AuditRecord,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services.amendment_service import (
    approval_faults,
    get_amendment_state,
    validate_modification_ordering,
)
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
    in_hand,
)
from leaguebot.core.services.season_service import live_season
from leaguebot.core.utils.log_lines import refusal_line, reply_reason
from leaguebot.core.utils.season_gate import stage_refusal
from leaguebot.results.services import review_posting, review_verdicts
from leaguebot.results.services.attendance_hook import AttendanceAfterReview
from leaguebot.results.services.result_submission_service import (
    amendment_wait_text,
    open_amendment_in_season,
)
from leaguebot.results.services.results_post_service import rescore_season
from leaguebot.results.services.review_posting import NAMES, display_names, posting_steps
from leaguebot.results.services.season_points_service import (
    StagedPointsNotApprovable,
    install_staged_points_on,
)
from leaguebot.results.services.standings_service import cascade_recompute_from_round_on

__all__ = ["KIND", "approval_in_hand", "held_text", "points_amendment_change", "staging_refusal"]

KIND = "results.points_amendment.approve"

_PLAN_NAMES = "plan_names"
_APPLY = "apply"
_CLOSE = "close"
_COMMAND = "results amend review"

MODULE_OFF = "❌ The Results & Standings module is not enabled on this server."
NOT_THE_CURRENT_SEASON = (
    "❌ The season these changes were staged for is no longer the current one. "
    "Nothing was approved."
)
SUCCESS = "✅ Amendment approved. All standings recomputed and reposted."
MODE_OFF = (
    "❌ Amendment mode is not active. Nothing was changed: these changes were already "
    "approved, or amendment mode was turned off after this panel was drawn."
)


def held_text(row: Any) -> str:
    """Why an amendment open in the season holds the approval, for the panel and the refusal.

    *row* is `open_amendment_in_season`'s: the round, its division and the amendment's channel.
    """
    return (
        f"Round {row['round_number']} of **{row['division_name']}** is being amended in "
        f"<#{row['channel_id']}>. Its corrections are not approved yet, and approving "
        "here reposts every round of every division, so it waits until that amendment "
        f"has finished — {amendment_wait_text()}."
    )


NOTHING_CHANGED = (
    "Nothing was changed: the season keeps its points, the staged changes stay staged and "
    "amendment mode stays on. Run `/results amend review` again."
)
APPROVED_BUT_INCOMPLETE = (
    "⚠️ Amendment approved: the new points are in force and every round is rescored, but some "
    "of it could not be done:"
)


def _discarded(result: dict[str, Any] | None) -> bool:
    return "discarded" in (result or {})


def _job(job: int) -> str:
    """" (job #N)" naming the job, or nothing where only the change's close is left (0)."""
    return f" (job #{job})" if job else ""


async def approval_in_hand(
    db_path: str, season_id: int, *, excluding: int | None = None
) -> int | None:
    """The number of the first job an approval of *season_id*'s points waits on, or None where
    none is queued, running or stopped; 0 where every job is done and only its close is left.

    *excluding* leaves out the change with that id, so that the approval's own check does not find
    itself. Read through core's `in_hand`: the check at the press, the staging commands and
    `/results rounds amend` all ask it.
    """
    for payload, job in await in_hand(db_path, [KIND], excluding=excluding):
        if payload.get("season_id") == season_id:
            return job or 0
    return None


def staging_refusal(season_number: int, job: int) -> str:
    """What a command that stages points changes is told while the season's approval is in hand,
    naming the job it waits on (not named where only its close is left, *job* 0)."""
    return (
        f"⏸️ Season {season_number}'s points amendment is being approved{_job(job)}, so the "
        "staged changes and amendment mode cannot be changed until that is done. Let it "
        "finish, or press **Retry** or **Discard** on its notice if it has stopped, then try "
        "again."
    )


async def _divisions(db: aiosqlite.Connection, season_id: int) -> list[aiosqlite.Row]:
    """The season's divisions in tier order, a cancelled one included, each with its first round
    that was not cancelled (None where it has none)."""
    cursor = await db.execute(
        "SELECT d.id, d.name, (SELECT r.id FROM rounds r WHERE r.division_id = d.id "
        "AND r.status != 'CANCELLED' ORDER BY r.round_number LIMIT 1) AS first_round_id "
        "FROM divisions d WHERE d.season_id = ? ORDER BY d.tier, d.id",
        (season_id,),
    )
    return list(await cursor.fetchall())


def _applied(ctx: OutcomeContext) -> bool:
    """Whether the save went through: it was done, and neither it nor a job it waits on was
    discarded or dropped."""
    for view in ctx.steps:
        if view.name in (_PLAN_NAMES, NAMES) and _discarded(view.result):
            return False
    applied = next((view for view in ctx.steps if view.name == _APPLY), None)
    if applied is None or not applied.done:
        return False
    result = applied.result or {}
    return not (_discarded(result) or result.get("dropped") or result.get("refused"))


def _left(ctx: OutcomeContext) -> list[str]:
    """What was not done, one line for each job a league admin discarded, the commands that
    finish the posts last."""
    lines = [*review_posting.not_done(ctx), *review_verdicts.not_done(ctx)]
    repair = [line for line in lines if line.startswith("Repair the cause")]
    return [*(line for line in lines if line not in repair), *repair]


def points_amendment_change(
    *, attendance: AttendanceAfterReview, now: Callable[[], datetime]
) -> ChangeType:
    """The change that approves a season's staged points; see the module. *attendance* is the
    hook the builder hands every change type that reaches attendance, and *now* the queue's
    clock."""

    async def check(ctx: CheckContext) -> Verdict:
        db_path = ctx.db_path
        season_id = int(ctx.payload["season_id"])
        async with get_connection(db_path) as db:
            cursor = await db.execute("SELECT module_enabled FROM results_module_config")
            row = await cursor.fetchone()
        if row is None or not row["module_enabled"]:
            return Verdict.refuse(MODULE_OFF)

        live = await live_season(db_path)
        refusal = stage_refusal(None if live is None else live.stage, _COMMAND)
        if refusal is not None:
            return Verdict.refuse(refusal)
        if live is None or live.id != season_id:
            return Verdict.refuse(NOT_THE_CURRENT_SEASON)

        job = await approval_in_hand(db_path, season_id, excluding=ctx.change_id)
        if job is not None:
            return Verdict.refuse(
                f"⏳ This points amendment is already being approved{_job(job)}. If it has "
                "stopped, press Retry or Discard on its notice in the log channel."
            )

        state = await get_amendment_state(db_path, season_id)
        if state is None or not state.amendment_active:
            return Verdict.refuse(MODE_OFF)

        held = await open_amendment_in_season(db_path, season_id)
        if held is not None:
            return Verdict.refuse(
                "⏸️ Not approved yet. " + held_text(held)
                + " **Nothing has been changed**; run `/results amend review` again then."
            )

        ordering = await validate_modification_ordering(db_path, season_id)
        if ordering:
            bullets = "\n• ".join(ordering)
            return Verdict.refuse(
                f"❌ Amendment not approved — the points would be out of order:\n"
                f"• {bullets}\n"
                f"Nothing has been changed. The staged changes are still there to repair.",
                "\n".join(ordering),
            )

        faults = await approval_faults(db_path, season_id, ctx.bot)
        if faults:
            bullets = "\n• ".join(faults)
            return Verdict.refuse(
                f"⛔ Amendment not approved — the result could not be published:\n"
                f"• {bullets}\n"
                f"**Nothing has been changed** — not the season's points, not the staged "
                f"changes, not amendment mode. Approving rescores and reposts every round of "
                f"every division, so it is refused entire rather than left half-published. "
                f"Repair the channels above and review again.",
                "\n".join(faults),
            )
        return Verdict.go()

    async def plan_names(ctx: StepContext) -> StepResult:
        """Plan one `names` job for each division with a round that was not cancelled."""
        async with get_connection(ctx.db_path) as db:
            divisions = await _divisions(db, int(ctx.payload["season_id"]))
        return StepResult(
            result={"divisions": len(divisions)},
            then=tuple(
                PlannedStep(NAMES, {"division_id": int(d["id"])})
                for d in divisions if d["first_round_id"] is not None
            ),
        )

    async def names_resolved(ctx: StepContext) -> bool:
        """The save is due only where no job it reads was discarded: a discarded `plan_names` or
        `names` leaves nothing changed."""
        return not any(
            view.name in (_PLAN_NAMES, NAMES) and _discarded(view.result) for view in ctx.steps
        )

    async def apply(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        season_id = int(ctx.payload["season_id"])
        try:
            changed = await install_staged_points_on(db, season_id)
        except StagedPointsNotApprovable as refused:
            return StepResult(result={"refused": refused.reply, "reason": refused.reason})
        await rescore_season(db, season_id)

        divisions = await _divisions(db, season_id)
        for division in divisions:
            if division["first_round_id"] is not None:
                await cascade_recompute_from_round_on(
                    db, int(division["id"]), int(division["first_round_id"]),
                    display_names(ctx, int(division["id"])),
                )
        # Each division's attendance, as at its latest round awaiting appeals or final, which is
        # the hook's to do and does nothing while attendance is off.
        latest: dict[int, int] = {}
        for division in divisions:
            row = await (
                await db.execute(
                    "SELECT id FROM rounds WHERE division_id = ? "
                    "AND status IN ('AWAITING_APPEAL_VERDICTS', 'FINAL') "
                    "ORDER BY round_number DESC LIMIT 1",
                    (division["id"],),
                )
            ).fetchone()
            if row is not None:
                latest[int(division["id"])] = int(row["id"])
                await attendance.recalculate_on(db, int(row["id"]), int(division["id"]))

        then: list[PlannedStep] = []
        reposted: set[int] = set()
        posted_in: set[int] = set()
        for division in divisions:
            posts = await review_posting.plan_division_posts(
                db, int(division["id"]), division_name=str(division["name"])
            )
            then.extend(posts)
            for post in posts:
                reposted.add(int(post.payload["round_id"]))
                posted_in.add(int(division["id"]))
        for division in divisions:
            if int(division["id"]) in latest:
                then.extend(review_verdicts.plan_attendance(
                    latest[int(division["id"])], int(division["id"]), str(division["name"])
                ))
        audit = AuditRecord(
            "POINTS_AMENDMENT_APPROVED",
            {"season_id": season_id, "changed": changed.before()},
            {"season_id": season_id, "changed": changed.after()},
        )
        return StepResult(
            result={
                "season_number": int(ctx.payload["season_number"]),
                "points": list(changed.lines()),
                "rounds": len(reposted),
                "divisions": len(posted_in),
            },
            audits=(audit,),
            then=tuple(then),
        )

    async def close(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        applied = next((view for view in ctx.steps if view.name == _APPLY), None)
        result = {} if applied is None else (applied.result or {})
        if result.get("refused"):
            reply = str(result["refused"])
            line = refusal_line(
                ctx.named, "`/results amend review`", reply_reason(reply),
                detail=str(result.get("reason") or "") or None,
            )
            return StepResult(result={"closed": True}, lines=(line,))
        if not _applied(ctx):
            return StepResult(result={"closed": True})
        left = _left(ctx)
        points = "; ".join(result.get("points") or []) or "none"
        line = (
            f"{ctx.named} | /results amend review | {'Incomplete' if left else 'Success'}\n"
            f"  season: {result.get('season_number', ctx.payload['season_number'])}\n"
            f"  points changed: {points}\n"
            f"  rounds rescored and reposted: {result.get('rounds', 0)} across "
            f"{result.get('divisions', 0)} division(s)"
            + "".join(f"\n  {text}" for text in left)
        )
        return StepResult(result={"closed": True}, lines=(line,))

    async def describe_plan_names(_ctx: StepContext) -> str:
        return "planning the drivers' display names for the standings"

    async def describe_apply(_ctx: StepContext) -> str:
        return "approving the season's points amendment"

    async def describe_close(_ctx: StepContext) -> str:
        return "recording the points amendment's approval"

    def outcome(ctx: OutcomeContext) -> str:
        applied = next((view for view in ctx.steps if view.name == _APPLY), None)
        refused = (applied.result or {}).get("refused") if applied is not None else None
        if refused:
            return str(refused)
        if not _applied(ctx):
            return NOTHING_CHANGED
        left = _left(ctx)
        return "\n".join([APPROVED_BUT_INCOMPLETE, *left]) if left else SUCCESS

    steps: dict[str, Step] = {
        **posting_steps(),
        **review_verdicts.verdict_steps(now, attendance),
        _PLAN_NAMES: Step(_PLAN_NAMES, StepKind.ACT, plan_names, describe=describe_plan_names),
        _APPLY: Step(
            _APPLY, StepKind.SAVE, apply, still_due=names_resolved, describe=describe_apply,
        ),
        _CLOSE: Step(_CLOSE, StepKind.SAVE, close, describe=describe_close),
    }
    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(_PLAN_NAMES), PlannedStep(_APPLY), PlannedStep(_CLOSE)),
        steps=steps,
        check=check,
        key=lambda payload: f"{KIND}:{payload['season_id']}",
        doing=lambda payload: f"Approving season {payload['season_number']}'s points amendment",
        outcome=outcome,
    )

