"""Cancelling a round or a division on the change queue (#439, slice 4b).

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

**A division's cancellation is the same change over a whole division** (`division_cancel_change`):
the timed work of every round of it is removed, read as the job runs; the save cancels the division
and each of its rounds that may still be cancelled, with the status each was cancelled from; the
notices go once for the division in its own words, and a check-in call is taken down for each round
called off that has one. It cancels a round whose results submission stands open with the rest.

**A second cancellation is refused at once, naming the job** (owner, 2026-10-08): a cancellation
of the round or of its division in hand refuses a round's, and one of the division a division's,
read through `cancellation_in_hand`. The refusal is made only as the change is asked for
(`CheckContext.change_id` is None), never as it runs: a division's cancellation asked after a
round's of that division is legitimate and runs behind it, and the check made as the round's
starts would find the division's later change and refuse itself. Run behind a cancellation of the
same round, "already cancelled" is what refuses it. `cancellation_in_hand` is read, too, by
`/round amend`, which is refused while a cancellation holds its round.
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
from leaguebot.core.models.round import ROUND_CANCELLABLE, Round, RoundStatus
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
    in_hand,
)
from leaguebot.core.services.season_lifecycle_service import (
    WIND_DOWN,
    advance_to_pending_completion_on,
)
from leaguebot.core.services.season_service import (
    SeasonImmutableError,
    SeasonService,
    cancel_division_on,
    cancel_round_on,
)
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.log_lines import refusal_line, reply_reason

if TYPE_CHECKING:
    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.scheduler_service import SchedulerService

__all__ = [
    "DIVISION_CANCEL",
    "ROUND_CANCEL",
    "cancellation_holding_amendment",
    "cancellation_in_hand",
    "division_cancel_change",
    "round_cancel_change",
]

ROUND_CANCEL = "season.round.cancel"
DIVISION_CANCEL = "season.division.cancel"

_COMMAND = "`/round cancel`"
NOT_ONGOING = f"❌ {_COMMAND} is available only while the season is ongoing."
ARCHIVED = "❌ This season is archived (COMPLETED) and cannot be modified."
DIVISION_COMMAND = "`/division cancel`"
DIVISION_NOT_ONGOING = f"❌ {DIVISION_COMMAND} is available only while the season is ongoing."


def _already_cancelled(payload: dict[str, Any]) -> str:
    return f"❌ Round {payload['round_number']} in **{payload['division_name']}** is already cancelled."


_HOW_TO_CLEAR = "If it has stopped, press Retry or Discard on its notice in the log channel."


def _job_of(job: int) -> str:
    """" (job #N)", or nothing where only the change's close is left."""
    return f" (job #{job})" if job else ""


def round_being_cancelled(payload: dict[str, Any], job: int) -> str:
    """The refusal of a second cancellation of a round."""
    return (
        f"⏳ Round {payload['round_number']} in **{payload['division_name']}** is already being "
        f"cancelled{_job_of(job)}. {_HOW_TO_CLEAR}"
    )


def division_being_cancelled(payload: dict[str, Any], job: int) -> str:
    """The refusal of a cancellation of a division, or of one of its rounds, while the
    division's own is in hand."""
    return (
        f"⏳ **{payload['division_name']}** is being cancelled{_job_of(job)}, and its rounds with "
        f"it. {_HOW_TO_CLEAR}"
    )


async def cancellation_in_hand(
    db_path: str, *, division_id: int, round_id: int | None = None
) -> int | None:
    """The first job of a cancellation that holds the division or the round, or None.

    Without *round_id*, a division's cancellation of *division_id*. With it, besides that, a
    round's cancellation of that round: a round's cancellation of another round of the division
    holds neither. A cancellation that is queued, running or stopped on a failure is in hand; one
    with every job done and only its close left gives 0. The nearest to its turn comes first.
    """
    for payload, job in await in_hand(db_path, (ROUND_CANCEL, DIVISION_CANCEL)):
        if "round_id" not in payload:
            if payload.get("division_id") == division_id:
                return job or 0
        elif round_id is not None and payload.get("round_id") == round_id:
            return job or 0
    return None


async def cancellation_holding_amendment(db_path: str, rnd: Round) -> str | None:
    """The refusal of `/round amend` while a cancellation of *rnd*, or of its division, is
    waiting, being carried out or stopped on the change queue, or None where none is.

    An amendment confirmed meanwhile would arm the round's timed work again, after the
    cancellation removed it, and forecasts would be posted for a round that is then cancelled
    (owner, 2026-10-08). Asked at the offer and again at the confirmation, as the rules are. It
    names the job, and leaves the name out where only the cancellation's close is left.
    """
    job = await cancellation_in_hand(db_path, division_id=rnd.division_id, round_id=rnd.id)
    if job is None:
        return None
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT name FROM divisions WHERE id = ?", (rnd.division_id,))
        found = await cursor.fetchone()
    division = found["name"] if found else "its division"
    return (
        f"⏸️ Round {rnd.round_number} in **{division}** is being cancelled{_job_of(job)}, so it "
        "cannot be amended. Let that finish, or press **Retry** or **Discard** on its notice if "
        "it has stopped."
    )


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
DIVISION_UNARM_DISCARDED = (
    "Nothing was cancelled: **{division}** stands as it was. Run `/division cancel` again."
)
DIVISION_SAVE_DISCARDED = (
    "Nothing was cancelled, but the rounds of **{division}** no longer have their timed work: "
    "run `/division cancel` again."
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


def _round_id(ctx: StepContext) -> int:
    """The round a job is about: its own, where a division called off several, else the change's."""
    round_id = ctx.step_payload.get("round_id")
    return int(ctx.payload["round_id"] if round_id is None else round_id)


def _named(text: str) -> Callable[[StepContext], Awaitable[str]]:
    """How a job is named in the lines that say it stopped the queue."""

    async def describe(ctx: StepContext) -> str:
        return text.format(
            number=ctx.step_payload.get("round_number", ctx.payload.get("round_number")),
            division=ctx.payload["division_name"],
            name=ctx.step_payload.get("division_name", ctx.payload["division_name"]),
        )

    return describe


def _follow_steps(
    *, modules: "ModuleService", seasons: SeasonService, scope: str
) -> dict[str, Step]:
    """The jobs both cancellations carry out after the save: each module's notice, a check-in
    call's take-down and the calendar's repost. *scope* is the notices' (`SCOPE_ROUND` or
    `SCOPE_DIVISION`). A job reads its division from its own payload (`division_id`,
    `division_name`, `season_id`), and a take-down its round from `round_id` there or, for a
    round's cancellation, from the change's.
    """

    async def division_of(season_id: int, division_id: int) -> Division | None:
        return next(
            (each for each in await seasons.get_divisions(season_id) if each.id == division_id),
            None,
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
            scope=scope,
            round_number=ctx.payload.get("round_number"),
            track_name=ctx.payload.get("track_name"),
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
            result=await notices.take_down_call(ctx.bot, division, _round_id(ctx))
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

    return {
        NOTIFY_CHECKIN: Step(
            NOTIFY_CHECKIN, StepKind.ACT, notify_checkin, still_due=attendance_on,
            describe=_named("posting the check-in notice for **{name}**"),
        ),
        TAKE_DOWN_CALL: Step(
            TAKE_DOWN_CALL, StepKind.ACT, take_down, still_due=attendance_on,
            describe=_named("taking down the check-in call of round {number} in **{name}**"),
        ),
        NOTIFY_FORECAST: Step(
            NOTIFY_FORECAST, StepKind.ACT, notify_forecast, still_due=weather_on,
            describe=_named("posting the forecast note for **{name}**"),
        ),
        NOTIFY_RESULTS: Step(
            NOTIFY_RESULTS, StepKind.ACT, notify_results, still_due=results_on,
            describe=_named("posting the results note for **{name}**"),
        ),
        POST_CALENDAR: Step(
            POST_CALENDAR, StepKind.ACT, post_calendar, still_due=calendar_posted,
            describe=_named("posting the calendar of **{name}**"),
        ),
    }


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
        if ctx.change_id is None:
            # Asked, not run: a cancellation of this round or of its division in hand refuses
            # it, but one queued behind a division's own must not refuse the division's, so the
            # check made as the change runs leaves it out.
            division_id = int(payload["division_id"])
            held = await cancellation_in_hand(ctx.db_path, division_id=division_id)
            if held is not None:
                return Verdict.refuse(division_being_cancelled(payload, held))
            held = await cancellation_in_hand(
                ctx.db_path, division_id=division_id, round_id=round_id
            )
            if held is not None:
                return Verdict.refuse(round_being_cancelled(payload, held))
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

    steps: dict[str, Step] = {
        **_follow_steps(modules=modules, seasons=seasons, scope=notices.SCOPE_ROUND),
        UNARM: Step(
            UNARM, StepKind.ACT, unarm,
            describe=_named("removing the timed work of round {number} in **{division}**"),
        ),
        APPLY: Step(
            APPLY, StepKind.SAVE, apply, still_due=unarmed,
            describe=_named("cancelling round {number} in **{division}**"),
        ),
        CLOSE: Step(
            CLOSE, StepKind.SAVE, close,
            describe=_named("recording the cancellation of round {number} in **{division}**"),
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
        if ctx.change_id is None:
            held = await cancellation_in_hand(
                ctx.db_path, division_id=int(payload["division_id"])
            )
            if held is not None:
                return Verdict.refuse(division_being_cancelled(payload, held))
        return Verdict.go()

    # ── The jobs ────────────────────────────────────────────────────────────────

    async def unarm(ctx: StepContext) -> StepResult:
        """Remove the timed work of every round of the division, read as the job runs: a round
        whose results are in loses its jobs too, as the division it belongs to is called off."""
        for each in await seasons.get_division_rounds(int(ctx.payload["division_id"])):
            scheduler.cancel_round(each.id)
        return StepResult(result={"unarmed": True})

    async def unarmed(ctx: StepContext) -> bool:
        """The save is due only where the timed work was removed, not where that job was
        discarded."""
        return not _discarded(_view(ctx, UNARM))

    async def apply(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The one save: the division and every round of it that may still be cancelled, each
        round with the status it was cancelled from, and the season moved on where this was the
        last division it waited on."""
        if ctx.actor_id is None or ctx.actor_name is None:
            raise RuntimeError("cancelling a division is the act of a member, and none is recorded")
        payload = ctx.payload
        division_id = int(payload["division_id"])
        cursor = await db.execute(
            "SELECT d.season_id, d.status, s.status AS season_status FROM divisions d "
            "JOIN seasons s ON s.id = d.season_id WHERE d.id = ?",
            (division_id,),
        )
        division = await cursor.fetchone()
        refusal = None
        if division is None:
            refusal = f"❌ Division `{payload['division_name']}` not found."
        elif division["status"] == "CANCELLED":
            refusal = f"❌ Division **{payload['division_name']}** is already cancelled."
        elif division["season_status"] in ("COMPLETED", "CANCELLED"):
            refusal = ARCHIVED
        if refusal is not None or division is None:
            # A backstop: the check passed, and something wrote the database after it.
            return StepResult(result={"refused": refusal})

        called_off = await cancel_division_on(
            db, division_id, actor_id=ctx.actor_id, actor_name=ctx.actor_name, now=now()
        )
        # Cancelling the last division still running leaves the season pending completion, in this
        # same save: `cancel_division_on` leaves the season's stage alone, for `/season cancel`.
        await advance_to_pending_completion_on(db, int(division["season_id"]))
        target = {
            "division_id": division_id,
            "division_name": str(payload["division_name"]),
            "season_id": int(division["season_id"]),
        }
        planned = [PlannedStep(NOTIFY_CHECKIN, target)]
        if called_off:
            marks = ",".join("?" * len(called_off))
            cursor = await db.execute(
                f"SELECT r.id, r.round_number FROM rounds r WHERE r.id IN ({marks}) "  # noqa: S608
                "AND EXISTS (SELECT 1 FROM rsvp_embed_messages m WHERE m.round_id = r.id) "
                "ORDER BY r.round_number",
                called_off,
            )
            planned += [
                PlannedStep(
                    TAKE_DOWN_CALL,
                    {**target, "round_id": int(row["id"]), "round_number": int(row["round_number"])},
                )
                for row in await cursor.fetchall()
            ]
        planned += [
            PlannedStep(NOTIFY_FORECAST, target),
            PlannedStep(NOTIFY_RESULTS, target),
            PlannedStep(POST_CALENDAR, {**target, "round_ids": called_off}),
        ]
        return StepResult(
            result={"called_off": called_off},
            then=tuple(planned),
            follow_ons=(FollowOn(WIND_DOWN, {}, f"Winding the season down after {ctx.what}"),),
        )

    async def close(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Write the one line that records the cancellation, or the refusal the save met."""
        if _discarded(_view(ctx, UNARM)) or _discarded(_view(ctx, APPLY)):
            return StepResult(result={"closed": True})
        refused = _refusal_of(ctx)
        if refused:
            return StepResult(
                result={"closed": True},
                lines=(refusal_line(ctx.named, DIVISION_COMMAND, reply_reason(refused)),),
            )
        line = (
            f"{ctx.named} | /division cancel | Success\n"
            f"  division: {ctx.payload['division_name']}"
            + checkin_audit(ctx)
            + notices.failure_log_lines(not_notified(ctx))
        )
        return StepResult(result={"closed": True}, lines=(line,))

    def outcome(ctx: OutcomeContext) -> str:
        name = ctx.payload["division_name"]
        if _discarded(_view(ctx, UNARM)):
            return DIVISION_UNARM_DISCARDED.format(division=name)
        refused = _refusal_of(ctx)
        if refused:
            return refused
        if _discarded(_view(ctx, APPLY)):
            return DIVISION_SAVE_DISCARDED.format(division=name)
        return f"✅ Division **{name}** cancelled." + notices.failure_lines(not_notified(ctx))

    steps: dict[str, Step] = {
        **_follow_steps(modules=modules, seasons=seasons, scope=notices.SCOPE_DIVISION),
        UNARM: Step(
            UNARM, StepKind.ACT, unarm,
            describe=_named("removing the timed work of the rounds of **{division}**"),
        ),
        APPLY: Step(
            APPLY, StepKind.SAVE, apply, still_due=unarmed,
            describe=_named("cancelling **{division}**"),
        ),
        CLOSE: Step(
            CLOSE, StepKind.SAVE, close,
            describe=_named("recording the cancellation of **{division}**"),
        ),
    }
    return ChangeType(
        kind=DIVISION_CANCEL,
        opening=(PlannedStep(UNARM), PlannedStep(APPLY), PlannedStep(CLOSE)),
        steps=steps,
        check=check,
        key=lambda payload: f"{DIVISION_CANCEL}:{payload['division_id']}",
        doing=lambda payload: f"Cancelling **{payload['division_name']}**",
        outcome=outcome,
    )
