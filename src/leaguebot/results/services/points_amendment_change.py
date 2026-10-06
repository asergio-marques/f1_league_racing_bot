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
from leaguebot.core.models.change import PlannedStep, StepKind, StepResult, Verdict
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
from leaguebot.core.utils.season_gate import stage_refusal
from leaguebot.results.services.attendance_hook import AttendanceAfterReview
from leaguebot.results.services.result_submission_service import (
    amendment_wait_text,
    open_amendment_in_season,
)

__all__ = ["KIND", "approval_in_hand", "held_text", "points_amendment_change", "staging_refusal"]

KIND = "results.points_amendment.approve"

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

    async def close(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        return StepResult(result={"closed": True})

    async def describe_close(_ctx: StepContext) -> str:
        return "recording the points amendment's approval"

    def outcome(ctx: OutcomeContext) -> str:
        return SUCCESS

    steps: dict[str, Step] = {
        _CLOSE: Step(_CLOSE, StepKind.SAVE, close, describe=describe_close),
    }
    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(_CLOSE),),
        steps=steps,
        check=check,
        key=lambda payload: f"{KIND}:{payload['season_id']}",
        doing=lambda payload: f"Approving season {payload['season_number']}'s points amendment",
        outcome=outcome,
    )

