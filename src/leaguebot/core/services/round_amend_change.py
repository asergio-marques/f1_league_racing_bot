"""Amending a round on the change queue (#439, slice 4b, amendment A).

`/round amend` offers its confirmation as it always has. Confirm reads the round for its number and
asks the queue for this change, which is judged when it is asked and again when it comes up to run,
in the words Confirm used: a round that has gone, a window that has passed or results entered
while the amendment waited refuse it with the same reply. It imports nothing from a module and no
cog: what it needs of weather, attendance and the formatting of the round list reaches it through
the `AmendHooks` the builder fills, and it reads the season through the service it is handed.

**The refusal because a cancellation is in hand is made only as the change is asked for**
(`CheckContext.change_id` is None), through `cancellation_changes.cancellation_holding_amendment`.
Run, it cannot fire: whatever was ahead of the amendment on the queue has finished, and a
cancellation queued behind it must not make the amendment refuse itself. Two requests asked in the
same instant can both pass; the queue then runs them in order and the second reads what the first
wrote, a renumbering included.

**The same steps arm a round again after a refused cancellation** (`round_rearm_change`, #439): a
cancellation refused at its save has already removed the timed work of its rounds, and asks one
`season.round.rearm` for each round that is still to be run or awaiting its results, which judges
the round as it stands when it runs and arms it as an amended round is armed, catching up what fell
due meanwhile. For a round already raced it arms the next-day clean-ups alone (the payload's
`cleanups_only`). Neither of its two jobs can be discarded.

`amendment_in_hand` reads the queue for an amendment of a division, so that a cancellation is
refused while one is waiting, running or stopped. It is read from the queue, never from memory
(architecture.md, "How a change is carried out").
"""
from __future__ import annotations

import dataclasses
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    GuildUnavailable,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
    forecasts_named,
)
from leaguebot.core.models.round import (
    ROUND_CANCELLABLE,
    ROUND_RACED_AWAITING_VERDICTS,
    Round,
    RoundFormat,
    RoundStatus,
)
from leaguebot.core.services.amendment_rules_service import (
    AmendmentVerdict,
    judge_amendment,
    status_refusal,
)
from leaguebot.core.services.approval_window_service import AttendanceWindows, WeatherWindows
from leaguebot.core.services import cancellation_notice_service as notices
from leaguebot.core.services.cancellation_changes import ROUND_REARM, cancellation_holding_amendment
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
    StepView,
    in_hand,
)
from leaguebot.core.services.scheduler_service import POST_RACE_CLEANUP_DELAY
from leaguebot.core.services.season_service import renumber_rounds_on, round_called
from leaguebot.core.utils.batch_notice import send_notice
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.log_lines import refusal_line, reply_reason

if TYPE_CHECKING:
    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.scheduler_service import SchedulerService
    from leaguebot.core.services.season_service import SeasonService

__all__ = [
    "AmendHooks",
    "ROUND_AMEND",
    "amendment_in_hand",
    "payload_changes",
    "round_amend_change",
    "round_rearm_change",
]

ROUND_AMEND = "season.round.amend"

#: The job names, as the stop notice, the tests and `StepView` know them.
JUDGE = "judge"
UNARM = "unarm"
APPLY = "apply"
ARM = "arm"
CLOSE = "close"
TAKE_DOWN_CALL = "take_down_call"
POST_CALL = "post_call"
DELETE_FORECAST = "delete_forecast"
#: Weather's own job, which the amendment plans a clean-up through under its own name.
PLAN_FORECAST_DELETES = "plan_forecast_deletes"
DRAW_FORECAST = "draw_forecast"
NOTIFY_INVALIDATION = "notify_invalidation"
RERUN_PHASE = "rerun_phase"
#: Attendance's own jobs of a check-in deadline, which the amendment plans as the deadline's
#: timer would, under the names attendance gives them (core names a kind's jobs as strings).
DISTRIBUTE = "distribute"
CLOSE_WITHOUT_CALL = "close_without_call"
LOCK_CALL = "lock_call"
ANNOUNCE = "announce"
CLEAN_UP_FORECAST = "clean_up_forecast"
CLEAN_UP_CHECK_IN = "clean_up_check_in"
#: Attendance's own jobs of taking a check-in down, under the names attendance gives them.
PLAN_CLEAN_UP = "plan"
DELETE_MESSAGE = "delete_message"
MARK_CLEARED = "mark_cleared"

AMENDED = "✅ Round amended successfully."
NOTHING_AMENDED = (
    "Nothing was amended: round {number} in **{division}** stands as it was. Run "
    "`/round amend` again."
)
SAVE_DISCARDED = (
    "Nothing was amended: round {number} in **{division}** stands as it was, its timed work "
    "armed again. Run `/round amend` again."
)

#: How a field of a round is named in the success line, where it differs from its column.
_PARAMETER_OF = {"track_name": "track"}

_EMPTY = StepView(name="", payload={}, result=None, done=False)

ROUND_GONE = "⛔ That round no longer exists. **Nothing has been changed.**"
_NOTHING_CHANGED = "**Nothing has been changed.** Run `/round amend` again to start over."


def no_longer_amendable(reasons: Iterable[str]) -> str:
    """The refusal of an amendment the rules no longer allow, as Confirm has always said it."""
    return (
        "⛔ This round can no longer be amended:\n"
        + "\n".join(f"• {reason}" for reason in reasons)
        + f"\n\n{_NOTHING_CHANGED}"
    )


@dataclass(frozen=True)
class AmendHooks:
    """What the amendment needs of weather, attendance and the formatting of the round list.

    The builder fills it, so that this module imports no module (architecture.md, "How modules and
    core fit together"). *windows* gives the lead times the amendment is judged against, as
    `LeagueBot.amendment_windows` does: attendance's only where it is on, weather's always.
    *withdraw_phases_on*, *reopen_check_in_on* write on the connection a save hands them and commit
    nothing. *forecast_steps* are weather's jobs (its draw, its post, the deletion of a forecast
    and the planning of deletions), which the amendment runs as jobs of its own: *plan_phase* plans
    the draw of a phase now, the draw planning its post and the post the deletion of the forecast
    it supersedes, and *forecast_failure* names what a discarded one of them leaves. *call_steps*
    are attendance's own jobs of a check-in call (its post), which the amendment runs as a job of
    its own, the call posted and recorded in one save by the job that posts it. A call posted
    again beside one a discarded take-down left standing replaces its record, and leaves it
    standing where the new one fails. *give_up_text* gives the line reporting a call whose deadline passed before it could be
    posted, or None where there is nothing to give up (a call standing, results in, the check-in
    over), and *give_up_on* closes the round's check-in on the connection a save hands it, as the
    start-up recovery does, clearing in the same save any check-in answers left for the round and
    the placements made on them, and says whether it did; it raises where the close cannot be
    saved, so that the queue stops rather than report a give-up never made.
    *deadline_steps* are attendance's own jobs of a check-in deadline (placing the reserves, taking
    the buttons off the call, posting the announcement) and *clean_up_steps* its jobs of taking a
    check-in down (the plan of the deletions, each deletion, the mark that it is over), which the
    amendment plans as the timers would for a moment that passed while it stood stopped, so that
    Discord refusing one stops the queue (its forecast, whichever of Phases 1 to 3 stands, is
    deleted by a job planned through *forecast_steps*). *round_list* formats
    the division's rounds for the reply.
    """

    windows: Callable[[], Awaitable[tuple[AttendanceWindows | None, WeatherWindows]]]
    withdraw_phases_on: Callable[[aiosqlite.Connection, int, Iterable[int]], Awaitable[None]]
    forecast_steps: Mapping[str, Step]
    plan_phase: Callable[[int, int], tuple[PlannedStep, ...]]
    forecast_failure: Callable[[StepView], tuple[str, str] | None]
    invalidation_text: Callable[[str], str]
    reopen_check_in_on: Callable[[aiosqlite.Connection, int], Awaitable[None]]
    call_steps: Mapping[str, Step]
    give_up_text: Callable[[str, int], Awaitable[str | None]]
    give_up_on: Callable[[aiosqlite.Connection, int], Awaitable[bool]]
    deadline_steps: Mapping[str, Step]
    clean_up_steps: Mapping[str, Step]
    round_list: Callable[[list[Round]], str]
    check_in_deletes: Callable[[int, int], Awaitable[bool]]
    forget_check_in_on: Callable[[aiosqlite.Connection, int, int], Awaitable[None]]


def payload_changes(amendments: Iterable[tuple[str, object]]) -> list[list[str]]:
    """The amendments a member confirmed, as the change's payload holds them: ``[field, value]``,
    the moment as ISO text and the format as its value."""
    changes: list[list[str]] = []
    for field, value in amendments:
        if isinstance(value, datetime):
            text = value.isoformat()
        elif isinstance(value, RoundFormat):
            text = value.value
        else:
            text = str(value)
        changes.append([field, text])
    return changes


def amended_values(changes: Iterable[Sequence[str]]) -> dict[str, Any]:
    """The payload's changes as the rules judge them: the moment a datetime, the format a
    `RoundFormat`."""
    values: dict[str, Any] = {}
    for field, text in changes:
        if field == "scheduled_at":
            values[field] = datetime.fromisoformat(text)
        elif field == "format":
            values[field] = RoundFormat(text)
        else:
            values[field] = text
    return values


async def amendment_in_hand(db_path: str, division_id: int) -> int | None:
    """The first job of an amendment of a round of *division_id* that is queued, running or
    stopped on a failure, 0 where only its close is left, or None where none is in hand."""
    for payload, job in await in_hand(db_path, (ROUND_AMEND,)):
        if payload.get("division_id") == division_id:
            return job or 0
    return None


def round_amend_change(
    *,
    modules: "ModuleService",
    seasons: "SeasonService",
    scheduler: "SchedulerService",
    hooks: AmendHooks,
    now: Callable[[], datetime],
) -> ChangeType:
    """The change that amends a round; see the module.

    The payload is ``{"round_id", "round_number", "division_id", "division_name", "changes"}``,
    *changes* being ``[[field, value]]`` (`payload_changes`). It names no season: a payload that
    does is read as holding every division of that season (`review_changes.division_job_in_hand`).
    The builder hands in the *modules* (each post's `still_due`), the *seasons* service, the
    *scheduler*, the *hooks* of the other modules and the queue's clock *now*.
    """
    return _round_change(
        rearm=False, modules=modules, seasons=seasons, scheduler=scheduler, hooks=hooks, now=now
    )


def round_rearm_change(
    *,
    modules: "ModuleService",
    seasons: "SeasonService",
    scheduler: "SchedulerService",
    hooks: AmendHooks,
    now: Callable[[], datetime],
) -> ChangeType:
    """The change that arms a round's timed work again after a refused cancellation; see the module.

    The payload is ``{"round_id", "round_number", "division_id", "division_name", "command"}``,
    *command* naming the cancellation that was refused, and ``"cleanups_only": True`` for a round
    already raced, whose next-day clean-ups alone are armed. It opens with the amendment's own
    judging and arming, unchanged but for when they are due and that neither can be discarded.
    The bot asks it, on the league's lane, one for each round.
    """
    return _round_change(
        rearm=True, modules=modules, seasons=seasons, scheduler=scheduler, hooks=hooks, now=now
    )


def _round_change(
    *,
    rearm: bool,
    modules: "ModuleService",
    seasons: "SeasonService",
    scheduler: "SchedulerService",
    hooks: AmendHooks,
    now: Callable[[], datetime],
) -> ChangeType:
    """The steps of `round_amend_change`, and with *rearm* the change that arms a round again.

    Only the check, the due-ness of the judging and the arming, their refusal to be discarded, the
    change's kind, key and words differ between the two; every other step is the amendment's.
    """

    async def rearm_check(ctx: CheckContext) -> Verdict:
        """A re-arming is due while the round can still be cancelled, or, for its clean-ups alone,
        while it is raced and they are still owed; a round gone or past that is dropped."""
        if await rearm_due(ctx):
            return Verdict.go()
        return Verdict.not_due("the round can no longer be armed again")

    async def rearm_due(ctx: StepContext | CheckContext) -> bool:
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        if rnd is None:
            return False
        if not ctx.payload.get("cleanups_only"):
            return rnd.status in ROUND_CANCELLABLE
        if rnd.status not in ROUND_RACED_AWAITING_VERDICTS and rnd.status != RoundStatus.FINAL.value:
            return False
        return await cleanups_owed(ctx.db_path, rnd)

    async def cleanups_owed(db_path: str, rnd: Round) -> bool:
        """Whether a raced round still owes its clean-ups: the day after it not yet come, or a
        forecast or a check-in call still standing that they would take down."""
        if moment_of(rnd) + POST_RACE_CLEANUP_DELAY > now():
            return True
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT EXISTS (SELECT 1 FROM forecast_messages f WHERE f.round_id = r.id "
                "AND f.phase_number IN (1, 2, 3)) AS forecast, "
                "(r.checkin_cleared = 0 AND EXISTS (SELECT 1 FROM rsvp_embed_messages m "
                "WHERE m.round_id = r.id)) AS call FROM rounds r WHERE r.id = ?",
                (rnd.id,),
            )
            found = await cursor.fetchone()
        return found is not None and bool(found["forecast"] or found["call"])

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        rnd = await seasons.get_round(int(payload["round_id"]))
        if rnd is None:
            return Verdict.refuse(ROUND_GONE)
        attendance, weather = await hooks.windows()
        verdict = judge_amendment(
            rnd,
            amended_values(payload["changes"]),
            now=now(),
            attendance=attendance,
            weather=weather,
        )
        if not verdict.allowed:
            return Verdict.refuse(
                no_longer_amendable(verdict.refusals),
                reason="it can no longer be amended:\n" + "\n".join(verdict.refusals),
            )
        if ctx.change_id is None:
            held = await cancellation_holding_amendment(ctx.db_path, rnd)
            if held is not None:
                return Verdict.refuse(held)
        return Verdict.go()

    # ── Reading the change as its jobs have left it ─────────────────────────────

    def view(ctx: OutcomeContext, name: str) -> StepView:
        return next((each for each in reversed(ctx.steps) if each.name == name), _EMPTY)

    def discarded(each: StepView) -> bool:
        return "discarded" in (each.result or {})

    def judged(ctx: OutcomeContext) -> dict[str, Any] | None:
        """What `judge` kept, or None where it was discarded or has not run."""
        result = view(ctx, JUDGE).result
        return result if result and result.get("judged") else None

    def unarmed(ctx: OutcomeContext) -> bool:
        result = view(ctx, UNARM).result
        return bool(result and result.get("unarmed"))

    def saved(ctx: OutcomeContext) -> bool:
        result = view(ctx, APPLY).result
        return bool(result and result.get("saved"))

    async def round_number_now(ctx: OutcomeContext) -> Any:
        """The number the round bears now, the press's where it cannot be read."""
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        return ctx.payload["round_number"] if rnd is None else rnd.round_number

    def named(text: str) -> Callable[[StepContext], Awaitable[str]]:
        async def describe(ctx: StepContext) -> str:
            return text.format(
                number=await round_number_now(ctx), division=ctx.payload["division_name"]
            )

        return describe

    # ── The jobs ────────────────────────────────────────────────────────────────

    async def judge(ctx: StepContext) -> StepResult:
        """Keep the windows the later jobs judge the amendment by, the round being still there.

        It writes nothing and refuses nothing. The windows are read here, outside any save,
        because a save reads no other connection. The amendment is judged when it is asked and
        again in the save, against what the save reads at the queue's clock (`judged_again`), so
        that its refusal is spoken once, at the save's own moment, however long a stop between
        them lasts. What the amendment withdraws and what becomes of the check-in call are not
        kept here, nor the phases to draw at once: the save plans the first from what it reads
        when it runs and the arming the rest (owner, 2026-10-09)."""
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        if rnd is None:
            raise LookupError(f"round {ctx.payload['round_id']} is no longer there")
        attendance, weather = await hooks.windows()
        return StepResult(
            result={
                "judged": True,
                "windows": {
                    "attendance": dataclasses.asdict(attendance) if attendance else None,
                    "weather": dataclasses.asdict(weather),
                },
            }
        )

    async def judge_went_through(ctx: StepContext) -> bool:
        return judged(ctx) is not None

    async def unarm(ctx: StepContext) -> StepResult:
        scheduler.cancel_round(int(ctx.payload["round_id"]))
        return StepResult(result={"unarmed": True})

    async def apply_due(ctx: StepContext) -> bool:
        """The save is due only where the judgement and the removal of the timed work went
        through, not where either was discarded."""
        return judged(ctx) is not None and unarmed(ctx)

    def judged_again(
        row: aiosqlite.Row, changes: Iterable[Sequence[str]], judgement: dict[str, Any]
    ) -> AmendmentVerdict:
        """*changes* judged once more, against the round as the save reads it, at the windows
        `judge` kept and at the queue's clock.

        `judge` reached its verdict when it ran, and a job after it can stop and be tried again
        hours later: the save would otherwise write an amendment whose window has since passed,
        or plan what follows it from windows that have since gone by."""
        windows = judgement["windows"]
        attendance = windows["attendance"]
        rnd = Round(
            id=int(row["id"]),
            division_id=int(row["division_id"]),
            round_number=int(row["round_number"]),
            format=RoundFormat(row["format"]),
            track_name=row["track_name"],
            scheduled_at=datetime.fromisoformat(row["scheduled_at"]),
            phase1_done=bool(row["phase1_done"]),
            phase2_done=bool(row["phase2_done"]),
            phase3_done=bool(row["phase3_done"]),
            status=row["status"],
        )
        return judge_amendment(
            rnd,
            amended_values(changes),
            now=now(),
            attendance=AttendanceWindows(**attendance) if attendance is not None else None,
            weather=WeatherWindows(**windows["weather"]),
        )

    async def apply(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The one save: the audit, the round's fields, what the amendment withdraws, the
        division's renumbering and the success line, reading nothing but the connection.

        It judges the amendment again before it writes (`judged_again`), and refuses, writing
        nothing, where the round's status or that judgement says it may no longer be made. What
        follows the save is planned from that judgement, made when the save runs, and kept with
        it for the arming to read: the forecasts it withdraws (`withdrawn`), those of them that
        were posted (`posted`, by the flags it reads), whether it reopens the check-in
        (`reopened`) and whether a call stood (`called`). Planned from `judge`'s instead,
        a save tried again after a window passed would withdraw a forecast that now stands, or
        leave a call to a schedule whose moment has gone (owner, 2026-10-09)."""
        if ctx.actor_id is None or ctx.actor_name is None:
            raise RuntimeError("amending a round is the act of a member, and none is recorded")
        judgement = judged(ctx) or {}
        round_id = int(ctx.payload["round_id"])
        cursor = await db.execute(
            "SELECT id, status, round_number, division_id, track_name, format, scheduled_at, "
            "phase1_done, phase2_done, phase3_done FROM rounds WHERE id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()
        refusal = None
        verdict: AmendmentVerdict | None = None
        if row is None:
            refusal = ROUND_GONE
        elif row["status"] not in ROUND_CANCELLABLE:
            refusal = no_longer_amendable([status_refusal(row["status"])])
        else:
            verdict = judged_again(row, ctx.payload["changes"], judgement)
            if not verdict.allowed:
                refusal = no_longer_amendable(verdict.refusals)
        if row is None or verdict is None or refusal is not None:
            # A backstop: the check passed, and something wrote the database after it.
            return StepResult(
                result={"refused": refusal},
                lines=(refusal_line(ctx.named, "`/round amend`", reply_reason(refusal or "")),),
            )

        division_id = int(row["division_id"])
        stamp = now().isoformat()
        changed = ""
        for field, new in ctx.payload["changes"]:
            old = row[field]
            await db.execute(
                "INSERT INTO audit_entries (actor_id, actor_name, division_id, change_type, "
                "old_value, new_value, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ctx.actor_id, ctx.actor_name, division_id, f"round.{field}",
                    str(old) if old is not None else "", str(new), stamp,
                ),
            )
            # Each field by a statement naming its column, so that nothing is spliced in.
            if field == "track_name":
                await db.execute("UPDATE rounds SET track_name = ? WHERE id = ?", (new, round_id))
            elif field == "format":
                await db.execute("UPDATE rounds SET format = ? WHERE id = ?", (new, round_id))
            elif field == "scheduled_at":
                await db.execute(
                    "UPDATE rounds SET scheduled_at = ? WHERE id = ?", (new, round_id)
                )
            else:
                raise ValueError(f"Field {field!r} is not amendable")
            if old != new:
                changed += (
                    f"\n  {_PARAMETER_OF.get(field, field)}: "
                    f"{old if old is not None else 'none'} → {new}"
                )
        cursor = await db.execute(
            "SELECT 1 FROM rsvp_embed_messages WHERE round_id = ?", (round_id,)
        )
        called = await cursor.fetchone() is not None
        flags = {1: row["phase1_done"], 2: row["phase2_done"], 3: row["phase3_done"]}
        withdrawn = sorted(n for n, outcome in verdict.phases.items() if not outcome.stands)
        # With attendance on, an allowed amendment always leaves the check-in open: one whose
        # deadline has passed is refused above.
        reopened = bool(verdict.check_in) and not verdict.check_in_stays_closed
        await hooks.withdraw_phases_on(db, round_id, withdrawn)
        if reopened:
            await hooks.reopen_check_in_on(db, round_id)
        if any(field == "scheduled_at" for field, _ in ctx.payload["changes"]):
            await renumber_rounds_on(db, division_id)
        values = amended_values(ctx.payload["changes"])
        return StepResult(
            result={
                "saved": True,
                "round_number": row["round_number"],
                "called": called,
                "reopened": reopened,
                "withdrawn": withdrawn,
                "posted": [n for n in withdrawn if flags[n]],
                "track": values.get("track_name") or row["track_name"] or "Unknown",
            },
            lines=(
                f"{ctx.named} | /round amend | Success\n"
                f"  round {row['round_number']} (round_id: {round_id}){changed}",
            ),
        )

    def moment_of(rnd: Round) -> datetime:
        """The round's moment, a naive one read as UTC, as it is stored."""
        moment = rnd.scheduled_at
        return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)

    def first_horizon_ahead(rnd: Round, weather: dict[str, Any]) -> bool:
        """Whether the round as it stands now still has its first forecast horizon to come. Read
        when the arming runs, not when the amendment was judged: a discarded save arms the round
        at its old moment, and a stop can hold the arming for hours."""
        return now() < moment_of(rnd) - timedelta(days=weather["phase_1_days"])

    def call_fell_due(rnd: Round, attendance: dict[str, Any] | None) -> bool:
        """Whether the round's check-in call has fallen due, at the queue's clock and the windows
        `judge` read: its moment has passed. Where its deadline has passed too, the call is given
        up rather than posted (`post_call`), the boundaries the start-up recovery of a missed call
        holds to (`_recover_missed_check_in_calls`)."""
        if attendance is None:
            return False
        return moment_of(rnd) - timedelta(days=attendance["notice_days"]) <= now()

    def deadline_passed(rnd: Round, attendance: dict[str, Any]) -> bool:
        """Whether the round's check-in deadline has passed, at the queue's clock."""
        return moment_of(rnd) - timedelta(hours=attendance["deadline_hours"]) <= now()

    def phases_due(rnd: Round, weather: dict[str, Any]) -> list[int]:
        """The forecast phases to draw at once, as the round stands at the queue's clock and the
        windows `judge` read: each not yet drawn whose horizon has passed, for a round whose moment
        is still ahead and which is no mystery round, as the start-up recovery judges a missed
        phase (`_recover_missed_phases`)."""
        moment, at = moment_of(rnd), now()
        if rnd.format == RoundFormat.MYSTERY or moment <= at:
            return []
        horizons = {
            1: (rnd.phase1_done, moment - timedelta(days=weather["phase_1_days"])),
            2: (rnd.phase2_done, moment - timedelta(days=weather["phase_2_days"])),
            3: (rnd.phase3_done, moment - timedelta(hours=weather["phase_3_hours"])),
        }
        return [n for n, (done, horizon) in horizons.items() if not done and horizon <= at]

    async def arm_due(ctx: StepContext) -> bool:
        """The arming is due where the timed work was removed and the round may still be
        cancelled, whatever became of the save: a round cancelled, or whose results were entered,
        while the amendment waited or stood stopped is armed with nothing."""
        if not unarmed(ctx):
            return False
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        return rnd is not None and rnd.status in ROUND_CANCELLABLE

    async def arm(ctx: StepContext) -> StepResult:
        """Arm the round's timed work as the round stands now, at the windows `judge` read, and
        catch up what fell due while it stood removed, as a restart does (owner, 2026-10-09).

        The amendment can stand stopped for hours, at the save or before it, and a discard or a
        refusal arms the round again at its old moment: a timer armed for a moment already past
        is skipped by the scheduler. So, judged at the queue's clock: a round still to be run
        whose moment has passed has its results submission run at once through the scheduler,
        its phases due are drawn at once (`phases_due`), and its call, where none stands and its
        check-in is not over, is posted where it has fallen due (`call_fell_due`).

        Under test mode the results submission is not run at once: `/test-mode advance` runs a
        test season's rounds in their turn (owner, 2026-10-09: "Leave it to /test-mode
        advance"). Its call is still posted (`post_call`)."""
        judgement = judged(ctx) or {}
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        if rnd is None:
            raise LookupError(f"round {ctx.payload['round_id']} is no longer there")
        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute(
                "SELECT d.tier, s.season_number, r.checkin_cleared, EXISTS ("
                "SELECT 1 FROM rsvp_embed_messages m WHERE m.round_id = r.id) AS called, EXISTS ("
                "SELECT 1 FROM rsvp_embed_messages m WHERE m.round_id = r.id "
                "AND m.deadline_run_at IS NULL AND m.distribution_msg_id IS NULL) AS undistributed, EXISTS ("
                "SELECT 1 FROM forecast_messages f WHERE f.round_id = r.id "
                "AND f.phase_number IN (1, 2, 3)) AS forecast_posted, ("
                "SELECT group_concat(f.phase_number) FROM forecast_messages f "
                "WHERE f.round_id = r.id AND f.phase_number IN (1, 2, 3)) AS forecast_phases, "
                "EXISTS (SELECT 1 FROM server_configs c WHERE c.test_mode_active = 1) AS test_mode "
                "FROM rounds r JOIN divisions d ON d.id = r.division_id "
                "JOIN seasons s ON s.id = d.season_id WHERE r.id = ?",
                (rnd.id,),
            )
            found = await cursor.fetchone()
        if found is None:
            raise LookupError(f"division {rnd.division_id} is no longer there")
        season_number, tier = int(found["season_number"]), int(found["tier"])
        weather = judgement["windows"]["weather"]
        weather_on = await modules.is_weather_enabled()
        if rearm and ctx.payload.get("cleanups_only"):
            # A round already raced: only the clean-ups of a day after it are owed, armed through
            # the scheduler and, where the day has come, run as the restart's catch-up runs them.
            check_in_on = (
                judgement["windows"]["attendance"] is not None
                and await modules.is_attendance_enabled()
            )
            if weather_on or check_in_on:
                scheduler.schedule_round_cleanups(
                    rnd, season_number=season_number, division_tier=tier,
                    forecast=weather_on, check_in=check_in_on,
                )
            return StepResult(
                result={"armed": True, "round_number": rnd.round_number},
                then=clean_ups_due(rnd, standing=dict(found), call_left=bool(found["called"])),
            )
        draw = phases_due(rnd, weather) if weather_on else []
        if weather_on and (
            rnd.format != RoundFormat.MYSTERY or first_horizon_ahead(rnd, weather)
        ):
            scheduler.schedule_round(
                rnd,
                season_number=season_number,
                division_tier=tier,
                phase_1_days=weather["phase_1_days"],
                phase_2_days=weather["phase_2_days"],
                phase_3_hours=weather["phase_3_hours"],
                # Drawn at once by a job of the amendment's: a timer for a horizon passed by
                # less than the scheduler's misfire grace would draw it a second time.
                skip_phases=frozenset(draw),
            )
        else:
            # Weather off, or a mystery round moved inside its first horizon, whose notice already
            # told the drivers and which must not be fired retroactively: its forecasts are not
            # armed, but its results submission is, or the round would never leave Not run (#133).
            scheduler.schedule_result_submission_jobs(
                [rnd], division_meta={rnd.division_id: (season_number, tier)}
            )
        attendance = judgement["windows"]["attendance"]
        if attendance is not None and await modules.is_attendance_enabled():
            scheduler.schedule_attendance_round(
                rnd,
                season_number=season_number,
                division_tier=tier,
                notice_days=attendance["notice_days"],
                last_notice_hours=attendance["last_notice_hours"],
                deadline_hours=attendance["deadline_hours"],
            )
        if (rnd.status == RoundStatus.NOT_RUN.value and moment_of(rnd) <= now()
                and not found["test_mode"]):
            # After the arming above, whose job for this moment it replaces.
            scheduler.run_result_submission_now(
                rnd, season_number=season_number, division_tier=tier
            )
        planned = posts(ctx, rnd, judgement, standing=dict(found), draw=draw)
        return StepResult(result={"armed": True, "round_number": rnd.round_number}, then=planned)

    def clean_ups_due(
        rnd: Round, *, standing: Mapping[str, Any], call_left: bool
    ) -> tuple[PlannedStep, ...]:
        """The clean-ups a day after the round, planned where that day has come: the forecast
        (whichever of Phases 1 to 3 still stands) and the standing check-in call."""
        planned: list[PlannedStep] = []
        if moment_of(rnd) + POST_RACE_CLEANUP_DELAY <= now():
            if standing["forecast_posted"]:
                phases = sorted(
                    {int(n) for n in str(standing["forecast_phases"] or "").split(",") if n}
                )
                planned.append(PlannedStep(
                    CLEAN_UP_FORECAST, {"phases": [1, 2, 3], "clean_up": True, "standing": phases}
                ))
            if call_left:
                planned.append(PlannedStep(CLEAN_UP_CHECK_IN))
        return tuple(planned)

    def posts(
        ctx: StepContext, rnd: Round, judgement: dict[str, Any], *,
        standing: Mapping[str, Any], draw: list[int],
    ) -> tuple[PlannedStep, ...]:
        """The posts the amendment owes, in today's order: the check-in call taken down and
        posted again, its deadline run, each withdrawn forecast that was posted deleted, the
        notice that the forecasts no longer stand, each phase in *draw* drawn, and the clean-ups
        due a day after the round. Planned once the round is armed, which is where the amendment
        stood before them; what the save withdraws, from what it read (`apply`), and only where it
        was saved. *standing* is what the arming read of the round's call and of its forecasts of
        Phases 1 to 3, whether one stands and which (`forecast_phases`), by which the clean-up is
        named.

        A call that stood at the save is taken down and its post planned with it, the post
        judging when it runs whether the call is due then (`call_still_due`): the take-down can
        stop on Discord and be retried after the new call's moment, which the timer armed
        meanwhile has found taken by the old call. Otherwise the call is planned only where none
        stands and the check-in is not over and it has fallen due as the round is armed, saved or
        not; one still to come is the timer's. Either way the post judges again, when it runs,
        that no call stands and the check-in is not over, since a restart or the timer can post
        one between the arming and the post.

        What fell due while the amendment stood stopped is caught up as the start-up recovery
        catches it up (`_recover_rsvp_views_and_deadlines`, `_recover_missed_cleanups`; owner,
        2026-10-09): a standing call left in place whose deadline has passed with no
        distribution recorded has its deadline run, and a day after the round its forecast
        (whichever of Phases 1 to 3 still stands) is deleted and its standing call taken down."""
        after = (view(ctx, APPLY).result or {}) if saved(ctx) else {}
        attendance = judgement["windows"]["attendance"]
        taking_down = bool(after.get("reopened") and after.get("called"))
        call_left = bool(standing["called"]) and not taking_down
        planned: list[PlannedStep] = []
        if taking_down:
            planned += [PlannedStep(TAKE_DOWN_CALL), PlannedStep(POST_CALL, {"repost": True})]
        elif (not standing["called"] and not standing["checkin_cleared"]
              and call_fell_due(rnd, attendance)):
            planned.append(PlannedStep(POST_CALL, {"repost": False}))
        if (attendance is not None and call_left and standing["undistributed"]
                and deadline_passed(rnd, attendance)):
            planned.append(PlannedStep(DISTRIBUTE))
        posted = after.get("posted", [])
        planned += [PlannedStep(DELETE_FORECAST, {"phase": n}) for n in posted]
        if posted:
            planned.append(PlannedStep(NOTIFY_INVALIDATION, {"track": after["track"]}))
        planned += [PlannedStep(RERUN_PHASE, {"phase": n}) for n in draw]
        planned += clean_ups_due(rnd, standing=standing, call_left=call_left)
        return tuple(planned)

    async def never_runs(ctx: StepContext) -> str:
        return (
            f"round {await round_number_now(ctx)} in **{ctx.payload['division_name']}** "
            "would never run"
        )

    async def would_be_left_unarmed(ctx: StepContext) -> str:
        """What dropping a re-arming's judging or arming would leave: the timed work removed for
        a cancellation that was refused, and nothing to run the round (or, for a round already
        raced, to take its call and forecasts down), so it is retried, never discarded."""
        called = await round_called(ctx.db_path, int(ctx.payload["round_id"]))
        if ctx.payload.get("cleanups_only"):
            return f"the call and forecasts of {called} would never be taken down"
        return f"{called} would be left with no timed work, never run"

    def armed_again(ctx: OutcomeContext) -> tuple[str, ...]:
        """The one line a re-arming writes to the log channel once its arming has run, with what
        a league admin discarded of its catch-up beneath it; none where it was dropped."""
        result = view(ctx, ARM).result
        if not result or not result.get("armed"):
            return ()
        called = f"round {result['round_number']} in **{ctx.payload['division_name']}**"
        command = str(ctx.payload.get("command", "/round cancel"))
        what = "next-day clean-ups" if ctx.payload.get("cleanups_only") else "timed work"
        return (
            f"⏱️ The {what} of {called} is armed again, `{command}` having been refused"
            + not_done(ctx),
        )

    async def attendance_on(_ctx: StepContext) -> bool:
        return await modules.is_attendance_enabled()

    def reposting(ctx: StepContext) -> bool:
        """Whether the call is posted again in place of one the amendment took down (its step
        payload, ``{"repost": True}``), rather than posted for the first time."""
        return bool(ctx.step_payload.get("repost"))

    def replacing(ctx: StepContext) -> bool:
        """Whether the call to post again replaces one still standing: the old call, whose
        take-down a league admin discarded. Otherwise any call standing when the post runs was
        posted after the take-down, or after the arming, by its timer, a restart or
        `/attendance post-check-in`, and is the round's live call."""
        return reposting(ctx) and discarded(view(ctx, TAKE_DOWN_CALL))

    async def call_state(db_path: str, round_id: int) -> tuple[bool, bool]:
        """Whether a call stands for the round, and whether its check-in is over
        (``checkin_cleared``), read when the job runs."""
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT r.checkin_cleared, EXISTS (SELECT 1 FROM rsvp_embed_messages m "
                "WHERE m.round_id = r.id) AS called FROM rounds r WHERE r.id = ?",
                (round_id,),
            )
            found = await cursor.fetchone()
        if found is None:
            return False, False
        return bool(found["called"]), bool(found["checkin_cleared"])

    async def call_message(db_path: str, round_id: int) -> str | None:
        """The message the round's standing call is recorded as, or None where none stands."""
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT message_id FROM rsvp_embed_messages WHERE round_id = ?", (round_id,)
            )
            found = await cursor.fetchone()
        return None if found is None else str(found["message_id"])

    async def call_still_due(ctx: StepContext) -> bool:
        """The call is posted only while attendance is on and the call has fallen due as the
        round stands when this job runs, and, as the start-up recovery of a missed call judges it
        (`_recover_missed_check_in_calls`), only where no call stands and the round's check-in
        is not over, both read then (owner, 2026-10-09). A call posted between the arming and
        this job, by its timer, a restart or `/attendance post-check-in`, is the round's live
        call, and posting again would call the division twice. The one exception is the old call
        a discarded take-down left standing, which a repost replaces (`replacing`), unless the
        check-in is over by then, as a clean-up a day after the round leaves it. A call still to
        come is posted by its timer, armed with the round; one whose deadline has passed is given
        up (`post_call`)."""
        if not await modules.is_attendance_enabled():
            return False
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        judgement = judged(ctx) or {}
        if rnd is None or not call_fell_due(rnd, judgement.get("windows", {}).get("attendance")):
            return False
        standing, cleared = await call_state(ctx.db_path, rnd.id)
        if cleared:
            return False
        return replacing(ctx) or not standing

    async def weather_on(_ctx: StepContext) -> bool:
        return await modules.is_weather_enabled()

    async def phase_still_due(ctx: StepContext) -> bool:
        """A phase is drawn at once only while weather is on and the phase is still due as the
        round stands when this job runs (`phases_due`): not drawn yet, its horizon passed, the
        race still ahead and the round no mystery round. A job before it can stop and be retried
        after the race, and a forecast for a round already raced is no forecast (owner,
        2026-10-09)."""
        if not await modules.is_weather_enabled():
            return False
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        judgement = judged(ctx) or {}
        weather = judgement.get("windows", {}).get("weather")
        if rnd is None or weather is None:
            return False
        return int(ctx.step_payload["phase"]) in phases_due(rnd, weather)

    async def take_down_call(ctx: StepContext) -> StepResult:
        """Take the round's standing check-in call down, keeping its record and stopping the queue
        where Discord will not delete a message. The check-in's audit it reads is not written.
        Whether the call is posted again is not settled here but by the next job, when it runs."""
        division = await seasons.get_division(int(ctx.payload["division_id"]))
        if division is None:
            raise LookupError(f"division {ctx.payload['division_id']} is no longer there")
        taken = await notices.take_down_call(
            ctx.bot, division, int(ctx.payload["round_id"]),
            check_in_deletes=hooks.check_in_deletes,
        )
        return StepResult(result={"taken_down": taken["taken_down"]})

    async def forget_call(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
        """Drop the taken-down call's record, in the save that marks the job done. Its answers
        stay: they are what the call posted again carries."""
        if result.result.get("taken_down"):
            await hooks.forget_check_in_on(
                db, int(ctx.payload["round_id"]), int(ctx.payload["division_id"])
            )

    async def post_call(ctx: StepContext) -> StepResult:
        """Post the call, carrying every answer already given. Discord refusing it stops the queue
        (owner, 2026-10-10, decision 3), attendance raising with what is left for the round, which
        the stop notice says (owner, 2026-10-11) and a Discard reads in `not_done`.

        The call is posted by attendance's own job (`hooks.call_steps`), which posts nothing it
        cannot record: the job's record writes the call in the save that marks the job done. A call
        posted again in place of one the amendment took down (`reposting`) carries the answers of
        the drivers still of the division, and where a discarded take-down left the old call
        standing (`replacing`) the new call's record replaces the old one's. The old call's
        messages are not deleted again: the discarded take-down names those left, for deletion by
        hand.

        Where the round's check-in deadline has passed by the time it runs, as after a stop,
        nothing is posted: the round's check-in is closed and the log channel told in the job's
        record, as the start-up recovery gives up a missed call (owner, 2026-10-09). A call is
        given up only where none stands and the check-in is not over: one standing is the round's
        live call, or the old one a discarded take-down left, and a check-in over is settled, and
        either is left as it is. Given up, any answers left to a call the amendment took down,
        and the placements made on them, are cleared in the same save, so that the log channel's
        line that the round counts nothing against anyone holds true (owner, 2026-10-09: "Clear
        the answers, keep the line true"). The call is posted under test mode too, unlike the
        start-up recovery, which leaves a test season's calls to `/test-mode advance` (owner,
        2026-10-09: "Post it anyway")."""
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        if rnd is None:
            raise LookupError(f"round {ctx.payload['round_id']} is no longer there")
        attendance = (judged(ctx) or {}).get("windows", {}).get("attendance")
        if attendance is not None and deadline_passed(rnd, attendance):
            standing, cleared = await call_state(ctx.db_path, rnd.id)
            if standing or cleared:
                return StepResult(result={"left": True})
            text = await hooks.give_up_text(ctx.db_path, rnd.id)
            if text is None:
                # Its results came in, or its check-in closed, as the give-up reads it.
                return StepResult(result={"left": True})
            return StepResult(result={"given_up": True}, lines=(text,))
        carried = dataclasses.replace(
            ctx, step_payload={**ctx.step_payload, "replacing": replacing(ctx)}
        )
        return await hooks.call_steps[POST_CALL].run(carried)

    async def record_call(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
        """The call's record, or the give-up's close, in the save that marks the job done; a call
        left as it stood is recorded as nothing."""
        if (result.result or {}).get("given_up"):
            await hooks.give_up_on(db, int(ctx.payload["round_id"]))
            return
        if not (result.result or {}).get("new"):
            return  # left as it stood: nothing posted, nothing to record
        record = hooks.call_steps[POST_CALL].record
        if record is not None:
            await record(db, ctx, result)

    async def notify_invalidation(ctx: StepContext) -> StepResult:
        """Tell the division's forecast channel that its forecasts no longer stand. A channel never
        set is done and unnamed; one set and gone, or a send Discord refuses, stops the queue."""
        division = await seasons.get_division(int(ctx.payload["division_id"]))
        if division is None:
            raise LookupError(f"division {ctx.payload['division_id']} is no longer there")
        if not division.forecast_channel_id:
            return StepResult(result={"unset": True})
        guild = await league_guild(ctx.bot)
        if guild is None:
            raise GuildUnavailable("the league's server is not in the cache")
        channel = guild.get_channel(int(division.forecast_channel_id))
        if channel is None:
            raise StepFailedOnDiscord(
                f"the channel <#{division.forecast_channel_id}> is no longer on the server"
            )
        try:
            await send_notice(channel, hooks.invalidation_text(str(ctx.step_payload["track"])))
        except discord.HTTPException as exc:
            raise StepFailedOnDiscord(
                f"the message could not be posted to <#{division.forecast_channel_id}>: {exc}"
            ) from exc
        return StepResult(result={"sent": True})

    async def rerun_phase(ctx: StepContext) -> StepResult:
        """Plan the draw of a phase whose horizon has already passed, by the amended moment or while
        the amendment stood stopped, where it is still due when the job runs (`phase_still_due`).
        The draw, its post and the deletion of the forecast it supersedes are jobs of the
        amendment's own, in that order, before the next phase's: Discord refusing the post or the
        deletion stops the queue (owner, 2026-10-10)."""
        phase = int(ctx.step_payload["phase"])
        return StepResult(
            result={"phase": phase}, then=hooks.plan_phase(int(ctx.payload["round_id"]), phase)
        )

    async def close_due(ctx: StepContext) -> bool:
        """The close is due where the amendment was saved, and where a job before the save was
        discarded, to read the number the round bears then for the reply."""
        return saved(ctx) or any(
            discarded(view(ctx, name)) for name in (JUDGE, UNARM, APPLY)
        )

    async def close(ctx: StepContext) -> StepResult:
        """Read the division's rounds for the reply, and the number the round bears now: an
        amendment ahead of this one can have renumbered the division since Confirm."""
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        rounds = [] if rnd is None else await seasons.get_division_rounds(rnd.division_id)
        return StepResult(
            result={
                "round_list": hooks.round_list(rounds) if rounds and saved(ctx) else "",
                "round_number": ctx.payload["round_number"] if rnd is None else rnd.round_number,
            }
        )

    def outcome(ctx: OutcomeContext) -> str:
        number = (view(ctx, CLOSE).result or {}).get("round_number", ctx.payload["round_number"])
        words = {"number": number, "division": ctx.payload["division_name"]}
        if discarded(view(ctx, JUDGE)) or discarded(view(ctx, UNARM)):
            return NOTHING_AMENDED.format(**words)
        # The arming runs whatever became of the save, and what it catches up can be discarded
        # too: it is named beneath the refusal or the discarded save as beneath a success
        # (owner, 2026-10-09).
        refused = (view(ctx, APPLY).result or {}).get("refused")
        if refused:
            return str(refused) + not_done(ctx)
        if discarded(view(ctx, APPLY)):
            return SAVE_DISCARDED.format(**words) + not_done(ctx)
        listed = (view(ctx, CLOSE).result or {}).get("round_list")
        return AMENDED + (f"\n\n{listed}" if listed else "") + not_done(ctx)

    def not_done(ctx: OutcomeContext) -> str:
        """What the amendment could not do, one entry for each post a league admin discarded, in
        the order of the jobs, each with what puts it right."""
        division = str(ctx.payload["division_name"])
        failures: list[notices.NoticeFailure] = []
        for each in ctx.steps:
            if not discarded(each):
                continue
            result = each.result or {}
            if each.name == TAKE_DOWN_CALL:
                ids = [str(one) for one in result.get("undeleted", [])]
                posted = view(ctx, POST_CALL).result or {}
                if posted.get("vanished"):
                    # The call itself was deleted, posting it again failed, and no call can be
                    # seen: what is left of it is named in the log channel.
                    failures.append(notices.NoticeFailure(
                        division, "check-in call",
                        "it could not be posted again and none can be seen; post it by hand "
                        "with `/attendance post-check-in` once the cause is cleared (or the "
                        "check-in channel is set), and see the log channel",
                    ))
                    continue
                if posted.get("earlier_left"):
                    # Posting the call again failed too, and the earlier call was kept: it is the
                    # round's only call, not one to delete.
                    failures.append(notices.NoticeFailure(
                        division, "check-in call",
                        "it could not be posted again, so the earlier call was kept and its "
                        "answers count; see the log channel",
                    ))
                    continue
                failures.append(notices.NoticeFailure(
                    division, "check-in call",
                    f"{len(ids)} message(s) could not be deleted and must be removed by hand "
                    f"(ids {', '.join(ids)})",
                ))
            elif each.name == POST_CALL:
                if result.get("vanished") or result.get("earlier_left"):
                    if discarded(view(ctx, TAKE_DOWN_CALL)):
                        # The discarded take-down's entry says what is left of the call.
                        continue
                if result.get("vanished"):
                    failures.append(notices.NoticeFailure(
                        division, "check-in call",
                        "it could not be posted again and none can be seen; post it by hand "
                        "with `/attendance post-check-in` once the cause is cleared (or the "
                        "check-in channel is set), and see the log channel",
                    ))
                elif result.get("earlier_left"):
                    failures.append(notices.NoticeFailure(
                        division, "check-in call",
                        "it could not be posted again, so the earlier call was kept and its "
                        "answers count; see the log channel",
                    ))
                else:
                    failures.append(notices.NoticeFailure(
                        division, "check-in call",
                        "the call could not be posted again, and a league admin discarded it; "
                        "post it with `/attendance post-check-in`",
                    ))
            elif each.name == DELETE_FORECAST:
                where = (
                    f"https://discord.com/channels/{result['guild_id']}/{result['channel_id']}/"
                    f"{result['message_id']}"
                    if "guild_id" in result and "message_id" in result
                    else f"message {result.get('message_id')} in <#{result.get('channel_id')}>"
                )
                if each.payload.get("clean_up"):
                    what = f"the Phase {result.get('phase')} forecast could not be deleted a day after the round"
                else:
                    which = "superseded" if each.payload.get("superseded") else "withdrawn"
                    what = f"the {which} Phase {result.get('phase')} forecast could not be deleted"
                failures.append(notices.NoticeFailure(
                    division, "forecast channel",
                    f"{what}, and a league admin discarded it; delete it by hand ({where})",
                ))
            elif each.name == NOTIFY_INVALIDATION:
                failures.append(notices.NoticeFailure(
                    division, "forecast channel",
                    "the notice that its forecasts no longer stand could not be posted, and a "
                    "league admin discarded it",
                ))
            elif each.name == RERUN_PHASE:
                failures.append(notices.NoticeFailure(
                    division, f"Phase {each.payload.get('phase')} forecast",
                    "not drawn, and a league admin discarded it; it is drawn when the bot next "
                    "starts, while the round is still to be run",
                ))
            elif each.name == DISTRIBUTE:
                failures.append(notices.NoticeFailure(
                    division, "check-in deadline",
                    "the reserves were not distributed, and a league admin discarded it; it is "
                    "run when the bot next starts, while the call stands",
                ))
            elif each.name == LOCK_CALL:
                failures.append(notices.NoticeFailure(
                    division, "check-in call",
                    "the buttons could not be taken off the call at its deadline, and a league "
                    "admin discarded it; the placements stand, and the call can be taken down "
                    "by hand",
                ))
            elif each.name == ANNOUNCE:
                failures.append(notices.NoticeFailure(
                    division, "reserve announcement",
                    "the reserves' announcement could not be posted, and a league admin "
                    "discarded it; the placements stand",
                ))
            elif each.name == CLEAN_UP_FORECAST:
                standing = each.payload.get("standing", ())
                several = len(set(standing)) > 1
                failures.append(notices.NoticeFailure(
                    division, "forecast channel",
                    f"{forecasts_named(standing)} {'were' if several else 'was'} not deleted a "
                    f"day after the round, and a league admin discarded it; "
                    f"{'they are' if several else 'it is'} deleted when the bot next starts",
                ))
            elif each.name == CLEAN_UP_CHECK_IN:
                failures.append(notices.NoticeFailure(
                    division, "check-in call",
                    "the check-in was not taken down a day after the round, and a league admin "
                    "discarded it; it is taken down when the bot next starts",
                ))
            elif each.name == DELETE_MESSAGE:
                # Named by the link its refused deletion kept, for an admin to open; by its
                # channel where it kept none.
                link = result.get("link") or f"in <#{each.payload.get('channel_id')}>"
                failures.append(notices.NoticeFailure(
                    division, "check-in call",
                    f"the {each.payload.get('what', 'message')} could not be deleted a day "
                    f"after the round, and a league admin discarded it; delete it by hand "
                    f"({link})",
                ))
            elif (weather := hooks.forecast_failure(each)) is not None:
                failures.append(notices.NoticeFailure(division, *weather))
        if not failures:
            return ""
        return "\n⚠️ **Not done**\n" + "\n".join(f"  • {each.describe()}" for each in failures)

    async def describe_forecast_delete(ctx: StepContext) -> str:
        if ctx.step_payload.get("clean_up"):
            return (
                f"deleting the Phase {ctx.step_payload['phase']} forecast of round "
                f"{await round_number_now(ctx)} in **{ctx.payload['division_name']}** a day "
                f"after it"
            )
        if ctx.step_payload.get("superseded"):
            return (
                f"deleting the superseded Phase {ctx.step_payload['phase']} forecast of round "
                f"{await round_number_now(ctx)} in **{ctx.payload['division_name']}**"
            )
        return await named("deleting a withdrawn forecast of round {number} in **{division}**")(ctx)

    async def describe_clean_up(ctx: StepContext) -> str:
        """The clean-up a day after the round, named by the forecasts standing when it was planned
        (`standing`), never by a phase that is not."""
        return (
            f"deleting {forecasts_named(ctx.step_payload.get('standing', ()))} of round "
            f"{await round_number_now(ctx)} in **{ctx.payload['division_name']}** a day after it"
        )

    async def describe_draw(ctx: StepContext) -> str:
        """The draw of a phase the amendment makes at once, and the job that plans it, each
        naming its phase."""
        phase = ctx.step_payload.get("phase", ctx.payload.get("phase"))
        return (
            f"drawing the Phase {phase} forecast of round {await round_number_now(ctx)} in "
            f"**{ctx.payload['division_name']}** now"
        )

    steps: dict[str, Step] = {
        JUDGE: Step(
            JUDGE, StepKind.ACT, judge,
            describe=named("judging the amendment of round {number} in **{division}**"),
        ),
        UNARM: Step(
            UNARM, StepKind.ACT, unarm, still_due=judge_went_through,
            describe=named("removing the timed work of round {number} in **{division}**"),
        ),
        APPLY: Step(
            APPLY, StepKind.SAVE, apply, still_due=apply_due,
            describe=named("amending round {number} in **{division}**"),
        ),
        ARM: Step(
            ARM, StepKind.ACT, arm, still_due=arm_due, undiscardable=never_runs,
            describe=named("arming the timed work of round {number} in **{division}** again"),
        ),
        TAKE_DOWN_CALL: Step(
            TAKE_DOWN_CALL, StepKind.ACT, take_down_call, still_due=attendance_on, record=forget_call,
            describe=named("taking down the check-in call of round {number} in **{division}**"),
        ),
        POST_CALL: Step(
            POST_CALL, StepKind.ACT, post_call, still_due=call_still_due, record=record_call,
            describe=named("posting the check-in call of round {number} in **{division}** again"),
        ),
        # Weather's own jobs, the draw and the deletion named for what the amendment does with them.
        **hooks.forecast_steps,
        DRAW_FORECAST: dataclasses.replace(
            hooks.forecast_steps[DRAW_FORECAST], describe=describe_draw,
        ),
        DELETE_FORECAST: dataclasses.replace(
            hooks.forecast_steps[DELETE_FORECAST], describe=describe_forecast_delete,
        ),
        NOTIFY_INVALIDATION: Step(
            NOTIFY_INVALIDATION, StepKind.ACT, notify_invalidation, still_due=weather_on,
            describe=named("telling **{division}** its forecasts no longer stand"),
        ),
        RERUN_PHASE: Step(
            RERUN_PHASE, StepKind.ACT, rerun_phase, still_due=phase_still_due,
            describe=describe_draw,
        ),
        # Attendance's own jobs of a deadline, named for the round the amendment stands for.
        DISTRIBUTE: dataclasses.replace(
            hooks.deadline_steps[DISTRIBUTE],
            describe=named("placing the reserves of round {number} in **{division}**"),
        ),
        CLOSE_WITHOUT_CALL: dataclasses.replace(
            hooks.deadline_steps[CLOSE_WITHOUT_CALL],
            describe=named(
                "closing the check-in of round {number} in **{division}**, which has no call"
            ),
        ),
        LOCK_CALL: hooks.deadline_steps[LOCK_CALL],
        ANNOUNCE: dataclasses.replace(
            hooks.deadline_steps[ANNOUNCE],
            describe=named("posting the reserve announcement of round {number} in **{division}**"),
        ),
        CLEAN_UP_FORECAST: dataclasses.replace(
            hooks.forecast_steps[PLAN_FORECAST_DELETES], name=CLEAN_UP_FORECAST,
            still_due=weather_on,
            describe=describe_clean_up,
        ),
        # Attendance's own jobs of a clean-up: its plan under the amendment's name for it, the
        # deletions and the mark that the check-in is over.
        CLEAN_UP_CHECK_IN: dataclasses.replace(
            hooks.clean_up_steps[PLAN_CLEAN_UP], name=CLEAN_UP_CHECK_IN,
            describe=named(
                "finding what is left of the check-in of round {number} in **{division}**"
            ),
        ),
        DELETE_MESSAGE: hooks.clean_up_steps[DELETE_MESSAGE],
        MARK_CLEARED: hooks.clean_up_steps[MARK_CLEARED],
        CLOSE: Step(
            CLOSE, StepKind.ACT, close, still_due=close_due,
            describe=named("listing the rounds of **{division}** for the reply"),
        ),
    }
    if rearm:
        for gone in (UNARM, APPLY, CLOSE):
            del steps[gone]
        steps[JUDGE] = dataclasses.replace(
            steps[JUDGE], still_due=rearm_due, undiscardable=would_be_left_unarmed,
            describe=named("judging whether the timed work of round {number} in **{division}** can be armed again"),
        )
        steps[ARM] = dataclasses.replace(
            steps[ARM], still_due=rearm_due, undiscardable=would_be_left_unarmed,
        )
        return ChangeType(
            kind=ROUND_REARM,
            opening=(PlannedStep(JUDGE), PlannedStep(ARM)),
            steps=steps,
            check=rearm_check,
            key=lambda payload: f"{ROUND_REARM}:{payload['round_id']}",
            doing=lambda payload: (
                f"Arming the timed work of round {payload['round_number']} in "
                f"**{payload['division_name']}** again"
            ),
            outcome=lambda _ctx: "",
            finished_lines=armed_again,
        )
    return ChangeType(
        kind=ROUND_AMEND,
        opening=tuple(PlannedStep(name) for name in (JUDGE, UNARM, APPLY, ARM, CLOSE)),
        steps=steps,
        check=check,
        key=lambda payload: (
            f"{ROUND_AMEND}:{payload['round_id']}:{json.dumps(payload['changes'])}"
        ),
        doing=lambda payload: (
            f"Amending round {payload['round_number']} in **{payload['division_name']}**"
        ),
        outcome=outcome,
    )
