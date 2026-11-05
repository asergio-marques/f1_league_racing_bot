"""Moving a season through its lifecycle as the events that drive it happen (issue #220).

``SeasonService.set_stage`` enforces which transitions are legal. This module decides which
transition an event calls for — a signup window opening or closing, and later placements
being confirmed and a season ending — so that every path reaching the event (a command, a
timer, a restart) moves the season the same way.

It reads the database directly rather than through a bot, so the signup window's forced
close, which runs from a command, a scheduled job and the startup sweep alike, can call it
with nothing but a path.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    GuildUnavailable,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.season import ONGOING_STAGES, InvalidStageTransition, SeasonStage, status_of_stage
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
    StepView,
)
from leaguebot.core.services.season_service import commit_placements_on
from leaguebot.core.utils.hooks import AwaitedHook, Hook
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import league_guild

if TYPE_CHECKING:
    from leaguebot.core.services.placement_service import PlacementService

log = logging.getLogger(__name__)

#: A signup is unsettled while its driver stands in one of these states: approved and not
#: yet placed, or still being judged. Closing a mid-season window with any of them standing
#: leaves placements to make.
UNSETTLED_STATES: tuple[str, ...] = (
    "UNASSIGNED",
    "PENDING_ADMIN_APPROVAL",
    "AWAITING_CORRECTION_PARAMETER",
    "PENDING_DRIVER_CORRECTION",
)

#: The stages a signup window may be opened from, and the stage opening it moves to.
WINDOW_OPENS_FROM: dict[SeasonStage, SeasonStage] = {
    SeasonStage.WAITING: SeasonStage.SIGNUPS,
    SeasonStage.ONGOING: SeasonStage.ONGOING_SIGNUPS,
}


def uncommitted_seat_excluded(seat_alias: str = "ts") -> str:
    """SQL predicate: the seat *seat_alias* is not held by an uncommitted driver mid-season.

    A driver placed in Ongoing, placements stands outside the championship until placements
    are confirmed (issue #220): no check-in, no attendance, no results, no standings, no
    lineup. Every reader of the championship that walks the seats adds this predicate.

    Written as the absence of an *uncommitted* placement in a confirmed season, rather than
    the presence of a committed one, so a seat whose occupant holds no placement row reads as
    it always did. In a season still in Placements every placement is uncommitted and nothing
    is being raced, so the predicate leaves those seats alone.
    """
    return (
        "NOT EXISTS (SELECT 1 FROM driver_season_assignments uc "
        "JOIN seasons ucs ON ucs.id = uc.season_id "
        f"WHERE uc.team_seat_id = {seat_alias}.id "
        f"AND uc.driver_profile_id = {seat_alias}.driver_profile_id "
        "AND uc.committed = 0 AND ucs.status = 'ACTIVE')"
    )


async def live_season_stage(db_path: str) -> tuple[int, SeasonStage] | None:
    """The server's active season and its stage, or None where it holds none."""
    async with get_connection(db_path) as db:
        return await live_season_stage_on(db)


async def live_season_stage_on(db: aiosqlite.Connection) -> tuple[int, SeasonStage] | None:
    """`live_season_stage` read on the caller's connection."""
    cursor = await db.execute(
        "SELECT id, stage FROM seasons "
        "WHERE status IN ('SETUP', 'ACTIVE') "
        "ORDER BY id DESC LIMIT 1",
    )
    row = await cursor.fetchone()
    if row is None or row["stage"] is None:
        return None
    return int(row["id"]), SeasonStage(row["stage"])


async def count_unsettled_signups_on(db: aiosqlite.Connection) -> int:
    """How many drivers hold a signup not yet placed or turned down, read on *db*."""
    placeholders = ",".join("?" for _ in UNSETTLED_STATES)
    cursor = await db.execute(
        f"SELECT COUNT(*) AS n FROM driver_profiles "
        f"WHERE current_state IN ({placeholders})",
        (*UNSETTLED_STATES,),
    )
    row = await cursor.fetchone()
    return int(row["n"]) if row is not None else 0


async def _move_on(
    db: aiosqlite.Connection, season_id: int, current: SeasonStage, target: SeasonStage
) -> None:
    """Write the transition on *db*, conditioned on the stage that was read; commits nothing."""
    cursor = await db.execute(
        "UPDATE seasons SET status = ?, stage = ? WHERE id = ? AND stage = ?",
        (status_of_stage(target).value, target.value, season_id, current.value),
    )
    if cursor.rowcount == 0:
        raise InvalidStageTransition(
            f"season {season_id} left {current.value} before it could move to {target.value}"
        )


async def advance_on_window_open_on(db: aiosqlite.Connection) -> SeasonStage | None:
    """Move the active season on for a signup window just opened, on the caller's connection.

    Commits nothing, so that the season's move is saved with the window's opening. Waiting
    becomes Signups; Ongoing becomes Ongoing, signups open. Returns the new stage, or None
    where the season stood in neither and nothing was moved."""
    found = await live_season_stage_on(db)
    if found is None:
        return None
    season_id, stage = found
    target = WINDOW_OPENS_FROM.get(stage)
    if target is None:
        return None
    await _move_on(db, season_id, stage, target)
    return target


async def advance_on_window_close_on(db: aiosqlite.Connection) -> SeasonStage | None:
    """Move the active season on for a signup window just closed, on the caller's connection.

    Commits nothing, so that the season's move is saved with the window's close and no window is
    ever recorded closed with its season left behind. Signups becomes Placements. Ongoing,
    signups open becomes Ongoing, placements where any signup remains unsettled, and Ongoing
    where none does. Returns the new stage, or None where the season stood in neither and
    nothing was moved: a window force-closed by disabling the module, for one, belongs to no
    stage.
    """
    found = await live_season_stage_on(db)
    if found is None:
        return None
    season_id, stage = found
    if stage is SeasonStage.SIGNUPS:
        target = SeasonStage.PLACEMENTS
    elif stage is SeasonStage.ONGOING_SIGNUPS:
        unsettled = await count_unsettled_signups_on(db)
        target = SeasonStage.ONGOING_PLACEMENTS if unsettled else SeasonStage.ONGOING
    else:
        return None
    await _move_on(db, season_id, stage, target)
    if target is SeasonStage.ONGOING:
        await advance_to_pending_completion_on(db, season_id)
    return target


async def _pending_placement_drivers_on(db: aiosqlite.Connection, season_id: int) -> list[dict]:
    """Every driver of *season_id* whose placement is still pending, as the rows are read.

    Pending are the unsettled signups — Unassigned, awaiting approval or mid-correction — and
    every driver Assigned only by a placement not yet committed.
    """
    placeholders = ",".join("?" for _ in UNSETTLED_STATES)
    cursor = await db.execute(
        f"SELECT id, discord_user_id, current_state, is_test_driver FROM driver_profiles "
        f"WHERE ("
        f"  current_state IN ({placeholders}) "
        f"  OR (current_state = 'ASSIGNED' AND id IN ("
        f"      SELECT driver_profile_id FROM driver_season_assignments "
        f"      WHERE season_id = ? AND committed = 0) "
        f"    AND id NOT IN ("
        f"      SELECT driver_profile_id FROM driver_season_assignments "
        f"      WHERE season_id = ? AND committed = 1))"
        f") ORDER BY id",
        (*UNSETTLED_STATES, season_id, season_id),
    )
    return [dict(r) for r in await cursor.fetchall()]


async def _turn_down_on(db: aiosqlite.Connection, season_id: int, drivers: list[dict]) -> None:
    """Write the turn-down of *drivers*' placements on *db*, committing nothing."""
    from leaguebot.core.models.driver_profile import DriverState
    from leaguebot.core.services.driver_service import write_transition

    await db.execute(
        "UPDATE team_seats SET driver_profile_id = NULL WHERE id IN ("
        "  SELECT team_seat_id FROM driver_season_assignments "
        "  WHERE season_id = ? AND committed = 0 AND team_seat_id IS NOT NULL)",
        (season_id,),
    )
    await db.execute(
        "DELETE FROM driver_season_assignments WHERE season_id = ? AND committed = 0",
        (season_id,),
    )
    for driver in drivers:
        await write_transition(
            db, driver["id"], DriverState(driver["current_state"]), DriverState.NOT_SIGNED_UP
        )
    if drivers:
        await db.execute(
            "INSERT INTO audit_entries "
            "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
            "VALUES (0, 'system', NULL, 'PENDING_PLACEMENTS_TURNED_DOWN', ?, ?, datetime('now'))",
            (
                json.dumps({d["id"]: d["current_state"] for d in drivers}, sort_keys=True),
                json.dumps({"state": "NOT_SIGNED_UP"}),
            ),
        )


async def turn_down_pending_placements_on(db: aiosqlite.Connection, season_id: int) -> list[dict]:
    """Turn down every placement of *season_id* still pending, as the reject command would, on *db*.

    The database side of the wind-down, committing nothing: it is part of the save that also
    moves the season on (#439). Each pending placement is discarded and its seat vacated, and
    each such driver returns to Not Signed Up through the transition table. A driver without the
    former-driver flag is thereby pending deletion. Gives each driver turned down as ``id``,
    ``user_id``, ``state`` (the one they were in) and ``is_test_driver``, for the jobs that follow
    the save: a signup in review has its channel told and closed, an approved driver loses the
    driver role.
    """
    drivers = await _pending_placement_drivers_on(db, season_id)
    await _turn_down_on(db, season_id, drivers)
    return [
        {
            "id": d["id"],
            "user_id": d["discord_user_id"],
            "state": d["current_state"],
            "is_test_driver": d["is_test_driver"],
        }
        for d in drivers
    ]


#: The jobs of one driver's Discord side of a season's end, as the stop notice, the tests and
#: ``StepView`` know them.
SIGNUP_NOTICE = "signup_notice"
CLOSE_SIGNUP = "close_signup"
TAKE_DRIVER_ROLE = "take_driver_role"
#: Why a season's end closes what a module holds open, as the hook is handed it: the season's
#: end (a completion, a cancellation), the abort of the season being set up, and the wind-down's
#: close as every division is done.
SEASON_END_CAUSE = "season end"
ABORT_CAUSE = "abort"
WIND_DOWN_CAUSE = "divisions done"


def close_jobs(
    season_end_closes: Hook, cause: str, season_number: int | None = None
) -> tuple[PlannedStep, ...]:
    """The jobs that close what each module holds open as a season ends, in the order the modules
    signed up to *season_end_closes*, given the *cause* and the season's number where the words of
    a job need it."""
    return tuple(
        step
        for jobs in season_end_closes.call(cause=cause, season_number=season_number)
        for step in jobs
    )


def kept_line(steps: Mapping[str, Step], view: StepView) -> str | None:
    """The line a job a league admin discarded leaves for doing by hand, in its own step's words
    (`Step.not_done`), or None where the step has none."""
    step = steps.get(view.name)
    if step is None or step.not_done is None or "discarded" not in (view.result or {}):
        return None
    return step.not_done(view)


async def require_guild(bot: LeagueBot) -> discord.Guild:
    """The league's server, or `GuildUnavailable` where it is not in the cache."""
    guild = await league_guild(bot)
    if guild is None:
        raise GuildUnavailable("the league's server is not in the cache")
    return guild


async def take_driver_role(
    guild: discord.Guild,
    driver: Mapping[str, Any],
    *,
    placement: PlacementService,
    driver_role_id: int,
    reason: str,
) -> dict:
    """Take the league's driver role back from one real driver (the `take_driver_role` job).

    A member who left the server, or a role gone from it, is passed over; Discord refusing
    raises, for the queue to stop on.
    """
    revoked = await placement.revoke_roles(
        guild, int(driver["user_id"]), driver_role_id, reason=reason
    )
    return {"revoked": revoked}


def driver_jobs(
    drivers: Sequence[Mapping[str, Any]],
    driver_role_id: int | None,
    *,
    notice: str,
    reason: str,
    driver_returned_jobs: Hook,
) -> tuple[PlannedStep, ...]:
    """The jobs of the Discord side of returning *drivers* to Not Signed Up, each driver's in order.

    *drivers* are mappings of ``user_id``, ``state`` (the one they were in) and
    ``is_test_driver``. Each driver's own jobs are the modules' (`driver_returned_jobs`, handed
    the driver and the *notice* a channel of theirs is told), in the order they signed up, then an
    approved real driver, Unassigned or Assigned, loses the driver role (`take_driver_role`)
    where the league has one. Nothing else needs Discord.
    """
    planned: list[PlannedStep] = []
    for driver in drivers:
        user_id = str(driver["user_id"])
        for jobs in driver_returned_jobs.call(driver, notice=notice):
            planned.extend(jobs)
        if (
            driver_role_id
            and not driver["is_test_driver"]
            and driver["state"] in ("UNASSIGNED", "ASSIGNED")
        ):
            planned.append(
                PlannedStep(
                    TAKE_DRIVER_ROLE,
                    {"user_id": user_id, "role_id": int(driver_role_id), "reason": reason},
                )
            )
    return tuple(planned)


def steps_of_the_modules(hook: Hook, **handed: Any) -> dict[str, Step]:
    """The step definitions the modules hand back to *hook* (called with *handed*), merged by name,
    so that core registers steps it does not name."""
    steps: dict[str, Step] = {}
    for defined in hook.call(**handed):
        steps.update(defined)
    return steps


#: How a driver the season's end could not tell for want of a signup channel is named.
NO_CHANNEL_LEFT = "{who} had no signup channel left to tell"


def close_signup_steps(
    placement: PlacementService,
    *,
    season_end_steps: Hook,
    line_prefix: str | None = None,
) -> dict[str, Step]:
    """The jobs of one driver's Discord side, shared by every change that ends signups: the
    driver role's taking back, and the steps the modules define for a season's end
    (`season_end_steps`, handed *line_prefix* for the closing line a change with none of its own
    writes).
    """

    async def take_role(ctx: StepContext) -> StepResult:
        guild = await require_guild(ctx.bot)
        result = await take_driver_role(
            guild, ctx.step_payload, placement=placement,
            driver_role_id=int(ctx.step_payload["role_id"]),
            reason=str(ctx.step_payload["reason"]),
        )
        return StepResult(result=result)

    async def describe_role(ctx: StepContext) -> str:
        return f"taking the driver role back from <@{ctx.step_payload['user_id']}>"

    steps: dict[str, Step] = {
        TAKE_DRIVER_ROLE: Step(TAKE_DRIVER_ROLE, StepKind.ACT, take_role, describe=describe_role),
        **steps_of_the_modules(season_end_steps, line_prefix=line_prefix),
    }
    return steps


#: Why no module may be disabled in Pending completion: the reply of every disable refused for it.
FROZEN_FOR_COMPLETION_REFUSAL = (
    "❌ No module can be disabled while the season is pending completion. "
    "Complete it with `/season complete` first."
)

#: Why no module may be enabled once a season's placements are confirmed: the reply of every
#: enable refused for it, at the ask (`module_cog.py`) and again where a switch's own check
#: reads the stage as its change starts.
PLACEMENTS_CONFIRMED_REFUSAL = (
    "❌ A module cannot be enabled once the season's placements have been confirmed. Enable it "
    "before then, or once the season has ended."
)

#: The kind of the change that winds a season down.
WIND_DOWN = "season.wind_down"
TURN_DOWN_STEP = "turn_down"

#: What a signup channel is told where the wind-down turns its signup down.
WIND_DOWN_NOTICE = (
    "🔒 Every division of this season is done, so its signups are closed. "
    "This channel will be automatically deleted in 24 hours."
)


async def wind_down_not_due_on(db: aiosqlite.Connection) -> str | None:
    """Why the wind-down would be dropped at its check now, or None where it would run: it runs
    only while the live season is in an ongoing stage with every division finished or cancelled.

    `wind_down_change`'s check, read on *db*, and how a close of the signup window tells a
    wind-down that will run from one asked just in case (`season_ending_on`): the bot asks one
    wherever a division may have been the season's last, and its check drops every one that was
    not.
    """
    found = await live_season_stage_on(db)
    if found is None or found[1] not in ONGOING_STAGES:
        return "the season is not in an ongoing stage"
    _, done = await _stage_and_whether_done_on(db, found[0])
    if not done:
        return "a division of the season is not done"
    return None


def wind_down_change(
    *,
    placement: PlacementService,
    season_end_closes: Hook,
    drivers_returned: AwaitedHook,
    driver_returned_jobs: Hook,
    season_end_steps: Hook,
) -> ChangeType:
    """The change that takes a season whose every division is done to Pending completion.

    Asked by the bot in the save of whatever finished the last division, so that the wind-down,
    which needs Discord, runs as a change of its own, after the steps of the change that asked
    for it. It is due only while the live season is in an ongoing stage with every division
    finished or cancelled; where it is not, the change is dropped. Its jobs, each of which stops
    the queue where it fails:

    1. The jobs the modules plan to close what each holds open (`season_end_closes`, for the cause
       `WIND_DOWN_CAUSE`): signup's closes its window where one stands open, and tells and closes
       the channel of each driver it returned, as jobs of its own.
    2. `turn_down`: one save turns down the pending placements, returns their drivers to Not
       Signed Up, moves the season on to Pending completion and writes its line.
    3. Then, for each driver turned down, `signup_notice` and `close_signup` for a signup in review,
       or `take_driver_role` for an approved driver.

    *placement* and the hooks the modules sign up to (*season_end_closes*, *drivers_returned*,
    *driver_returned_jobs*, *season_end_steps*) are what the jobs reach Discord and the other
    modules through, handed by the builder.
    """

    async def check(ctx: CheckContext) -> Verdict:
        async with get_connection(ctx.db_path) as db:
            reason = await wind_down_not_due_on(db)
        return Verdict.go() if reason is None else Verdict.not_due(reason)

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    def plan(_payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        return (*close_jobs(season_end_closes, WIND_DOWN_CAUSE), PlannedStep(TURN_DOWN_STEP))

    def finished_lines(ctx: OutcomeContext) -> tuple[str, ...]:
        # Only what the modules' close left undone is told, in their own words: the drivers it
        # returned are told by jobs of their own, whatever became of the post.
        left = [text for view in ctx.steps if (text := kept_line(steps, view)) is not None]
        if left:
            return ("System | Every division is done | Incomplete"
                    + "".join(f"\n  not done: {text}" for text in left),)
        return ()

    steps = wind_down_steps(drivers_returned=drivers_returned, driver_returned_jobs=driver_returned_jobs)
    steps.update(
        close_signup_steps(
            placement, season_end_steps=season_end_steps,
            line_prefix="System | Every division is done",
        )
    )
    return ChangeType(
        kind=WIND_DOWN,
        opening=(),
        plan=plan,
        steps=steps,
        check=check,
        key=lambda _payload: WIND_DOWN,
        doing=lambda _payload: "Winding the season down",
        outcome=outcome,
        finished_lines=finished_lines,
    )


def wind_down_steps(
    *,
    drivers_returned: AwaitedHook,
    driver_returned_jobs: Hook,
) -> dict[str, Step]:
    """The job that takes a season whose every division is done out of its ongoing stages.

    Shared by the change that winds a season down and by the completion, which winds one down
    first where it finds one (#439): `turn_down`, one save that turns down the pending placements,
    returns their drivers to Not Signed Up, moves the season on to Pending completion and writes
    its line. The jobs that close what the modules hold open come before it
    (`close_jobs`), and the jobs that tell and close each driver's signup
    (`close_signup_steps`) are the change's own to carry. The modules' parts of the `turn_down`
    save are its hooks: the drivers returned (*drivers_returned*) and the jobs planned for each
    (*driver_returned_jobs*).
    """

    async def turn_down(db: aiosqlite.Connection, _ctx: StepContext) -> StepResult:
        cursor = await db.execute(
            "SELECT id FROM seasons WHERE status IN ('SETUP', 'ACTIVE') ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        if row is None:
            return StepResult(result={"moved": False})
        season_id = int(row["id"])
        stage, done = await _stage_and_whether_done_on(db, season_id)
        if stage is None or SeasonStage(stage) not in ONGOING_STAGES or not done:
            return StepResult(result={"moved": False})
        drivers: list[dict] = []
        if stage != SeasonStage.ONGOING.value:
            drivers = await turn_down_pending_placements_on(db, season_id)
            await drivers_returned.call(db, drivers)
            current, _ = await _stage_and_whether_done_on(db, season_id)
            if current in (SeasonStage.ONGOING_SIGNUPS.value, SeasonStage.ONGOING_PLACEMENTS.value):
                await _move_on(db, season_id, SeasonStage(current), SeasonStage.ONGOING)
        moved = await advance_to_pending_completion_on(db, season_id)
        cursor = await db.execute("SELECT driver_role_id FROM server_configs")
        config = await cursor.fetchone()
        planned = driver_jobs(
            drivers, config["driver_role_id"] if config else None,
            notice=WIND_DOWN_NOTICE, reason="Season's divisions done; signup turned down",
            driver_returned_jobs=driver_returned_jobs,
        )
        lines = (
            (f"System | Every division is done | Pending placements turned down: {len(drivers)}",)
            if drivers
            else ()
        )
        return StepResult(result={"moved": moved}, lines=lines, then=planned)

    async def describe_turn_down(_ctx: StepContext) -> str:
        return "turning the pending placements down and moving the season on"

    return {
        TURN_DOWN_STEP: Step(
            TURN_DOWN_STEP, StepKind.SAVE, turn_down, describe=describe_turn_down
        ),
    }


async def configuration_fixed(db_path: str) -> int | None:
    """The number of the season whose confirmed configuration is fixed, or None where it is free.

    The signup module is enabled, disabled and configured only while the server holds no
    active season, or while its season stands in Configuration. From the confirmation of
    that configuration to the season's end, it is fixed: its time slots and its questions are
    what that season's signups were made under.

    The league's base role and driver role are fixed by the same rule, though they are core's
    and not the module's (issue #276). A driver role changed mid-season would leave every
    driver holding the old one for good, the season's end revoking only the new.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT season_number, stage FROM seasons "
            "WHERE status IN ('SETUP', 'ACTIVE') "
            "ORDER BY id DESC LIMIT 1",
        )
        row = await cursor.fetchone()
    if row is None or row["stage"] == SeasonStage.CONFIGURATION.value:
        return None
    return int(row["season_number"])


async def modules_frozen_for_completion(db_path: str) -> bool:
    """True while the server's season stands in Pending completion.

    Nothing but repairing a division's channels, amending the results of a round already
    final, and completing the season may be done then (issue #224, decided 2026-09-20 — the
    season's points amendment was once permitted here too and no longer is), so no module may
    be disabled.
    """
    found = await live_season_stage(db_path)
    return found is not None and found[1] is SeasonStage.PENDING_COMPLETION


async def _stage_and_whether_done_on(
    db: aiosqlite.Connection, season_id: int
) -> tuple[str | None, bool]:
    """The season's stage, and whether every one of its divisions is finished or cancelled.

    A season with no division is not done: it has had nothing to race. Read on *db*.
    """
    cursor = await db.execute(
        "SELECT stage, "
        "  (SELECT COUNT(*) FROM divisions d WHERE d.season_id = s.id) AS divisions, "
        "  (SELECT COUNT(*) FROM divisions d WHERE d.season_id = s.id "
        "     AND d.status NOT IN ('FINISHED', 'CANCELLED')) AS outstanding "
        "FROM seasons s WHERE s.id = ?",
        (season_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None, False
    return row["stage"], row["divisions"] > 0 and row["outstanding"] == 0


async def advance_to_pending_completion_on(db: aiosqlite.Connection, season_id: int) -> bool:
    """Move a season in Ongoing to Pending completion once every division is done (issue #220).

    A division is done when it is finished or cancelled. Only a season in plain Ongoing is
    moved here, this needing nothing but the database. A season with a signup window open or
    placements still to confirm has signups to close and placements to turn down first, which
    need Discord: :func:`wind_down_change` does that, on the change queue, from wherever a
    division can finish. Returns True where this call moved it.

    Commits nothing: the move is part of whatever save the caller makes, so that a division
    finishing and the season it was the last of moving on are saved together or not at all.
    """
    stage, done = await _stage_and_whether_done_on(db, season_id)
    if stage != SeasonStage.ONGOING.value or not done:
        return False
    try:
        await _move_on(db, season_id, SeasonStage.ONGOING, SeasonStage.PENDING_COMPLETION)
    except InvalidStageTransition:
        return False
    log.info("season %s is pending completion", season_id)
    return True


@dataclass(frozen=True)
class PlacementsConfirmed:
    """What :func:`confirm_placements_on` did: how many placements it committed."""

    committed: int


async def confirm_placements_on(db: aiosqlite.Connection, season_id: int) -> PlacementsConfirmed:
    """Commit a season's uncommitted placements and return it to Ongoing, on *db*, committing nothing.

    The mid-season confirmation's one save holds this (issue #439): the placements and the
    season's return from Ongoing, placements to Ongoing are saved together or not at all. The
    stage is moved first, conditioned on the season still being in Ongoing, placements, so
    that a season in any other stage raises :class:`InvalidStageTransition` with nothing
    written. It never moves the season on to Pending completion: a season whose every
    division is done is refused before the confirmation is carried out, and the wind-down
    that moves it on discards the placements not yet committed (core specification, Pending
    completion).
    """
    await _move_on(db, season_id, SeasonStage.ONGOING_PLACEMENTS, SeasonStage.ONGOING)
    return PlacementsConfirmed(committed=await commit_placements_on(db, season_id))


#: The states the driver pass returns to Not Signed Up (issue #220). Season Banned and League
#: Banned are left untouched: bans are to be specified on their own.
DRIVER_PASS_STATES: tuple[str, ...] = (
    "UNASSIGNED",
    "ASSIGNED",
    "PENDING_SIGNUP_COMPLETION",
    "PENDING_ADMIN_APPROVAL",
    "AWAITING_CORRECTION_PARAMETER",
    "PENDING_DRIVER_CORRECTION",
)


async def delete_driver_profiles(
    db, profile_ids: list[int], *, keep_history: bool, profiles_deleted: AwaitedHook
) -> list[str]:
    """Delete *profile_ids* and everything that holds them, within the caller's transaction.

    Every reference to a profile without a cascade has to go, or let go, first. The modules'
    parts go through *profiles_deleted*, called on this connection with the profile ids before
    core's own rows: attendance deletes its rows of the drivers, and results lets go of the
    profile in its result and standings rows while naming the driver by user id still. Core's
    own are then seen to: seats are vacated and placements deleted. A driver's signups are keyed
    by the Discord account and are never touched.

    *keep_history* keeps the driver's history entries, which name them by identifier and let
    go of the profile themselves (`driver_history_entries.driver_profile_id` is `ON DELETE SET
    NULL`) — how test mode keeps its drivers' history. Otherwise the entries are deleted with
    the driver, as the driver pass deletes a real driver who never raced: the archive keeps no
    placement and no history of them.

    Returns every Discord account the deleted profiles held, current and past, sorted. They are
    read first, the cascade taking `driver_accounts` with the profiles. Their portraits are
    keyed by account and go nowhere with the profile, so a caller able to reach the league's
    image configuration discards them once its transaction commits (issue #235).
    """
    if not profile_ids:
        return []
    placeholders = ",".join("?" for _ in profile_ids)
    ids = list(profile_ids)
    cursor = await db.execute(
        f"SELECT discord_user_id FROM driver_accounts WHERE driver_profile_id IN ({placeholders}) "
        "ORDER BY discord_user_id",
        ids,
    )
    accounts = [str(r[0]) for r in await cursor.fetchall()]
    await profiles_deleted.call(db, ids)
    await db.execute(
        f"UPDATE team_seats SET driver_profile_id = NULL WHERE driver_profile_id IN ({placeholders})",
        ids,
    )
    await db.execute(
        f"DELETE FROM driver_season_assignments WHERE driver_profile_id IN ({placeholders})", ids
    )
    if not keep_history:
        await db.execute(
            f"DELETE FROM driver_history_entries WHERE driver_profile_id IN ({placeholders})", ids
        )
    await db.execute(f"DELETE FROM driver_profiles WHERE id IN ({placeholders})", ids)
    return accounts


@dataclass(frozen=True)
class DriverPass:
    """What the driver pass did, for the jobs that follow the save it was made in.

    *reset* is the number of drivers returned to Not Signed Up, *deleted* the ids of the
    profiles deleted, *accounts* every Discord account those profiles held (their portraits are
    keyed by account, not by profile), *drivers* each driver returned, as a mapping of
    ``user_id``, ``state`` (the one they were in) and ``is_test_driver``, and *driver_role_id*
    the league's driver role, as it stood when the pass ran.
    """

    reset: int
    deleted: list[int]
    accounts: list[str]
    drivers: list[dict]
    driver_role_id: int | None


async def run_driver_pass_on(
    db: aiosqlite.Connection, *, profiles_deleted: AwaitedHook
) -> DriverPass:
    """The database side of the driver pass that ends a season, on the connection handed.

    The pass runs inside the save that records a season's end (completion, cancellation and
    abort alike, #220), so this commits nothing. Its Discord side (each driver's signup channel
    and driver role) and the discarding of the deleted drivers' portraits are jobs of their own
    after that save, and are not done here.

    1. Every driver Unassigned, Assigned, mid-signup or in review returns to Not Signed Up,
       through the transition table.
    2. Every real driver at Not Signed Up without the former-driver flag, pending deletion, is
       deleted, with their placements and history entries. Their signups remain.

    A former driver is kept. A driver created by test mode is not deleted here: switching test
    mode off does that, and keeps their history.
    """
    placeholders = ",".join("?" for _ in DRIVER_PASS_STATES)
    cursor = await db.execute(
        f"SELECT id, discord_user_id, current_state, is_test_driver FROM driver_profiles "
        f"WHERE current_state IN ({placeholders})",
        (*DRIVER_PASS_STATES,),
    )
    to_reset = [dict(r) for r in await cursor.fetchall()]
    cursor = await db.execute("SELECT driver_role_id FROM server_configs")
    cfg_row = await cursor.fetchone()
    driver_role_id = cfg_row["driver_role_id"] if cfg_row else None

    from leaguebot.core.models.driver_profile import DriverState
    from leaguebot.core.services.driver_service import write_transition

    # Through the transition table, as every change of a driver's state is.
    for driver in to_reset:
        await write_transition(
            db, driver["id"], DriverState(driver["current_state"]),
            DriverState.NOT_SIGNED_UP,
        )
    cursor = await db.execute(
        "SELECT id FROM driver_profiles WHERE is_test_driver = 0 "
        "AND former_driver = 0 AND current_state = 'NOT_SIGNED_UP'",
    )
    pending_deletion = [r["id"] for r in await cursor.fetchall()]
    accounts = await delete_driver_profiles(
        db, pending_deletion, keep_history=False, profiles_deleted=profiles_deleted
    )
    await db.execute(
        "INSERT INTO audit_entries "
        "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
        "VALUES (0, 'system', NULL, 'DRIVER_PASS', ?, ?, datetime('now'))",
        (
            json.dumps({d["id"]: d["current_state"] for d in to_reset}, sort_keys=True),
            json.dumps({"state": "NOT_SIGNED_UP", "deleted": sorted(pending_deletion)}),
        ),
    )
    return DriverPass(
        reset=len(to_reset),
        deleted=pending_deletion,
        accounts=accounts,
        drivers=[
            {
                "user_id": d["discord_user_id"],
                "state": d["current_state"],
                "is_test_driver": d["is_test_driver"],
            }
            for d in to_reset
        ],
        driver_role_id=driver_role_id,
    )
