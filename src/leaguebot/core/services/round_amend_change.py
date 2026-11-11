"""Amending a round on the change queue.

`/round amend` offers its confirmation. Confirm reads the round for its number and asks the queue
for this change, which is judged when it is asked and again when it comes up to run, in the words
Confirm used: a round that has gone, a window that has passed or results entered while the amendment
waited refuse it with the same reply. It imports nothing from a module and no cog: what it needs of
weather and attendance reaches it through the hooks the builder makes and signs them up to (the
windows, the save, the posts and clean-ups, the steps they define and the words for a step left
undone), and it reads the season through the service it is handed.

**The refusal because a cancellation is in hand is made only as the change is asked for**
(`CheckContext.change_id` is None), through `cancellation_changes.cancellation_holding_amendment`.
Run, it cannot fire: whatever was ahead of the amendment on the queue has finished, and a
cancellation queued behind it must not make the amendment refuse itself. Two requests asked in the
same instant can both pass; the queue then runs them in order and the second reads what the first
wrote, a renumbering included.

**The same steps arm a round again after a refused cancellation** (`round_rearm_change`): a
cancellation refused at its save has already removed the timed work of its rounds, and asks one
`season.round.rearm` for each round that is still to be run or awaiting its results, which judges
the round as it stands when it runs and arms it as an amended round is armed, catching up what fell
due meanwhile. For a round already raced, or one that ended cancelled for every session being
entered as not held, it arms the next-day clean-ups alone (the payload's `cleanups_only`). Neither
of its two jobs can be discarded.

`amendment_in_hand` reads the queue for an amendment of a division, so that a cancellation is
refused while one is waiting, running or stopped. It is read from the queue, never from memory
(architecture.md, "How a change is carried out").
"""
from __future__ import annotations

import dataclasses
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
    forecasts_named,
)
from leaguebot.core.models.round import (
    ROUND_CANCELLABLE,
    ROUND_RACED_AWAITING_VERDICTS,
    AttendanceWindows,
    Round,
    RoundFormat,
    RoundStatus,
    WeatherWindows,
    read_scheduled_at,
    stored_scheduled_at,
    utc_text,
)
from leaguebot.core.services.amendment_rules_service import (
    AmendmentVerdict,
    judge_amendment,
    status_refusal,
)
from leaguebot.core.services.round_moments import (
    read_phases_drawn,
    read_round_windows,
    round_event_moments,
)
from leaguebot.core.services.round_moments import phases_due as round_moments_phases_due
from leaguebot.core.services.round_steps import JUDGE, named_now, round_number_now as number_now
from leaguebot.core.services import cancellation_notice_service as notices
from leaguebot.core.services.cancellation_changes import ROUND_REARM, cancellation_holding_amendment
from leaguebot.core.services.change_queue import (
    in_hand,
)
from leaguebot.core.services.change_types import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
    StepView,
)
from leaguebot.core.services.season_service import renumber_rounds_on, round_called
from leaguebot.core.utils.formatting import format_round_list
from leaguebot.core.utils.hooks import AwaitedHook, Hook
from leaguebot.core.utils.log_lines import refusal_line, reply_reason

if TYPE_CHECKING:
    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.scheduler_service import SchedulerService
    from leaguebot.core.services.season_service import SeasonService

__all__ = [
    "ROUND_AMEND",
    "amendment_in_hand",
    "payload_changes",
    "round_amend_change",
    "round_rearm_change",
]

ROUND_AMEND = "season.round.amend"

#: The job names, as the stop notice, the tests and `StepView` know them. The core ones first, then
#: those the outcome names for the words of a step left undone (the modules define each step).
UNARM = "unarm"
APPLY = "apply"
ARM = "arm"
CLOSE = "close"
TAKE_DOWN_CALL = "take_down_call"
POST_CALL = "post_call"
DELETE_FORECAST = "delete_forecast"
NOTIFY_INVALIDATION = "notify_invalidation"
RERUN_PHASE = "rerun_phase"
DISTRIBUTE = "distribute"
LOCK_CALL = "lock_call"
ANNOUNCE = "announce"
CLEAN_UP_FORECAST = "clean_up_forecast"
CLEAN_UP_CHECK_IN = "clean_up_check_in"
DELETE_MESSAGE = "delete_message"

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


def _as_logged(field: str, value: object) -> str:
    """*value* of *field* as the success line shows it: a moment in UTC without its offset, any
    other value as it stands."""
    if value is None:
        return "none"
    if field == "scheduled_at":
        return utc_text(read_scheduled_at(str(value)))
    return str(value)

_EMPTY = StepView(name="", payload={}, result=None, done=False)

ROUND_GONE = "⛔ That round no longer exists. **Nothing has been changed.**"
_NOTHING_CHANGED = "**Nothing has been changed.** Run `/round amend` again to start over."


def no_longer_amendable(reasons: Iterable[str]) -> str:
    """The refusal of an amendment the rules no longer allow, in the words Confirm uses."""
    return (
        "⛔ This round can no longer be amended:\n"
        + "\n".join(f"• {reason}" for reason in reasons)
        + f"\n\n{_NOTHING_CHANGED}"
    )


def payload_changes(amendments: Iterable[tuple[str, object]]) -> list[list[str]]:
    """The amendments a member confirmed, as the change's payload holds them: ``[field, value]``,
    the moment as its stored text and the format as its value."""
    changes: list[list[str]] = []
    for field, value in amendments:
        if isinstance(value, datetime):
            text = stored_scheduled_at(value)
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
    amendment_steps: Hook,
    amendment_step_failure: Hook,
    round_windows_read: AwaitedHook,
    phases_drawn_read: AwaitedHook,
    round_amended: AwaitedHook,
    round_armed: AwaitedHook,
    round_clean_ups_due: AwaitedHook,
    now: Callable[[], datetime],
) -> ChangeType:
    """The change that amends a round; see the module.

    The payload is ``{"round_id", "round_number", "division_id", "division_name", "changes"}``,
    *changes* being ``[[field, value]]`` (`payload_changes`). It names no season: a payload that
    does is read as holding every division of that season (`review_changes.division_job_in_hand`).
    The builder hands in the *modules* (each post's `still_due`), the *seasons* service, the
    *scheduler*, *round_windows_read* (the hook their windows are
    read through), *phases_drawn_read* (the hook the phases drawn are read through, weather's
    record of them), *round_amended* (the moment inside the save), *round_armed* and
    *round_clean_ups_due* (the moments the round is armed again, at which the modules plan their
    posts and their clean-ups) and the queue's clock *now*.
    """
    return _round_change(
        rearm=False, modules=modules, seasons=seasons, scheduler=scheduler,
        amendment_steps=amendment_steps, amendment_step_failure=amendment_step_failure,
        round_windows_read=round_windows_read, phases_drawn_read=phases_drawn_read,
        round_amended=round_amended,
        round_armed=round_armed, round_clean_ups_due=round_clean_ups_due, now=now,
    )


def round_rearm_change(
    *,
    modules: "ModuleService",
    seasons: "SeasonService",
    scheduler: "SchedulerService",
    amendment_steps: Hook,
    amendment_step_failure: Hook,
    round_windows_read: AwaitedHook,
    phases_drawn_read: AwaitedHook,
    round_amended: AwaitedHook,
    round_armed: AwaitedHook,
    round_clean_ups_due: AwaitedHook,
    now: Callable[[], datetime],
) -> ChangeType:
    """The change that arms a round's timed work again after a refused cancellation; see the module.

    The payload is ``{"round_id", "round_number", "division_id", "division_name", "command"}``,
    *command* naming the cancellation that was refused, and ``"cleanups_only": True`` for a round
    already raced or ended cancelled as not held, whose next-day clean-ups alone are armed. It opens with the amendment's own
    judging and arming, unchanged but for when they are due and that neither can be discarded.
    The bot asks it, on the league's lane, one for each round.
    """
    return _round_change(
        rearm=True, modules=modules, seasons=seasons, scheduler=scheduler,
        amendment_steps=amendment_steps, amendment_step_failure=amendment_step_failure,
        round_windows_read=round_windows_read, phases_drawn_read=phases_drawn_read,
        round_amended=round_amended,
        round_armed=round_armed, round_clean_ups_due=round_clean_ups_due, now=now,
    )


def _round_change(
    *,
    rearm: bool,
    modules: "ModuleService",
    seasons: "SeasonService",
    scheduler: "SchedulerService",
    amendment_steps: Hook,
    amendment_step_failure: Hook,
    round_windows_read: AwaitedHook,
    phases_drawn_read: AwaitedHook,
    round_amended: AwaitedHook,
    round_armed: AwaitedHook,
    round_clean_ups_due: AwaitedHook,
    now: Callable[[], datetime],
) -> ChangeType:
    """The steps of `round_amend_change`, and with *rearm* the change that arms a round again.

    Only the check, the due-ness of the judging and the arming, their refusal to be discarded, the
    change's kind, key and words differ between the two; every other step is the amendment's.
    """

    async def read_windows() -> tuple[AttendanceWindows | None, WeatherWindows]:
        """The lead times the amendment is judged against, read through the windows hook:
        attendance's only where it is on, weather's always."""
        attendance, weather = await read_round_windows(round_windows_read, amending=True)
        if weather is None:
            raise LookupError("no module handed back weather's window to the amendment")
        return attendance, weather

    async def rearm_check(ctx: CheckContext) -> Verdict:
        """A re-arming is due while the round can still be cancelled, or, for its clean-ups alone,
        while it is raced or ended cancelled as not held; a round gone or past that is dropped."""
        if await rearm_due(ctx):
            return Verdict.go()
        return Verdict.not_due("the round can no longer be armed again")

    async def rearm_due(ctx: StepContext | CheckContext) -> bool:
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        if rnd is None:
            return False
        if not ctx.payload.get("cleanups_only"):
            return rnd.status in ROUND_CANCELLABLE
        if rnd.status in ROUND_RACED_AWAITING_VERDICTS or rnd.status == RoundStatus.FINAL.value:
            return True
        if rnd.status != RoundStatus.CANCELLED.value:
            return False
        # A round that ended cancelled for every session being entered as not held keeps its
        # clean-ups, a day after it; a round called off has none.
        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute(
                "SELECT not_held FROM rounds WHERE id = ?", (rnd.id,)
            )
            row = await cursor.fetchone()
        return row is not None and bool(row["not_held"])

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        rnd = await seasons.get_round(int(payload["round_id"]))
        if rnd is None:
            return Verdict.refuse(ROUND_GONE)
        attendance, weather = await read_windows()
        drawn = await read_phases_drawn(phases_drawn_read, [rnd.id])
        verdict = judge_amendment(
            rnd,
            amended_values(payload["changes"]),
            now=now(),
            attendance=attendance,
            weather=weather,
            drawn=drawn.get(rnd.id),
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
        return await number_now(seasons, ctx)

    def named(text: str) -> Callable[[StepContext], Awaitable[str]]:
        return named_now(seasons, text)

    # ── The jobs ────────────────────────────────────────────────────────────────

    async def judge(ctx: StepContext) -> StepResult:
        """Keep the windows the later jobs judge the amendment by, the round being still there.

        It writes nothing and refuses nothing. The windows are read here, outside any save,
        because a save reads no other connection. The amendment is judged when it is asked and
        again in the save, against what the save reads at the queue's clock (`judged_again`), so
        that its refusal is spoken once, at the save's own moment, however long a stop between
        them lasts. What the amendment withdraws and what becomes of the check-in call are not
        kept here, nor the phases to draw at once: the save plans the first from what it reads
        when it runs and the arming the rest."""
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        if rnd is None:
            raise LookupError(f"round {ctx.payload['round_id']} is no longer there")
        attendance, weather = await read_windows()
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
        row: aiosqlite.Row,
        changes: Iterable[Sequence[str]],
        judgement: dict[str, Any],
        drawn: Mapping[int, bool],
    ) -> AmendmentVerdict:
        """*changes* judged once more, against the round as the save reads it and the phases
        *drawn* as weather says of them on the save's connection, at the windows `judge` kept and
        at the queue's clock.

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
            scheduled_at=read_scheduled_at(row["scheduled_at"]),
            status=row["status"],
        )
        return judge_amendment(
            rnd,
            amended_values(changes),
            now=now(),
            attendance=AttendanceWindows(**attendance) if attendance is not None else None,
            weather=WeatherWindows(**windows["weather"]),
            drawn=drawn,
        )

    async def apply(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The one save: the audit, the round's fields, what the amendment withdraws, the
        division's renumbering and the success line, reading nothing but the connection.

        It judges the amendment again before it writes (`judged_again`), and refuses, writing
        nothing, where the round's status or that judgement says it may no longer be made. What
        follows the save is planned from that judgement, made when the save runs, and kept with
        it for the arming to read: what each module's part of the save kept (`round_amended`: the
        forecasts it withdraws and those of them that were posted, whether the check-in was
        reopened and whether a call stood). Planned from `judge`'s instead,
        a save tried again after a window passed would withdraw a forecast that now stands, or
        leave a call to a schedule whose moment has gone."""
        if ctx.actor_id is None or ctx.actor_name is None:
            raise RuntimeError("amending a round is the act of a member, and none is recorded")
        judgement = judged(ctx) or {}
        round_id = int(ctx.payload["round_id"])
        cursor = await db.execute(
            "SELECT id, status, round_number, division_id, track_name, format, scheduled_at "
            "FROM rounds WHERE id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()
        refusal = None
        verdict: AmendmentVerdict | None = None
        drawn = (await read_phases_drawn(phases_drawn_read, [round_id], db=db)).get(round_id, {})
        if row is None:
            refusal = ROUND_GONE
        elif row["status"] not in ROUND_CANCELLABLE:
            refusal = no_longer_amendable([status_refusal(row["status"])])
        else:
            verdict = judged_again(row, ctx.payload["changes"], judgement, drawn)
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
                    f"{_as_logged(field, old)} → {_as_logged(field, new)}"
                )
        withdrawn = sorted(n for n, outcome in verdict.phases.items() if not outcome.stands)
        # With attendance on, an allowed amendment always leaves the check-in open: one whose
        # deadline has passed is refused above.
        reopened = bool(verdict.check_in) and not verdict.check_in_stays_closed
        kept: dict[str, Any] = {}
        for part in await round_amended.call(
            db, round_id, withdrawn=withdrawn, reopened=reopened
        ):
            kept.update(part or {})
        if any(field == "scheduled_at" for field, _ in ctx.payload["changes"]):
            await renumber_rounds_on(db, division_id)
        values = amended_values(ctx.payload["changes"])
        return StepResult(
            result={
                **kept,
                "saved": True,
                "round_number": row["round_number"],
                "track": values.get("track_name") or row["track_name"] or "Unknown",
            },
            lines=(
                f"{ctx.named} | /round amend | Success\n"
                f"  round {row['round_number']} (round_id: {round_id}){changed}",
            ),
        )

    def first_horizon_ahead(rnd: Round, weather: dict[str, Any]) -> bool:
        """Whether the round as it stands now still has its first forecast horizon to come. Read
        when the arming runs, not when the amendment was judged: a discarded save arms the round
        at its old moment, and a stop can hold the arming for hours."""
        moments = round_event_moments(
            rnd.scheduled_at, weather=WeatherWindows(**weather), attendance=None
        )
        return now() < moments["weather_p1"]

    def phases_due(rnd: Round, weather: dict[str, Any], drawn: Mapping[int, bool]) -> list[int]:
        """The forecast phases to draw at once, as the round stands at the queue's clock, the
        phases *drawn* and the windows `judge` read (`round_moments.phases_due`)."""
        return round_moments_phases_due(rnd, WeatherWindows(**weather), now(), drawn=drawn)

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
        catch up what fell due while it stood removed, as a restart does.

        The amendment can stand stopped for hours, at the save or before it, and a discard or a
        refusal arms the round again at its old moment: a timer armed for a moment already past
        is skipped by the scheduler. So, judged at the queue's clock: a round still to be run
        whose moment has passed has its results submission run at once through the scheduler,
        its phases due are drawn at once (`phases_due`), and its call, where none stands and its
        check-in is not over, is posted where it has fallen due (`call_fell_due`).

        Under test mode the results submission is not run at once: `/test-mode advance` runs a
        test season's rounds in their turn. Its call is still posted (`post_call`)."""
        judgement = judged(ctx) or {}
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        if rnd is None:
            raise LookupError(f"round {ctx.payload['round_id']} is no longer there")
        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute(
                "SELECT d.tier, s.season_number, "
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
            # A round raced, or ended cancelled as not held: only the clean-ups of a day after it
            # are owed, armed through the scheduler and, where the day has come, run as the
            # restart's catch-up runs them.
            check_in_on = (
                judgement["windows"]["attendance"] is not None
                and await modules.is_attendance_enabled()
            )
            planned = await plan_clean_ups(ctx, rnd, kept={})
            if weather_on or check_in_on:
                scheduler.schedule_round_cleanups(
                    rnd, season_number=season_number, division_tier=tier,
                    forecast=weather_on, check_in=check_in_on, now=now(),
                )
            return StepResult(result={"armed": True, "round_number": rnd.round_number}, then=planned)
        drawn = (await read_phases_drawn(phases_drawn_read, [rnd.id])).get(rnd.id, {})
        draw = phases_due(rnd, weather, drawn) if weather_on else []
        planned = await plan_posts(ctx, rnd, judgement, draw=draw)
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
            # armed, but its results submission is, or the round would never leave Not run.
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
                now=now(),
            )
        result_at = round_event_moments(rnd.scheduled_at, weather=None, attendance=None)["results"]
        if rnd.status == RoundStatus.NOT_RUN.value and result_at <= now() and not found["test_mode"]:
            # After the arming above, whose job for this moment it replaces.
            scheduler.run_result_submission_now(
                rnd, season_number=season_number, division_tier=tier
            )
        return StepResult(result={"armed": True, "round_number": rnd.round_number}, then=planned)

    async def plan_clean_ups(
        ctx: StepContext, rnd: Round, *, kept: Mapping[str, Any]
    ) -> tuple[PlannedStep, ...]:
        """The clean-ups a day after the round, which the modules plan where that day has come:
        the forecast (whichever of Phases 1 to 3 still stands), then the standing check-in call.
        Read when the arming runs, as the arming reads the round."""
        async with get_connection(ctx.db_path) as db:
            handed = await round_clean_ups_due.call(db, rnd.id, rnd=rnd, kept=kept, now=now)
        return tuple(step for steps in handed for step in steps or ())

    async def plan_posts(
        ctx: StepContext, rnd: Round, judgement: dict[str, Any], *, draw: list[int]
    ) -> tuple[PlannedStep, ...]:
        """The posts the amendment owes, which the modules plan in today's order: the check-in
        call taken down and posted again, its deadline run, each withdrawn forecast that was posted
        deleted, the notice that the forecasts no longer stand, each phase in *draw* drawn, and the
        clean-ups due a day after the round (`plan_clean_ups`). Planned once the round is armed,
        which is where the amendment stood before them, but read before the timed work is
        scheduled; what the save withdraws, from what each module's part of it kept (`apply`), and
        only where it was saved.

        What each module plans, and how it reads its own tables, is its own (`round_armed`)."""
        windows = judgement["windows"]
        attendance = windows["attendance"]
        kept = (view(ctx, APPLY).result or {}) if saved(ctx) else {}
        async with get_connection(ctx.db_path) as db:
            handed = await round_armed.call(
                db, rnd.id, rnd=rnd, kept=kept,
                attendance=AttendanceWindows(**attendance) if attendance is not None else None,
                weather=WeatherWindows(**windows["weather"]), draw=draw, now=now,
            )
        planned = [step for steps in handed for step in steps or ()]
        return (*planned, *await plan_clean_ups(ctx, rnd, kept=kept))

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
        what = (
            "next-day clean-ups of {} are" if ctx.payload.get("cleanups_only")
            else "timed work of {} is"
        ).format(called)
        return (
            f"⏱️ The {what} armed again, `{command}` having been refused"
            + not_done(ctx),
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
                "round_list": format_round_list(rounds) if rounds and saved(ctx) else "",
                "round_number": ctx.payload["round_number"] if rnd is None else rnd.round_number,
            }
        )

    def outcome(ctx: OutcomeContext) -> str:
        number = (view(ctx, CLOSE).result or {}).get("round_number", ctx.payload["round_number"])
        words = {"number": number, "division": ctx.payload["division_name"]}
        if discarded(view(ctx, JUDGE)) or discarded(view(ctx, UNARM)):
            return NOTHING_AMENDED.format(**words)
        # The arming runs whatever became of the save, and what it catches up can be discarded
        # too: it is named beneath the refusal or the discarded save as beneath a success.
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
            elif (undone := next(
                (line for line in amendment_step_failure.call(each) if line is not None), None
            )) is not None:
                failures.append(notices.NoticeFailure(division, *undone))
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
        # What each module defines of the amendment, by name: it plans them as the round is armed.
        **{name: step for defined in amendment_steps.call() for name, step in defined.items()},
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
