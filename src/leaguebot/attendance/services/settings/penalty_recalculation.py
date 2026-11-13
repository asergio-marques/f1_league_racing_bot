"""A penalty changed while a season is ongoing applies to the whole season.

`/attendance config no-rsvp-penalty`, `absent-penalty` and `no-show-penalty` are settings like the
rest (`attendance_settings`), and `penalty_change` makes each of them more where the season is in
one of its three ongoing stages and a round of it has had its penalties approved. The new value
then reaches every approved round of every live division of the season, exactly as `/attendance
sync` from the division's first approved round would give it:

- **One save.** The value, its audit entry and its line, and every division's recalculation
  (`recalculate_division_on`, the one copy of what the sync saves for a division) and the
  sanctions the division's latest round owes, are on the setting's one connection, so a stop
  leaves all of it or none, and the penalty is never seen saved without the recalculation.
  Sanctions are judged at each division's latest round only, never retrospectively: a driver the
  new value takes over a threshold at an earlier round is sanctioned once, under the latest
  round's heading, and a sanction already applied is never undone by a lowered penalty.
- **Later jobs.** The roles, the cards, the lineups and the sheets are the sync's own jobs
  (`posting_steps`), planned by the save, which stop the lane where Discord refuses them. This is
  the one departure from `setting_change`'s rule that a setting never holds up the queue: a
  change that recalculates and posts as `/attendance sync` does follows the sync's rule whatever
  starts it, a lost sanction card being worse than a held lane. A sheet or a lineup two
  divisions' sanctions both owe is posted once, against its division's latest round.
- **Not all applied.** Where a sanction could not be applied the setting's line reads
  `Incomplete`, as the sync's does, naming each driver, the sanction and the reason, and ending
  with `sync_hint_text` for each division with a failure; the reply says the same.
- **Refusals** are the sync's, made at the ask and again as the change starts: an amendment open
  in a division recalculated, and a channel the recalculation posts to that cannot be reached.
  Neither is judged for a value already held, which changes nothing.
- **The Reserve team's capacity** is read before the save (`CHECK_CAPACITY`), as the sync reads it,
  for each division recalculated, and not at all for a value already held.
- **The tables it writes** are declared (`WRITES`): the setting's, the recalculation's and the
  sanctions', the authoriser confining the save to them as it does any setting.

A season not ongoing, or none of whose divisions has an approved round, saves the value alone.
*Rejected:* a second copy of the recalculation for the setting, which the sync's would drift from.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.attendance.services.sanctions import attendance_sanctions
from leaguebot.attendance.services.sanctions import attendance_sync_change as sync
from leaguebot.attendance.services.scoring import attendance_recompute
from leaguebot.attendance.services.sheet import sheet_posting
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import PlannedStep, StepKind, StepResult, Verdict, VerdictKind
from leaguebot.core.models.division import DivisionStatus
from leaguebot.core.models.round import RoundStatus
from leaguebot.core.models.season import ONGOING_STAGES
from leaguebot.core.services.drivers import lineup_posting
from leaguebot.core.services.queue.change_types import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.queue.setting_change import SAVE
from leaguebot.core.services.seasons import season_service
from leaguebot.core.utils.league_server import league_guild
from leaguebot.results.services.round_amendment.amendment_writes import open_amendment_in_division

if TYPE_CHECKING:
    from leaguebot.core.services.drivers.lineup_posting import LineupPoster
    from leaguebot.core.services.drivers.placement_service import PlacementService
    from leaguebot.core.services.drivers.team_role_service import TeamRoleService
    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.results.services.module.attendance_hook import AttendanceAfterReview

#: The job that reads each division's Reserve capacity, before the save.
CHECK_CAPACITY = "penalty_check_reserve"

#: The tables the change writes: the penalty's own, the recalculation's, a sanction's seat, status
#: and card, and what the jobs after the save record (a lineup's and a sheet's message).
WRITES = (
    "attendance_config", "attendance_division_config", "divisions", "driver_profiles",
    "driver_round_attendance", "driver_season_assignments", "team_seats",
    "verdict_banner_messages",
)

_APPROVED = (RoundStatus.AWAITING_APPEAL_VERDICTS.value, RoundStatus.FINAL.value)


@dataclass(frozen=True)
class _Recalculated:
    """A live division of the ongoing season with approved rounds: its id, name and its first and
    latest approved round."""

    division_id: int
    name: str
    first_round_id: int
    first_round_number: int
    latest_round_id: int


async def _recalculated_on(db: aiosqlite.Connection) -> tuple[int, list[_Recalculated]]:
    """The ongoing season's id and the divisions a penalty's change recalculates, in tier order;
    ``(0, [])`` where no season is ongoing or no division has an approved round."""
    season = await season_service.live_season_on(db)
    if season is None or season.stage not in ONGOING_STAGES:
        return 0, []
    found: list[_Recalculated] = []
    divisions = await season_service.season_divisions_on(db, season.id)
    for division in sorted(divisions, key=lambda each: (each.tier, each.id)):
        if division.status == DivisionStatus.CANCELLED:
            continue
        states = await season_service.division_round_states_on(db, division.id)
        approved = [(round_id, number) for round_id, (number, status) in states.items()
                    if status in _APPROVED]
        if approved:
            found.append(_Recalculated(
                division.id, division.name or f"division {division.id}",
                approved[0][0], approved[0][1], approved[-1][0],
            ))
    return (season.id, found) if found else (0, [])


def _with_outcome(line: str, outcome: str) -> str:
    """*line*, a setting's log line, with *outcome* in place of the one it carries."""
    first, newline, rest = line.partition("\n")
    return f"{first.rpartition(' | ')[0]} | {outcome}{newline}{rest}"


def _merged(planned: list[PlannedStep], latest: dict[int, int]) -> list[PlannedStep]:
    """*planned*, the jobs of every division's recalculation, with a division's sheet and its
    lineup once each: a sheet two divisions' sanctions both owe is one post, against the division's
    latest round (*latest*, by division) and marking everyone either sanctioned."""
    merged: list[PlannedStep] = []
    sheets: dict[int, PlannedStep] = {}
    lineups: set[int] = set()
    for step in planned:
        if step.name == sheet_posting.POST_SHEET:
            division_id = int(step.payload["division_id"])
            payload = {**step.payload, "round_id": latest.get(division_id,
                                                              step.payload["round_id"])}
            if division_id in sheets:
                both = {*sheets[division_id].payload["sanctioned"], *payload["sanctioned"]}
                sheets[division_id].payload["sanctioned"] = sorted(both)
                continue
            sheets[division_id] = PlannedStep(step.name, payload)
            merged.append(sheets[division_id])
        elif step.name == lineup_posting.POST_LINEUP:
            division_id = int(step.payload["division_id"])
            if division_id in lineups:
                continue
            lineups.add(division_id)
            merged.append(step)
        else:
            merged.append(step)
    return merged


def penalty_change(
    change: ChangeType,
    *,
    column: str,
    placement: "PlacementService",
    roles: "TeamRoleService",
    lineups: "LineupPoster",
    attendance: "AttendanceAfterReview",
    modules: "ModuleService",
    now: Callable[[], datetime],
) -> ChangeType:
    """*change*, the setting of the penalty in *column* as `setting_change` builds it, made to
    recalculate the ongoing season; see the module. *placement* applies the sanctions the new
    value owes, *roles*, *lineups*, *attendance* and *modules* are the jobs' (`posting_steps`) and
    *now* is the queue's clock."""
    base_check, base_save, base_outcome = change.check, change.steps[SAVE], change.outcome

    async def check(ctx: CheckContext) -> Verdict:
        verdict = await base_check(ctx)
        if verdict.kind is not VerdictKind.GO:
            return verdict
        async with get_connection(ctx.db_path) as db:
            row = await (await db.execute(f"SELECT {column} FROM attendance_config")).fetchone()  # noqa: S608
            season_id, divisions = await _recalculated_on(db)
        if row is None or row[0] == ctx.payload["value"] or not divisions:
            return verdict
        for division in divisions:
            held = await open_amendment_in_division(ctx.db_path, division.division_id)
            refusal = sync.amendment_verdict(held, division.name)
            if refusal is not None:
                return refusal
        faults = await attendance_recompute.recalculation_faults(
            ctx.db_path, season_id, await league_guild(ctx.bot), ctx.bot
        )
        return sync.faults_verdict(faults) or verdict

    async def check_capacity(ctx: StepContext) -> StepResult:
        """Each division's Reserve capacity, read before the save, where the save will recalculate:
        nothing is read for a value already held."""
        async with get_connection(ctx.db_path) as db:
            row = await (await db.execute(f"SELECT {column} FROM attendance_config")).fetchone()  # noqa: S608
            _season_id, divisions = await _recalculated_on(db)
        if row is None or row[0] == ctx.payload["value"]:
            divisions = []
        return StepResult(result={"capacity": {
            str(each.division_id): await sync.reserve_capacity(
                ctx.db_path, placement, each.latest_round_id, each.division_id
            )
            for each in divisions
        }})

    async def describe_capacity(_ctx: StepContext) -> str:
        return "checking the Reserve teams can take a driver"

    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The setting's save, then the season's recalculation on the same connection."""
        saved = await base_save.run(db, ctx)
        if not saved.result["changed"]:
            return saved
        season_id, divisions = await _recalculated_on(db)
        if not divisions:
            return saved
        acting = ctx.bot.user
        # Sanctions are enforced by a bot that has logged in, which is when it has a user.
        assert acting is not None, "sanctions enforced before the bot logged in"
        capacity: dict[str, dict[str, Any]] = next(
            ((view.result or {}).get("capacity", {}) for view in ctx.steps
             if view.name == CHECK_CAPACITY), {},
        )
        lines: list[str] = []
        planned: list[PlannedStep] = []
        applied: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        hints: list[str] = []
        for each in divisions:
            done = await sync.recalculate_division_on(
                db, db_path=ctx.db_path, placement=placement, acting=acting, now=now(),
                division_id=each.division_id, division_name=each.name, season_id=season_id,
                from_round_id=each.first_round_id,
                capacity=capacity.get(str(each.division_id), {}),
            )
            lines.extend(done.lines)
            planned.extend(done.then)
            applied.extend(done.result["applied"])
            failed.extend(done.result["failed"])
            if done.result["failed"]:
                hints.append(attendance_sanctions.sync_hint_text(
                    each.name, each.first_round_number
                ))
        names = [each.name for each in divisions]
        setting_line = "\n".join([
            _with_outcome(saved.lines[0], "Incomplete") if failed else saved.lines[0],
            f"  attendance recalculated at the new value: {', '.join(names)}",
            f"  sanctions applied: {len(applied)}",
            *(f"  {sync._failure(entry)}" for entry in failed),
            *(f"  {hint}" for hint in hints),
        ])
        return dataclasses.replace(
            saved,
            result={**saved.result, "recalculated": names, "applied": applied, "failed": failed,
                    "hints": hints},
            lines=(setting_line, *lines),
            then=tuple(_merged(planned, {each.division_id: each.latest_round_id
                                         for each in divisions})),
        )

    def outcome(ctx: OutcomeContext) -> str:
        reply = base_outcome(ctx)
        saved = next((dict(view.result) for view in ctx.steps
                      if view.name == SAVE and view.result), {})
        if not saved.get("recalculated"):
            return reply
        names = ", ".join(f"**{name}**" for name in saved["recalculated"])
        return "\n".join([
            reply,
            f"Attendance was recalculated at the new value, as `/attendance sync` would, for "
            f"every approved round of {names}, and their sheets are posted again.",
            *sync.sanction_reply_lines(saved["applied"], saved["failed"]),
            *saved["hints"],
            *sync.not_done_lines(ctx),
        ])

    steps = {
        **change.steps,
        SAVE: dataclasses.replace(change.steps[SAVE], run=save),
        CHECK_CAPACITY: Step(CHECK_CAPACITY, StepKind.ACT, check_capacity,
                             describe=describe_capacity),
        **sync.posting_steps(
            roles=roles, lineups=lineups, attendance=attendance, modules=modules, now=now
        ),
    }
    return dataclasses.replace(
        change,
        opening=(PlannedStep(CHECK_CAPACITY), PlannedStep(SAVE)),
        steps=steps,
        check=check,
        outcome=outcome,
        writes=WRITES,
    )
