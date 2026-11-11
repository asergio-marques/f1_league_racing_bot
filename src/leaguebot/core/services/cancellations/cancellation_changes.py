"""Cancelling a round, a division or a season on the change queue.

`/round cancel` checks its confirmation word, the season and the names at the press, in the season
cog, then asks the queue for this change. Every other gate is the change type's `check`, which
judges the request when it is asked and again when it comes up to run, in the command's words, so
that a round whose results were entered, or whose submission accepted a session, while the
cancellation waited is refused with the same reply. It imports nothing from a module and no cog:
what the modules take part in (what stands in a cancellation's way, the rounds called off, the
notices a division told is owed, and the steps a module plans) reaches it as hooks the builder signs
them up to, and it reads the season through the service it is handed.

**Cancellation notices.** Each division told is also told by `cancellation_notices`, called inside
the save, after the modules' `rounds_called_off` steps are planned for it: every subscriber hands
back a `CancellationNotices`, whose `steps` are placed before the rounds-called-off modules'
`with_division` steps for that division and whose `after_with_division` are placed after them, so
that the notices keep their order without this module naming which job is whose. A job the hook
plans says in its own payload or result which target it is (`notice_target`, "check-in channel" and
so on), so that `not_notified` can name it without naming the module; a channel a job could not
delete (`channel_target`) and a call Discord would not delete (`call_target`) are named the same
way, from the job's own payload rather than its result, without naming the job that plans either.

**A round whose submission stands open is cancelled only while it has accepted nothing**
(Constitution XII): any `session_results` row, of either status, is data the cancellation would
lose, and refuses it, at ask, as it runs and in the save as a backstop (`unarm` lies between the
check and the save). With nothing accepted, the save closes the submission, and deleting its channel
is results' own `delete_channel` job, the first after the save, because the channel is where a
manager pastes.

**The timed work is removed first, then everything the cancellation writes is one save** (`apply`,
architecture.md, "All or nothing in one step"): the round recorded cancelled with the status it was
cancelled from, its division finished and the season moved on where that was the last it waited on,
reading nothing but the connection it is handed. The removal goes first because a cancelled round
whose weather jobs stood would have its forecasts posted; a stop there means the scheduler itself
failed, and the removal of a division's or a season's rounds lists every round before it takes any,
so that a stopped removal has taken none. **The save cannot be discarded**: with the timed work off,
dropping it would leave the rounds neither cancelled nor ever run, so a stopped save can only be
retried. **A save that refuses arms the timed work again**: the check lies before the removal and
the save after it, so a session accepted on a round's submission between them, or a round that moved
on, refuses the save with the timed work already off. The refusal then asks, in its own save, one
`season.round.rearm` (`round_amend_change`) for each round the removal took jobs from: the whole of
its timed work for a round still to be run or awaiting its results, as it stood, and only the
next-day clean-ups for one already raced. The reply and the refusal's log line name the rounds being
armed again. A refusal at the check lies before the removal and owes none. **Everything else follows
the save as a job of its own**, each of which stops the queue where it fails: each enabled module's
notice, the check-in call's take-down, the calendar's repost, and a closing job that writes the one
line. The wind-down is a change of its own, asked in the save where the division finished. A job is
planned whether or not its module is on, and drops itself as it runs where it is not; a notice job
whose channel was never set is done, and named in the reply.

**A division's cancellation is the same change over a whole division** (`division_cancel_change`):
the timed work of every round of it is removed, read as the job runs; the save cancels the division
and each of its rounds that may still be cancelled, with the status each was cancelled from; the
notices go once for the division in its own words, and a check-in call is taken down for each round
called off that has one. It cancels a round whose results submission stands open with the rest only
while that submission has accepted nothing: it is refused, naming the lowest-numbered round, once
any round of the division has one that has (a round in its review counts), and it closes the empty
ones in the save and deletes their channels, in round order, first after it.

**A second cancellation is refused at once, naming the job**: a cancellation of the round or of its
division in hand refuses a round's, and one of the division a division's, read through
`cancellation_in_hand`. The refusal is made only as the change is asked for
(`CheckContext.change_id` is None), never as it runs: a division's cancellation asked after a
round's of that division is legitimate and runs behind it, and the check made as the round's starts
would find the division's later change and refuse itself. Run behind a cancellation of the same
round, "already cancelled" is what refuses it. `cancellation_in_hand` is read, too, by
`/round amend` (`cancellation_holding_amendment`), which refuses every amendment of any round of a
division, whatever it changes, while a cancellation of the division or of any round of it is in
hand: chiefly because an amended time renumbers the rounds under the cancellation.

**The other way round, a cancellation is refused while an amendment is in hand**: a `/round amend`
of a round of the division that is queued, running or stopped on the queue refuses a cancellation of
the division, or of any round of it, naming the job. It is read from the queue, through the
*amendment_in_hand* the builder hands both change types (the amendment change imports this module,
so this one cannot import it), never from a flag kept in memory (architecture.md), so a restart
forgets nothing. Like the second cancellation it is made only as the change is asked for, never as
it runs: the queue runs one change at a time and leaves nothing overtaking a stopped job, so
whatever amendment was ahead of this cancellation has finished, its renumbering included, and the
cancellation reads the round's number as it then stands.

**A season's cancellation is the same change over every division** (`season_cancel_change`), in the
order the core specification sets: the timed work of every round removed (`unarm`); then one save,
`apply`, that records the cancellation (each empty open submission closed, the uncommitted
placements discarded, the raced rounds awaiting verdicts made final, and every division not
cancelled cancelled with its rounds that may still be cancelled), **leaving the season itself
ongoing**, so that its channels stay readable while it is announced; the notices, the calls taken
down and the calendars of every division that was still running, in the season's words; each real
driver's roles taken back, after the notices, since the check-in notice mentions the division role;
the signup window closed and the forecasts posted under test mode cleared; and then a second save,
`end`, that writes the history marked cancelled, runs the driver pass, switches test mode off and
marks the season cancelled, **last**. The saved test-mode state is kept: a season cancelled is one
abandoned, not run to its end. The jobs the other ends of a season share (`season_end_changes`)
carry out the rest. A run that stops at `end` and is discarded is run again, and tells no division
twice, since by then none is still running.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.cancellation import (
    SCOPE_DIVISION,
    SCOPE_ROUND,
    SCOPE_SEASON,
    CalledOffSteps,
    CancellationRound,
)
from leaguebot.core.models.change import (
    AuditRecord,
    FollowOn,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.round import (
    ROUND_CANCELLABLE,
    ROUND_RACED_AWAITING_VERDICTS,
    Round,
    RoundStatus,
)
from leaguebot.core.models.season import ONGOING_STAGES, SeasonStage
from leaguebot.core.models.season_end import CANCELLATION
from leaguebot.core.services.cancellations import cancellation_notice_service as notices
from leaguebot.core.services.posting import standing_posts
from leaguebot.core.services.calendar_post_service import POST_CALENDAR, calendar_steps
from leaguebot.core.services.queue.queue_records import (
    in_hand,
)
from leaguebot.core.services.queue.change_types import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
    StepView,
)
from leaguebot.core.services.modules.image_drawing import ImageDrawing
from leaguebot.core.services.seasons.season_end_changes import (
    END,
    SEASON_CANCEL,
    SEASON_ENDED_NOTICE,
    anything_discarded,
    not_done_reply,
    season_end_in_hand,
    season_end_refusal,
    shared_not_done,
    test_mode_on,
)
from leaguebot.core.services.seasons.season_end_service import (
    DISCARD_PORTRAITS,
    FLUSH_FORECASTS,
    REVOKE_ROLES,
    refusal_of_the_modules,
    season_role_targets_on,
    shared_steps,
    write_driver_history_entries_on,
)
from leaguebot.core.services.seasons.season_lifecycle_service import (
    SEASON_END_CAUSE,
    WIND_DOWN,
    advance_to_pending_completion_on,
    close_jobs,
    driver_jobs,
    run_driver_pass_on,
    steps_of_the_modules,
)
from leaguebot.core.services.seasons.season_service import (
    SeasonImmutableError,
    SeasonService,
    round_called,
    cancel_division_on,
    cancel_round_on,
    cancel_season_divisions_on,
    cancel_season_on,
    close_raced_rounds_on,
    discard_uncommitted_placements_on,
)
from leaguebot.core.services.test_mode_service import switch_test_mode_off_on
from leaguebot.core.utils.hooks import AwaitedHook, Hook
from leaguebot.core.utils.log_lines import refusal_line, reply_reason

if TYPE_CHECKING:
    from leaguebot.core.services.scheduler_service import SchedulerService
    from leaguebot.core.services.team_role_service import TeamRoleService

__all__ = [
    "DIVISION_CANCEL",
    "ROUND_CANCEL",
    "ROUND_REARM",
    "SEASON_CANCEL",
    "cancellation_holding_amendment",
    "cancellation_in_hand",
    "division_cancel_change",
    "not_notified",
    "round_cancel_change",
    "season_cancel_change",
]

ROUND_CANCEL = "season.round.cancel"
DIVISION_CANCEL = "season.division.cancel"
#: The change a refused cancellation asks, for each round whose timed work it removed, to arm it
#: again (`round_amend_change.round_rearm_change`). Named here because the amendment's module
#: imports this one, so this one cannot import it.
ROUND_REARM = "season.round.rearm"

_COMMAND = "`/round cancel`"
_ROUND_REARM_COMMAND = "/round cancel"
NOT_ONGOING = f"❌ {_COMMAND} is available only while the season is ongoing."
ARCHIVED = "❌ This season is archived (COMPLETED) and cannot be modified."
DIVISION_COMMAND = "`/division cancel`"
_DIVISION_REARM_COMMAND = "/division cancel"
DIVISION_NOT_ONGOING = f"❌ {DIVISION_COMMAND} is available only while the season is ongoing."
SEASON_COMMAND = "`/season cancel`"
_SEASON_REARM_COMMAND = "/season cancel"
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
    cancellation removed it, and forecasts would be posted for a round that is then cancelled.
    An amended date of another round would renumber the division, and the round being cancelled
    would be announced by a number it no longer bears: so every round of the division is held.
    A season's completion, cancellation or abort in hand holds it too, ahead of the rest. Asked
    at the offer and again at the confirmation, as the rules are. It names the job, and leaves
    the name out where only the cancellation's close is left.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT s.id, s.season_number FROM divisions d JOIN seasons s ON s.id = d.season_id "
            "WHERE d.id = ?",
            (rnd.division_id,),
        )
        season = await cursor.fetchone()
    if season is not None:
        hand = await season_end_in_hand(db_path, int(season["id"]))
        if hand is not None:
            return season_end_refusal(None, hand, season["season_number"])
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


async def _refusal_of_the_modules(
    hook: AwaitedHook, db: aiosqlite.Connection, scope: str, rounds: list[CancellationRound]
) -> str | None:
    """What stands in the way of cancelling *rounds*: the first refusal a module hands back, or
    None. Each module is asked, on *db*, the connection it is handed (`cancellation_refused`)."""
    for found in await hook.call(db, scope, rounds):
        if found is not None:
            return str(found)
    return None


async def _planned_by_the_modules(
    hook: AwaitedHook, db: aiosqlite.Connection, scope: str, rounds: list[CancellationRound]
) -> CalledOffSteps:
    """What each module does for *rounds*, called off, inside the save on *db*, and the steps it
    plans (`rounds_called_off`), together in the order the modules signed up."""
    first: list[PlannedStep] = []
    with_division: dict[int, list[PlannedStep]] = {}
    for planned in await hook.call(db, scope, rounds):
        first += planned.first
        for division_id, steps in planned.with_division.items():
            with_division.setdefault(division_id, []).extend(steps)
    return CalledOffSteps(
        tuple(first), {division_id: tuple(steps) for division_id, steps in with_division.items()}
    )


async def _notices_planned_by_the_modules(
    hook: AwaitedHook, db: aiosqlite.Connection, scope: str, target: dict[str, Any]
) -> tuple[tuple[PlannedStep, ...], tuple[PlannedStep, ...]]:
    """Each module's own steps for *target*'s notice, told inside the save on *db*
    (`cancellation_notices`), together in the order the modules signed up: attendance's
    check-in notice, weather's forecast note, then results' results note.

    Returns the steps planned before the rounds-called-off modules' `with_division` steps, and
    those planned after, each in that order: today, attendance's check-in notice before, and
    weather's and results' notes after.
    """
    steps: list[PlannedStep] = []
    after: list[PlannedStep] = []
    for planned in await hook.call(db, scope, target):
        steps += planned.steps
        after += planned.after_with_division
    return tuple(steps), tuple(after)


#: The job names, as the stop notice and the tests know them.
UNARM = "unarm"
APPLY = "apply"
CLOSE = "close"

UNARM_DISCARDED = (
    "Nothing was cancelled: round {number} in **{division}** stands as it was. Run "
    "`/round cancel` again."
)
DIVISION_UNARM_DISCARDED = (
    "Nothing was cancelled: **{division}** stands as it was. Run `/division cancel` again."
)
_DISCARDED_NOTICE = "the notice could not be posted, and a league admin discarded it"
_DISCARDED_CHANNEL = (
    "it could not be deleted, and a league admin discarded it; delete <#{channel_id}> by hand"
)
_DISCARDED_CALENDAR = (
    "the calendar could not be posted, and a league admin discarded it; run "
    "`/division calendar-sync` while the season is still being raced"
)
#: The same once the season is cancelled, which `/division calendar-sync` refuses.
_DISCARDED_CALENDAR_CANCELLED = (
    "the calendar could not be posted, and a league admin discarded it; no command posts it "
    "again now the season is cancelled; the earlier calendar stands, to delete by hand if it "
    "should not"
)

SEASON_UNARM_DISCARDED = (
    "Nothing was cancelled: season {number} stands as it was. Run `/season cancel` again."
)
SEASON_END_DISCARDED = (
    "Season {number}'s divisions and rounds are cancelled, but the season itself is not: the "
    "save that records its end was discarded. Run `/season cancel` again to finish it; if the "
    "season has since moved to pending completion, complete it with `/season complete`."
)

_EMPTY = StepView(name="", payload={}, result=None, done=False)


def _view(ctx: OutcomeContext, name: str) -> StepView:
    """The last job named *name*, or an empty view where the change has none."""
    return next((view for view in reversed(ctx.steps) if view.name == name), _EMPTY)


def _discarded(view: StepView) -> bool:
    return "discarded" in (view.result or {})


def _season_ended(ctx: OutcomeContext) -> bool:
    """Whether a season cancel's end was saved: `end` is done, and neither refused nor
    discarded."""
    view = _view(ctx, END)
    return view.done and not (_discarded(view) or (view.result or {}).get("refused"))


def _refusal_of(ctx: OutcomeContext) -> str | None:
    """What the save refused with as it ran, or None."""
    return (_view(ctx, APPLY).result or {}).get("refused")


#: What a refused round's cancellation adds to its reply once its timed work is being armed again.
_REARMING = "Its timed work, removed for the cancellation, is being armed again as it stood."
_REARMING_ROUNDS = (
    "The timed work of its rounds still to be run, removed for the cancellation, is being armed "
    "again as it stood: {rounds}."
)


def _removed_by(ctx: OutcomeContext) -> dict[int, list[str]]:
    """The jobs the unarming removed, by round id, as it recorded them; none where it was
    discarded or removed nothing."""
    recorded = (_view(ctx, UNARM).result or {}).get("removed") or {}
    return {int(round_id): list(jobs) for round_id, jobs in recorded.items() if jobs}


async def _rearm_on(
    db: aiosqlite.Connection, ctx: OutcomeContext, command: str
) -> tuple[tuple[FollowOn, ...], list[str]]:
    """The re-armings a refused cancellation asks, and the rounds it names as armed again.

    A cancellation's check lies before its first job, which removes timed work, and its save lies
    after, so a save that refuses (a session accepted since, the round moved on) leaves rounds
    with their timed work gone and nothing cancelled. One ``season.round.rearm`` is asked for each
    round the unarming removed jobs from, in this save, so the refusal is never recorded without
    it: a round still to be run or awaiting its results has all its timed work armed again, and
    one already raced (final, or awaiting verdicts) or ended cancelled for every session being
    entered as not held (`rounds.not_held`), which keeps its clean-ups, only its next-day
    clean-ups. A round the save finds called off, or gone, is left alone. Returns the follow-ons and, as the reply names them,
    the rounds armed in full ("round 3 in **Pro**"), by tier then round number.
    """
    removed = _removed_by(ctx)
    if not removed:
        return (), []
    marks = ",".join("?" * len(removed))
    cursor = await db.execute(
        "SELECT r.id, r.round_number, r.status, r.not_held, d.id AS division_id, "
        "d.name AS division_name "
        f"FROM rounds r JOIN divisions d ON d.id = r.division_id WHERE r.id IN ({marks}) "  # noqa: S608
        "ORDER BY d.tier, r.round_number",
        list(removed),
    )
    follow_ons: list[FollowOn] = []
    names: list[str] = []
    for row in await cursor.fetchall():
        cleanups_only = (
            row["status"] in ROUND_RACED_AWAITING_VERDICTS
            or row["status"] == RoundStatus.FINAL.value
            or (row["status"] == RoundStatus.CANCELLED.value and bool(row["not_held"]))
        )
        if not cleanups_only and row["status"] not in ROUND_CANCELLABLE:
            continue
        called = f"round {row['round_number']} in **{row['division_name']}**"
        payload: dict[str, Any] = {
            "round_id": int(row["id"]),
            "round_number": int(row["round_number"]),
            "division_id": int(row["division_id"]),
            "division_name": str(row["division_name"]),
            "command": command,
        }
        if cleanups_only:
            payload["cleanups_only"] = True
            what = f"arming the next-day clean-ups of {called} again"
        else:
            names.append(called)
            what = f"arming the timed work of {called} again"
        follow_ons.append(FollowOn(ROUND_REARM, payload, what))
    return tuple(follow_ons), names


async def _refused_rearming(
    db: aiosqlite.Connection, ctx: OutcomeContext, text: str, command: str
) -> StepResult:
    """A save's refusal, with the re-armings it asks (see `_rearm_on`) and the rounds it names."""
    follow_ons, names = await _rearm_on(db, ctx, command)
    return StepResult(result={"refused": text, "rearmed": names}, follow_ons=follow_ons)


def _rearmed(ctx: OutcomeContext) -> list[str]:
    """The rounds the refusing save named as armed again, as the reply writes them."""
    return list((_view(ctx, APPLY).result or {}).get("rearmed") or [])


def _rearming_detail(ctx: OutcomeContext) -> str | None:
    """The line the log channel's refusal gains beneath it, naming the rounds armed again."""
    names = _rearmed(ctx)
    if not names:
        return None
    return "  timed work being armed again: " + ", ".join(name.replace("**", "") for name in names)


def _rearming_sentence(ctx: OutcomeContext, *, one: bool = False) -> str:
    """What a refusal's reply adds of the timed work being armed again; nothing where none is."""
    names = _rearmed(ctx)
    if not names:
        return ""
    return "\n" + (_REARMING if one else _REARMING_ROUNDS.format(rounds=", ".join(names)))


def _saved_number(ctx: OutcomeContext) -> Any:
    """The number of the round cancelled, as the closing job read it after a discarded removal,
    else as the save found it, else as the removal of its timed work did, else the press's: a
    discarded removal never ran the save, and the round may have been renumbered since each."""
    for name in (CLOSE, APPLY, UNARM):
        number = (_view(ctx, name).result or {}).get("round_number")
        if number is not None:
            return number
    return ctx.payload["round_number"]


def not_notified(ctx: OutcomeContext) -> list[notices.NoticeFailure]:
    """Every place the cancellation could not reach, one for each job that found none to tell or
    was discarded, in the order of the jobs. A calendar posted as text is not a place unreached:
    it was posted, and its fallback is recorded by the posting's own log line, not named here.

    A module's own notice job is named by `notice_target`, its own word for what it is
    ("check-in channel", "forecast channel", "results channel") carried on the job's own payload
    (`cancellation_notice.notices_on`, each module), so that core names it without naming the
    module or its job. A channel a job could not delete is named the same way, by its own
    `channel_target` (results' `close_called_off_on`); a call Discord would not delete is named
    by its own `call_target` (attendance's `take_down_called_off_on`), read without naming either
    job. Both a channel's and a call's discard are named from the job's own payload rather than
    its result, so a fault that is not Discord's — one that leaves no partial result to keep, such
    as the division going missing — is still named. The reasons stay core's text."""
    failures: list[notices.NoticeFailure] = []
    for view in ctx.steps:
        result = view.result or {}
        target = view.payload.get("notice_target")
        channel_target = view.payload.get("channel_target")
        call_target = view.payload.get("call_target")
        name = str(view.payload.get("division_name", ""))
        if target is not None:
            if _discarded(view):
                failures.append(notices.NoticeFailure(name, target, _DISCARDED_NOTICE))
            elif result.get("unset"):
                failures.append(notices.NoticeFailure(name, target, "no channel is set"))
        elif channel_target is not None:
            if _discarded(view):
                failures.append(notices.NoticeFailure(
                    name, channel_target,
                    _DISCARDED_CHANNEL.format(channel_id=view.payload["channel_id"]),
                ))
        elif call_target is not None:
            if _discarded(view):
                ids = [str(each) for each in result.get("undeleted", [])]
                failures.append(notices.NoticeFailure(
                    name,
                    call_target,
                    f"{len(ids)} message(s) could not be deleted and must be removed by hand "
                    f"(ids {', '.join(ids)})",
                ))
        elif view.name == standing_posts.DELETE_REPLACED:
            if _discarded(view):
                link = result.get("link") or f"in <#{view.payload['channel_id']}>"
                failures.append(notices.NoticeFailure(
                    name, "calendar",
                    f"the earlier calendar could not be deleted ({link}); delete it by hand",
                ))
        elif view.name == POST_CALENDAR:
            if _discarded(view):
                # A season cancelled for good is refused by `/division calendar-sync`; one whose
                # end was discarded is not cancelled, and the command still works.
                cancelled = ctx.kind == SEASON_CANCEL and _season_ended(ctx)
                failures.append(notices.NoticeFailure(
                    name, "calendar",
                    _DISCARDED_CALENDAR_CANCELLED if cancelled else _DISCARDED_CALENDAR,
                ))
            # A calendar posted as text is not discarded: nothing further to name here.
    return failures


def _audit_lines(ctx: OutcomeContext) -> str:
    """The check-in of each round called off, read before its call came down and kept on the
    job whether it went through or was discarded: every job's own result carrying `audit`,
    read without naming the job that set it."""
    return "".join(
        str(view.result["audit"])
        for view in ctx.steps
        if view.result is not None and "audit" in view.result
    )


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


def _follow_steps(*, seasons: SeasonService, drawing: ImageDrawing) -> dict[str, Step]:
    """The jobs core carries out after the save: the calendar's repost. Every notice of the
    cancellation is now its own module's step, planned through `cancellation_notices` and
    registered on `cancellation_steps` (`attendance.services.cancellation_notice`,
    `weather.services.cancellation_notice`, `results.services.cancellation_notice`). A job
    reads its division from its own payload (`division_id`, `division_name`, `season_id`).

    *drawing* is the image module's own answer to core's drawing protocol, handed on to
    `calendar_steps`.
    """
    return {**calendar_steps(seasons, drawing)}


def round_cancel_change(
    *,
    seasons: SeasonService,
    scheduler: "SchedulerService",
    drawing: ImageDrawing,
    cancellation_refused: AwaitedHook,
    rounds_called_off: AwaitedHook,
    cancellation_notices: AwaitedHook,
    cancellation_steps: Hook,
    amendment_in_hand: Callable[[int], Awaitable[int | None]],
    now: Callable[[], datetime],
) -> ChangeType:
    """The change that cancels a round; see the module.

    The payload is ``{"round_id", "round_number", "track_name", "division_id", "division_name",
    "season_number"}``. It names no season: a payload that does is read as holding every division
    of that season (`review_changes.division_job_in_hand`); the season a job needs travels on the
    jobs' own payloads. The builder hands in the *seasons* service, the *scheduler*, the image
    module's drawing (*drawing*), the hooks the modules sign up to (*cancellation_refused*,
    *rounds_called_off*, *cancellation_notices* and *cancellation_steps*, whose steps are
    registered), *amendment_in_hand* (the job a `/round amend` of a round of a division waits on,
    or None) and the queue's clock *now*.
    """

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        season = await seasons.get_confirmed_season()
        if season is not None and ctx.change_id is None:
            # **While the season's own end is in hand**: refused at once, naming the job, ahead
            # of any stage gate. Only as it is asked.
            hand = await season_end_in_hand(ctx.db_path, season.id)
            if hand is not None:
                return Verdict.refuse(season_end_refusal(None, hand, season.season_number))
        if season is None or season.stage not in ONGOING_STAGES:
            return Verdict.refuse(NOT_ONGOING)
        try:
            await seasons.assert_season_mutable(season)
        except SeasonImmutableError:
            return Verdict.refuse(ARCHIVED)

        round_id = int(payload["round_id"])
        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute(
                "SELECT status, round_number, track_name FROM rounds WHERE id = ?", (round_id,)
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
        async with get_connection(ctx.db_path) as db:
            refusal = await _refusal_of_the_modules(
                cancellation_refused, db, SCOPE_ROUND,
                [CancellationRound(
                    round_id, int(row["round_number"]), row["track_name"],
                    int(payload["division_id"]), str(payload["division_name"]), season.id, True,
                )],
            )
        if refusal is not None:
            return Verdict.refuse(refusal)
        return Verdict.go()

    # ── The jobs ────────────────────────────────────────────────────────────────

    async def unarm(ctx: StepContext) -> StepResult:
        # The number is read first, so that nothing after the removal can stop this job: a
        # removal stopped and discarded would be named as nothing done.
        number = await _round_number_now(ctx)
        round_id = int(ctx.payload["round_id"])
        removed = scheduler.cancel_round(round_id)
        return StepResult(
            result={"unarmed": True, "round_number": number, "removed": {str(round_id): removed}}
        )

    async def unarmed(ctx: StepContext) -> bool:
        """The save is due only where the timed work was removed, not where that job was
        discarded."""
        return not _discarded(_view(ctx, UNARM))

    async def would_be_left_unrun(ctx: StepContext) -> str:
        """What dropping the save would leave: the timed work is off and nothing cancelled, so
        the save is retried, never discarded."""
        called = await round_called(ctx.db_path, int(ctx.payload["round_id"]))
        return f"{called} would be left with no timed work, neither cancelled nor ever run"

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
            this_round = [CancellationRound(
                round_id, int(division["round_number"]), division["track_name"],
                int(division["id"]), str(ctx.payload["division_name"]),
                int(division["season_id"]), True,
            )]
            # A backstop for the check: `unarm` lies between them, and a session may have been
            # accepted since. Nothing is written then, and the timed work `unarm` took is armed again.
            refusal = await _refusal_of_the_modules(
                cancellation_refused, db, SCOPE_ROUND, this_round
            )
            if refusal is not None:
                return await _refused_rearming(db, ctx, refusal, _ROUND_REARM_COMMAND)
            previous = await cancel_round_on(
                db, round_id, actor_id=ctx.actor_id, actor_name=ctx.actor_name, now=now()
            )
        if division is None or previous is None:
            # A backstop: the check passed, and something wrote the database after it.
            return await _refused_rearming(
                db, ctx, await refusal_in_save(db, ctx.payload), _ROUND_REARM_COMMAND
            )

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
        # An open submission with nothing accepted is closed in this save, and its channel
        # deleted first of all after it, since that is where a manager pastes.
        by_modules = await _planned_by_the_modules(rounds_called_off, db, SCOPE_ROUND, this_round)
        by_notices, after_notices = await _notices_planned_by_the_modules(
            cancellation_notices, db, SCOPE_ROUND, target
        )
        planned = list(by_modules.first)
        planned += by_notices
        planned += by_modules.with_division.get(target["division_id"], ())
        planned += after_notices
        planned += [
            PlannedStep(POST_CALENDAR, {**target, "round_ids": [round_id], "only_if_posted": True}),
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

        Where the removal was discarded, the save never ran and read no number: the round is read
        here, the last job, so that the outcome names it by the number it bears now, which another
        round's amended date may have changed while the removal stood stopped."""
        payload = ctx.payload
        if _discarded(_view(ctx, UNARM)):
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
                lines=(refusal_line(
                    ctx.named, _COMMAND, reply_reason(refused), detail=_rearming_detail(ctx)
                ),),
            )
        line = (
            f"{ctx.named} | /round cancel | Success\n"
            f"  division: {payload['division_name']}\n"
            f"  round: {_saved_number(ctx)}"
            + _audit_lines(ctx)
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
            return refused + _rearming_sentence(ctx, one=True)
        return (
            f"✅ Round **{words['number']}** in **{words['division']}** cancelled."
            + notices.failure_lines(not_notified(ctx))
        )

    steps: dict[str, Step] = {
        **_follow_steps(seasons=seasons, drawing=drawing),
        **steps_of_the_modules(cancellation_steps),
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
            undiscardable=would_be_left_unrun,
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
    seasons: SeasonService,
    scheduler: "SchedulerService",
    drawing: ImageDrawing,
    cancellation_refused: AwaitedHook,
    rounds_called_off: AwaitedHook,
    cancellation_notices: AwaitedHook,
    cancellation_steps: Hook,
    amendment_in_hand: Callable[[int], Awaitable[int | None]],
    now: Callable[[], datetime],
) -> ChangeType:
    """The change that cancels a division and the rounds of it that may still be cancelled.

    The payload is ``{"division_id", "division_name", "season_number"}``. It is the round's
    cancellation over a whole division: the same jobs, with the timed work of every round
    removed first. A round whose results submission stands open is cancelled with the rest only
    while that submission has accepted nothing (Constitution XII): any round of the division whose
    open submission has accepted a session refuses the whole cancellation, at ask, as it runs and in
    the save as a backstop, naming the lowest-numbered such round. In the save the empty submissions
    of the rounds called off are closed, and the deletion of each channel is a job, in round order,
    first after it.
    """

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        season = await seasons.get_confirmed_season()
        if season is not None and ctx.change_id is None:
            # **While the season's own end is in hand**: refused at once, naming the job, ahead
            # of any stage gate. Only as it is asked.
            hand = await season_end_in_hand(ctx.db_path, season.id)
            if hand is not None:
                return Verdict.refuse(season_end_refusal(None, hand, season.season_number))
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
        async with get_connection(ctx.db_path) as db:
            refusal = await _refusal_of_the_modules(
                cancellation_refused, db, SCOPE_DIVISION,
                [
                    CancellationRound(
                        each.id, each.round_number, each.track_name, int(payload["division_id"]),
                        str(payload["division_name"]), season.id,
                        each.status in ROUND_CANCELLABLE,
                    )
                    for each in await seasons.get_division_rounds(int(payload["division_id"]))
                ],
            )
        if refusal is not None:
            return Verdict.refuse(refusal)
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
        round_ids = [
            each.id for each in await seasons.get_division_rounds(int(ctx.payload["division_id"]))
        ]
        removed = scheduler.cancel_rounds(round_ids)
        return StepResult(result={
            "unarmed": True, "removed": {str(round_id): jobs for round_id, jobs in removed.items()},
        })

    async def unarmed(ctx: StepContext) -> bool:
        """The save is due only where the timed work was removed, not where that job was
        discarded."""
        return not _discarded(_view(ctx, UNARM))

    async def would_be_left_unrun(ctx: StepContext) -> str:
        """What dropping the save would leave: the timed work is off and nothing cancelled, so
        the save is retried, never discarded."""
        return (
            f"the rounds of **{ctx.payload['division_name']}** would be left with no timed work, "
            "neither cancelled nor ever run"
        )

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
            return await _refused_rearming(db, ctx, refusal or "", _DIVISION_REARM_COMMAND)

        cursor = await db.execute(
            "SELECT id, round_number, track_name, status FROM rounds WHERE division_id = ? "
            "ORDER BY round_number",
            (division_id,),
        )
        standing = {
            int(each["id"]): CancellationRound(
                int(each["id"]), int(each["round_number"]), each["track_name"], division_id,
                str(payload["division_name"]), int(division["season_id"]),
                each["status"] in ROUND_CANCELLABLE,
            )
            for each in await cursor.fetchall()
        }
        # A backstop for the check: `unarm` lies between them, and a session may have been
        # accepted since. Nothing is written then, and the timed work `unarm` took is armed again.
        refusal = await _refusal_of_the_modules(
            cancellation_refused, db, SCOPE_DIVISION, list(standing.values())
        )
        if refusal is not None:
            return await _refused_rearming(db, ctx, refusal, _DIVISION_REARM_COMMAND)

        called_off = await cancel_division_on(
            db, division_id, actor_id=ctx.actor_id, actor_name=ctx.actor_name, now=now()
        )
        after = await _planned_by_the_modules(
            rounds_called_off, db, SCOPE_DIVISION, [standing[each] for each in called_off]
        )
        # Cancelling the last division still running leaves the season pending completion, in this
        # same save: `cancel_division_on` leaves the season's stage alone, for `/season cancel`.
        await advance_to_pending_completion_on(db, int(division["season_id"]))
        target = {
            "division_id": division_id,
            "division_name": str(payload["division_name"]),
            "season_id": int(division["season_id"]),
        }
        by_notices, after_notices = await _notices_planned_by_the_modules(
            cancellation_notices, db, SCOPE_DIVISION, target
        )
        planned = list(after.first)
        planned += by_notices
        planned += after.with_division.get(division_id, ())
        planned += after_notices
        planned += [
            PlannedStep(POST_CALENDAR, {**target, "round_ids": called_off, "only_if_posted": True}),
        ]
        return StepResult(
            result={"called_off": called_off},
            then=tuple(planned),
            follow_ons=(FollowOn(WIND_DOWN, {}, f"Winding the season down after {ctx.what}"),),
        )

    async def close(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Write the one line that records the cancellation, or the refusal the save met."""
        if _discarded(_view(ctx, UNARM)):
            return StepResult(result={"closed": True})
        refused = _refusal_of(ctx)
        if refused:
            return StepResult(
                result={"closed": True},
                lines=(refusal_line(
                    ctx.named, DIVISION_COMMAND, reply_reason(refused), detail=_rearming_detail(ctx)
                ),),
            )
        line = (
            f"{ctx.named} | /division cancel | Success\n"
            f"  division: {ctx.payload['division_name']}"
            + _audit_lines(ctx)
            + notices.failure_log_lines(not_notified(ctx))
        )
        return StepResult(result={"closed": True}, lines=(line,))

    def outcome(ctx: OutcomeContext) -> str:
        name = ctx.payload["division_name"]
        if _discarded(_view(ctx, UNARM)):
            return DIVISION_UNARM_DISCARDED.format(division=name)
        refused = _refusal_of(ctx)
        if refused:
            return refused + _rearming_sentence(ctx)
        return f"✅ Division **{name}** cancelled." + notices.failure_lines(not_notified(ctx))

    steps: dict[str, Step] = {
        **_follow_steps(seasons=seasons, drawing=drawing),
        **steps_of_the_modules(cancellation_steps),
        UNARM: Step(
            UNARM, StepKind.ACT, unarm,
            describe=_named("removing the timed work of the rounds of **{division}**"),
        ),
        APPLY: Step(
            APPLY, StepKind.SAVE, apply, still_due=unarmed,
            describe=_named("cancelling **{division}**"),
            undiscardable=would_be_left_unrun,
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


def season_cancel_change(
    *,
    seasons: SeasonService,
    scheduler: "SchedulerService",
    roles: "TeamRoleService",
    drawing: ImageDrawing,
    cancellation_refused: AwaitedHook,
    rounds_called_off: AwaitedHook,
    cancellation_notices: AwaitedHook,
    rounds_made_final: AwaitedHook,
    cancellation_steps: Hook,
    season_end_refused: AwaitedHook,
    season_standing_read: AwaitedHook,
    season_end_closes: Hook,
    drivers_returned: AwaitedHook,
    driver_returned_jobs: Hook,
    season_end_steps: Hook,
    test_mode_flushed: AwaitedHook,
    drivers_deleted: AwaitedHook,
    profiles_deleted: AwaitedHook,
    now: Callable[[], datetime],
) -> ChangeType:
    """The change that cancels the season being raced; see the module.

    The payload is ``{"season_id", "season_number"}``. The builder hands in the *seasons*
    service, the *scheduler*, the the team-role service, *roles* (which takes back roles), the image
    module's drawing (*drawing*), the hooks the modules sign up to (*cancellation_refused*,
    *rounds_called_off*, *cancellation_notices*, *rounds_made_final*, *cancellation_steps*,
    *season_end_refused*, *season_standing_read*, *season_end_closes*, *drivers_returned*,
    *driver_returned_jobs*, *season_end_steps*, *test_mode_flushed*, *drivers_deleted*), the hook
    each driver deleted calls (*profiles_deleted*) and the queue's clock *now*.
    """

    async def check(ctx: CheckContext) -> Verdict:
        season = await seasons.get_confirmed_season()
        if season is None or season.id != int(ctx.payload["season_id"]):
            return Verdict.refuse(SEASON_NO_SEASON)

        # **A second cancellation, asked while the first is in hand**: refused at once, naming the
        # job. Only as it is asked: as the change runs, it would find itself.
        if ctx.change_id is None:
            hand = await season_end_in_hand(ctx.db_path, season.id)
            if hand is not None:
                return Verdict.refuse(
                    season_end_refusal(SEASON_CANCEL, hand, season.season_number)
                )
        if season.stage not in ONGOING_STAGES:
            return Verdict.refuse(SEASON_NOT_ONGOING)

        # **Not while a round is being amended**: cancelling writes every driver's history from the
        # standings, which hold an open amendment's corrections before they are approved, and the
        # history is never rewritten.
        held = await refusal_of_the_modules(
            season_end_refused, ctx.db_path, season.id, CANCELLATION
        )
        if held is not None:
            return Verdict.refuse(held)

        # **Not while a submission holds accepted results** (Constitution XII): a session accepted,
        # or entered as not held, is data the cancellation would lose, and a round in its review
        # counts.
        standing: list[CancellationRound] = []
        for division in await seasons.get_divisions(season.id):
            if division.status == "CANCELLED":
                continue
            standing += [
                CancellationRound(
                    each.id, each.round_number, each.track_name, division.id, division.name,
                    season.id, each.status in ROUND_CANCELLABLE,
                )
                for each in await seasons.get_division_rounds(division.id)
            ]
        async with get_connection(ctx.db_path) as db:
            refusal = await _refusal_of_the_modules(
                cancellation_refused, db, SCOPE_SEASON, standing
            )
        if refusal is not None:
            return Verdict.refuse(refusal)
        return Verdict.go()

    # ── The jobs ────────────────────────────────────────────────────────────────

    async def unarm(ctx: StepContext) -> StepResult:
        """Remove the timed work of every round of every division, read as the job runs, and the
        job that would end the season on its own."""
        round_ids = [
            each.id
            for division in await seasons.get_divisions(int(ctx.payload["season_id"]))
            for each in await seasons.get_division_rounds(division.id)
        ]
        removed = scheduler.cancel_rounds(round_ids, season_end=True)
        return StepResult(result={
            "unarmed": True, "removed": {str(round_id): jobs for round_id, jobs in removed.items()},
        })

    async def unarmed(ctx: StepContext) -> bool:
        """The save is due only where the timed work was removed, not where that job was
        discarded."""
        return not _discarded(_view(ctx, UNARM))

    async def would_be_left_unrun(ctx: StepContext) -> str:
        """What dropping the save would leave: the timed work is off and nothing cancelled, so
        the save is retried, never discarded."""
        return (
            f"the rounds of season {ctx.payload['season_number']} would be left with no timed "
            "work, neither cancelled nor ever run"
        )

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
            return await _refused_rearming(db, ctx, SEASON_NO_SEASON, _SEASON_REARM_COMMAND)
        if SeasonStage(season["stage"]) not in ONGOING_STAGES:
            return await _refused_rearming(db, ctx, SEASON_NOT_ONGOING, _SEASON_REARM_COMMAND)

        cursor = await db.execute(
            "SELECT d.id AS division_id, d.name AS division_name, r.id AS round_id, "
            "r.round_number, r.track_name, r.status FROM rounds r "
            "JOIN divisions d ON d.id = r.division_id WHERE d.season_id = ? "
            "AND d.status != 'CANCELLED' ORDER BY d.tier, r.round_number",
            (season_id,),
        )
        standing = [
            CancellationRound(
                int(row["round_id"]), int(row["round_number"]), row["track_name"],
                int(row["division_id"]), str(row["division_name"]), season_id,
                row["status"] in ROUND_CANCELLABLE,
            )
            for row in await cursor.fetchall()
        ]
        # A backstop for the check: `unarm` lies between them, and a session may have been
        # accepted since. Nothing is written then, and the timed work `unarm` took is armed again.
        refusal = await _refusal_of_the_modules(
            cancellation_refused, db, SCOPE_SEASON, standing
        )
        if refusal is not None:
            return await _refused_rearming(db, ctx, refusal, _SEASON_REARM_COMMAND)

        # The empty open submissions are closed first, then the placements not yet confirmed
        # discarded so that they earn no history, then the rounds raced and awaiting verdicts made
        # final: the driver pass at the end would otherwise delete the drivers of those rounds.
        after = await _planned_by_the_modules(rounds_called_off, db, SCOPE_SEASON, standing)
        await discard_uncommitted_placements_on(db, season_id)
        await close_raced_rounds_on(
            db,
            season_id,
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            now=now(),
            rounds_made_final=rounds_made_final,
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

        planned = list(after.first)
        for division_id, division_name in running:
            target = {
                "division_id": division_id,
                "division_name": division_name,
                "season_id": season_id,
            }
            by_notices, after_notices = await _notices_planned_by_the_modules(
                cancellation_notices, db, SCOPE_SEASON, target
            )
            planned += by_notices
            rounds = called_off.get(division_id, [])
            planned += after.with_division.get(division_id, ())
            planned += after_notices
            planned += [
                PlannedStep(POST_CALENDAR, {**target, "round_ids": rounds, "only_if_posted": True}),
            ]
        # The roles are taken after the notices: the check-in notice mentions the division role.
        for target_driver in await season_role_targets_on(db, season_id):
            planned.append(PlannedStep(REVOKE_ROLES, {
                "user_id": int(target_driver["user_id"]),
                "role_ids": [int(role) for role in target_driver["role_ids"]],
                "reason": SEASON_CANCELLED_REASON,
            }))
        planned.extend(
            close_jobs(season_end_closes, SEASON_END_CAUSE, int(ctx.payload["season_number"]))
        )
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
        await write_driver_history_entries_on(
            db, season_id, number, season_standing_read=season_standing_read,
            force_cancelled=True,
        )
        driver_pass = await run_driver_pass_on(
            db, profiles_deleted=profiles_deleted, now=now()
        )
        await drivers_returned.call(db, driver_pass.drivers)
        if test_mode:
            await switch_test_mode_off_on(db, profiles_deleted=profiles_deleted)
        # The season's row is the last thing written (Constitution, the Season Archive). The
        # saved test-mode state is kept: a season cancelled is one abandoned.
        await cancel_season_on(db, season_id)

        planned = list(
            driver_jobs(
                driver_pass.drivers, driver_pass.driver_role_id,
                notice=SEASON_ENDED_NOTICE, reason=SEASON_CANCELLED_REASON,
                driver_returned_jobs=driver_returned_jobs,
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

    async def close(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Write the one line that records the cancellation, or the refusal a save met."""
        result = {"closed": True}
        refused = _refused(ctx)
        if refused is not None:
            return StepResult(
                result=result,
                lines=(
                    refusal_line(
                        ctx.named, SEASON_COMMAND, reply_reason(refused),
                        detail=_rearming_detail(ctx),
                    ),
                ),
            )
        if _discarded(_view(ctx, UNARM)):
            return StepResult(result=result)
        if not _season_ended(ctx):
            # The season is not cancelled, but what was discarded on the way is still named.
            failed = notices.failure_log_lines(not_notified(ctx))
            if not failed:
                return StepResult(result=result)
            return StepResult(
                result=result, lines=(f"{ctx.named} | /season cancel | Incomplete" + failed,)
            )
        line = (
            f"{ctx.named} | /season cancel | Success"
            + _audit_lines(ctx)
            + notices.failure_log_lines(not_notified(ctx))
            + "".join(f"\n  not done: {each}" for each in shared_not_done(ctx, steps))
        )
        return StepResult(result=result, lines=(line,))

    def outcome(ctx: OutcomeContext) -> str:
        number = ctx.payload["season_number"]
        if _discarded(_view(ctx, UNARM)):
            return SEASON_UNARM_DISCARDED.format(number=number)
        refused = _refused(ctx)
        if refused:
            return refused + _rearming_sentence(ctx)
        if not _season_ended(ctx):
            return SEASON_END_DISCARDED.format(number=number) + notices.failure_lines(
                not_notified(ctx)
            )
        return (
            SEASON_CANCELLED
            + notices.failure_lines(not_notified(ctx))
            + not_done_reply(
                shared_not_done(ctx, steps), discarded=anything_discarded(ctx)
            )
        )

    def describing(text: str) -> Callable[[StepContext], Awaitable[str]]:
        async def describe(ctx: StepContext) -> str:
            return text.format(number=ctx.payload["season_number"])

        return describe

    steps: dict[str, Step] = {
        **_follow_steps(seasons=seasons, drawing=drawing),
        **shared_steps(
            roles, scheduler, season_end_steps=season_end_steps,
            test_mode_flushed=test_mode_flushed, drivers_deleted=drivers_deleted,
        ),
        **steps_of_the_modules(cancellation_steps),
        UNARM: Step(
            UNARM, StepKind.ACT, unarm,
            describe=describing("removing the timed work of the rounds of season {number}"),
        ),
        APPLY: Step(
            APPLY, StepKind.SAVE, apply, still_due=unarmed,
            describe=describing("cancelling season {number}"),
            undiscardable=would_be_left_unrun,
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
