"""Cancelling a round on the change queue (#439, slice 4b).

`/round cancel` checks its confirmation word, the season and the names at the press, in the season
cog, then asks the queue for this change. Every other gate it had is the change type's `check`,
which judges the request when it is asked and again when it comes up to run, in the words the
command used, so that a round whose results were entered, or whose submission opened, while the
cancellation waited is refused with the same reply. It imports nothing from a module and no cog:
results' `is_submission_open` reaches it through the builder, and it reads the season through the
service it is handed.

**The timed work is removed first, then everything the cancellation writes is one save**
(`apply`, architecture.md, "All or nothing in one step"): the round recorded cancelled with the
status it was cancelled from, its division finished and the season moved on where that was the
last it waited on, reading nothing but the connection it is handed. The removal goes first, as the
command did it, because a cancelled round whose weather jobs stood would have its forecasts
posted; removing never raises, so a stop there means the scheduler itself failed. **Everything
else follows the save as a job of its own**, each of which stops the queue where it fails: each
enabled module's notice, the check-in call's take-down, the calendar's repost, and a closing job
that writes the one line. The wind-down is a change of its own, asked in the save where the
division finished. A job is planned whether or not its module is on, and drops itself as it runs
where it is not; a notice job whose channel was never set is done, and named in the reply.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    FollowOn,
    GuildUnavailable,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.round import ROUND_CANCELLABLE, RoundStatus
from leaguebot.core.models.division import Division
from leaguebot.core.models.season import ONGOING_STAGES
from leaguebot.core.services import cancellation_notice_service as notices
from leaguebot.core.services.calendar_post_service import post_division_calendar, tracks_by_name
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
    StepView,
)
from leaguebot.core.services.season_lifecycle_service import WIND_DOWN
from leaguebot.core.services.season_service import (
    SeasonImmutableError,
    SeasonService,
    cancel_round_on,
)
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.log_lines import refusal_line, reply_reason

if TYPE_CHECKING:
    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.scheduler_service import SchedulerService

__all__ = ["DIVISION_CANCEL", "ROUND_CANCEL", "division_cancel_change", "round_cancel_change"]

ROUND_CANCEL = "season.round.cancel"
DIVISION_CANCEL = "season.division.cancel"

_COMMAND = "`/round cancel`"
NOT_ONGOING = f"❌ {_COMMAND} is available only while the season is ongoing."
ARCHIVED = "❌ This season is archived (COMPLETED) and cannot be modified."
DIVISION_NOT_ONGOING = "❌ `/division cancel` is available only while the season is ongoing."


def _already_cancelled(payload: dict[str, Any]) -> str:
    return f"❌ Round {payload['round_number']} in **{payload['division_name']}** is already cancelled."


def _results_entered(payload: dict[str, Any]) -> str:
    return (
        f"❌ Cannot cancel Round {payload['round_number']} — its results have already been "
        "entered, and the drivers' reports and appeals depend on it."
    )


def _submission_open(payload: dict[str, Any]) -> str:
    return (
        f"❌ Cannot cancel Round {payload['round_number']} — a results submission channel is "
        "currently open. Close the submission first."
    )


#: The job names, as the stop notice, the tests and `StepView` know them.
UNARM = "unarm"
APPLY = "apply"
NOTIFY_CHECKIN = "notify_checkin"
TAKE_DOWN_CALL = "take_down_call"
NOTIFY_FORECAST = "notify_forecast"
NOTIFY_RESULTS = "notify_results"
POST_CALENDAR = "post_calendar"
CLOSE = "close"

UNARM_DISCARDED = (
    "Nothing was cancelled: round {number} in **{division}** stands as it was. Run "
    "`/round cancel` again."
)
SAVE_DISCARDED = (
    "Nothing was cancelled, but round {number} in **{division}** no longer has its timed work: "
    "run `/round cancel` again."
)
_DISCARDED_NOTICE = "the notice could not be posted, and a league admin discarded it"
_DISCARDED_CALENDAR = (
    "the calendar could not be posted, and a league admin discarded it; run "
    "`/division calendar-sync`"
)

_EMPTY = StepView(name="", payload={}, result=None, done=False)


async def _guild(bot: Any) -> Any:
    """The league's server, or `GuildUnavailable` where it is not in the cache."""
    guild = await league_guild(bot)
    if guild is None:
        raise GuildUnavailable("the league's server is not in the cache")
    return guild


def _view(ctx: OutcomeContext, name: str) -> StepView:
    """The last job named *name*, or an empty view where the change has none."""
    return next((view for view in reversed(ctx.steps) if view.name == name), _EMPTY)


def _discarded(view: StepView) -> bool:
    return "discarded" in (view.result or {})


def _refusal_of(ctx: OutcomeContext) -> str | None:
    """What the save refused with as it ran, or None."""
    return (_view(ctx, APPLY).result or {}).get("refused")


def not_notified(ctx: OutcomeContext) -> list[notices.NoticeFailure]:
    """Every place the cancellation could not reach, one for each job that found none to tell or
    was discarded, in the order of the jobs, and the calendar posted as text."""
    failures: list[notices.NoticeFailure] = []
    for view in ctx.steps:
        result = view.result or {}
        target = {
            NOTIFY_CHECKIN: "check-in channel",
            NOTIFY_FORECAST: "forecast channel",
            NOTIFY_RESULTS: "results channel",
        }.get(view.name)
        name = str(view.payload.get("division_name", ""))
        if target is not None:
            if _discarded(view):
                failures.append(notices.NoticeFailure(name, target, _DISCARDED_NOTICE))
            elif result.get("unset"):
                failures.append(notices.NoticeFailure(name, target, "no channel is set"))
        elif view.name == TAKE_DOWN_CALL and _discarded(view):
            ids = [str(each) for each in result.get("undeleted", [])]
            failures.append(notices.NoticeFailure(
                name,
                "check-in call",
                f"{len(ids)} message(s) could not be deleted and must be removed by hand "
                f"(ids {', '.join(ids)})",
            ))
        elif view.name == POST_CALENDAR:
            if _discarded(view):
                failures.append(notices.NoticeFailure(name, "calendar", _DISCARDED_CALENDAR))
            elif result.get("fell_back"):
                failures.append(notices.NoticeFailure(
                    name, "calendar",
                    f"posted as text, as the picture could not be drawn ({result['problem']})",
                ))
    return failures


def checkin_audit(ctx: OutcomeContext) -> str:
    """The check-in of each round called off, read before its call came down and kept on the
    job whether it went through or was discarded."""
    return "".join(
        str((view.result or {}).get("audit", ""))
        for view in ctx.steps
        if view.name == TAKE_DOWN_CALL
    )


def round_cancel_change(
    *,
    modules: "ModuleService",
    seasons: SeasonService,
    scheduler: "SchedulerService",
    submission_open: Callable[[str, int], Awaitable[bool]],
    now: Callable[[], datetime],
) -> ChangeType:
    """The change that cancels a round; see the module.

    The payload is ``{"round_id", "round_number", "track_name", "division_id", "division_name",
    "season_number"}``. It names no season: a payload that does is read as holding every division
    of that season (`review_changes.division_job_in_hand`); the season a job needs travels on the
    jobs' own payloads. The builder hands in the *modules* (each notice's `still_due`), the
    *seasons* service, the *scheduler*, results' *submission_open* and the queue's clock *now*.
    """

    async def division_of(season_id: int, division_id: int) -> Division | None:
        return next(
            (each for each in await seasons.get_divisions(season_id) if each.id == division_id),
            None,
        )

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        season = await seasons.get_confirmed_season()
        if season is None or season.stage not in ONGOING_STAGES:
            return Verdict.refuse(NOT_ONGOING)
        try:
            await seasons.assert_season_mutable(season)
        except SeasonImmutableError:
            return Verdict.refuse(ARCHIVED)

        round_id = int(payload["round_id"])
        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute("SELECT status FROM rounds WHERE id = ?", (round_id,))
            row = await cursor.fetchone()
        if row is None:
            return Verdict.refuse(
                f"❌ Round {payload['round_number']} not found in division "
                f"`{payload['division_name']}`."
            )
        if row["status"] == RoundStatus.CANCELLED.value:
            return Verdict.refuse(_already_cancelled(payload))
        if row["status"] not in ROUND_CANCELLABLE:
            return Verdict.refuse(_results_entered(payload))
        if await submission_open(ctx.db_path, round_id):
            return Verdict.refuse(_submission_open(payload))
        return Verdict.go()

    # ── The jobs ────────────────────────────────────────────────────────────────

    async def unarm(ctx: StepContext) -> StepResult:
        scheduler.cancel_round(int(ctx.payload["round_id"]))
        return StepResult(result={"unarmed": True})

    async def unarmed(ctx: StepContext) -> bool:
        """The save is due only where the timed work was removed, not where that job was
        discarded."""
        return not _discarded(_view(ctx, UNARM))

    async def refusal_in_save(db: aiosqlite.Connection, payload: dict[str, Any]) -> str:
        """Why the guarded save moved nothing: the same words the check refuses in."""
        cursor = await db.execute(
            "SELECT status FROM rounds WHERE id = ?", (int(payload["round_id"]),)
        )
        row = await cursor.fetchone()
        if row is None:
            return (
                f"❌ Round {payload['round_number']} not found in division "
                f"`{payload['division_name']}`."
            )
        if row["status"] == RoundStatus.CANCELLED.value:
            return _already_cancelled(payload)
        if row["status"] not in ROUND_CANCELLABLE:
            return _results_entered(payload)
        return ARCHIVED

    async def apply(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The one save; see the module."""
        if ctx.actor_id is None or ctx.actor_name is None:
            raise RuntimeError("cancelling a round is the act of a member, and none is recorded")
        round_id = int(ctx.payload["round_id"])
        cursor = await db.execute(
            "SELECT d.id, d.season_id, d.status FROM rounds r "
            "JOIN divisions d ON d.id = r.division_id WHERE r.id = ?",
            (round_id,),
        )
        division = await cursor.fetchone()
        previous = None
        if division is not None:
            previous = await cancel_round_on(
                db, round_id, actor_id=ctx.actor_id, actor_name=ctx.actor_name, now=now()
            )
        if division is None or previous is None:
            # A backstop: the check passed, and something wrote the database after it.
            return StepResult(result={"refused": await refusal_in_save(db, ctx.payload)})

        cursor = await db.execute("SELECT status FROM divisions WHERE id = ?", (division["id"],))
        after = await cursor.fetchone()
        finished = (
            after is not None and after["status"] == "FINISHED" and division["status"] != "FINISHED"
        )
        target = {
            "division_id": int(division["id"]),
            "division_name": str(ctx.payload["division_name"]),
            "season_id": int(division["season_id"]),
        }
        cursor = await db.execute(
            "SELECT 1 FROM rsvp_embed_messages WHERE round_id = ?", (round_id,)
        )
        called = await cursor.fetchone() is not None
        planned = [PlannedStep(NOTIFY_CHECKIN, target)]
        if called:
            planned.append(PlannedStep(TAKE_DOWN_CALL, target))
        planned += [
            PlannedStep(NOTIFY_FORECAST, target),
            PlannedStep(NOTIFY_RESULTS, target),
            PlannedStep(POST_CALENDAR, {**target, "round_ids": [round_id]}),
        ]
        return StepResult(
            result={"previous": previous},
            then=tuple(planned),
            follow_ons=(
                (FollowOn(WIND_DOWN, {}, f"Winding the season down after {ctx.what}"),)
                if finished
                else ()
            ),
        )

    async def notify(ctx: StepContext, module: str) -> StepResult:
        guild = await _guild(ctx.bot)
        division = await division_of(
            int(ctx.step_payload["season_id"]), int(ctx.step_payload["division_id"])
        )
        if division is None:
            raise LookupError(f"division {ctx.step_payload['division_id']} is no longer there")
        problem = await notices.post_module_notice(
            ctx.bot, guild, division, module,
            scope=notices.SCOPE_ROUND,
            round_number=int(ctx.payload["round_number"]),
            track_name=ctx.payload["track_name"],
        )
        return StepResult(result={"unset": True} if problem else {"sent": True})

    async def notify_checkin(ctx: StepContext) -> StepResult:
        return await notify(ctx, "attendance")

    async def notify_forecast(ctx: StepContext) -> StepResult:
        return await notify(ctx, "weather")

    async def notify_results(ctx: StepContext) -> StepResult:
        return await notify(ctx, "results")

    async def attendance_on(_ctx: StepContext) -> bool:
        return await modules.is_attendance_enabled()

    async def weather_on(_ctx: StepContext) -> bool:
        return await modules.is_weather_enabled()

    async def results_on(_ctx: StepContext) -> bool:
        return await modules.is_results_enabled()

    async def take_down(ctx: StepContext) -> StepResult:
        division = await division_of(
            int(ctx.step_payload["season_id"]), int(ctx.step_payload["division_id"])
        )
        if division is None:
            raise LookupError(f"division {ctx.step_payload['division_id']} is no longer there")
        return StepResult(
            result=await notices.take_down_call(ctx.bot, division, int(ctx.payload["round_id"]))
        )

    async def calendar_posted(ctx: StepContext) -> bool:
        """The calendar is posted again only where it was ever posted."""
        division = await division_of(
            int(ctx.step_payload["season_id"]), int(ctx.step_payload["division_id"])
        )
        return division is not None and bool(division.calendar_message_id)

    async def post_calendar(ctx: StepContext) -> StepResult:
        """Post the division's calendar again, its division and rounds read as the job runs, the
        rounds called off drawn as cancelled."""
        guild = await _guild(ctx.bot)
        division = await division_of(
            int(ctx.step_payload["season_id"]), int(ctx.step_payload["division_id"])
        )
        if division is None:
            raise LookupError(f"division {ctx.step_payload['division_id']} is no longer there")
        called_off = {int(each) for each in ctx.step_payload["round_ids"]}
        rounds = [
            dataclasses.replace(each, status=RoundStatus.CANCELLED.value)
            if each.id in called_off
            else each
            for each in await seasons.get_division_rounds(division.id)
        ]
        posting = await post_division_calendar(
            ctx.bot,
            guild,
            division,
            rounds,
            await tracks_by_name(ctx.db_path),
            season_number=ctx.payload["season_number"],
            raise_on_failure=True,
            as_text=ctx.tries > 0,
        )
        return StepResult(
            result={
                "division": division.name,
                "problem": posting.problem,
                "fell_back": posting.fell_back,
            }
        )

    async def close(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Write the one line that records the cancellation, or the refusal the save met."""
        payload = ctx.payload
        if _discarded(_view(ctx, UNARM)) or _discarded(_view(ctx, APPLY)):
            return StepResult(result={"closed": True})
        refused = _refusal_of(ctx)
        if refused:
            return StepResult(
                result={"closed": True},
                lines=(refusal_line(ctx.named, _COMMAND, reply_reason(refused)),),
            )
        line = (
            f"{ctx.named} | /round cancel | Success\n"
            f"  division: {payload['division_name']}\n"
            f"  round: {payload['round_number']}"
            + checkin_audit(ctx)
            + notices.failure_log_lines(not_notified(ctx))
        )
        return StepResult(result={"closed": True}, lines=(line,))

    def outcome(ctx: OutcomeContext) -> str:
        words = {
            "number": ctx.payload["round_number"], "division": ctx.payload["division_name"],
        }
        if _discarded(_view(ctx, UNARM)):
            return UNARM_DISCARDED.format(**words)
        refused = _refusal_of(ctx)
        if refused:
            return refused
        if _discarded(_view(ctx, APPLY)):
            return SAVE_DISCARDED.format(**words)
        return (
            f"✅ Round **{words['number']}** in **{words['division']}** cancelled."
            + notices.failure_lines(not_notified(ctx))
        )

    # ── How each job is named, in the lines that say it stopped the queue ──────

    def named(round_text: str) -> Callable[[StepContext], Awaitable[str]]:
        async def describe(ctx: StepContext) -> str:
            return round_text.format(
                number=ctx.payload["round_number"],
                division=ctx.payload["division_name"],
                name=ctx.step_payload.get("division_name", ctx.payload["division_name"]),
            )

        return describe

    steps: dict[str, Step] = {
        UNARM: Step(
            UNARM, StepKind.ACT, unarm,
            describe=named("removing the timed work of round {number} in **{division}**"),
        ),
        APPLY: Step(
            APPLY, StepKind.SAVE, apply, still_due=unarmed,
            describe=named("cancelling round {number} in **{division}**"),
        ),
        NOTIFY_CHECKIN: Step(
            NOTIFY_CHECKIN, StepKind.ACT, notify_checkin, still_due=attendance_on,
            describe=named("posting the check-in notice for **{name}**"),
        ),
        TAKE_DOWN_CALL: Step(
            TAKE_DOWN_CALL, StepKind.ACT, take_down, still_due=attendance_on,
            describe=named("taking down the check-in call of round {number} in **{name}**"),
        ),
        NOTIFY_FORECAST: Step(
            NOTIFY_FORECAST, StepKind.ACT, notify_forecast, still_due=weather_on,
            describe=named("posting the forecast note for **{name}**"),
        ),
        NOTIFY_RESULTS: Step(
            NOTIFY_RESULTS, StepKind.ACT, notify_results, still_due=results_on,
            describe=named("posting the results note for **{name}**"),
        ),
        POST_CALENDAR: Step(
            POST_CALENDAR, StepKind.ACT, post_calendar, still_due=calendar_posted,
            describe=named("posting the calendar of **{name}**"),
        ),
        CLOSE: Step(
            CLOSE, StepKind.SAVE, close,
            describe=named("recording the cancellation of round {number} in **{division}**"),
        ),
    }
    return ChangeType(
        kind=ROUND_CANCEL,
        opening=(PlannedStep(UNARM), PlannedStep(APPLY), PlannedStep(CLOSE)),
        steps=steps,
        check=check,
        key=lambda payload: f"{ROUND_CANCEL}:{payload['round_id']}",
        doing=lambda payload: (
            f"Cancelling round {payload['round_number']} in **{payload['division_name']}**"
        ),
        outcome=outcome,
    )


def division_cancel_change(
    *,
    modules: "ModuleService",
    seasons: SeasonService,
    scheduler: "SchedulerService",
    now: Callable[[], datetime],
) -> ChangeType:
    """The change that cancels a division and the rounds of it that may still be cancelled.

    The payload is ``{"division_id", "division_name", "season_number"}``. It is the round's
    cancellation over a whole division: the same jobs, with the timed work of every round
    removed first. A round whose results submission stands open is cancelled with the rest, so
    the change is handed no *submission_open* (the owner decided so, 2026-10-08).
    """

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        season = await seasons.get_confirmed_season()
        if season is None or season.stage not in ONGOING_STAGES:
            return Verdict.refuse(DIVISION_NOT_ONGOING)
        try:
            await seasons.assert_season_mutable(season)
        except SeasonImmutableError:
            return Verdict.refuse(ARCHIVED)
        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute(
                "SELECT status FROM divisions WHERE id = ?", (int(payload["division_id"]),)
            )
            row = await cursor.fetchone()
        if row is None:
            return Verdict.refuse(f"❌ Division `{payload['division_name']}` not found.")
        if row["status"] == "CANCELLED":
            return Verdict.refuse(
                f"❌ Division **{payload['division_name']}** is already cancelled."
            )
        return Verdict.go()

    return ChangeType(
        kind=DIVISION_CANCEL,
        opening=(),
        steps={},
        check=check,
        key=lambda payload: f"{DIVISION_CANCEL}:{payload['division_id']}",
        doing=lambda payload: f"Cancelling **{payload['division_name']}**",
        outcome=lambda _ctx: "",
    )
