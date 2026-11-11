"""Completing and aborting a season, carried out on the change queue.

`/season complete` checks the season at the press, in the season cog, then asks the queue for this
change, so that no part of the season's end is caught or committed on its own and no success line
is written whatever has happened. This module holds the change; the cog holds the press.

**The check, when asked and again when the change runs** (`check`), refuses in this order, in the
command's own words: no season being raced, or the payload's season no longer the one; a round
being amended, which would carry its corrections into the final classification before they are
approved; rounds still to be finalised, or a division neither finished nor cancelled. It imports
no module and no cog: results' refusal of a round being amended reaches it through the
`season_end_refused` hook the builder hands it, and `ctx.bot` is used for Discord alone.

**The jobs, in the order the core specification sets**, each of which stops the queue where it
fails:

1. `settle` (a save): the scheduler's season-end job removed, each division still marked Active
   brought up to date with its rounds, and the season, now in Pending completion, planned its jobs.
   A season still in one of the ongoing stages whose every division is done is wound down first
   (its `turn_down`, as the wind-down change does it) and settled again.
2. Each division's final classification: `settle` leaves a cancelled division out by its own
   `divisions.status`, asks `final_round_read` which round each remaining division's
   classification is drawn against (results' own read of its `session_results`), then plans
   `season_completed` for that division and round — results' final standings, then attendance's
   final sheet, the modules' own steps (`season_completed_steps`), defined beside their opening
   posts in `season_standings_posts` and `sheet_posting`.
3. A `revoke_roles` for each real driver, then `close_window`, then `flush_forecasts` where test
   mode is on.
4. `end` (one save): the history (each driver's last standing asked of the modules through
   `season_standing_read`), the driver pass, test mode switched off and the season marked
   completed, **last**, with its audit record. A fault leaves the season as it was, Pending
   completion, for the queue to retry.
5. Then the Discord side of the driver pass (`signup_notice` and `close_signup` for each signup
   closed, `take_driver_role` for each approved driver), `discard_portraits` where the pass deleted
   a driver, and `discard_backup` where test mode was on.
6. `close` (a save) writes the one line: Success, or Incomplete with what a league admin discarded.

A job a league admin discards is dropped and the rest run on; the reply and the line say what was
not done and what to do by hand. A season whose `end` is discarded is not completed, and says to
run the command again.

**Aborting** (`season_abort_change`) is the same change over a season whose placements were never
confirmed. `/season abort` checks its confirmation word and finds that season at the press, then
asks the queue. The change is checked when asked and again when it runs, in the words the command
used: no season set up or active, or one past the confirmation of its placements. Its jobs:
`close_window` (the signup close timer cancelled, the window closed), `flush_forecasts` where
test mode is on, then `end`, one save that runs the driver pass (no history is written, the season
having none), switches test mode off and deletes the season, **last**. After that save each driver
the pass reaches has their signup channel's notice and lock or their driver role as jobs of their
own, and the portraits of the drivers deleted are discarded. The saved test-mode state is kept,
a season aborted being one abandoned. The signup records are keyed by account and outlive the season, so a channel is still
held after the save.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.core.db.database import get_connection, sole_row
from leaguebot.core.models.change import (
    AuditRecord,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.season import ONGOING_STAGES, SeasonStage
from leaguebot.core.models.season_end import COMPLETION
from leaguebot.core.services.seasons import approval_checks
from leaguebot.core.services.posting import standing_posts
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
from leaguebot.core.services.seasons.season_end_service import (
    DISCARD_BACKUP,
    DELETE_FORECAST,
    DISCARD_PORTRAITS,
    FLUSH_FORECASTS,
    REVOKE_ROLES,
    refusal_of_the_modules,
    season_role_targets_on,
    shared_steps,
    write_driver_history_entries_on,
)
from leaguebot.core.services.seasons.season_lifecycle_service import (
    ABORT_CAUSE,
    CLOSE_SIGNUP,
    NO_CHANNEL_LEFT,
    SEASON_END_CAUSE,
    SIGNUP_NOTICE,
    TAKE_DRIVER_ROLE,
    TURN_DOWN_STEP,
    WIND_DOWN,
    WIND_DOWN_CAUSE,
    advance_to_pending_completion_on,
    close_jobs,
    driver_jobs,
    kept_line,
    require_guild,
    run_driver_pass_on,
    wind_down_not_due_on,
    wind_down_steps,
)
from leaguebot.core.services.seasons.season_service import (
    SeasonService,
    complete_season_on,
    completion_refusal_on,
    refresh_division_status_on,
)
from leaguebot.core.services.test_mode.test_mode_service import switch_test_mode_off_on
from leaguebot.core.utils.hooks import AwaitedHook, Hook
from leaguebot.core.utils.log_lines import refusal_line, reply_reason

if TYPE_CHECKING:
    from leaguebot.core.services.scheduler_service import SchedulerService
    from leaguebot.core.services.drivers.team_role_service import TeamRoleService

__all__ = [
    "SEASON_ABORT",
    "SEASON_CANCEL",
    "SEASON_COMPLETE",
    "SEASON_ENDS",
    "anything_discarded",
    "discarded",
    "not_done_reply",
    "season_abort_change",
    "season_complete_change",
    "season_end_in_hand",
    "season_ending",
    "season_ending_jobs",
    "season_ending_on",
    "season_end_refusal",
    "shared_not_done",
    "test_mode_on",
    "view_of",
]

SEASON_COMPLETE = "season.complete"
#: The cancellation's kind is named here beside the others', which the helper that finds a season's
#: end in hand reads; the change itself is `cancellation_changes.season_cancel_change`.
SEASON_CANCEL = "season.cancel"
SEASON_ABORT = "season.abort"

#: The job names this change adds to the shared ones, as the stop notice, the tests and
#: ``StepView`` know them. The modules' own final posts are told apart from the shared jobs by the
#: names `season_completed_steps` defines.
SETTLE = "settle"
END = "end"
CLOSE = "close"

_COMMAND = "`/season complete`"
_ABORT_COMMAND = "`/season abort`"

#: What a signup channel is told where the driver pass closes its signup as the season ends.
SEASON_ENDED_NOTICE = (
    "🔒 This season has ended. This channel will be automatically deleted in 24 hours."
)
SEASON_ENDED_REASON = "Season ended"

NO_SEASON = "❌ No season is being raced, so there is none to complete."

ABORT_ONLY_BEFORE = (
    "❌ `/season abort` is available only before a season's placements are first confirmed. An "
    "ongoing season is cancelled with `/season cancel`."
)
ABORTED = (
    "✅ The season has been aborted. Nothing of it remains, and a new season may be set up with "
    "`/season setup`."
)
ABORT_END_DISCARDED = (
    "Nothing was aborted: the season stands as it was. Run `/season abort` again."
)

#: The stages before a season's placements are first confirmed, when it may be aborted.
PRE_CONFIRMATION = frozenset({
    SeasonStage.CONFIGURATION,
    SeasonStage.WAITING,
    SeasonStage.SIGNUPS,
    SeasonStage.PLACEMENTS,
})

COMPLETED = "✅ Season marked as complete."
SETTLE_DISCARDED = (
    "Nothing was completed: season {number} stands as it was. Run `/season complete` again."
)
WIND_DOWN_DISCARDED = (
    "Nothing was completed: the season could not be moved to pending completion. Run "
    "`/season complete` again."
)
END_DISCARDED = (
    "Season {number} was not completed: the save that records its end was discarded. Run "
    "`/season complete` again; its final classifications will be posted again."
)
BACKUP_KEPT = (
    "The saved test-mode state could not be deleted. It is deleted with the next `/test-mode "
    "toggle` that switches test mode off."
)
FORECASTS_KEPT = (
    "The forecasts posted under test mode could not be cleared. Delete them by hand from each "
    "forecast channel."
)
PORTRAITS_KEPT = "The portraits of the drivers deleted could not be discarded."


def _forecast_kept(view: StepView) -> str:
    """The line for a forecast whose deletion a league admin discarded, with its link where the
    job kept one: it stands in its channel, to be deleted by hand."""
    result = view.result or {}
    message = result.get("message_id", view.payload.get("message_id"))
    if "guild_id" in result and message is not None:
        where = f"https://discord.com/channels/{result['guild_id']}/{result['channel_id']}/{message}"
    else:
        where = f"message {message} in <#{result.get('channel_id')}>"
    return (
        f"The Phase {view.payload.get('phase', result.get('phase'))} forecast of round "
        f"{view.payload.get('round_number', '?')} could not be deleted: delete it by hand "
        f"({where})."
    )


def completion_in_hand_refusal(season_number: Any, job: int) -> str:
    """The refusal of a second `/season complete` while the first is waiting, being carried out
    or stopped on a failure; it names the job, and leaves it out where only the close is left."""
    named = f" (job #{job})" if job else ""
    return (
        f"⏳ Season {season_number} is already being completed{named}. If it has stopped, press "
        "Retry or Discard on its notice in the log channel."
    )


def cancellation_in_hand_refusal(season_number: Any, job: int) -> str:
    """The refusal of a second `/season cancel` while the first is in hand; as the completion's."""
    named = f" (job #{job})" if job else ""
    return (
        f"⏳ Season {season_number} is already being cancelled{named}. If it has stopped, press "
        "Retry or Discard on its notice in the log channel."
    )


def abort_in_hand_refusal(job: int) -> str:
    """The refusal of a second `/season abort` while the first is in hand."""
    named = f" (job #{job})" if job else ""
    return (
        f"⏳ The season being set up is already being aborted{named}. If it has stopped, press "
        "Retry or Discard on its notice in the log channel."
    )


def season_end_refusal(own: str | None, hand: tuple[str, int], season_number: Any) -> str:
    """The refusal of a request while the season's end *hand* (its kind and job, as
    `season_end_in_hand` gives them) is in hand.

    A request that is itself the same kind of end, *own*, is a second one and is told so in the
    words of its command; any other request is told the season is being ended and that this cannot
    be done until that is finished. The job is left out where only the end's close is left.
    """
    kind, job = hand
    if own == kind:
        if kind == SEASON_COMPLETE:
            return completion_in_hand_refusal(season_number, job)
        if kind == SEASON_CANCEL:
            return cancellation_in_hand_refusal(season_number, job)
        return abort_in_hand_refusal(job)
    named = f" (job #{job})" if job else ""
    tail = (
        ", so this cannot be done until that is finished. If it has stopped, press Retry or "
        "Discard on its notice in the log channel."
    )
    if kind == SEASON_COMPLETE:
        return f"⏳ Season {season_number} is being completed{named}{tail}"
    if kind == SEASON_CANCEL:
        return f"⏳ Season {season_number} is being cancelled{named}{tail}"
    return f"⏳ The season being set up is being aborted{named}{tail}"


async def season_end_in_hand(
    db_path: str, season_id: int, *, excluding: int | None = None
) -> tuple[str, int] | None:
    """Which end of season *season_id* is in hand on the queue, as its kind and the job it waits on.

    A completion, cancellation or abort of the season that is queued, running or stopped on a
    failure is in hand; one done, refused, dropped or discarded is not, nor is another season's.
    Where two are in hand, the one nearest its turn is given: the one whose first job not done
    comes first. The job is 0 where only the end's own close is left. *excluding* leaves out the
    change whose id it is, so that a check made as the change runs does not find itself.

    Every request about a season reads it, only as the request is asked: one rule for every
    request about a season whose end is in hand.
    """
    nearest: tuple[str, int] | None = None
    for kind in (SEASON_COMPLETE, SEASON_CANCEL, SEASON_ABORT):
        for payload, job in await in_hand(db_path, (kind,), excluding=excluding):
            if payload.get("season_id") != season_id:
                continue
            if nearest is None or (job or 0) < nearest[1]:
                nearest = (kind, job or 0)
    return nearest


#: The changes that end a season: its completion, cancellation and abort. Each closes the signup
#: window itself, where one stands open, and moves the season its own way.
SEASON_ENDS = (SEASON_COMPLETE, SEASON_CANCEL, SEASON_ABORT)


async def _any_in_hand_on(db: aiosqlite.Connection, kinds: tuple[str, ...]) -> bool:
    """Whether a change of *kinds* is queued, running or stopped on a failure, read on *db*."""
    marks = ", ".join("?" for _ in kinds)
    cursor = await db.execute(
        f"SELECT 1 FROM queued_changes WHERE kind IN ({marks}) "  # noqa: S608 — marks only
        "AND state IN ('QUEUED', 'RUNNING') LIMIT 1",
        kinds,
    )
    return await cursor.fetchone() is not None


async def season_ending_on(db: aiosqlite.Connection) -> bool:
    """Whether a season's end (its completion, cancellation or abort) or its wind-down to Pending
    completion is in hand on the queue, queued, running or stopped on a failure, as read on *db*.

    A close of the signup window leaves such a season to it, neither moving the season on nor, for
    the bot's own close, returning its drivers (the signup specification's close of the window):
    the end and the wind-down close the window themselves. A wind-down counts only where it would
    run now, the season meeting its check (`wind_down_not_due_on`): the bot asks one wherever a
    division may have been the season's last, and one its check will drop closes nothing, so it
    holds no close back. One season is live at a time, so an end in hand is the live season's.
    Read on the caller's connection, so that a record may ask it inside its save.
    """
    if await _any_in_hand_on(db, SEASON_ENDS):
        return True
    return await _any_in_hand_on(db, (WIND_DOWN,)) and await wind_down_not_due_on(db) is None


async def season_ending(db_path: str) -> bool:
    """`season_ending_on`, read on a connection of its own, for a check or a job's `still_due`."""
    async with get_connection(db_path) as db:
        return await season_ending_on(db)


async def season_ending_jobs(db_path: str) -> list[int | None]:
    """The job each change `season_ending` counts waits on, oldest first: `None` for one whose
    jobs are all done and only its own close is left. Empty where none is in hand."""
    kinds: list[str] = list(SEASON_ENDS)
    async with get_connection(db_path) as db:
        if await wind_down_not_due_on(db) is None:
            kinds.append(WIND_DOWN)
    return [job for _payload, job in await in_hand(db_path, kinds)]


def view_of(ctx: OutcomeContext, name: str) -> StepView | None:
    """The last job named *name*: a save tried again is planned afresh, and the last stands."""
    return next((view for view in reversed(ctx.steps) if view.name == name), None)


def discarded(view: StepView | None) -> bool:
    """Whether a league admin discarded the job."""
    return view is not None and "discarded" in (view.result or {})


def anything_discarded(ctx: OutcomeContext) -> bool:
    """Whether a league admin discarded any job of the change. Only then is a season's end
    incomplete: a driver with no signup channel left is named beneath a line that still reads as
    a success."""
    return any(discarded(view) for view in ctx.steps)


def not_done_reply(lines: Sequence[str], *, discarded: bool) -> str:
    """The part of a season's end's reply naming what was not done: under the "Not everything
    could be done" heading where anything was *discarded*; otherwise *lines*, which can then only
    name drivers with no signup channel left to tell, each on a line of its own under no heading."""
    if discarded:
        return approval_checks.not_done_section(list(lines))
    if not lines:
        return ""
    return "\n\n" + "\n".join(lines)


def shared_not_done(
    ctx: OutcomeContext, steps: Mapping[str, Step], *, classification: frozenset[str] = frozenset(),
) -> list[str]:
    """What the jobs every end of a season shares left undone, one line for each job a league
    admin discarded, in the order of the jobs, the drivers whose roles were not taken back
    gathered into one line.

    A signup channel whose notice was discarded but which was closed is named as closed without
    its notice; one that was not closed is named as not closed alone. A job whose step words its
    own discard (`Step.not_done` of one of the change's *steps*, signup's close of its window) is
    named in those words. *classification* names the modules' own final-post steps, already
    named first by `_classification_not_done`; completion passes it to skip them here, and
    abort, which plans none, leaves it empty.
    """
    lines: list[str] = []
    roles: list[str] = []
    roles_at: int | None = None
    closed = {
        str(view.payload["user_id"])
        for view in ctx.steps
        if view.name == CLOSE_SIGNUP and view.done and not discarded(view)
    }
    for view in ctx.steps:
        if view.name == SIGNUP_NOTICE and view.done and (view.result or {}).get("no_channel"):
            lines.append(NO_CHANNEL_LEFT.format(who=f"<@{view.payload['user_id']}>"))
        if not discarded(view):
            continue
        each = view.payload
        if view.name in classification:
            # Named first, ahead of the shared jobs, by `_classification_not_done`.
            continue
        words = kept_line(steps, view)
        if words is not None:
            lines.append(words)
        elif view.name == REVOKE_ROLES:
            if roles_at is None:
                roles_at = len(lines)
                lines.append("")
            roles.append(f"<@{each['user_id']}>")
        elif view.name == FLUSH_FORECASTS:
            lines.append(FORECASTS_KEPT)
        elif view.name == DELETE_FORECAST:
            lines.append(_forecast_kept(view))
        elif view.name == standing_posts.DELETE_REPLACED:
            # A calendar's earlier post is named by the cancellation's own outcome; here only a
            # part of a forecast posted in parts.
            part = standing_posts.not_deleted(view)
            if part is not None and "forecast" in str(each.get("what", "")):
                lines.append(part)
        elif view.name == SIGNUP_NOTICE:
            if str(each["user_id"]) in closed:
                lines.append(
                    f"<@{each['user_id']}> — their signup channel was closed without its notice"
                )
        elif view.name == CLOSE_SIGNUP:
            lines.append(
                f"<@{each['user_id']}> — their signup channel could not be closed. Delete it "
                "by hand."
            )
        elif view.name == TAKE_DRIVER_ROLE:
            lines.append(
                f"<@{each['user_id']}> — the driver role could not be taken back. Remove it "
                "by hand."
            )
        elif view.name == DISCARD_PORTRAITS:
            lines.append(PORTRAITS_KEPT)
    if roles_at is not None:
        number = ctx.payload.get("season_number")
        of = f" of season {number}" if number else ""
        lines[roles_at] = (
            f"{', '.join(roles)} — their roles{of} could not be taken back. Remove their "
            "division and team roles and the driver role by hand."
        )
    return lines


def _classification_not_done(
    ctx: OutcomeContext, steps: Mapping[str, Step], classification: frozenset[str],
) -> list[str]:
    """What a module's discarded final post leaves undone, in its own step's words
    (`kept_line`), the classification lines kept ahead of the shared season-end ones.
    *classification* names the steps `season_completed_steps` defined."""
    lines: list[str] = []
    for view in ctx.steps:
        if not discarded(view) or view.name not in classification:
            continue
        words = kept_line(steps, view)
        if words is not None:
            lines.extend(words.split("\n"))
    return lines


def _not_done(
    ctx: OutcomeContext, steps: Mapping[str, Step], classification: frozenset[str],
) -> list[str]:
    """What a league admin's Discards left undone, in the order of the jobs."""
    # The jobs run the classification first, then the shared ones, then the backup's deletion.
    lines = [
        *_classification_not_done(ctx, steps, classification),
        *shared_not_done(ctx, steps, classification=classification),
    ]
    if discarded(view_of(ctx, DISCARD_BACKUP)):
        lines.append(BACKUP_KEPT)
    return lines


def season_complete_change(
    *,
    seasons: "SeasonService",
    scheduler: "SchedulerService",
    roles: "TeamRoleService",
    final_round_read: AwaitedHook,
    season_standing_read: AwaitedHook,
    season_completed: AwaitedHook,
    season_completed_steps: Hook,
    season_end_refused: AwaitedHook,
    season_end_closes: Hook,
    drivers_returned: AwaitedHook,
    driver_turned_down: AwaitedHook,
    driver_returned_jobs: Hook,
    season_end_steps: Hook,
    test_mode_flushed: AwaitedHook,
    drivers_deleted: AwaitedHook,
    profiles_deleted: AwaitedHook,
    now: Callable[[], datetime],
) -> ChangeType:
    """The change that completes the confirmed season; see the module.

    The builder hands in the *seasons* service (the season the check reads), the *scheduler* (whose
    season-end job `settle` removes), the the team-role service, *roles* (which takes back roles), the hooks
    the modules sign up to (*final_round_read*, *season_standing_read*, *season_completed*,
    *season_completed_steps*, *season_end_refused*, *season_end_closes*, *drivers_returned*,
    *driver_turned_down*, *driver_returned_jobs*, *season_end_steps*, *test_mode_flushed*,
    *drivers_deleted*), the hook each driver deleted calls (*profiles_deleted*) and the queue's
    clock *now*, which stamps the audit entries of the turn-down and the driver pass.
    """

    async def check(ctx: CheckContext) -> Verdict:
        season = await seasons.get_confirmed_season()
        if season is None or season.id != int(ctx.payload["season_id"]):
            return Verdict.refuse(NO_SEASON)

        # **A second completion, asked while the first is in hand**: refused at once, naming
        # the job. Only as it is asked: as the change runs, it would find itself.
        if ctx.change_id is None:
            hand = await season_end_in_hand(ctx.db_path, season.id)
            if hand is not None:
                return Verdict.refuse(
                    season_end_refusal(SEASON_COMPLETE, hand, season.season_number)
                )

        # **Not while a round is being amended**: completing posts each division's final
        # classification from the database, which holds an open amendment's corrections before they
        # are approved.
        held = await refusal_of_the_modules(
            season_end_refused, ctx.db_path, season.id, COMPLETION
        )
        if held is not None:
            return Verdict.refuse(held)

        async with get_connection(ctx.db_path) as db:
            refusal = await completion_refusal_on(db, season.id)
        if refusal is not None:
            # The reply is a list when rounds are outstanding, and the line carries all of it.
            return Verdict.refuse(refusal, "\n".join(refusal.splitlines()[1:]))
        return Verdict.go()

    async def settle(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        season_id = int(ctx.payload["season_id"])
        cursor = await db.execute(
            "SELECT status, season_number FROM seasons WHERE id = ?", (season_id,)
        )
        season = await cursor.fetchone()
        if season is None or season["status"] != "ACTIVE":
            return StepResult(result={"refused": NO_SEASON})
        refusal = await completion_refusal_on(db, season_id)
        if refusal is not None:
            return StepResult(result={"refused": refusal})

        again = bool(ctx.step_payload.get("again"))
        if not again:
            # Nothing is due to end the season on its own any more.
            scheduler.cancel_season_end()
        cursor = await db.execute(
            "SELECT id FROM divisions WHERE season_id = ? AND status = 'ACTIVE' ORDER BY id",
            (season_id,),
        )
        for division in await cursor.fetchall():
            await refresh_division_status_on(db, int(division["id"]))
        await advance_to_pending_completion_on(db, season_id)

        cursor = await db.execute("SELECT stage FROM seasons WHERE id = ?", (season_id,))
        stage = SeasonStage((await sole_row(cursor))["stage"])
        if stage in ONGOING_STAGES:
            if again:
                # The wind-down was discarded: the season is still in an ongoing stage.
                return StepResult(result={"unsettled": True})
            return StepResult(
                result={"wound_down": True},
                then=(
                    *close_jobs(season_end_closes, WIND_DOWN_CAUSE),
                    PlannedStep(TURN_DOWN_STEP),
                ),
            )
        if stage is not SeasonStage.PENDING_COMPLETION:
            return StepResult(result={"refused": NO_SEASON})

        number = int(season["season_number"])
        planned: list[PlannedStep] = []
        cursor = await db.execute(
            "SELECT id, name FROM divisions WHERE season_id = ? AND status != 'CANCELLED' "
            "ORDER BY id",
            (season_id,),
        )
        for division in await cursor.fetchall():
            division_id = int(division["id"])
            division_name = str(division["name"])
            rounds = await final_round_read.call(db, division_id)
            round_id = next((each for each in rounds if each is not None), None)
            if round_id is None:
                continue
            planned.extend(
                job
                for jobs in await season_completed.call(
                    db, division_id, division_name, int(round_id)
                )
                for job in jobs
            )
        for target in await season_role_targets_on(db, season_id):
            planned.append(
                PlannedStep(
                    REVOKE_ROLES,
                    {
                        "user_id": int(target["user_id"]),
                        "role_ids": [int(role) for role in target["role_ids"]],
                        "reason": SEASON_ENDED_REASON,
                    },
                )
            )
        planned.extend(close_jobs(season_end_closes, SEASON_END_CAUSE, number))
        if await test_mode_on(db):
            planned.append(PlannedStep(FLUSH_FORECASTS))
        planned.append(PlannedStep(END))
        return StepResult(
            result={"season_number": number, "settled": True}, then=tuple(planned)
        )

    async def end(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        season_id = int(ctx.payload["season_id"])
        cursor = await db.execute(
            "SELECT status, season_number FROM seasons WHERE id = ?", (season_id,)
        )
        season = await cursor.fetchone()
        if season is None or season["status"] != "ACTIVE":
            return StepResult(result={"refused": NO_SEASON})
        number = int(season["season_number"])

        test_mode = await test_mode_on(db)
        await write_driver_history_entries_on(
            db, season_id, number, season_standing_read=season_standing_read
        )
        driver_pass = await run_driver_pass_on(
            db, profiles_deleted=profiles_deleted, now=now()
        )
        await drivers_returned.call(db, driver_pass.drivers)
        if test_mode:
            await switch_test_mode_off_on(db, profiles_deleted=profiles_deleted)
        # The archive is the last thing written (Constitution, the Season Archive).
        await complete_season_on(db, season_id)

        planned = list(
            driver_jobs(
                driver_pass.drivers, driver_pass.driver_role_id,
                notice=SEASON_ENDED_NOTICE, reason=SEASON_ENDED_REASON,
                driver_returned_jobs=driver_returned_jobs,
            )
        )
        if driver_pass.accounts:
            planned.append(PlannedStep(DISCARD_PORTRAITS, {"accounts": driver_pass.accounts}))
        if test_mode:
            planned.append(PlannedStep(DISCARD_BACKUP))
        audit = AuditRecord(
            "SEASON_COMPLETED",
            {"season_id": season_id, "status": "ACTIVE"},
            {
                "season_id": season_id,
                "season_number": number,
                "status": "COMPLETED",
                "drivers_returned": driver_pass.reset,
                "drivers_deleted": len(driver_pass.deleted),
            },
        )
        return StepResult(
            result={
                "season_number": number,
                "drivers_returned": driver_pass.reset,
                "drivers_deleted": len(driver_pass.deleted),
            },
            audits=(audit,),
            then=tuple(planned),
        )

    def _completed(ctx: OutcomeContext) -> bool:
        """Whether the season's end was saved: `end` is done, and neither refused nor discarded."""
        view = view_of(ctx, END)
        if view is None or not view.done:
            return False
        return not (discarded(view) or (view.result or {}).get("refused"))

    def _refused(ctx: OutcomeContext) -> str | None:
        for name in (SETTLE, END):
            for view in ctx.steps:
                if view.name == name:
                    refused = (view.result or {}).get("refused")
                    if refused:
                        return str(refused)
        return None

    def _first_settle_discarded(ctx: OutcomeContext) -> bool:
        first = next((view for view in ctx.steps if view.name == SETTLE), None)
        return discarded(first)

    def _unsettled(ctx: OutcomeContext) -> bool:
        """Whether the wind-down did not bring the season to Pending completion: its save was
        discarded, so that the season was never settled again, or it left the season where it was."""
        settles = [view for view in ctx.steps if view.name == SETTLE]
        if not settles or not (settles[0].result or {}).get("wound_down"):
            return False
        return len(settles) < 2 or bool((settles[1].result or {}).get("unsettled"))

    async def close(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        result = {"closed": True}
        refused = _refused(ctx)
        if refused is not None:
            return StepResult(
                result=result,
                lines=(refusal_line(ctx.named, _COMMAND, reply_reason(refused)),),
            )
        if not _completed(ctx):
            return StepResult(result=result)
        left = _not_done(ctx, steps, classification_names)
        number = ctx.payload["season_number"]
        status = "Incomplete" if anything_discarded(ctx) else "Success"
        line = (
            f"{ctx.named} | /season complete | {status}\n"
            f"  season: Season #{number}" + "".join(f"\n  not done: {each}" for each in left)
        )
        return StepResult(result=result, lines=(line,))

    def outcome(ctx: OutcomeContext) -> str:
        refused = _refused(ctx)
        if refused is not None:
            return refused
        number = ctx.payload["season_number"]
        if _first_settle_discarded(ctx):
            return SETTLE_DISCARDED.format(number=number)
        if _unsettled(ctx):
            return WIND_DOWN_DISCARDED
        if not _completed(ctx):
            return END_DISCARDED.format(number=number)
        return COMPLETED + not_done_reply(
            _not_done(ctx, steps, classification_names), discarded=anything_discarded(ctx)
        )

    # ── How each job is named, in the lines that say it stopped the queue ──────

    async def describe_settle(ctx: StepContext) -> str:
        number = ctx.payload["season_number"]
        return (
            f"checking season {number} is ready to complete"
            if ctx.step_payload.get("again")
            else f"bringing the divisions of season {number} up to date"
        )

    async def describe_end(ctx: StepContext) -> str:
        return f"recording the end of season {ctx.payload['season_number']}"

    async def describe_close(_ctx: StepContext) -> str:
        return "recording the completion of the season"

    winding = wind_down_steps(
        drivers_returned=drivers_returned, driver_turned_down=driver_turned_down,
        driver_returned_jobs=driver_returned_jobs, now=now,
    )
    turn_down = winding[TURN_DOWN_STEP]

    async def turn_down_then_settle(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The wind-down's save, which settles the season again before the jobs for the drivers it
        turned down: they follow the second `settle`, which plans the final classifications and
        the rest ahead of them."""
        result = await turn_down.run(db, ctx)
        return replace(result, then=(PlannedStep(SETTLE, {"again": True}), *result.then))

    classification_steps = {
        name: step
        for defined in season_completed_steps.call()
        for name, step in defined.items()
    }
    classification_names = frozenset(classification_steps)

    steps: dict[str, Step] = {
        SETTLE: Step(SETTLE, StepKind.SAVE, settle, describe=describe_settle),
        **classification_steps,
        END: Step(END, StepKind.SAVE, end, describe=describe_end),
        CLOSE: Step(CLOSE, StepKind.SAVE, close, describe=describe_close),
        **winding,
        TURN_DOWN_STEP: replace(turn_down, run=turn_down_then_settle),
        **shared_steps(
            roles, scheduler, season_end_steps=season_end_steps,
            test_mode_flushed=test_mode_flushed, drivers_deleted=drivers_deleted,
        ),
    }
    return ChangeType(
        kind=SEASON_COMPLETE,
        opening=(PlannedStep(SETTLE), PlannedStep(CLOSE)),
        steps=steps,
        check=check,
        key=lambda payload: f"{SEASON_COMPLETE}:{payload['season_id']}",
        doing=lambda payload: f"Completing season {payload['season_number']}",
        outcome=outcome,
    )


def season_abort_change(
    *,
    seasons: "SeasonService",
    scheduler: "SchedulerService",
    roles: "TeamRoleService",
    season_end_closes: Hook,
    drivers_returned: AwaitedHook,
    driver_returned_jobs: Hook,
    season_end_steps: Hook,
    test_mode_flushed: AwaitedHook,
    drivers_deleted: AwaitedHook,
    rounds_deleted: AwaitedHook,
    divisions_deleted: AwaitedHook,
    season_deleted: AwaitedHook,
    profiles_deleted: AwaitedHook,
    now: Callable[[], datetime],
) -> ChangeType:
    """The change that aborts the season being set up; see the module.

    The payload is ``{"season_id"}``: a season set up has no number until its placements are
    confirmed. The builder hands in the *seasons* service (the season the check reads), the
    *scheduler* (whose job store holds the saved test-mode state's scheduler half), the
    the team-role service, *roles* (which takes back roles), the hooks the modules sign up to
    (*season_end_closes*, *drivers_returned*, *driver_returned_jobs*, *season_end_steps*,
    *test_mode_flushed*, *drivers_deleted*), the hooks the season's deletion calls
    (*rounds_deleted*, *divisions_deleted*, *season_deleted*), the hook each driver deleted calls
    (*profiles_deleted*) and the queue's clock *now*, which stamps the driver pass's audit entry.
    """

    async def check(ctx: CheckContext) -> Verdict:
        season = await seasons.get_setup_or_active_season()
        if season is None or season.id != int(ctx.payload["season_id"]):
            return Verdict.refuse(ABORT_ONLY_BEFORE)

        # **Another end of the season in hand**: refused at once, naming the job, ahead of the
        # stage gate, whatever stage the season is in: a second abort, or a cancellation or
        # completion of a season past its placements. Only as it is asked: as the change runs, it
        # would find itself.
        if ctx.change_id is None:
            hand = await season_end_in_hand(ctx.db_path, season.id)
            if hand is not None:
                return Verdict.refuse(
                    season_end_refusal(SEASON_ABORT, hand, season.season_number)
                )
        if season.stage not in PRE_CONFIRMATION:
            return Verdict.refuse(ABORT_ONLY_BEFORE)
        return Verdict.go()

    async def end(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The one save: the driver pass, test mode and the season's deletion, last."""
        season_id = int(ctx.payload["season_id"])
        cursor = await db.execute(
            "SELECT status, stage FROM seasons WHERE id = ?", (season_id,)
        )
        season = await cursor.fetchone()
        if (
            season is None
            or season["status"] != "SETUP"
            or SeasonStage(season["stage"]) not in PRE_CONFIRMATION
        ):
            return StepResult(result={"refused": ABORT_ONLY_BEFORE})

        test_mode = await test_mode_on(db)
        driver_pass = await run_driver_pass_on(
            db, profiles_deleted=profiles_deleted, now=now()
        )
        await drivers_returned.call(db, driver_pass.drivers)
        if test_mode:
            await switch_test_mode_off_on(db, profiles_deleted=profiles_deleted)
        # The season's deletion is the last thing written. The saved test-mode state is kept: a
        # season aborted is one abandoned.
        await SeasonService.delete_season(
            db, season_id,
            rounds_deleted=rounds_deleted,
            divisions_deleted=divisions_deleted,
            season_deleted=season_deleted,
            profiles_deleted=profiles_deleted,
        )

        planned = list(
            driver_jobs(
                driver_pass.drivers, driver_pass.driver_role_id,
                notice=SEASON_ENDED_NOTICE, reason=SEASON_ENDED_REASON,
                driver_returned_jobs=driver_returned_jobs,
            )
        )
        if driver_pass.accounts:
            planned.append(PlannedStep(DISCARD_PORTRAITS, {"accounts": driver_pass.accounts}))
        return StepResult(
            result={
                "drivers_returned": driver_pass.reset,
                "drivers_deleted": len(driver_pass.deleted),
            },
            then=tuple(planned),
        )

    def _end_result(ctx: OutcomeContext) -> dict[str, Any]:
        view = view_of(ctx, END)
        return dict(view.result or {}) if view is not None else {}

    def _refused(ctx: OutcomeContext) -> str | None:
        refused = _end_result(ctx).get("refused")
        return str(refused) if refused else None

    def _aborted(ctx: OutcomeContext) -> bool:
        """Whether the season was deleted: `end` is done, and neither refused nor discarded."""
        view = view_of(ctx, END)
        if view is None or not view.done:
            return False
        return not (discarded(view) or (view.result or {}).get("refused"))

    async def close(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        result = {"closed": True}
        refused = _refused(ctx)
        if refused is not None:
            return StepResult(
                result=result,
                lines=(refusal_line(ctx.named, _ABORT_COMMAND, reply_reason(refused)),),
            )
        if not _aborted(ctx):
            return StepResult(result=result)
        counts = _end_result(ctx)
        line = (
            f"{ctx.named} | /season abort | Success\n"
            f"  drivers returned to Not Signed Up: {counts.get('drivers_returned', 0)}\n"
            f"  drivers deleted: {counts.get('drivers_deleted', 0)}"
            + "".join(f"\n  not done: {each}" for each in shared_not_done(ctx, steps))
        )
        return StepResult(result=result, lines=(line,))

    def outcome(ctx: OutcomeContext) -> str:
        refused = _refused(ctx)
        if refused is not None:
            return refused
        if not _aborted(ctx):
            return ABORT_END_DISCARDED
        return ABORTED + not_done_reply(
            shared_not_done(ctx, steps), discarded=anything_discarded(ctx)
        )

    async def describe_end(_ctx: StepContext) -> str:
        return "aborting the season being set up"

    async def describe_close(_ctx: StepContext) -> str:
        return "recording the abort of the season being set up"

    async def test_mode_still_on(ctx: StepContext) -> bool:
        """The forecasts are cleared only where test mode posted them: the job is planned with the
        abort, before the save that switches test mode off, and reads the flag as it comes up."""
        async with get_connection(ctx.db_path) as db:
            return await test_mode_on(db)

    shared = shared_steps(
        roles, scheduler, season_end_steps=season_end_steps,
        test_mode_flushed=test_mode_flushed, drivers_deleted=drivers_deleted,
    )
    steps: dict[str, Step] = {
        **shared,
        FLUSH_FORECASTS: replace(shared[FLUSH_FORECASTS], still_due=test_mode_still_on),
        END: Step(END, StepKind.SAVE, end, describe=describe_end),
        CLOSE: Step(CLOSE, StepKind.SAVE, close, describe=describe_close),
    }
    def plan(_payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        return (
            *close_jobs(season_end_closes, ABORT_CAUSE),
            PlannedStep(FLUSH_FORECASTS),
            PlannedStep(END),
            PlannedStep(CLOSE),
        )

    return ChangeType(
        kind=SEASON_ABORT,
        opening=(),
        plan=plan,
        steps=steps,
        check=check,
        key=lambda payload: f"{SEASON_ABORT}:{payload['season_id']}",
        doing=lambda payload: "Aborting the season being set up",
        outcome=outcome,
    )


async def test_mode_on(db: aiosqlite.Connection) -> bool:
    """Whether the server is in test mode, read on the connection handed."""
    cursor = await db.execute("SELECT test_mode_active FROM server_configs")
    row = await cursor.fetchone()
    return row is not None and bool(row["test_mode_active"])


