"""Completing and aborting a season, carried out on the change queue (#439, slice 5).

`/season complete` checks the season at the press, in the season cog, then asks the queue for this
change. It used to end the season inside the command: the final classifications caught to a
problems list, then the history, the roles, the signup window, the driver pass and test mode each
caught or committed on its own, and the success line written whatever had happened. This module
holds the change; the cog holds the press.

**The check, when asked and again when the change runs** (`check`), refuses in this order, in the
words the command used: no season being raced, or the payload's season no longer the one; a round
being amended, which would carry its corrections into the final classification before they are
approved; rounds still to be finalised, or a division neither finished nor cancelled. It imports
no module and no cog: results' open amendment reaches it through the builder's `SeasonEndHooks`,
and `ctx.bot` is used for Discord alone.

**The jobs, in the order the core specification sets**, each of which stops the queue where it
fails:

1. `settle` (a save): the scheduler's season-end job removed, each division still marked Active
   brought up to date with its rounds, and the season, now in Pending completion, planned its jobs.
   A season still in one of the ongoing stages whose every division is done is wound down first
   (`wind_down`, `turn_down`, as the wind-down change does them) and settled again.
2. Each division's `final_standings`, then its `final_sheet`, drawn against its last round with
   results while the season is still active.
3. A `revoke_roles` for each real driver, then `close_window`, then `flush_forecasts` where test
   mode is on.
4. `end` (one save): the history, the driver pass, test mode switched off and the season marked
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
own, the portraits of the drivers deleted are discarded and the setup the bot holds in memory is
let go of (`forget_setup`). The saved test-mode state is kept, a season aborted being one
abandoned. The signup records are keyed by account and outlive the season, so a channel is still
held after the save.
"""
from __future__ import annotations

from dataclasses import replace
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
from leaguebot.core.services import approval_checks, season_classification_service
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
    StepView,
    in_hand,
)
from leaguebot.core.services.season_end_service import (
    CLOSE_WINDOW,
    DISCARD_BACKUP,
    DISCARD_PORTRAITS,
    FLUSH_FORECASTS,
    FORGET_SETUP,
    REVOKE_ROLES,
    SEASON_END_CAUSE,
    season_end_steps,
    season_role_targets_on,
    write_driver_history_entries_on,
)
from leaguebot.core.services.season_lifecycle_service import (
    CLOSE_SIGNUP,
    SIGNUP_NOTICE,
    TAKE_DRIVER_ROLE,
    TURN_DOWN_STEP,
    WIND_DOWN_STEP,
    SeasonEndHooks,
    advance_to_pending_completion_on,
    driver_jobs,
    require_guild,
    run_driver_pass_on,
    wind_down_steps,
)
from leaguebot.core.services.season_approval_change import NOT_FORGOTTEN
from leaguebot.core.services.season_service import (
    SeasonService,
    complete_season_on,
    completion_refusal_on,
    refresh_division_status_on,
)
from leaguebot.core.services.test_mode_service import switch_test_mode_off_on
from leaguebot.core.utils.log_lines import refusal_line, reply_reason

if TYPE_CHECKING:
    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.placement_service import PlacementService
    from leaguebot.core.services.scheduler_service import SchedulerService

__all__ = [
    "SEASON_ABORT",
    "SEASON_CANCEL",
    "SEASON_COMPLETE",
    "discarded",
    "season_abort_change",
    "season_complete_change",
    "season_end_in_hand",
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
#: ``StepView`` know them.
SETTLE = "settle"
FINAL_STANDINGS = "final_standings"
FINAL_SHEET = "final_sheet"
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
WINDOW_KEPT = "The signup window could not be closed. Close it with `/signup close`."
FORECASTS_KEPT = (
    "The forecasts posted under test mode could not be cleared. Delete them by hand from each "
    "forecast channel."
)
PORTRAITS_KEPT = "The portraits of the drivers deleted could not be discarded."


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

    Every request about a season reads it, only as the request is asked (the owner, 2026-10-09:
    one rule for every request about a season whose end is in hand).
    """
    nearest: tuple[str, int] | None = None
    for kind in (SEASON_COMPLETE, SEASON_CANCEL, SEASON_ABORT):
        for payload, job in await in_hand(db_path, (kind,), excluding=excluding):
            if payload.get("season_id") != season_id:
                continue
            if nearest is None or (job or 0) < nearest[1]:
                nearest = (kind, job or 0)
    return nearest


def amended(held: Any) -> str:
    """The refusal for a round being amended: its corrections would reach the final
    classification before they are approved (#345)."""
    return (
        f"❌ Cannot complete season — round {held['round_number']} of "
        f"**{held['division_name']}** is being amended in <#{held['channel_id']}>. "
        "Finish or cancel it first: completing posts every division's final "
        "classification, which would carry its corrections before they are approved."
    )


def view_of(ctx: OutcomeContext, name: str) -> StepView | None:
    """The last job named *name*: a save tried again is planned afresh, and the last stands."""
    return next((view for view in reversed(ctx.steps) if view.name == name), None)


def discarded(view: StepView | None) -> bool:
    """Whether a league admin discarded the job."""
    return view is not None and "discarded" in (view.result or {})


def shared_not_done(ctx: OutcomeContext) -> list[str]:
    """What the jobs every end of a season shares left undone, one line for each job a league
    admin discarded, in the order of the jobs, the drivers whose roles were not taken back
    gathered into one line.

    A signup channel whose notice was discarded but which was closed is named as closed without
    its notice; one that was not closed is named as not closed alone.
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
        if not discarded(view):
            continue
        each = view.payload
        if view.name == REVOKE_ROLES:
            if roles_at is None:
                roles_at = len(lines)
                lines.append("")
            roles.append(f"<@{each['user_id']}>")
        elif view.name == CLOSE_WINDOW:
            lines.append(WINDOW_KEPT)
        elif view.name == FLUSH_FORECASTS:
            lines.append(FORECASTS_KEPT)
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


def _classification_not_done(ctx: OutcomeContext) -> list[str]:
    lines: list[str] = []
    for view in ctx.steps:
        if not discarded(view) or view.name not in (FINAL_STANDINGS, FINAL_SHEET):
            continue
        name = view.payload["division_name"]
        if view.name == FINAL_SHEET:
            lines.append(
                f"**{name}** — its final classification (attendance sheet) could not be "
                "posted. No command posts it again."
            )
            continue
        lines.append(
            f"**{name}** — its final classification could not be posted. No command posts it "
            "again."
        )
        stranded = (view.result or {}).get("new")
        if stranded:
            ids = ", ".join(str(each) for each in stranded)
            lines.append(
                f"**{name}** — a part of its final classification was posted before the job "
                f"stopped and stands in <#{(view.result or {}).get('channel_id')}> (message "
                f"{ids}): delete it by hand."
            )
    return lines


def _not_done(ctx: OutcomeContext) -> list[str]:
    """What a league admin's Discards left undone, in the order of the jobs."""
    # The jobs run the classification first, then the shared ones, then the backup's deletion.
    lines = [*_classification_not_done(ctx), *shared_not_done(ctx)]
    if discarded(view_of(ctx, DISCARD_BACKUP)):
        lines.append(BACKUP_KEPT)
    return lines


def season_complete_change(
    *,
    modules: "ModuleService",
    seasons: "SeasonService",
    scheduler: "SchedulerService",
    placement: "PlacementService",
    hooks: SeasonEndHooks,
) -> ChangeType:
    """The change that completes the confirmed season; see the module.

    The builder hands in *modules* (which of results and attendance are on), the *seasons*
    service (the season the check reads), the *scheduler* (whose season-end job `settle` removes),
    the *placement* service (which takes back roles) and the season end's *hooks* (what other
    modules and the cog do for it).
    """

    async def check(ctx: CheckContext) -> Verdict:
        season = await seasons.get_confirmed_season()
        if season is None or season.id != int(ctx.payload["season_id"]):
            return Verdict.refuse(NO_SEASON)

        # **A second completion, asked while the first is in hand** (owner, 2026-10-09): refused
        # at once, naming the job. Only as it is asked: as the change runs, it would find itself.
        if ctx.change_id is None:
            hand = await season_end_in_hand(ctx.db_path, season.id)
            if hand is not None:
                return Verdict.refuse(
                    season_end_refusal(SEASON_COMPLETE, hand, season.season_number)
                )

        # **Not while a round is being amended** (#345, decided 2026-09-21): completing posts
        # each division's final classification from the database, which holds an open
        # amendment's corrections before they are approved.
        held = await hooks.amendment_open(ctx.db_path, season.id)
        if held is not None:
            return Verdict.refuse(amended(held))

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
                then=(PlannedStep(WIND_DOWN_STEP), PlannedStep(TURN_DOWN_STEP)),
            )
        if stage is not SeasonStage.PENDING_COMPLETION:
            return StepResult(result={"refused": NO_SEASON})

        number = int(season["season_number"])
        planned: list[PlannedStep] = []
        for each in await _last_rounds_with_results(db, season_id):
            planned.append(PlannedStep(FINAL_STANDINGS, each))
            planned.append(PlannedStep(FINAL_SHEET, each))
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
        planned.append(PlannedStep(CLOSE_WINDOW, {"cause": SEASON_END_CAUSE}))
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
        await write_driver_history_entries_on(db, season_id, number)
        driver_pass = await run_driver_pass_on(db)
        if test_mode:
            await switch_test_mode_off_on(db)
        # The archive is the last thing written (Constitution, the Season Archive).
        await complete_season_on(db, season_id)

        planned = list(
            driver_jobs(
                driver_pass.drivers, driver_pass.driver_role_id,
                notice=SEASON_ENDED_NOTICE, reason=SEASON_ENDED_REASON,
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

    async def final_standings(ctx: StepContext) -> StepResult:
        guild = await require_guild(ctx.bot)
        each = ctx.step_payload
        await season_classification_service.post_final_standings(
            ctx.bot, guild, ctx.db_path, int(each["division_id"]), int(each["round_id"]),
            as_text=ctx.tries > 0, kept=ctx.kept,
        )
        return StepResult()

    async def standings_due(_ctx: StepContext) -> bool:
        return await modules.is_results_enabled()

    async def final_sheet(ctx: StepContext) -> StepResult:
        guild = await require_guild(ctx.bot)
        each = ctx.step_payload
        await season_classification_service.post_final_sheet(
            ctx.bot, guild, ctx.db_path, int(each["division_id"]), int(each["round_id"]),
            as_text=ctx.tries > 0,
        )
        return StepResult()

    async def sheet_due(_ctx: StepContext) -> bool:
        return await modules.is_attendance_enabled()

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
        refused = _refused(ctx)
        if refused is not None:
            return StepResult(
                result={"closed": True},
                lines=(refusal_line(ctx.named, _COMMAND, reply_reason(refused)),),
            )
        if not _completed(ctx):
            return StepResult(result={"closed": True})
        left = _not_done(ctx)
        number = ctx.payload["season_number"]
        line = (
            f"{ctx.named} | /season complete | {'Incomplete' if left else 'Success'}\n"
            f"  season: Season #{number}" + "".join(f"\n  not done: {each}" for each in left)
        )
        return StepResult(result={"closed": True}, lines=(line,))

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
        return COMPLETED + approval_checks.not_done_section(_not_done(ctx))

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

    async def describe_standings(ctx: StepContext) -> str:
        return f"posting the final standings of **{ctx.step_payload['division_name']}**"

    async def describe_sheet(ctx: StepContext) -> str:
        return f"posting the final attendance sheet of **{ctx.step_payload['division_name']}**"

    async def describe_close(_ctx: StepContext) -> str:
        return "recording the completion of the season"

    winding = wind_down_steps(hooks)
    turn_down = winding[TURN_DOWN_STEP]

    async def turn_down_then_settle(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The wind-down's save, which settles the season again before the jobs for the drivers it
        turned down: they follow the second `settle`, which plans the final classifications and
        the rest ahead of them."""
        result = await turn_down.run(db, ctx)
        return replace(result, then=(PlannedStep(SETTLE, {"again": True}), *result.then))

    steps: dict[str, Step] = {
        SETTLE: Step(SETTLE, StepKind.SAVE, settle, describe=describe_settle),
        FINAL_STANDINGS: Step(
            FINAL_STANDINGS, StepKind.ACT, final_standings, still_due=standings_due,
            describe=describe_standings,
        ),
        FINAL_SHEET: Step(
            FINAL_SHEET, StepKind.ACT, final_sheet, still_due=sheet_due,
            describe=describe_sheet,
        ),
        END: Step(END, StepKind.SAVE, end, describe=describe_end),
        CLOSE: Step(CLOSE, StepKind.SAVE, close, describe=describe_close),
        **winding,
        TURN_DOWN_STEP: replace(turn_down, run=turn_down_then_settle),
        **season_end_steps(placement, hooks),
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
    placement: "PlacementService",
    hooks: SeasonEndHooks,
) -> ChangeType:
    """The change that aborts the season being set up; see the module.

    The payload is ``{"season_id"}``: a season set up has no number until its placements are
    confirmed. The builder hands in the *seasons* service (the season the check reads), the
    *placement* service (which takes back roles) and the season end's *hooks*.
    """

    async def check(ctx: CheckContext) -> Verdict:
        season = await seasons.get_setup_or_active_season()
        if season is None or season.id != int(ctx.payload["season_id"]):
            return Verdict.refuse(ABORT_ONLY_BEFORE)

        # **Another end of the season in hand** (owner, 2026-10-09, answers A and B): refused at
        # once, naming the job, ahead of the stage gate, whatever stage the season is in: a second
        # abort, or a cancellation or completion of a season past its placements. Only as it is
        # asked: as the change runs, it would find itself.
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
        driver_pass = await run_driver_pass_on(db)
        if test_mode:
            await switch_test_mode_off_on(db)
        # The season's deletion is the last thing written. The saved test-mode state is kept: a
        # season aborted is one abandoned.
        await SeasonService.delete_season(db, season_id)

        planned = list(
            driver_jobs(
                driver_pass.drivers, driver_pass.driver_role_id,
                notice=SEASON_ENDED_NOTICE, reason=SEASON_ENDED_REASON,
            )
        )
        if driver_pass.accounts:
            planned.append(PlannedStep(DISCARD_PORTRAITS, {"accounts": driver_pass.accounts}))
        planned.append(PlannedStep(FORGET_SETUP))
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

    def _not_done_abort(ctx: OutcomeContext) -> list[str]:
        lines = shared_not_done(ctx)
        if discarded(view_of(ctx, FORGET_SETUP)):
            lines.append(NOT_FORGOTTEN)
        return lines

    async def close(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        refused = _refused(ctx)
        if refused is not None:
            return StepResult(
                result={"closed": True},
                lines=(refusal_line(ctx.named, _ABORT_COMMAND, reply_reason(refused)),),
            )
        if not _aborted(ctx):
            return StepResult(result={"closed": True})
        counts = _end_result(ctx)
        line = (
            f"{ctx.named} | /season abort | Success\n"
            f"  drivers returned to Not Signed Up: {counts.get('drivers_returned', 0)}\n"
            f"  drivers deleted: {counts.get('drivers_deleted', 0)}"
            + "".join(f"\n  not done: {each}" for each in _not_done_abort(ctx))
        )
        return StepResult(result={"closed": True}, lines=(line,))

    def outcome(ctx: OutcomeContext) -> str:
        refused = _refused(ctx)
        if refused is not None:
            return refused
        if not _aborted(ctx):
            return ABORT_END_DISCARDED
        return ABORTED + approval_checks.not_done_section(_not_done_abort(ctx))

    async def describe_window_close(_ctx: StepContext) -> str:
        return "closing the signup window as the season being set up is aborted"

    async def describe_end(_ctx: StepContext) -> str:
        return "aborting the season being set up"

    async def describe_close(_ctx: StepContext) -> str:
        return "recording the abort of the season being set up"

    async def test_mode_still_on(ctx: StepContext) -> bool:
        """The forecasts are cleared only where test mode posted them: the job is planned with the
        abort, before the save that switches test mode off, and reads the flag as it comes up."""
        async with get_connection(ctx.db_path) as db:
            return await test_mode_on(db)

    shared = season_end_steps(placement, hooks)
    steps: dict[str, Step] = {
        **shared,
        FLUSH_FORECASTS: replace(shared[FLUSH_FORECASTS], still_due=test_mode_still_on),
        CLOSE_WINDOW: replace(shared[CLOSE_WINDOW], describe=describe_window_close),
        END: Step(END, StepKind.SAVE, end, describe=describe_end),
        CLOSE: Step(CLOSE, StepKind.SAVE, close, describe=describe_close),
    }
    return ChangeType(
        kind=SEASON_ABORT,
        opening=(
            PlannedStep(CLOSE_WINDOW, {"cause": SEASON_END_CAUSE}),
            PlannedStep(FLUSH_FORECASTS),
            PlannedStep(END),
            PlannedStep(CLOSE),
        ),
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


async def _last_rounds_with_results(
    db: aiosqlite.Connection, season_id: int
) -> list[dict[str, Any]]:
    """Each division of *season_id*, by id, with the last of its rounds that has results.

    The final classification is drawn against that round, whose classification it *is*. A
    division that ran no round has none to publish and is left out, and so is a division
    cancelled, which gets no final classification (decided 2026-10-09).
    """
    cursor = await db.execute(
        """
        SELECT d.id AS division_id, d.name AS division_name,
               (SELECT r.id FROM rounds r
                 JOIN session_results sr ON sr.round_id = r.id
                WHERE r.division_id = d.id AND sr.status = 'ACTIVE'
                ORDER BY r.round_number DESC LIMIT 1) AS round_id
        FROM divisions d
        WHERE d.season_id = ? AND d.status != 'CANCELLED'
        ORDER BY d.id
        """,
        (season_id,),
    )
    return [
        {
            "division_id": int(row["division_id"]),
            "division_name": str(row["division_name"]),
            "round_id": int(row["round_id"]),
        }
        for row in await cursor.fetchall()
        if row["round_id"] is not None
    ]
