"""The jobs that tell a league of a round's review: its verdicts, its attendance sheet and the
sanctions that follow (#439, slice 2).

The approvals of a round's reports and appeals, and an amendment's replay, plan these jobs from
this module, as they plan their posts from `review_posting`: `plan_verdicts` gives the heading and
one verdict for each record an approval saved, `plan_attendance` the sheet and the sanctions after
it, `verdict_steps(now, attendance)` the jobs themselves for a change type's `steps`, and `not_done(ctx)` one
line for each job a league admin discarded, for the outcome and the `Incomplete` line.

**Every failure stops the queue** (the owner's rule), and so a job here never swallows what the
old code reported and went on from. A division with no verdicts channel set, or one given and
since deleted, fails the job that needs it ("reported, not skipped"): the manager sets the channel
and presses Retry. A retry posts as text (Constitution XIV, rule 8).

**The heading is a job of its own** (`announce_heading`), ahead of the verdicts, and a heading
Discord refuses stops the queue with no verdict posted ahead of it; once discarded, the verdicts go
out beneath none, and the outcome says so. It treats a banner `try_post` returns as None as a
failure. Its `record` saves the banner row, so the sanctions that follow read the heading back from
its row and a stop and a restart between a verdict and a sanction post no second one.

**A verdict is a job for each record** (`announce_verdict`), its `record` saving the message and
channel it was announced in with the job's mark. A try that posted and failed to save its id left
the message standing, which the next try removes first from `ctx.kept`.

**The appeals prompt is a job** (`post_appeals_prompt`), planned after the verdicts: it opens the
appeals stage in the round's submission channel, and its `record` saves the prompt's id on the
channel's row, so that a restart or a Discard knows which prompt stands. A try that posted and
failed to save its id left the message standing, which the next try removes first from `ctx.kept`.

**Attendance is the hook's** (`results/services/attendance_hook.py`), which does nothing while
attendance is off, so no job here asks the switch. The sheet is a job (`attendance_sheet`) that
raises where it could not post, in place of the old retry queue; a sheet that stops the queue holds
the sanctions behind it until it is retried or discarded, which delays them and never skips them.

**Each sanction is a job of its own.** `plan_sanctions` reads the drivers owed one and plans, for
each, `apply_sanction` then `announce_sanction`, and, where any was owed, `refresh_lineup` and the
sheet again for this division and for each division a sack reached. One that does not apply (a
division with no reserve team, a role change Discord refuses) stops the queue until it is retried
or discarded. An `apply_sanction` is due only while its driver is still owed the sanction, so a
retry applies only what is owed and a driver `/attendance sync` sanctioned meanwhile is not
sanctioned twice; its announcement, the lineup and the later sheets are due only where a sanction
was applied by one of these jobs. The sync command is named only once a job is discarded: a failure
carries the hint the hook gives for it on the job's kept result, which the discard keeps.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any


from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    GuildUnavailable,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
)
from leaguebot.core.services.change_queue import OutcomeContext, Step, StepContext
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.image.services import image_verdict_banner_post
from leaguebot.results.services import verdict_announcement_service as vas
from leaguebot.results.services.attendance_hook import AttendanceAfterReview
from leaguebot.results.services.result_submission_service import (
    _build_penalty_review_state,
    send_appeals_prompt,
)
from leaguebot.results.services.results_post_service import _delete_posting
from leaguebot.results.services.review_posting import _channel, _league_guild

log = logging.getLogger(__name__)

POST_APPEALS_PROMPT = "post_appeals_prompt"
ANNOUNCE_HEADING = "announce_heading"
ANNOUNCE_VERDICT = "announce_verdict"
ATTENDANCE_SHEET = "attendance_sheet"
PLAN_SANCTIONS = "plan_sanctions"
APPLY_SANCTION = "apply_sanction"
ANNOUNCE_SANCTION = "announce_sanction"
REFRESH_LINEUP = "refresh_lineup"

_SYNC = "Run `/attendance sync` to finish it."


def verdict_steps(
    now: Callable[[], datetime], attendance: AttendanceAfterReview
) -> dict[str, Step]:
    """The jobs of this module, keyed by their names, for a change type's `steps`. *now* is the
    queue's clock, which stamps the heading's record; *attendance* the hook the builder handed
    the change type, which the jobs use and never look up on the bot."""

    def bound(fn: Any) -> Any:
        async def run(ctx: StepContext) -> Any:
            return await fn(ctx, attendance)

        return run


    async def record_heading(db: Any, ctx: StepContext, result: StepResult) -> None:
        await vas._record_banner_on(
            db, int(ctx.step_payload["round_id"]), result.result["channel_id"],
            int(result.result["message_id"]), now=now(),
        )

    async def record_verdict(db: Any, ctx: StepContext, result: StepResult) -> None:
        payload = ctx.step_payload
        await vas._record_announcement_on(
            db, payload["table"], int(payload["record"]["id"]),
            int(result.result["message_id"]), result.result["channel_id"],
        )

    return {
        POST_APPEALS_PROMPT: appeals_prompt_step(),
        ANNOUNCE_HEADING: Step(
            ANNOUNCE_HEADING, StepKind.ACT, _announce_heading, describe=_describe_heading,
            record=record_heading,
        ),
        ANNOUNCE_VERDICT: Step(
            ANNOUNCE_VERDICT, StepKind.ACT, _announce_verdict, describe=_describe_verdict,
            record=record_verdict,
        ),
        ATTENDANCE_SHEET: Step(
            ATTENDANCE_SHEET, StepKind.ACT, bound(_attendance_sheet), still_due=_after_a_sanction,
            describe=_describe_sheet,
        ),
        PLAN_SANCTIONS: Step(
            PLAN_SANCTIONS, StepKind.ACT, bound(_plan_sanctions), describe=_describe_plan,
        ),
        APPLY_SANCTION: Step(
            APPLY_SANCTION, StepKind.ACT, bound(_apply_sanction), still_due=bound(_still_owed),
            describe=_describe_apply,
        ),
        ANNOUNCE_SANCTION: Step(
            ANNOUNCE_SANCTION, StepKind.ACT, bound(_announce_sanction), still_due=_was_applied,
            describe=_describe_announce,
        ),
        REFRESH_LINEUP: Step(
            REFRESH_LINEUP, StepKind.ACT, bound(_refresh_lineup), still_due=_after_a_sanction,
            describe=_describe_lineup,
        ),
    }


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def plan_verdicts(
    round_id: int, round_number: int, table: str, records: list[dict[str, Any]]
) -> list[PlannedStep]:
    """The heading over round *round_id*'s verdicts and one verdict for each of *records*, rows
    of *table* (``penalty_records`` or ``appeal_records``); nothing where there are none, since a
    heading is posted only where a verdict follows it."""
    if not records:
        return []
    return [
        PlannedStep(ANNOUNCE_HEADING, {"round_id": round_id, "round_number": round_number}),
        *(
            PlannedStep(ANNOUNCE_VERDICT, {
                "round_id": round_id, "round_number": round_number, "table": table,
                "record": record,
            })
            for record in records
        ),
    ]


def plan_round_verdicts(
    round_id: int, round_number: int, penalties: list[dict[str, Any]],
    appeals: list[dict[str, Any]],
) -> list[PlannedStep]:
    """One heading over round *round_id*'s verdicts, then one verdict for each of its *penalties*
    (reports) and then each of its *appeals*: the run an amendment's rebuild announces again. A
    round with neither plans nothing."""
    if not penalties and not appeals:
        return []
    return [
        *plan_verdicts(round_id, round_number, "penalty_records", penalties),
        *plan_verdicts(round_id, round_number, "appeal_records", appeals)[
            1 if penalties else 0:
        ],
    ]


def plan_attendance(round_id: int, division_id: int, division_name: str) -> list[PlannedStep]:
    """The division's attendance sheet as at *round_id*, then the job that plans the sanctions the
    round's attendance owes. Callers pass the latest round the totals were carried to, which is
    where a sheet is drawn and a threshold read (decided with #238)."""
    return [
        PlannedStep(ATTENDANCE_SHEET, {
            "round_id": round_id, "division_id": division_id, "division": division_name,
            "sanctioned": [], "after_sanctions": False,
        }),
        PlannedStep(PLAN_SANCTIONS, {
            "round_id": round_id, "division_id": division_id, "division": division_name,
        }),
    ]


# ---------------------------------------------------------------------------
# What the outcome says of a job discarded
# ---------------------------------------------------------------------------


def _discarded(view: Any) -> bool:
    return "discarded" in (view.result or {})


def _hint(view: Any) -> str:
    return (view.result or {}).get("hint") or _SYNC


def _sanction_name(candidate: dict[str, Any]) -> str:
    return "autosack" if candidate["sanction"] == "AUTOSACK" else "autoreserve"


def not_done(ctx: OutcomeContext) -> list[str]:
    """One line for each job of this module a league admin discarded, in the order the jobs stood:
    what was not done, and what the manager does about it where there is something to do."""
    lines: list[str] = []
    for view in ctx.steps:
        if not _discarded(view):
            continue
        payload = view.payload
        if view.name == ANNOUNCE_HEADING:
            lines.append(
                f"⚠️ The heading over round {payload.get('round_number', '?')}'s verdicts was "
                "not posted: the verdicts stand beneath none."
            )
        elif view.name == ANNOUNCE_VERDICT:
            record = payload["record"]
            kind = "appeal" if payload["table"] == "appeal_records" else "penalty"
            lines.append(
                f"⚠️ The {kind} verdict for <@{record['driver_user_id']}> was not announced. "
                "Post it in the verdicts channel yourself."
            )
        elif view.name == POST_APPEALS_PROMPT:
            lines.append(
                "⚠️ The appeals review prompt was not posted. It is being posted again."
            )
        elif view.name == ATTENDANCE_SHEET:
            lines.append(
                f"⚠️ The attendance sheet of {payload.get('division', 'the division')} was not "
                f"posted. {_hint(view)}"
            )
        elif view.name == APPLY_SANCTION:
            candidate = payload["candidate"]
            lines.append(
                f"⚠️ The {_sanction_name(candidate)} of <@{candidate['driver_user_id']}> was not "
                f"applied. {_hint(view)}"
            )
        elif view.name == ANNOUNCE_SANCTION:
            candidate = payload["candidate"]
            lines.append(
                f"⚠️ The {_sanction_name(candidate)} of <@{candidate['driver_user_id']}> was "
                "applied but not announced. Post its card in the verdicts channel yourself."
            )
        elif view.name == REFRESH_LINEUP:
            lines.append(
                f"⚠️ The lineup of {payload.get('division', 'the division')} was not posted. It "
                "is posted with the next change to that division's drivers."
            )
    return lines


# ---------------------------------------------------------------------------
# What a job reads when it runs
# ---------------------------------------------------------------------------


async def _failed(
    hook: AttendanceAfterReview, division_id: int, round_id: int, error: Exception
) -> Exception:
    """*error* as the failure a job stops the queue with, carrying on its kept result the line
    telling the manager how to finish the job should it be discarded. A fault in the bot's own
    reach of the league's server stands as it is."""
    if isinstance(error, GuildUnavailable):
        return error
    try:
        hint = await hook.sync_hint(division_id, round_id)
    except Exception:  # noqa: BLE001 — the hint is a courtesy to the failure it rides on
        log.exception("could not form the attendance sync hint for division %s", division_id)
        hint = _SYNC
    kept = {**(getattr(error, "result", None) or {}), "hint": hint}
    failure = StepFailedOnDiscord(str(error) or type(error).__name__, result=kept)
    if not isinstance(error, StepFailedOnDiscord):
        # An unexpected fault stays the cause, which the stop notice names by its type. A failure
        # the hook already worded is not wrapped, so the notice carries its reason: what to repair.
        failure.__cause__ = error
    return failure


async def _channel_of(ctx: StepContext, round_id: int) -> tuple[Any, dict[str, Any]]:
    """The round's verdicts channel and its context, or the failure naming what to repair."""
    context = await vas._get_announcement_context(ctx.db_path, round_id)
    if not context:
        raise StepFailedOnDiscord(f"round {round_id} could not be read")
    raw = context.get("penalty_channel_id")
    if raw is None:
        raise StepFailedOnDiscord(f"{context['division_name']} has no verdicts channel set")
    channel = ctx.bot.get_channel(int(raw))
    if channel is None:
        raise StepFailedOnDiscord(
            f"{context['division_name']}'s verdicts channel (id {int(raw)}) is not in the server"
        )
    return channel, context


async def _take_down_kept(channel: Any, ctx: StepContext) -> None:
    """Remove the message the job's last try sent and could not save the id of, so that a retry
    leaves no second copy."""
    message_id = (ctx.kept or {}).get("message_id")
    if message_id is None:
        return
    text_channel = as_text_channel(channel)
    if text_channel is None:
        return
    left = await _delete_posting(
        text_channel, int(message_id), [int(message_id)], label="verdict", failures=[]
    )
    if left:
        raise StepFailedOnDiscord(
            "the message the earlier try sent could not be removed",
            result={"message_id": int(message_id)},
        )


# ---------------------------------------------------------------------------
# The appeals prompt
# ---------------------------------------------------------------------------


def appeals_prompt_step() -> Step:
    """The `post_appeals_prompt` job on its own, for a change type whose whole business is the
    prompt (`results.appeals.open`) as well as for those that open the appeals stage after their
    verdicts. It is planned with a payload naming the ``round_id`` and ``division_id``."""

    async def record(db: Any, ctx: StepContext, result: StepResult) -> None:
        await db.execute(
            "UPDATE round_submission_channels SET appeals_prompt_message_id = ? "
            "WHERE round_id = ?",
            (int(result.result["message_id"]), int(ctx.step_payload["round_id"])),
        )

    return Step(
        POST_APPEALS_PROMPT, StepKind.ACT, _post_appeals_prompt,
        describe=_describe_appeals_prompt, record=record,
    )




async def _submission_channel_id(db_path: str, round_id: int) -> int | None:
    async with get_connection(db_path) as db:
        row = await (
            await db.execute(
                "SELECT channel_id FROM round_submission_channels WHERE round_id = ?",
                (round_id,),
            )
        ).fetchone()
    return None if row is None or row["channel_id"] is None else int(row["channel_id"])


async def _describe_appeals_prompt(ctx: StepContext) -> str:
    channel_id = await _submission_channel_id(ctx.db_path, int(ctx.step_payload["round_id"]))
    where = "" if channel_id is None else f" in <#{channel_id}>"
    return f"posting the appeals review prompt{where}"


async def _post_appeals_prompt(ctx: StepContext) -> StepResult:
    """Open the appeals stage in the channel the report stage ran in. Raises where it cannot."""
    payload = ctx.step_payload
    round_id, division_id = int(payload["round_id"]), int(payload["division_id"])
    channel_id = await _submission_channel_id(ctx.db_path, round_id)
    if channel_id is None:
        raise LookupError(f"round {round_id} has no submission channel to post the prompt in")
    guild = await _league_guild(ctx.bot)
    channel = _channel(guild, channel_id, "submission")
    # A try that posted the prompt and failed to save its id left the message standing.
    kept = (ctx.kept or {}).get("message_id")
    if kept is not None:
        await _delete_posting(channel, int(kept), [int(kept)], label="appeals review prompt",
                              failures=[])
    state = await _build_penalty_review_state(ctx.bot, round_id, division_id, channel.id)
    message = await send_appeals_prompt(ctx.bot, channel, state)
    return StepResult(result={"message_id": message.id, "channel_id": channel.id})


# ---------------------------------------------------------------------------
# The heading and the verdicts
# ---------------------------------------------------------------------------


async def _describe_heading(ctx: StepContext) -> str:
    context = await vas._get_announcement_context(ctx.db_path, int(ctx.step_payload["round_id"]))
    raw = context.get("penalty_channel_id") if context else None
    where = "" if raw is None else f" in <#{raw}>"
    return f"posting the heading over round {ctx.step_payload.get('round_number', '?')}'s verdicts{where}"


async def _announce_heading(ctx: StepContext) -> StepResult:
    round_id = int(ctx.step_payload["round_id"])
    channel, context = await _channel_of(ctx, round_id)
    await _take_down_kept(channel, ctx)
    drawing = image_verdict_banner_post.build_drawing(
        season_number=context.get("season_number"),
        division_name=context["division_name"],
        division_tier=context.get("division_tier"),
        round_number=context.get("round_number"),
        race_name=context.get("race_name"),
        country_name=context.get("country_name"),
    )
    message = await image_verdict_banner_post.try_post(
        ctx.bot, channel, drawing, as_text=ctx.tries > 0
    )
    if message is None:
        raise StepFailedOnDiscord("the heading over the verdicts could not be posted")
    return StepResult(result={"message_id": message.id, "channel_id": channel.id})


async def _describe_verdict(ctx: StepContext) -> str:
    payload = ctx.step_payload
    context = await vas._get_announcement_context(ctx.db_path, int(payload["round_id"]))
    raw = context.get("penalty_channel_id") if context else None
    where = "" if raw is None else f" in <#{raw}>"
    kind = "appeal" if payload["table"] == "appeal_records" else "penalty"
    return f"announcing the {kind} verdict for <@{payload['record']['driver_user_id']}>{where}"


async def _announce_verdict(ctx: StepContext) -> StepResult:
    payload = ctx.step_payload
    round_id = int(payload["round_id"])
    channel, _context = await _channel_of(ctx, round_id)
    await _take_down_kept(channel, ctx)
    message_id, channel_id = await vas.announce_verdict(
        ctx.bot, ctx.db_path, round_id, payload["table"], payload["record"],
        as_text=ctx.tries > 0,
    )
    return StepResult(result={"message_id": message_id, "channel_id": channel_id})


# ---------------------------------------------------------------------------
# The attendance sheet and the sanctions
# ---------------------------------------------------------------------------


def _applied(ctx: StepContext) -> list[dict[str, Any]]:
    """The sanctions this change's jobs applied: each `apply_sanction` job that ran to the end,
    neither dropped nor discarded."""
    return [
        view.payload["candidate"]
        for view in ctx.steps
        if view.name == APPLY_SANCTION and view.done
        and not (view.result or {}).get("dropped") and not _discarded(view)
    ]


async def _after_a_sanction(ctx: StepContext) -> bool:
    """The lineup and the sheets drawn after the sanctions are due only where a sanction was
    applied; the sheet the approval posts first is not one of them."""
    if ctx.step_name == ATTENDANCE_SHEET and not ctx.step_payload.get("after_sanctions"):
        return True
    return bool(_applied(ctx))


async def _describe_sheet(ctx: StepContext) -> str:
    return f"posting the attendance sheet of {ctx.step_payload.get('division', 'the division')}"


async def _attendance_sheet(ctx: StepContext, hook: AttendanceAfterReview) -> StepResult:
    payload = ctx.step_payload
    try:
        await hook.post_sheet(
            int(payload["round_id"]), int(payload["division_id"]),
            sanctioned={int(p) for p in payload.get("sanctioned", [])}, as_text=ctx.tries > 0,
        )
    except Exception as error:
        raise await _failed(hook, int(payload["division_id"]), int(payload["round_id"]), error)
    return StepResult()


async def _division_name(db_path: str, division_id: int) -> str:
    async with get_connection(db_path) as db:
        row = await (
            await db.execute("SELECT name FROM divisions WHERE id = ?", (division_id,))
        ).fetchone()
    return "the division" if row is None else str(row["name"])


async def _describe_plan(ctx: StepContext) -> str:
    return (
        f"working out which drivers of {ctx.step_payload.get('division', 'the division')} are "
        "owed an attendance sanction"
    )


async def _plan_sanctions(ctx: StepContext, hook: AttendanceAfterReview) -> StepResult:
    payload = ctx.step_payload
    round_id, division_id = int(payload["round_id"]), int(payload["division_id"])
    division = payload.get("division", "the division")
    owed = await hook.sanction_candidates(round_id, division_id)
    planned: list[PlannedStep] = []
    for candidate in owed:
        job = {"round_id": round_id, "division_id": division_id, "division": division,
               "candidate": candidate}
        planned.append(PlannedStep(APPLY_SANCTION, job))
        planned.append(PlannedStep(ANNOUNCE_SANCTION, job))
    if owed:
        planned.append(PlannedStep(REFRESH_LINEUP, {"division_id": division_id, "division": division}))
        sheets: dict[int, set[int]] = {division_id: {int(c["driver_profile_id"]) for c in owed}}
        for candidate in owed:
            for other in candidate.get("other_divisions", []):
                sheets.setdefault(int(other), set()).add(int(candidate["driver_profile_id"]))
        for sheet_division in [division_id, *sorted(d for d in sheets if d != division_id)]:
            planned.append(PlannedStep(ATTENDANCE_SHEET, {
                "round_id": round_id, "division_id": sheet_division,
                "division": division if sheet_division == division_id
                else await _division_name(ctx.db_path, sheet_division),
                "sanctioned": sorted(sheets[sheet_division]), "after_sanctions": True,
            }))
    return StepResult(result={"owed": len(owed)}, then=tuple(planned))


def _same_driver(view: Any, payload: dict[str, Any]) -> bool:
    return (
        view.payload.get("candidate", {}).get("driver_profile_id")
        == payload["candidate"]["driver_profile_id"]
    )


async def _still_owed(ctx: StepContext, hook: AttendanceAfterReview) -> bool:
    """The driver is still over the threshold and not yet sanctioned, so a try applies only what
    is owed and a driver `/attendance sync` sanctioned meanwhile is not sanctioned twice."""
    payload = ctx.step_payload
    owed = await hook.sanction_candidates(int(payload["round_id"]), int(payload["division_id"]))
    return any(c["driver_profile_id"] == payload["candidate"]["driver_profile_id"] for c in owed)


async def _was_applied(ctx: StepContext) -> bool:
    """The announcement is due only where this change's own job applied the sanction."""
    return any(
        view.name == APPLY_SANCTION and _same_driver(view, ctx.step_payload) and view.done
        and not (view.result or {}).get("dropped") and not _discarded(view)
        for view in ctx.steps
    )


async def _describe_apply(ctx: StepContext) -> str:
    candidate = ctx.step_payload["candidate"]
    return (
        f"applying the {_sanction_name(candidate)} of <@{candidate['driver_user_id']}> in "
        f"{ctx.step_payload.get('division', 'the division')}"
    )


async def _apply_sanction(ctx: StepContext, hook: AttendanceAfterReview) -> StepResult:
    payload = ctx.step_payload
    try:
        line = await hook.apply_sanction(
            int(payload["round_id"]), int(payload["division_id"]), payload["candidate"], None
        )
    except Exception as error:
        raise await _failed(hook, int(payload["division_id"]), int(payload["round_id"]), error)
    return StepResult(lines=(line,) if line else ())


async def _describe_announce(ctx: StepContext) -> str:
    candidate = ctx.step_payload["candidate"]
    return (
        f"announcing the {_sanction_name(candidate)} of <@{candidate['driver_user_id']}> in the "
        "verdicts channel"
    )


async def _announce_sanction(ctx: StepContext, hook: AttendanceAfterReview) -> StepResult:
    payload = ctx.step_payload
    await hook.announce_sanction(
        int(payload["round_id"]), int(payload["division_id"]), payload["candidate"],
        as_text=ctx.tries > 0,
    )
    return StepResult()


async def _describe_lineup(ctx: StepContext) -> str:
    return f"posting the lineup of {ctx.step_payload.get('division', 'the division')} afresh"


async def _refresh_lineup(ctx: StepContext, hook: AttendanceAfterReview) -> StepResult:
    await hook.refresh_lineup(int(ctx.step_payload["division_id"]))
    return StepResult()


__all__ = [
    "ANNOUNCE_HEADING", "ANNOUNCE_SANCTION", "ANNOUNCE_VERDICT", "APPLY_SANCTION",
    "ATTENDANCE_SHEET", "PLAN_SANCTIONS", "POST_APPEALS_PROMPT", "REFRESH_LINEUP", "appeals_prompt_step",
    "not_done", "plan_attendance",
    "plan_round_verdicts", "plan_verdicts", "verdict_steps",
]
