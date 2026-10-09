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
)
from leaguebot.core.models.round import ROUND_CANCELLABLE, Round, RoundFormat, RoundStatus
from leaguebot.core.services.amendment_rules_service import (
    AmendmentVerdict,
    judge_amendment,
    status_refusal,
)
from leaguebot.core.services.approval_window_service import AttendanceWindows, WeatherWindows
from leaguebot.core.services import cancellation_notice_service as notices
from leaguebot.core.services.cancellation_changes import cancellation_holding_amendment
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
from leaguebot.core.services.season_service import renumber_rounds_on
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
NOTIFY_INVALIDATION = "notify_invalidation"
RERUN_PHASE = "rerun_phase"
RUN_DEADLINE = "run_deadline"
CLEAN_UP_FORECAST = "clean_up_forecast"
CLEAN_UP_CHECK_IN = "clean_up_check_in"

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
    nothing. *delete_forecast* raises `StepFailedOnDiscord` and keeps the forecast's record where
    Discord refuses. *run_phase* draws a phase now, *repost_call* takes down whatever call stands
    and posts the check-in call again, *post_call* posts it where none stands, posting nothing
    where one does (judged under the round's check-in lock, so that a call posted by its timer or
    a restart at the same moment is not posted twice), *give_up_call* reports a call whose
    deadline passed before it could be posted and closes the round's check-in, as the start-up
    recovery does, raising where the close cannot be saved so that the queue stops rather than
    report a give-up never made, handed the bot, the round's ``round_id``, ``round_number``,
    ``division_id``, ``division_name`` and ``season_number``, and whether to clear the round's
    check-in answers and the placements made on them in the same save.
    *run_deadline* runs the round's check-in deadline, *clean_up_forecast* deletes its Phase 3
    forecast and *clean_up_check_in* takes its check-in down, each as its timer would, for a
    moment that passed while the amendment stood stopped. *round_list* formats the division's
    rounds for the reply.
    """

    windows: Callable[[], Awaitable[tuple[AttendanceWindows | None, WeatherWindows]]]
    withdraw_phases_on: Callable[[aiosqlite.Connection, int, Iterable[int]], Awaitable[None]]
    delete_forecast: Callable[[Any, int, int, int], Awaitable[None]]
    invalidation_text: Callable[[str], str]
    run_phase: Callable[[int, int, Any], Awaitable[None]]
    reopen_check_in_on: Callable[[aiosqlite.Connection, int], Awaitable[None]]
    repost_call: Callable[[int, int, Any], Awaitable[None]]
    post_call: Callable[[int, Any], Awaitable[None]]
    give_up_call: Callable[[Any, Mapping[str, Any], bool], Awaitable[None]]
    run_deadline: Callable[[int, Any], Awaitable[None]]
    clean_up_forecast: Callable[[int, Any], Awaitable[None]]
    clean_up_check_in: Callable[[int, Any], Awaitable[None]]
    round_list: Callable[[list[Round]], str]


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
        """Judge the amendment against the round as it stands, and keep the windows the later
        jobs judge it by.

        It writes nothing. The windows are read here, outside any save, because a save reads no
        other connection. What the amendment withdraws and what becomes of the check-in call are
        not kept here, nor the phases to draw at once: the save plans the first from what it
        reads when it runs and the arming the rest, and a stop between them can last for hours
        (owner, 2026-10-09)."""
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        if rnd is None:
            raise LookupError(f"round {ctx.payload['round_id']} is no longer there")
        values = amended_values(ctx.payload["changes"])
        attendance, weather = await hooks.windows()
        moment = now()
        verdict = judge_amendment(
            rnd, values, now=moment, attendance=attendance, weather=weather
        )
        return StepResult(
            result={
                "judged": True,
                # The check judged the amendment as it started; this is the verdict reached as
                # `judge` ran, retried or not, and the save refuses on it. The save judges it once
                # more against what it reads (`judged_again`), for a stop at a later job.
                "refused": None if verdict.allowed else no_longer_amendable(verdict.refusals),
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
        nothing, where the round's status, `judge`'s verdict or that judgement says it may no
        longer be made. What follows the save is planned from that judgement, made when the save
        runs, and kept with it for the arming to read: the forecasts it withdraws (`withdrawn`),
        those of them that were posted (`posted`, by the flags it reads), whether it reopens the
        check-in (`reopened`) and whether a call stood (`called`). Planned from `judge`'s instead,
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
        elif judgement.get("refused"):
            refusal = str(judgement["refused"])
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
                "AND m.distribution_msg_id IS NULL) AS undistributed, EXISTS ("
                "SELECT 1 FROM forecast_messages f WHERE f.round_id = r.id "
                "AND f.phase_number = 3) AS phase3_posted, EXISTS ("
                "SELECT 1 FROM server_configs c WHERE c.test_mode_active = 1) AS test_mode "
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
        return StepResult(result={"armed": True}, then=planned)

    def posts(
        ctx: StepContext, rnd: Round, judgement: dict[str, Any], *,
        standing: Mapping[str, Any], draw: list[int],
    ) -> tuple[PlannedStep, ...]:
        """The posts the amendment owes, in today's order: the check-in call taken down and
        posted again, its deadline run, each withdrawn forecast that was posted deleted, the
        notice that the forecasts no longer stand, each phase in *draw* drawn, and the clean-ups
        due a day after the round. Planned once the round is armed, which is where the amendment
        stood before them; what the save withdraws, from what it read (`apply`), and only where it
        was saved. *standing* is what the arming read of the round's call and Phase 3 forecast.

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
        distribution recorded has its deadline run, and a day after the round its Phase 3
        forecast is deleted and its standing call taken down."""
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
            planned.append(PlannedStep(RUN_DEADLINE))
        posted = after.get("posted", [])
        planned += [PlannedStep(DELETE_FORECAST, {"phase": n}) for n in posted]
        if posted:
            planned.append(PlannedStep(NOTIFY_INVALIDATION, {"track": after["track"]}))
        planned += [PlannedStep(RERUN_PHASE, {"phase": n}) for n in draw]
        if moment_of(rnd) + POST_RACE_CLEANUP_DELAY <= now():
            if standing["phase3_posted"]:
                planned.append(PlannedStep(CLEAN_UP_FORECAST))
            if call_left:
                planned.append(PlannedStep(CLEAN_UP_CHECK_IN))
        return tuple(planned)

    async def never_runs(ctx: StepContext) -> str:
        return (
            f"round {await round_number_now(ctx)} in **{ctx.payload['division_name']}** "
            "would never run"
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

    async def call_still_due(ctx: StepContext) -> bool:
        """The call is posted only while attendance is on and the call has fallen due as the
        round stands when this job runs, and, as the start-up recovery of a missed call judges it
        (`_recover_missed_check_in_calls`), only where no call stands and the round's check-in
        is not over, both read then (owner, 2026-10-09). A call posted between the arming and
        this job, by its timer, a restart or `/attendance post-check-in`, is the round's live
        call, and posting again would call the division twice. The one exception is the old call
        a discarded take-down left standing, which a repost replaces (`replacing`). A call still to
        come is posted by its timer, armed with the round; one whose deadline has passed is given
        up (`post_call`)."""
        if not await modules.is_attendance_enabled():
            return False
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        judgement = judged(ctx) or {}
        if rnd is None or not call_fell_due(rnd, judgement.get("windows", {}).get("attendance")):
            return False
        if replacing(ctx):
            return True
        standing, cleared = await call_state(ctx.db_path, rnd.id)
        return not standing and not cleared

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
        taken = await notices.take_down_call(ctx.bot, division, int(ctx.payload["round_id"]))
        return StepResult(result={"taken_down": taken["taken_down"]})

    async def post_call(ctx: StepContext) -> StepResult:
        """Post the call, carrying every answer already given. Discord refusing it fails as the
        timer's call does, reported by attendance; a fault of the bot's own stops the queue
        (owner, 2026-10-08: slice 6 deals with the rest).

        A first post goes through `post_call`, which posts nothing where a call already stands,
        judged under the round's check-in lock, so that a call posted by the timer or a restart
        since this job was judged due is not posted twice. A call posted again in place of one the
        amendment took down (`reposting`) goes through `repost_call`, which also drops the answers
        of drivers no longer of the division, and takes down the old call where a discarded
        take-down left it standing.

        Where the round's check-in deadline has passed by the time it runs, as after a stop,
        nothing is posted: the log channel is told and the round's check-in closed, as the
        start-up recovery gives up a missed call (`give_up_call`; owner, 2026-10-09). A call is
        given up only where none stands: one standing is the round's live call, or the old one a
        discarded take-down left, and is left as it is. Given up after the amendment took the old
        call down, the round's answers to it and the placements made on them are cleared in the
        same save, so that the log channel's line that the round counts nothing against anyone
        holds true (owner, 2026-10-09: "Clear the answers, keep the line true"). The call is
        posted under test mode too, unlike the start-up recovery, which leaves a test season's
        calls to `/test-mode advance` (owner, 2026-10-09: "Post it anyway")."""
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        if rnd is None:
            raise LookupError(f"round {ctx.payload['round_id']} is no longer there")
        attendance = (judged(ctx) or {}).get("windows", {}).get("attendance")
        if attendance is not None and deadline_passed(rnd, attendance):
            standing, _cleared = await call_state(ctx.db_path, rnd.id)
            if standing:
                return StepResult(result={"left": True})
            async with get_connection(ctx.db_path) as db:
                cursor = await db.execute(
                    "SELECT r.id AS round_id, r.round_number, d.id AS division_id, "
                    "d.name AS division_name, s.season_number FROM rounds r "
                    "JOIN divisions d ON d.id = r.division_id "
                    "JOIN seasons s ON s.id = d.season_id WHERE r.id = ?",
                    (rnd.id,),
                )
                found = await cursor.fetchone()
            if found is None:
                raise LookupError(f"division {rnd.division_id} is no longer there")
            await hooks.give_up_call(ctx.bot, dict(found), reposting(ctx))
            return StepResult(result={"given_up": True})
        if reposting(ctx):
            await hooks.repost_call(rnd.id, rnd.division_id, ctx.bot)
        else:
            await hooks.post_call(rnd.id, ctx.bot)
        return StepResult(result={"posted": True})

    async def delete_forecast(ctx: StepContext) -> StepResult:
        """Delete the withdrawn forecast of one phase, keeping its record and stopping the queue
        where Discord refuses."""
        phase = int(ctx.step_payload["phase"])
        try:
            await hooks.delete_forecast(
                ctx.bot, int(ctx.payload["round_id"]), int(ctx.payload["division_id"]), phase
            )
        except StepFailedOnDiscord as exc:
            guild = await league_guild(ctx.bot)
            kept = {**(exc.result or {}), "phase": phase}
            if guild is not None:
                kept["guild_id"] = guild.id
            raise StepFailedOnDiscord(exc.reason, result=kept) from exc.__cause__
        return StepResult(result={"phase": phase})

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
        """Draw a phase whose horizon has already passed, by the amended moment or while the
        amendment stood stopped, where it is still due when the job runs (`phase_still_due`).
        Discord refusing its post goes the way weather's own posts go (owner, 2026-10-08:
        slice 6)."""
        phase = int(ctx.step_payload["phase"])
        await hooks.run_phase(phase, int(ctx.payload["round_id"]), ctx.bot)
        return StepResult(result={"phase": phase})

    def catching_up(
        hook: Callable[[int, Any], Awaitable[None]], done: str
    ) -> Callable[[StepContext], Awaitable[StepResult]]:
        """A job running *hook* for the round, as its timer would have: the deadline or a
        clean-up whose moment passed while the amendment stood stopped. What Discord refuses goes
        the way the timer's own run goes; a fault of the bot's own stops the queue."""

        async def run(ctx: StepContext) -> StepResult:
            await hook(int(ctx.payload["round_id"]), ctx.bot)
            return StepResult(result={done: True})

        return run

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
                failures.append(notices.NoticeFailure(
                    division, "check-in call",
                    f"{len(ids)} message(s) could not be deleted and must be removed by hand "
                    f"(ids {', '.join(ids)})",
                ))
            elif each.name == POST_CALL:
                failures.append(notices.NoticeFailure(
                    division, "check-in call",
                    "the call could not be posted again, and a league admin discarded it; post "
                    "it with `/attendance post-check-in`",
                ))
            elif each.name == DELETE_FORECAST:
                where = (
                    f"https://discord.com/channels/{result['guild_id']}/{result['channel_id']}/"
                    f"{result['message_id']}"
                    if "guild_id" in result and "message_id" in result
                    else f"message {result.get('message_id')} in <#{result.get('channel_id')}>"
                )
                failures.append(notices.NoticeFailure(
                    division, "forecast channel",
                    f"the withdrawn Phase {result.get('phase')} forecast could not be deleted, "
                    f"and a league admin discarded it; delete it by hand ({where})",
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
            elif each.name == RUN_DEADLINE:
                failures.append(notices.NoticeFailure(
                    division, "check-in deadline",
                    "the reserves were not distributed, and a league admin discarded it; it is "
                    "run when the bot next starts, while the call stands",
                ))
            elif each.name == CLEAN_UP_FORECAST:
                failures.append(notices.NoticeFailure(
                    division, "forecast channel",
                    "the Phase 3 forecast was not deleted a day after the round, and a league "
                    "admin discarded it; it is deleted when the bot next starts",
                ))
            elif each.name == CLEAN_UP_CHECK_IN:
                failures.append(notices.NoticeFailure(
                    division, "check-in call",
                    "the check-in was not taken down a day after the round, and a league admin "
                    "discarded it; it is taken down when the bot next starts",
                ))
        if not failures:
            return ""
        return "\n⚠️ **Not done**\n" + "\n".join(f"  • {each.describe()}" for each in failures)

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
            TAKE_DOWN_CALL, StepKind.ACT, take_down_call, still_due=attendance_on,
            describe=named("taking down the check-in call of round {number} in **{division}**"),
        ),
        POST_CALL: Step(
            POST_CALL, StepKind.ACT, post_call, still_due=call_still_due,
            describe=named("posting the check-in call of round {number} in **{division}** again"),
        ),
        DELETE_FORECAST: Step(
            DELETE_FORECAST, StepKind.ACT, delete_forecast,
            describe=named("deleting a withdrawn forecast of round {number} in **{division}**"),
        ),
        NOTIFY_INVALIDATION: Step(
            NOTIFY_INVALIDATION, StepKind.ACT, notify_invalidation, still_due=weather_on,
            describe=named("telling **{division}** its forecasts no longer stand"),
        ),
        RERUN_PHASE: Step(
            RERUN_PHASE, StepKind.ACT, rerun_phase, still_due=phase_still_due,
            describe=named("drawing a forecast of round {number} in **{division}** now"),
        ),
        RUN_DEADLINE: Step(
            RUN_DEADLINE, StepKind.ACT, catching_up(hooks.run_deadline, "run"),
            still_due=attendance_on,
            describe=named("running the check-in deadline of round {number} in **{division}**"),
        ),
        CLEAN_UP_FORECAST: Step(
            CLEAN_UP_FORECAST, StepKind.ACT, catching_up(hooks.clean_up_forecast, "cleaned_up"),
            still_due=weather_on,
            describe=named(
                "deleting the Phase 3 forecast of round {number} in **{division}** a day after it"
            ),
        ),
        CLEAN_UP_CHECK_IN: Step(
            CLEAN_UP_CHECK_IN, StepKind.ACT, catching_up(hooks.clean_up_check_in, "cleaned_up"),
            still_due=attendance_on,
            describe=named(
                "taking down the check-in of round {number} in **{division}** a day after it"
            ),
        ),
        CLOSE: Step(
            CLOSE, StepKind.ACT, close, still_due=close_due,
            describe=named("listing the rounds of **{division}** for the reply"),
        ),
    }
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
