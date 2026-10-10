"""Cancelling a round, a division or a season on the change queue (#439, slices 4b and 5).

`/round cancel` checks its confirmation word, the season and the names at the press, in the season
cog, then asks the queue for this change. Every other gate it had is the change type's `check`,
which judges the request when it is asked and again when it comes up to run, in the words the
command used, so that a round whose results were entered, or whose submission accepted a session,
while the cancellation waited is refused with the same reply. It imports nothing from a module and
no cog: results' readers and closer of the open submissions (`SubmissionHooks`) reach it through
the builder, and it reads the season through the service it is handed.

**A round whose submission stands open is cancelled only while it has accepted nothing** (owner,
2026-10-08, amending Constitution XII): any `session_results` row, of either status, is data the
cancellation would lose, and refuses it, at ask, as it runs and in the save as a backstop (`unarm`
lies between the check and the save). With nothing accepted, the save closes the submission, and
deleting its channel is results' own `delete_channel` job, the first after the save, because the
channel is where a manager pastes.

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
called off that has one. It cancels a round whose results submission stands open with the rest only
while that submission has accepted nothing: it is refused, naming the lowest-numbered round, once
any round of the division has one that has (a round in its review counts), and it closes the empty
ones in the save and deletes their channels, in round order, first after it.

**A second cancellation is refused at once, naming the job** (owner, 2026-10-08): a cancellation
of the round or of its division in hand refuses a round's, and one of the division a division's,
read through `cancellation_in_hand`. The refusal is made only as the change is asked for
(`CheckContext.change_id` is None), never as it runs: a division's cancellation asked after a
round's of that division is legitimate and runs behind it, and the check made as the round's
starts would find the division's later change and refuse itself. Run behind a cancellation of the
same round, "already cancelled" is what refuses it. `cancellation_in_hand` is read, too, by
`/round amend` (`cancellation_holding_amendment`), which refuses every amendment of any round of
a division, whatever it changes, while a cancellation of the division or of any round of it is in
hand: chiefly because an amended time renumbers the rounds under the cancellation.

**The other way round, a cancellation is refused while an amendment is in hand** (owner,
2026-10-08): a `/round amend` of a round of the division that is queued, running or stopped on the
queue refuses a cancellation of the division, or of any round of it, naming the job. It is read
from the queue, through the *amendment_in_hand* the builder hands both change types (the amendment
change imports this module, so this one cannot import it), never from a flag kept in memory
(architecture.md), so a restart forgets nothing. Like the second cancellation it is made only as
the change is asked for, never as it runs: the queue runs one change at a time and leaves nothing
overtaking a stopped job, so whatever amendment was ahead of this cancellation has finished, its
renumbering included, and the cancellation reads the round's number as it then stands.

**A season's cancellation is the same change over every division** (`season_cancel_change`, slice
5), in the order the core specification sets: the timed work of every round removed (`unarm`); then
one save, `apply`, that records the cancellation (each empty open submission closed, the
uncommitted placements discarded, the raced rounds awaiting verdicts made final, and every division
not cancelled cancelled with its rounds that may still be cancelled), **leaving the season itself
ongoing**, so that its channels stay readable while it is announced; the notices, the calls taken
down and the calendars of every division that was still running, in the season's words; each real
driver's roles taken back, after the notices, since the check-in notice mentions the division role;
the signup window closed and the forecasts posted under test mode cleared; and then a second save,
`end`, that writes the history marked cancelled, runs the driver pass, switches test mode off and
marks the season cancelled, **last**. The saved test-mode state is kept: a season cancelled is one
abandoned, not run to its end. The jobs the other ends of a season share
(`season_end_changes`) carry out the rest. A run that stops at `end` and is discarded is run again, and tells no
division twice, since by then none is still running.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    AuditRecord,
    FollowOn,
    GuildUnavailable,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.round import ROUND_CANCELLABLE, Round, RoundStatus
from leaguebot.core.models.division import Division
from leaguebot.core.models.season import ONGOING_STAGES, SeasonStage
from leaguebot.core.services import approval_checks
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
from leaguebot.core.services.season_end_changes import (
    END,
    SEASON_CANCEL,
    SEASON_ENDED_NOTICE,
    season_end_in_hand,
    season_end_refusal,
    shared_not_done,
    test_mode_on,
)
from leaguebot.core.services.season_end_service import (
    CLOSE_WINDOW,
    DISCARD_PORTRAITS,
    FLUSH_FORECASTS,
    REVOKE_ROLES,
    SEASON_END_CAUSE,
    season_end_steps,
    season_role_targets_on,
    write_driver_history_entries_on,
)
from leaguebot.core.services.season_lifecycle_service import (
    WIND_DOWN,
    SeasonEndHooks,
    advance_to_pending_completion_on,
    driver_jobs,
    run_driver_pass_on,
)
from leaguebot.core.services.season_service import (
    SeasonImmutableError,
    SeasonService,
    cancel_division_on,
    cancel_round_on,
    cancel_season_divisions_on,
    cancel_season_on,
    close_raced_rounds_on,
    discard_uncommitted_placements_on,
)
from leaguebot.core.services.test_mode_service import switch_test_mode_off_on
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.log_lines import refusal_line, reply_reason

if TYPE_CHECKING:
    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.placement_service import PlacementService
    from leaguebot.core.services.scheduler_service import SchedulerService

__all__ = [
    "DIVISION_CANCEL",
    "ROUND_CANCEL",
    "SEASON_CANCEL",
    "cancellation_holding_amendment",
    "cancellation_in_hand",
    "division_cancel_change",
    "round_cancel_change",
    "season_cancel_change",
]

ROUND_CANCEL = "season.round.cancel"
DIVISION_CANCEL = "season.division.cancel"

_COMMAND = "`/round cancel`"
NOT_ONGOING = f"❌ {_COMMAND} is available only while the season is ongoing."
ARCHIVED = "❌ This season is archived (COMPLETED) and cannot be modified."
DIVISION_COMMAND = "`/division cancel`"
DIVISION_NOT_ONGOING = f"❌ {DIVISION_COMMAND} is available only while the season is ongoing."
SEASON_COMMAND = "`/season cancel`"
SEASON_NO_SEASON = (
    "❌ No season is being raced, so there is none to cancel. A season whose placements are yet "
    "to be confirmed is abandoned with `/season abort`."
)
SEASON_NOT_ONGOING = (
    "❌ Every division of this season is done. Complete it with `/season complete` instead."
)
SEASON_CANCELLED = "✅ Season cancelled."
SEASON_CANCELLED_REASON = "Season cancelled"


def _division_finished(name: str) -> str:
    """The refusal of a division's cancellation once every round of it is over: there is
    nothing left to call off, and its results stand."""
    return f"❌ Division **{name}** has finished and cannot be cancelled."


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


def division_being_amended(payload: dict[str, Any], job: int) -> str:
    """The refusal of a cancellation of a division, or of one of its rounds, while a
    `/round amend` of a round of it is in hand on the queue, naming the job it waits on."""
    return (
        f"⏸️ A round of **{payload['division_name']}** is being amended{_job_of(job)}, so its "
        f"rounds cannot be cancelled until that is done. Let that finish, or press Retry or "
        "Discard on its notice if it has stopped."
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
    """The refusal of `/round amend` while a cancellation of *rnd*, of its division, or of
    another round of its division is waiting, being carried out or stopped on the change queue,
    or None where none is.

    An amendment confirmed meanwhile would arm the round's timed work again, after the
    cancellation removed it, and forecasts would be posted for a round that is then cancelled
    (owner, 2026-10-08). An amended date of another round would renumber the division, and the
    round being cancelled would be announced by a number it no longer bears: so every round of
    the division is held (owner, 2026-10-08, "Hold amends in the division"). Asked at the offer
    and again at the confirmation, as the rules are. It names the job, and leaves the name out
    where only the cancellation's close is left.
    """
    job = await cancellation_in_hand(db_path, division_id=rnd.division_id, round_id=rnd.id)
    if job is not None:
        return (
            f"⏸️ Round {rnd.round_number} in **{await _division_name(db_path, rnd)}** is being "
            f"cancelled{_job_of(job)}, so it cannot be amended. Let that finish, or press "
            "**Retry** or **Discard** on its notice if it has stopped."
        )
    for payload, other in await in_hand(db_path, (ROUND_CANCEL,)):
        if payload.get("division_id") == rnd.division_id:
            return (
                f"⏸️ A round of **{await _division_name(db_path, rnd)}** is being "
                f"cancelled{_job_of(other or 0)}, so its rounds cannot be amended until that is "
                "done. Let that finish, or press **Retry** or **Discard** on its notice if it has "
                "stopped."
            )
    return None


async def _division_name(db_path: str, rnd: Round) -> str:
    """The name of *rnd*'s division, as a refusal names it."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT name FROM divisions WHERE id = ?", (rnd.division_id,))
        found = await cursor.fetchone()
    return str(found["name"]) if found else "its division"


def _results_entered(payload: dict[str, Any]) -> str:
    return (
        f"❌ Cannot cancel Round {payload['round_number']} — its results have already been "
        "entered, and the drivers' reports and appeals depend on it."
    )


def _division_accepted(payload: dict[str, Any], number: int, channel_id: int) -> str:
    return (
        f"❌ Cannot cancel **{payload['division_name']}** — results have already been accepted in "
        f"the submission channel of round {number} (<#{channel_id}>), and cancelling would lose "
        "them."
    )


def _accepted(payload: dict[str, Any], channel_id: int) -> str:
    return (
        f"❌ Cannot cancel Round {payload['round_number']} — results have already been accepted "
        f"in its submission channel <#{channel_id}>, and cancelling would lose them."
    )


class OpenSubmissionRead(Protocol):
    """A round's results submission standing open, as results reads it: its round, its channel,
    and whether it has accepted any session's results (or a session entered as not held)."""

    @property
    def round_id(self) -> int: ...
    @property
    def channel_id(self) -> int: ...
    @property
    def accepted(self) -> bool: ...


@dataclasses.dataclass(frozen=True)
class SubmissionHooks:
    """What a cancellation needs of results' submissions, handed it by the builder so that this
    module imports no module (architecture.md, "How modules and core fit together").

    *open_submissions* reads, on a connection of its own, each open submission among some rounds;
    *open_submissions_on* does so on the save's connection; *close_submissions_on* marks each one
    closed on it, committing nothing, and gives ``(round_id, channel_id)`` for each; *delete_step*
    is results' own `delete_channel` job, which deletes a channel and completes where it is
    already gone.
    """

    open_submissions: Callable[[str, Iterable[int]], Awaitable[list[Any]]]
    open_submissions_on: Callable[[aiosqlite.Connection, Iterable[int]], Awaitable[list[Any]]]
    close_submissions_on: Callable[[aiosqlite.Connection, Iterable[int]], Awaitable[list[tuple[int, int]]]]
    delete_step: Step


#: The job names, as the stop notice, the tests and `StepView` know them.
UNARM = "unarm"
APPLY = "apply"
NOTIFY_CHECKIN = "notify_checkin"
TAKE_DOWN_CALL = "take_down_call"
NOTIFY_FORECAST = "notify_forecast"
NOTIFY_RESULTS = "notify_results"
POST_CALENDAR = "post_calendar"
CLOSE = "close"
DELETE_CHANNEL = "delete_channel"

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
_DISCARDED_CHANNEL = (
    "it could not be deleted, and a league admin discarded it; delete <#{channel_id}> by hand"
)
_DISCARDED_CALENDAR = (
    "the calendar could not be posted, and a league admin discarded it; run "
    "`/division calendar-sync`"
)

SEASON_UNARM_DISCARDED = (
    "Nothing was cancelled: season {number} stands as it was. Run `/season cancel` again."
)
SEASON_SAVE_DISCARDED = (
    "Nothing was cancelled, but the rounds of season {number} no longer have their timed work: "
    "run `/season cancel` again."
)
SEASON_END_DISCARDED = (
    "Season {number}'s divisions and rounds are cancelled, but the season itself is not: the "
    "save that records its end was discarded. Run `/season cancel` again to finish it; if the "
    "season has since moved to pending completion, complete it with `/season complete`."
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


def _saved_number(ctx: OutcomeContext) -> Any:
    """The number of the round cancelled, as the closing job read it after a discarded save, else
    as the save found it, else as the removal of its timed work did, else the press's: a
    discarded save never read it, and the round may have been renumbered since each of them."""
    for name in (CLOSE, APPLY, UNARM):
        number = (_view(ctx, name).result or {}).get("round_number")
        if number is not None:
            return number
    return ctx.payload["round_number"]


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
        elif view.name == DELETE_CHANNEL:
            if _discarded(view):
                failures.append(notices.NoticeFailure(
                    name,
                    f"results submission channel of round {view.payload.get('round_number')}",
                    _DISCARDED_CHANNEL.format(channel_id=view.payload["channel_id"]),
                ))
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


async def _round_number_now(ctx: StepContext) -> Any:
    """The number the round bears now, which another round's amended date may have changed since
    the press; the press's where the round cannot be read."""
    if "round_id" not in ctx.payload:
        return ctx.payload.get("round_number")
    async with get_connection(ctx.db_path) as db:
        cursor = await db.execute(
            "SELECT round_number FROM rounds WHERE id = ?", (int(ctx.payload["round_id"]),)
        )
        row = await cursor.fetchone()
    return ctx.payload.get("round_number") if row is None else row["round_number"]


def _named(
    text: str, *, number_now: bool = False
) -> Callable[[StepContext], Awaitable[str]]:
    """How a job is named in the lines that say it stopped the queue; with *number_now*, by the
    number its round bears when the line is written, for a job that runs before the save reads it."""

    async def describe(ctx: StepContext) -> str:
        number = ctx.step_payload.get("round_number")
        if number is None:
            number = await _round_number_now(ctx) if number_now else ctx.payload.get("round_number")
        # A season's payload names no division: its jobs carry theirs on their own.
        division = ctx.payload.get("division_name", ctx.step_payload.get("division_name", ""))
        return text.format(
            number=number,
            division=division,
            name=ctx.step_payload.get("division_name", division),
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
            round_number=ctx.step_payload.get("round_number", ctx.payload.get("round_number")),
            track_name=ctx.step_payload.get("track_name", ctx.payload.get("track_name")),
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
    submissions: SubmissionHooks,
    amendment_in_hand: Callable[[int], Awaitable[int | None]],
    now: Callable[[], datetime],
) -> ChangeType:
    """The change that cancels a round; see the module.

    The payload is ``{"round_id", "round_number", "track_name", "division_id", "division_name",
    "season_number"}``. It names no season: a payload that does is read as holding every division
    of that season (`review_changes.division_job_in_hand`); the season a job needs travels on the
    jobs' own payloads. The builder hands in the *modules* (each notice's `still_due`), the
    *seasons* service, the *scheduler*, results' *submissions* (`SubmissionHooks`), *amendment_in_hand* (the job a
    `/round amend` of a round of a division waits on, or None) and the queue's clock *now*.
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
            cursor = await db.execute(
                "SELECT status, round_number FROM rounds WHERE id = ?", (round_id,)
            )
            row = await cursor.fetchone()
        if row is None:
            return Verdict.refuse(
                f"❌ Round {payload['round_number']} not found in division "
                f"`{payload['division_name']}`."
            )
        # The number the round bears now: another round's amended date may have renumbered the
        # division since the press, and the refusal names the round it is about.
        payload = {**payload, "round_number": row["round_number"]}
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
            amended = await amendment_in_hand(division_id)
            if amended is not None:
                return Verdict.refuse(division_being_amended(payload, amended))
        if row["status"] not in ROUND_CANCELLABLE:
            return Verdict.refuse(_results_entered(payload))
        for each in await submissions.open_submissions(ctx.db_path, [round_id]):
            if each.accepted:
                return Verdict.refuse(_accepted(payload, each.channel_id))
        return Verdict.go()

    # ── The jobs ────────────────────────────────────────────────────────────────

    async def unarm(ctx: StepContext) -> StepResult:
        # The number is read first, so that nothing after the removal can stop this job: a
        # removal stopped and discarded would be named as nothing done.
        number = await _round_number_now(ctx)
        scheduler.cancel_round(int(ctx.payload["round_id"]))
        return StepResult(result={"unarmed": True, "round_number": number})

    async def unarmed(ctx: StepContext) -> bool:
        """The save is due only where the timed work was removed, not where that job was
        discarded."""
        return not _discarded(_view(ctx, UNARM))

    async def refusal_in_save(db: aiosqlite.Connection, payload: dict[str, Any]) -> str:
        """Why the guarded save moved nothing: the same words the check refuses in."""
        cursor = await db.execute(
            "SELECT status, round_number FROM rounds WHERE id = ?", (int(payload["round_id"]),)
        )
        row = await cursor.fetchone()
        if row is None:
            return (
                f"❌ Round {payload['round_number']} not found in division "
                f"`{payload['division_name']}`."
            )
        payload = {**payload, "round_number": row["round_number"]}
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
            "SELECT d.id, d.season_id, d.status, r.round_number, r.track_name FROM rounds r "
            "JOIN divisions d ON d.id = r.division_id WHERE r.id = ?",
            (round_id,),
        )
        division = await cursor.fetchone()
        previous = None
        if division is not None:
            # A backstop for the check: `unarm` lies between them, and a session may have been
            # accepted since. Nothing is written then.
            for each in await submissions.open_submissions_on(db, [round_id]):
                if each.accepted:
                    return StepResult(result={"refused": _accepted(
                        {**ctx.payload, "round_number": division["round_number"]},
                        each.channel_id,
                    )})
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
        # The round's number and track as the save finds them: another round's date amended
        # while this waited may have renumbered the division since the press.
        target = {
            "division_id": int(division["id"]),
            "division_name": str(ctx.payload["division_name"]),
            "season_id": int(division["season_id"]),
            "round_number": int(division["round_number"]),
            "track_name": division["track_name"],
        }
        cursor = await db.execute(
            "SELECT 1 FROM rsvp_embed_messages WHERE round_id = ?", (round_id,)
        )
        called = await cursor.fetchone() is not None
        # An open submission with nothing accepted is closed in this save, and its channel
        # deleted first of all after it, since that is where a manager pastes.
        planned = [
            PlannedStep(DELETE_CHANNEL, {
                "channel_id": channel_id,
                "round_id": closed_round,
                "what": "results submission channel",
                "reason": "Round cancelled",
                "division_name": target["division_name"],
                "round_number": target["round_number"],
            })
            for closed_round, channel_id in await submissions.close_submissions_on(db, [round_id])
        ]
        planned.append(PlannedStep(NOTIFY_CHECKIN, target))
        if called:
            planned.append(PlannedStep(TAKE_DOWN_CALL, target))
        planned += [
            PlannedStep(NOTIFY_FORECAST, target),
            PlannedStep(NOTIFY_RESULTS, target),
            PlannedStep(POST_CALENDAR, {**target, "round_ids": [round_id]}),
        ]
        return StepResult(
            result={
                "previous": previous,
                "round_number": target["round_number"],
                "track_name": target["track_name"],
            },
            then=tuple(planned),
            follow_ons=(
                (FollowOn(WIND_DOWN, {}, f"Winding the season down after {ctx.what}"),)
                if finished
                else ()
            ),
        )

    async def close(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Write the one line that records the cancellation, or the refusal the save met.

        Where the save was discarded, it never read the round's number: the round is read here,
        the last job, so that the outcome names it by the number it bears now, which another
        round's amended date may have changed while the save stood stopped."""
        payload = ctx.payload
        if _discarded(_view(ctx, UNARM)) or _discarded(_view(ctx, APPLY)):
            cursor = await db.execute(
                "SELECT round_number FROM rounds WHERE id = ?", (int(payload["round_id"]),)
            )
            row = await cursor.fetchone()
            result: dict[str, Any] = {"closed": True}
            if row is not None:
                result["round_number"] = row["round_number"]
            return StepResult(result=result)
        refused = _refusal_of(ctx)
        if refused:
            return StepResult(
                result={"closed": True},
                lines=(refusal_line(ctx.named, _COMMAND, reply_reason(refused)),),
            )
        line = (
            f"{ctx.named} | /round cancel | Success\n"
            f"  division: {payload['division_name']}\n"
            f"  round: {_saved_number(ctx)}"
            + checkin_audit(ctx)
            + notices.failure_log_lines(not_notified(ctx))
        )
        return StepResult(result={"closed": True}, lines=(line,))

    def outcome(ctx: OutcomeContext) -> str:
        words = {
            "number": _saved_number(ctx), "division": ctx.payload["division_name"],
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
        DELETE_CHANNEL: submissions.delete_step,
        UNARM: Step(
            UNARM, StepKind.ACT, unarm,
            describe=_named(
                "removing the timed work of round {number} in **{division}**", number_now=True
            ),
        ),
        APPLY: Step(
            APPLY, StepKind.SAVE, apply, still_due=unarmed,
            describe=_named(
                "cancelling round {number} in **{division}**", number_now=True
            ),
        ),
        CLOSE: Step(
            CLOSE, StepKind.SAVE, close,
            describe=_named(
                "recording the cancellation of round {number} in **{division}**", number_now=True
            ),
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
    submissions: SubmissionHooks,
    amendment_in_hand: Callable[[int], Awaitable[int | None]],
    now: Callable[[], datetime],
) -> ChangeType:
    """The change that cancels a division and the rounds of it that may still be cancelled.

    The payload is ``{"division_id", "division_name", "season_number"}``. It is the round's
    cancellation over a whole division: the same jobs, with the timed work of every round
    removed first. A round whose results submission stands open is cancelled with the rest only
    while that submission has accepted nothing (owner, 2026-10-08, amending Constitution XII):
    any round of the division whose open submission has accepted a session refuses the whole
    cancellation, at ask, as it runs and in the save as a backstop, naming the lowest-numbered
    such round. In the save the empty submissions of the rounds called off are closed, and the
    deletion of each channel is a job, in round order, first after it.
    """

    async def accepted_in(
        rounds: list[tuple[int, int]], read: Callable[[list[int]], Awaitable[list[Any]]]
    ) -> tuple[int, int] | None:
        """``(round number, channel id)`` of the lowest-numbered round among *rounds*
        (``(id, number)`` pairs) whose open submission has accepted a session, or None."""
        numbers = dict(rounds)
        held = [each for each in await read(list(numbers)) if each.accepted]
        if not held:
            return None
        first = min(held, key=lambda each: numbers[each.round_id])
        return numbers[first.round_id], first.channel_id

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
                "SELECT status, name FROM divisions WHERE id = ?", (int(payload["division_id"]),)
            )
            row = await cursor.fetchone()
        if row is None:
            return Verdict.refuse(f"❌ Division `{payload['division_name']}` not found.")
        if row["status"] == "CANCELLED":
            return Verdict.refuse(
                f"❌ Division **{payload['division_name']}** is already cancelled."
            )
        if row["status"] == "FINISHED":
            return Verdict.refuse(_division_finished(str(row["name"])))
        found = await accepted_in(
            [
                (each.id, each.round_number)
                for each in await seasons.get_division_rounds(int(payload["division_id"]))
            ],
            lambda ids: submissions.open_submissions(ctx.db_path, ids),
        )
        if found is not None:
            return Verdict.refuse(_division_accepted(payload, *found))
        if ctx.change_id is None:
            held = await cancellation_in_hand(
                ctx.db_path, division_id=int(payload["division_id"])
            )
            if held is not None:
                return Verdict.refuse(division_being_cancelled(payload, held))
            amended = await amendment_in_hand(int(payload["division_id"]))
            if amended is not None:
                return Verdict.refuse(division_being_amended(payload, amended))
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
            "SELECT d.season_id, d.status, d.name, s.status AS season_status FROM divisions d "
            "JOIN seasons s ON s.id = d.season_id WHERE d.id = ?",
            (division_id,),
        )
        division = await cursor.fetchone()
        refusal = None
        if division is None:
            refusal = f"❌ Division `{payload['division_name']}` not found."
        elif division["status"] == "CANCELLED":
            refusal = f"❌ Division **{payload['division_name']}** is already cancelled."
        elif division["status"] == "FINISHED":
            refusal = _division_finished(str(division["name"]))
        elif division["season_status"] in ("COMPLETED", "CANCELLED"):
            refusal = ARCHIVED
        if refusal is not None or division is None:
            # A backstop: the check passed, and something wrote the database after it.
            return StepResult(result={"refused": refusal})

        cursor = await db.execute(
            "SELECT id, round_number FROM rounds WHERE division_id = ?", (division_id,)
        )
        numbers = {int(each["id"]): int(each["round_number"]) for each in await cursor.fetchall()}
        # A backstop for the check: `unarm` lies between them, and a session may have been
        # accepted since. Nothing is written then.
        found = await accepted_in(
            list(numbers.items()), lambda ids: submissions.open_submissions_on(db, ids)
        )
        if found is not None:
            return StepResult(result={"refused": _division_accepted(payload, *found)})

        called_off = await cancel_division_on(
            db, division_id, actor_id=ctx.actor_id, actor_name=ctx.actor_name, now=now()
        )
        closed = sorted(
            await submissions.close_submissions_on(db, called_off),
            key=lambda each: numbers[each[0]],
        )
        # Cancelling the last division still running leaves the season pending completion, in this
        # same save: `cancel_division_on` leaves the season's stage alone, for `/season cancel`.
        await advance_to_pending_completion_on(db, int(division["season_id"]))
        target = {
            "division_id": division_id,
            "division_name": str(payload["division_name"]),
            "season_id": int(division["season_id"]),
        }
        planned = [
            PlannedStep(DELETE_CHANNEL, {
                "channel_id": channel_id,
                "round_id": closed_round,
                "what": "results submission channel",
                "reason": "Division cancelled",
                "division_name": target["division_name"],
                "round_number": numbers[closed_round],
            })
            for closed_round, channel_id in closed
        ]
        planned.append(PlannedStep(NOTIFY_CHECKIN, target))
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
        DELETE_CHANNEL: submissions.delete_step,
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


def season_amended(held: Any) -> str:
    """The refusal of a season's cancellation while a round is being amended: its corrections
    would reach every driver's history before they are approved (#345)."""
    return (
        f"❌ Cannot cancel the season — round {held['round_number']} of "
        f"**{held['division_name']}** is being amended in <#{held['channel_id']}>. "
        "Finish or cancel it first: cancelling writes every driver's history from the "
        "standings, which would carry its corrections before they are approved."
    )


def _season_accepted(division_name: str, number: int, channel_id: int) -> str:
    return (
        f"❌ Cannot cancel the season — results have already been accepted in the submission "
        f"channel of round {number} of **{division_name}** (<#{channel_id}>), and cancelling "
        "would lose them."
    )


def _first_accepted(
    standing: dict[int, tuple[str, int]], open_ones: Iterable[Any]
) -> str | None:
    """The refusal naming the first round, by division then round, whose open submission has
    accepted a session, or None. *standing* maps a round's id to its division's name and its
    number, in the order the season lists them."""
    held = [each for each in open_ones if each.accepted]
    if not held:
        return None
    order = list(standing)
    first = min(held, key=lambda each: order.index(each.round_id))
    name, number = standing[first.round_id]
    return _season_accepted(name, number, first.channel_id)


def season_cancel_change(
    *,
    modules: "ModuleService",
    seasons: SeasonService,
    scheduler: "SchedulerService",
    placement: "PlacementService",
    submissions: SubmissionHooks,
    hooks: SeasonEndHooks,
    now: Callable[[], datetime],
) -> ChangeType:
    """The change that cancels the season being raced; see the module.

    The payload is ``{"season_id", "season_number"}``. The builder hands in the *modules*, the
    *seasons* service, the *scheduler*, the *placement* service (which takes back roles), results'
    *submissions*, the season end's *hooks* and the queue's clock *now*.
    """

    async def check(ctx: CheckContext) -> Verdict:
        season = await seasons.get_confirmed_season()
        if season is None or season.id != int(ctx.payload["season_id"]):
            return Verdict.refuse(SEASON_NO_SEASON)

        # **A second cancellation, asked while the first is in hand** (owner, 2026-10-09):
        # refused at once, naming the job. Only as it is asked: as the change runs, it would find
        # itself.
        if ctx.change_id is None:
            hand = await season_end_in_hand(ctx.db_path, season.id)
            if hand is not None:
                return Verdict.refuse(
                    season_end_refusal(SEASON_CANCEL, hand, season.season_number)
                )
        if season.stage not in ONGOING_STAGES:
            return Verdict.refuse(SEASON_NOT_ONGOING)

        # **Not while a round is being amended** (#345, decided 2026-09-21): cancelling writes
        # every driver's history from the standings, which hold an open amendment's corrections
        # before they are approved, and the history is never rewritten.
        held = await hooks.amendment_open(ctx.db_path, season.id)
        if held is not None:
            return Verdict.refuse(season_amended(held))

        # **Not while a submission holds accepted results** (owner, 2026-10-08, amending
        # Constitution XII): a session accepted, or entered as not held, is data the cancellation
        # would lose, and a round in its review counts.
        standing: dict[int, tuple[str, int]] = {}
        for division in await seasons.get_divisions(season.id):
            if division.status == "CANCELLED":
                continue
            for each in await seasons.get_division_rounds(division.id):
                standing[each.id] = (division.name, each.round_number)
        refusal = _first_accepted(
            standing, await submissions.open_submissions(ctx.db_path, list(standing))
        )
        if refusal is not None:
            return Verdict.refuse(refusal)
        return Verdict.go()

    # ── The jobs ────────────────────────────────────────────────────────────────

    async def unarm(ctx: StepContext) -> StepResult:
        """Remove the timed work of every round of every division, read as the job runs, and the
        job that would end the season on its own."""
        for division in await seasons.get_divisions(int(ctx.payload["season_id"])):
            for each in await seasons.get_division_rounds(division.id):
                scheduler.cancel_round(each.id)
        scheduler.cancel_season_end()
        return StepResult(result={"unarmed": True})

    async def unarmed(ctx: StepContext) -> bool:
        """The save is due only where the timed work was removed, not where that job was
        discarded."""
        return not _discarded(_view(ctx, UNARM))

    async def apply(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The first save: the cancellation recorded, the season itself left ongoing; see the
        module. It plans every job that announces the cancellation, then the second save."""
        if ctx.actor_id is None or ctx.actor_name is None:
            raise RuntimeError("cancelling a season is the act of a member, and none is recorded")
        season_id = int(ctx.payload["season_id"])
        cursor = await db.execute(
            "SELECT status, stage FROM seasons WHERE id = ?", (season_id,)
        )
        season = await cursor.fetchone()
        if season is None or season["status"] != "ACTIVE":
            return StepResult(result={"refused": SEASON_NO_SEASON})
        if SeasonStage(season["stage"]) not in ONGOING_STAGES:
            return StepResult(result={"refused": SEASON_NOT_ONGOING})

        cursor = await db.execute(
            "SELECT d.name AS division_name, r.id AS round_id, r.round_number FROM rounds r "
            "JOIN divisions d ON d.id = r.division_id WHERE d.season_id = ? "
            "AND d.status != 'CANCELLED' ORDER BY d.tier, r.round_number",
            (season_id,),
        )
        standing = {
            int(row["round_id"]): (str(row["division_name"]), int(row["round_number"]))
            for row in await cursor.fetchall()
        }
        # A backstop for the check: `unarm` lies between them, and a session may have been
        # accepted since. Nothing is written then.
        refusal = _first_accepted(standing, await submissions.open_submissions_on(db, list(standing)))
        if refusal is not None:
            return StepResult(result={"refused": refusal})

        # The empty open submissions are closed first, then the placements not yet confirmed
        # discarded so that they earn no history, then the rounds raced and awaiting verdicts made
        # final: the driver pass at the end would otherwise delete the drivers of those rounds
        # (#216).
        closed = sorted(
            await submissions.close_submissions_on(db, list(standing)),
            key=lambda each: list(standing).index(each[0]),
        )
        await discard_uncommitted_placements_on(db, season_id)
        await close_raced_rounds_on(
            db, season_id, actor_id=ctx.actor_id, actor_name=ctx.actor_name, now=now()
        )
        # Told: every division still running. A division cancelled was told when it was, and one
        # finished has no round left to call off and nothing to hear.
        cursor = await db.execute(
            "SELECT id, name FROM divisions WHERE season_id = ? "
            "AND status NOT IN ('CANCELLED', 'FINISHED') ORDER BY tier",
            (season_id,),
        )
        running = [(int(row["id"]), str(row["name"])) for row in await cursor.fetchall()]
        called_off = await cancel_season_divisions_on(
            db, season_id, actor_id=ctx.actor_id, actor_name=ctx.actor_name, now=now()
        )

        planned = [
            PlannedStep(DELETE_CHANNEL, {
                "channel_id": channel_id,
                "round_id": closed_round,
                "what": "results submission channel",
                "reason": "Season cancelled",
                "division_name": standing[closed_round][0],
                "round_number": standing[closed_round][1],
            })
            for closed_round, channel_id in closed
        ]
        for division_id, division_name in running:
            target = {
                "division_id": division_id,
                "division_name": division_name,
                "season_id": season_id,
            }
            planned.append(PlannedStep(NOTIFY_CHECKIN, target))
            rounds = called_off.get(division_id, [])
            if rounds:
                marks = ",".join("?" * len(rounds))
                cursor = await db.execute(
                    f"SELECT r.id, r.round_number FROM rounds r WHERE r.id IN ({marks}) "  # noqa: S608
                    "AND EXISTS (SELECT 1 FROM rsvp_embed_messages m WHERE m.round_id = r.id) "
                    "ORDER BY r.round_number",
                    rounds,
                )
                planned += [
                    PlannedStep(TAKE_DOWN_CALL, {
                        **target, "round_id": int(row["id"]),
                        "round_number": int(row["round_number"]),
                    })
                    for row in await cursor.fetchall()
                ]
            planned += [
                PlannedStep(NOTIFY_FORECAST, target),
                PlannedStep(NOTIFY_RESULTS, target),
                PlannedStep(POST_CALENDAR, {**target, "round_ids": rounds}),
            ]
        # The roles are taken after the notices: the check-in notice mentions the division role.
        for target_driver in await season_role_targets_on(db, season_id):
            planned.append(PlannedStep(REVOKE_ROLES, {
                "user_id": int(target_driver["user_id"]),
                "role_ids": [int(role) for role in target_driver["role_ids"]],
                "reason": SEASON_CANCELLED_REASON,
            }))
        planned.append(PlannedStep(CLOSE_WINDOW, {"cause": SEASON_END_CAUSE}))
        if await test_mode_on(db):
            planned.append(PlannedStep(FLUSH_FORECASTS))
        planned.append(PlannedStep(END))
        return StepResult(
            result={"divisions": [division_id for division_id, _ in running]},
            then=tuple(planned),
        )

    async def end(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The second save: the history, the driver pass, test mode and the season, last."""
        season_id = int(ctx.payload["season_id"])
        cursor = await db.execute(
            "SELECT status, season_number FROM seasons WHERE id = ?", (season_id,)
        )
        season = await cursor.fetchone()
        if season is None or season["status"] != "ACTIVE":
            return StepResult(result={"refused": SEASON_NO_SEASON})
        number = int(season["season_number"])

        test_mode = await test_mode_on(db)
        await write_driver_history_entries_on(db, season_id, number, force_cancelled=True)
        driver_pass = await run_driver_pass_on(db)
        if test_mode:
            await switch_test_mode_off_on(db)
        # The season's row is the last thing written (Constitution, the Season Archive). The
        # saved test-mode state is kept: a season cancelled is one abandoned.
        await cancel_season_on(db, season_id)

        planned = list(
            driver_jobs(
                driver_pass.drivers, driver_pass.driver_role_id,
                notice=SEASON_ENDED_NOTICE, reason=SEASON_CANCELLED_REASON,
            )
        )
        if driver_pass.accounts:
            planned.append(PlannedStep(DISCARD_PORTRAITS, {"accounts": driver_pass.accounts}))
        audit = AuditRecord(
            "SEASON_CANCELLED",
            {"season_id": season_id, "status": "ACTIVE"},
            {
                "season_id": season_id,
                "season_number": number,
                "status": "CANCELLED",
                "drivers_returned": driver_pass.reset,
                "drivers_deleted": len(driver_pass.deleted),
            },
        )
        return StepResult(
            result={"season_number": number},
            audits=(audit,),
            then=tuple(planned),
        )

    def _refused(ctx: OutcomeContext) -> str | None:
        """What a save refused with as it ran, or None."""
        for name in (APPLY, END):
            refused = (_view(ctx, name).result or {}).get("refused")
            if refused:
                return str(refused)
        return None

    def _ended(ctx: OutcomeContext) -> bool:
        """Whether the season's end was saved: `end` is done, and neither refused nor discarded."""
        view = _view(ctx, END)
        return view.done and not (_discarded(view) or (view.result or {}).get("refused"))

    async def close(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Write the one line that records the cancellation, or the refusal a save met."""
        refused = _refused(ctx)
        if refused is not None:
            return StepResult(
                result={"closed": True},
                lines=(refusal_line(ctx.named, SEASON_COMMAND, reply_reason(refused)),),
            )
        if _discarded(_view(ctx, UNARM)) or _discarded(_view(ctx, APPLY)) or not _ended(ctx):
            return StepResult(result={"closed": True})
        line = (
            f"{ctx.named} | /season cancel | Success"
            + checkin_audit(ctx)
            + notices.failure_log_lines(not_notified(ctx))
            + "".join(f"\n  not done: {each}" for each in shared_not_done(ctx))
        )
        return StepResult(result={"closed": True}, lines=(line,))

    def outcome(ctx: OutcomeContext) -> str:
        number = ctx.payload["season_number"]
        if _discarded(_view(ctx, UNARM)):
            return SEASON_UNARM_DISCARDED.format(number=number)
        refused = _refused(ctx)
        if refused:
            return refused
        if _discarded(_view(ctx, APPLY)):
            return SEASON_SAVE_DISCARDED.format(number=number)
        if not _ended(ctx):
            return SEASON_END_DISCARDED.format(number=number)
        return (
            SEASON_CANCELLED
            + notices.failure_lines(not_notified(ctx))
            + approval_checks.not_done_section(shared_not_done(ctx))
        )

    def describing(text: str) -> Callable[[StepContext], Awaitable[str]]:
        async def describe(ctx: StepContext) -> str:
            return text.format(number=ctx.payload["season_number"])

        return describe

    steps: dict[str, Step] = {
        **_follow_steps(modules=modules, seasons=seasons, scope=notices.SCOPE_SEASON),
        **season_end_steps(placement, hooks),
        DELETE_CHANNEL: submissions.delete_step,
        UNARM: Step(
            UNARM, StepKind.ACT, unarm,
            describe=describing("removing the timed work of the rounds of season {number}"),
        ),
        APPLY: Step(
            APPLY, StepKind.SAVE, apply, still_due=unarmed,
            describe=describing("cancelling season {number}"),
        ),
        END: Step(
            END, StepKind.SAVE, end,
            describe=describing("recording the end of season {number}"),
        ),
        CLOSE: Step(
            CLOSE, StepKind.SAVE, close,
            describe=describing("recording the cancellation of season {number}"),
        ),
    }
    return ChangeType(
        kind=SEASON_CANCEL,
        opening=(PlannedStep(UNARM), PlannedStep(APPLY), PlannedStep(CLOSE)),
        steps=steps,
        check=check,
        key=lambda payload: f"{SEASON_CANCEL}:{payload['season_id']}",
        doing=lambda payload: f"Cancelling season {payload['season_number']}",
        outcome=outcome,
    )
