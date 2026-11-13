"""A round amended, as the check-in answers it, and the steps test mode's advance asks.

An amendment moves a round and so what its check-in owes, and attendance takes part in it through
three of core's events, subscribed in wiring:

- `round_amended_on` reopens the round's check-in in the amendment's save where the amendment
  reopened it, handing back whether a call stood when the save was made and whether the check-in
  was reopened, kept with the save for the arming to read;
- `amendment_posts`, when the round is armed again, plans the posts the round now owes of
  attendance's, in order: the standing call taken down and posted again, or a call posted where
  none stands, and the deadline run where it passed;
- `amendment_clean_ups` hands core the clean-ups due.

`amendment_steps` is what an amendment asks of the check-in: the steps of the call, the deadline
and the clean-up it plans, with the take-down of the call it replaces. `give_up_text_for` words
the line giving up a call for a round whose deadline has passed.

`advance_steps` is the step `/test-mode advance` runs for each of attendance's kinds of round job,
each asking the queue as the bot, early, with `advanced` in its payload so that no clock judges it.

The jobs themselves are the call's (`check_in_call`), the deadline's (`check_in_deadline`) and the
clean-up's (`check_in_clean_up`); what they share is in `check_in_changes`.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.attendance.models.attendance import (
    CHECK_IN_CALL,
    CHECK_IN_CLEAN_UP,
    CHECK_IN_DEADLINE,
    CHECK_IN_LAST_NOTICE,
)
from leaguebot.attendance.services import rsvp_service
from leaguebot.attendance.services.attendance_config_service import AttendanceService
from leaguebot.attendance.services.check_in_call import (
    check_in_steps,
    close_check_in_on,
    given_up_text,
)
from leaguebot.attendance.services.check_in_changes import (
    ANNOUNCE,
    CLEAN_UP_CHECK_IN,
    CLOSE_WITHOUT_CALL,
    DELETE_MESSAGE,
    DISTRIBUTE,
    LOCK_CALL,
    MARK_CLEARED,
    PLAN_CLEAN_UP,
    POST_CALL,
    TAKE_DOWN_CALL,
    CheckInViewFactory,
    call_row,
    check_in_clean_up_steps,
    standing_call_row,
    take_down_steps,
)
from leaguebot.attendance.services.check_in_deadline import check_in_deadline_steps
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import ChangeOrigin, PlannedStep, StepKind, StepResult
from leaguebot.core.models.round import ROUND_CANCELLABLE, AttendanceWindows, Round
from leaguebot.core.services.queue.change_queue import ChangeQueue
from leaguebot.core.services.queue.change_types import Step, StepContext
from leaguebot.core.services.rounds.round_moments import clean_up_moment, round_event_moments
from leaguebot.core.services.rounds.round_steps import judged_windows, named_now
from leaguebot.core.services.seasons import season_service
from leaguebot.core.services.test_mode.test_mode_service import (
    AdvanceOutcome,
    AdvanceStep,
    AdvanceStepFailed,
    PhaseEntry,
)

if TYPE_CHECKING:
    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.core.services.posting.output_router import OutputRouter

log = logging.getLogger(__name__)


async def round_amended_on(
    db: aiosqlite.Connection, round_id: int, *, reopened: bool, **_context: Any
) -> dict[str, Any]:
    """Attendance's part of the amendment's save: reopen the round's check-in on *db* where the
    amendment *reopened* it, committing nothing. Subscribed to core's `round_amended`.

    Hands back what the amendment's arming reads of it, kept with the save: whether a call stood
    when the save was made, read before the check-in was reopened, and whether it was reopened.
    """
    cursor = await db.execute(
        "SELECT 1 FROM rsvp_embed_messages WHERE round_id = ?", (round_id,)
    )
    called = await cursor.fetchone() is not None
    if reopened:
        await rsvp_service.reopen_check_in_on(db, round_id)
    return {"called": called, "reopened": reopened}


def _call_fell_due(
    rnd: Round, attendance: AttendanceWindows | None, now: Callable[[], datetime]
) -> bool:
    """Whether the round's check-in call has fallen due at *now*: its moment has passed."""
    if attendance is None:
        return False
    moments = round_event_moments(rnd.scheduled_at, weather=None, attendance=attendance)
    return moments["rsvp_notice"] <= now()


def _deadline_passed(
    rnd: Round, attendance: AttendanceWindows, now: Callable[[], datetime]
) -> bool:
    """Whether the round's check-in deadline has passed at *now*."""
    moments = round_event_moments(rnd.scheduled_at, weather=None, attendance=attendance)
    return moments["rsvp_deadline"] <= now()


async def amendment_posts(
    db: aiosqlite.Connection,
    round_id: int,
    *,
    rnd: Round,
    kept: Mapping[str, Any],
    attendance: AttendanceWindows | None,
    now: Callable[[], datetime],
    **_context: Any,
) -> tuple[PlannedStep, ...]:
    """The posts an amendment owes of attendance's, in order, planned as the round is armed
    again, reading the round's call on *db*: the check-in call taken down and posted again, or
    posted where none stands, and the deadline run where it passed. Subscribed to core's
    `round_armed`.

    A call that stood at the save (*kept*, `round_amended_on`) is taken down and its post planned
    with it, the post judging when it runs whether the call is due then: the take-down can stop on
    Discord and be retried after the new call's moment, which the timer armed meanwhile has found
    taken by the old call. Otherwise the call is planned only where none stands and the check-in
    is not over and it has fallen due as the round is armed, saved or not; one still to come is
    the timer's. A standing call left in place whose deadline has passed with no distribution
    recorded has its deadline run, as the start-up recovery runs it.
    """
    found = await _amendment_standing(db, round_id)
    taking_down = bool(kept.get("reopened") and kept.get("called"))
    call_left = bool(found["called"]) and not taking_down
    planned: list[PlannedStep] = []
    if taking_down:
        planned += [
            PlannedStep(TAKE_DOWN_CALL, {
                "division_id": rnd.division_id,
                "division_name": found["division_name"],
                "season_id": found["season_id"],
                "round_id": round_id,
                "round_number": rnd.round_number,
            }),
            PlannedStep(POST_CALL, {"repost": True}),
        ]
    elif (not found["called"] and not found["checkin_cleared"]
          and _call_fell_due(rnd, attendance, now)):
        planned.append(PlannedStep(POST_CALL, {"repost": False}))
    if (attendance is not None and call_left and found["undistributed"]
            and _deadline_passed(rnd, attendance, now)):
        planned.append(PlannedStep(DISTRIBUTE))
    return tuple(planned)


async def amendment_clean_ups(
    db: aiosqlite.Connection,
    round_id: int,
    *,
    rnd: Round,
    kept: Mapping[str, Any],
    now: Callable[[], datetime],
    **_context: Any,
) -> tuple[PlannedStep, ...]:
    """The clean-up of the round's check-in a day after the round, planned where that day has
    come and a call stands that the amendment did not take down. Read on *db*. Subscribed to
    core's `round_clean_ups_due`."""
    if clean_up_moment(rnd.scheduled_at) > now():
        return ()
    found = await _amendment_standing(db, round_id)
    if found["called"] and not (kept.get("reopened") and kept.get("called")):
        return (PlannedStep(CLEAN_UP_CHECK_IN),)
    return ()


async def _amendment_standing(db: aiosqlite.Connection, round_id: int) -> dict[str, Any]:
    """What an amendment's arming reads of a round's check-in: whether a call stands, whether
    one stands that no deadline has run (no distribution recorded), whether the check-in is over
    (``checkin_cleared``), and the division's name and season."""
    row = await call_row(db, round_id)
    if row is None:
        raise LookupError(f"round {round_id} is no longer there")
    cursor = await db.execute(
        "SELECT EXISTS (SELECT 1 FROM rsvp_embed_messages WHERE round_id = ?) AS called, "
        "EXISTS (SELECT 1 FROM rsvp_embed_messages WHERE round_id = ? "
        "AND deadline_run_at IS NULL AND distribution_msg_id IS NULL) AS undistributed",
        (round_id, round_id),
    )
    found = await cursor.fetchone()
    return {
        "checkin_cleared": row["checkin_cleared"],
        "division_name": row["division_name"],
        "season_id": row["season_id"],
        "called": bool(found["called"]) if found else False,
        "undistributed": bool(found["undistributed"]) if found else False,
    }


async def give_up_text_for(db_path: str, round_id: int) -> str | None:
    """The line giving up *round_id*'s call, or None where there is nothing to give up: no call
    is owed a round that is gone, whose results are in or whose check-in is over, and a call
    standing is the round's live one."""
    async with get_connection(db_path) as db:
        row = await call_row(db, round_id)
        standing = await standing_call_row(db, round_id)
    if row is None or standing is not None:
        return None
    if row["checkin_cleared"] or row["round_status"] not in ROUND_CANCELLABLE:
        log.info("check-in call give-up: round %d's results are in or its check-in is over — "
                 "nothing given up", round_id)
        return None
    return given_up_text(row)


def amendment_steps(
    service: AttendanceService,
    router: OutputRouter,
    *,
    now: Callable[[], datetime],
    view_of: CheckInViewFactory,
    seasons: season_service.SeasonService,
    modules: ModuleService,
    deletes: Callable[[int, int], Awaitable[bool]],
) -> dict[str, Step]:
    """The jobs attendance defines for an amendment, by name: taking the round's call down
    (`take_down_steps`, named for the number the round bears now), posting it again, the three of
    a check-in deadline (placing the reserves, taking the buttons off the call, announcing),
    closing a check-in that has no call, and the four of taking a check-in down (its plan under
    the amendment's name `CLEAN_UP_CHECK_IN`, each deletion, the mark that it is over). *service* is
    attendance's, *router* writes the log channel's lines, *now* the queue's clock, *seasons* reads the round, *modules* says whether
    attendance is on, *deletes* is `check_in_deletes` and *view_of* builds the buttons of a call
    posted again. Subscribed to core's `amendment_steps`.
    """
    taken_down = take_down_steps(seasons=seasons, modules=modules, deletes=deletes)
    call = check_in_steps(service, router, now=now, view_of=view_of)
    deadline = check_in_deadline_steps(service, now=now)
    clean_up = check_in_clean_up_steps()

    def named(text: str) -> Callable[[StepContext], Awaitable[str]]:
        return named_now(seasons, text)

    def view(ctx: StepContext, name: str) -> Any:
        return next((each for each in reversed(ctx.steps) if each.name == name), None)

    def reposting(ctx: StepContext) -> bool:
        """Whether the call is posted again in place of one the amendment took down (its step
        payload, ``{"repost": True}``), rather than posted for the first time."""
        return bool(ctx.step_payload.get("repost"))

    def replacing(ctx: StepContext) -> bool:
        """Whether the call to post again replaces one still standing: the old call, whose
        take-down a league admin discarded. Otherwise any call standing when the post runs was
        posted after the take-down, or after the arming, by its timer, a restart or
        `/attendance post-check-in`, and is the round's live call."""
        taking_down = view(ctx, TAKE_DOWN_CALL)
        return reposting(ctx) and taking_down is not None and "discarded" in (
            taking_down.result or {}
        )

    def window(ctx: StepContext) -> AttendanceWindows | None:
        """The window the amendment's judging kept, which the call is judged against."""
        return judged_windows(ctx)[0]

    async def call_state(db_path: str, round_id: int) -> tuple[bool, bool]:
        """Whether a call stands for the round, and whether its check-in is over
        (``checkin_cleared``), read when the job runs."""
        async with get_connection(db_path) as db:
            row = await call_row(db, round_id)
            standing = await standing_call_row(db, round_id)
        if row is None:
            return False, False
        return standing is not None, bool(row["checkin_cleared"])

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
        if rnd is None or not _call_fell_due(rnd, window(ctx), now):
            return False
        standing, cleared = await call_state(ctx.db_path, rnd.id)
        if cleared:
            return False
        return replacing(ctx) or not standing

    async def post_call(ctx: StepContext) -> StepResult:
        """Post the call, carrying every answer already given. Discord refusing it stops the queue
        (owner, 2026-10-10, decision 3), attendance raising with what is left for the round, which
        the stop notice says (owner, 2026-10-11) and a Discard reads in the amendment's outcome.

        The call is posted by the check-in call's own job, which posts nothing it cannot record:
        the job's record writes the call in the save that marks the job done. A call posted again
        in place of one the amendment took down (`reposting`) carries the answers of the drivers
        still of the division, and where a discarded take-down left the old call standing
        (`replacing`) the new call's record replaces the old one's. The old call's messages are
        not deleted again: the discarded take-down names those left, for deletion by hand.

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
        attendance = window(ctx)
        if attendance is not None and _deadline_passed(rnd, attendance, now):
            standing, cleared = await call_state(ctx.db_path, rnd.id)
            if standing or cleared:
                return StepResult(result={"left": True})
            text = await give_up_text_for(ctx.db_path, rnd.id)
            if text is None:
                # Its results came in, or its check-in closed, as the give-up reads it.
                return StepResult(result={"left": True})
            return StepResult(result={"given_up": True}, lines=(text,))
        carried = dataclasses.replace(
            ctx, step_payload={**ctx.step_payload, "replacing": replacing(ctx)}
        )
        return await call[POST_CALL].run(carried)

    async def record_call(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
        """The call's record, or the give-up's close, in the save that marks the job done; a call
        left as it stood is recorded as nothing."""
        if (result.result or {}).get("given_up"):
            await close_check_in_on(db, int(ctx.payload["round_id"]))
            return
        if not (result.result or {}).get("new"):
            return  # left as it stood: nothing posted, nothing to record
        record = call[POST_CALL].record
        if record is not None:
            await record(db, ctx, result)

    return {
        TAKE_DOWN_CALL: dataclasses.replace(
            taken_down[TAKE_DOWN_CALL],
            describe=named("taking down the check-in call of round {number} in **{division}**"),
        ),
        POST_CALL: Step(
            POST_CALL, StepKind.ACT, post_call, still_due=call_still_due, record=record_call,
            describe=named("posting the check-in call of round {number} in **{division}** again"),
        ),
        # The jobs of a deadline, named for the round the amendment stands for.
        DISTRIBUTE: dataclasses.replace(
            deadline[DISTRIBUTE],
            describe=named("placing the reserves of round {number} in **{division}**"),
        ),
        CLOSE_WITHOUT_CALL: dataclasses.replace(
            deadline[CLOSE_WITHOUT_CALL],
            describe=named(
                "closing the check-in of round {number} in **{division}**, which has no call"
            ),
        ),
        LOCK_CALL: deadline[LOCK_CALL],
        ANNOUNCE: dataclasses.replace(
            deadline[ANNOUNCE],
            describe=named("posting the reserve announcement of round {number} in **{division}**"),
        ),
        # The jobs of a clean-up: its plan under the amendment's name for it, the deletions and
        # the mark that the check-in is over.
        CLEAN_UP_CHECK_IN: dataclasses.replace(
            clean_up[PLAN_CLEAN_UP], name=CLEAN_UP_CHECK_IN,
            describe=named(
                "finding what is left of the check-in of round {number} in **{division}**"
            ),
        ),
        DELETE_MESSAGE: clean_up[DELETE_MESSAGE],
        MARK_CLEARED: clean_up[MARK_CLEARED],
    }


def advance_steps(queue: ChangeQueue, db_path: str) -> dict[str, AdvanceStep]:
    """The step `/test-mode advance` runs for each of attendance's kinds of round job: the check-in
    call, last notice, deadline and clean-up, each asking *queue* as the bot, early.

    **Each is asked as advance's** (`advanced`): judged by no clock, so that under test mode the
    step that ends the check-in is the one advance fires, a past-dated round's call being posted
    however late it is. The payload carries the entry's division and round numbers and no
    division id, as the timers' does not carry the flag; the call's also says it replaces nothing
    and is not late. A fault is named as the command words the step ("the RSVP notice"). Each
    step's words, and the change it asks, are what advance has always said and asked.
    """

    def step_for(
        kind: str, *, step: str, what: str, emoji: str, name: str, job: str, taken_down: str | None
    ) -> AdvanceStep:
        async def run(entry: PhaseEntry) -> AdvanceOutcome:
            division, number = entry["division_name"], entry["round_number"]
            payload: dict[str, Any] = {
                "round_id": entry["round_id"], "division_name": division,
                "round_number": number, "advanced": True,
            }
            if kind == CHECK_IN_CALL:
                payload |= {"replaces": None, "late": False}
            try:
                change = await queue.ask(
                    kind, payload, what=f"{what} of round {number} in **{division}**",
                    origin=ChangeOrigin.BOT,
                )
            except Exception as exc:  # noqa: BLE001 — named by the command, which reports it
                raise AdvanceStepFailed(step) from exc
            if change is None:
                return AdvanceOutcome.not_due(
                    f"{emoji} The **{name}** for **{division}** — **Round {number}** was not due."
                )
            if taken_down is not None:
                reply = (
                    f"{emoji} The **{name}** for **{division}** — **Round {number}** is on the "
                    f"change queue: it {taken_down} as soon as the queue reaches it."
                )
            else:
                reply = (
                    f"{emoji} The **{name}** for **{division}** — **Round {number}** "
                    f"(**{entry['track_name']}**) is on the change queue: it goes to the RSVP "
                    f"channel as soon as the queue reaches it."
                )
            return AdvanceOutcome.asked(
                reply, f"  phase: {job}\n  division: {division}\n  round: {number}"
            )

        return run

    return {
        "rsvp_notice": step_for(
            CHECK_IN_CALL, step="the RSVP notice", what="the RSVP notice", emoji="📨",
            name="RSVP notice", job="rsvp_notice", taken_down=None,
        ),
        "rsvp_last_notice": step_for(
            CHECK_IN_LAST_NOTICE, step="the RSVP last-notice", what="the RSVP last-notice",
            emoji="⏰", name="RSVP last-notice", job="rsvp_last_notice", taken_down=None,
        ),
        "rsvp_deadline": step_for(
            CHECK_IN_DEADLINE, step="the RSVP deadline", what="the RSVP deadline", emoji="🏁",
            name="RSVP deadline", job="rsvp_deadline", taken_down=None,
        ),
        "rsvp_cleanup": step_for(
            CHECK_IN_CLEAN_UP, step="the check-in cleanup",
            what="taking down the check-in a day after the round", emoji="🧹",
            name="check-in cleanup", job="rsvp_cleanup",
            taken_down="takes down the check-in call, last notice and reserve announcement",
        ),
    }
