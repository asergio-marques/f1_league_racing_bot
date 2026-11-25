"""`core.driver_assign_bulk`: `/driver assign-bulk` on the change queue, many drivers seated from one
pasted list.

A change type of its own on the league lane, registered by the builder. The payload names the
season (`season_id`) and every line of the paste, in its order (`lines`): the line's number as the
manager saw it, the driver (`driver_profile_id`, None for a member holding no profile,
`discord_user_id`, `driver_name`), the division (`division_id`, with its name, `division`) and the
team (`team_name`, the shorthand, and `team_full_name`).

**All or nothing.** Every line is judged as a single `/driver assign` is, in the order of the list,
and one refused line refuses the paste, naming every refused line by its number
(`format_placement_errors`). A line is judged against what the lines before it would take, which
the database does not yet hold: `PlacementService.judge_assign_paste_on` keeps the running tally of
seats and of divisions entered, and `guard_assign_paste` counts the drivers placed before a line
into the templates' capacity. A real driver in test mode is refused first
(`guard_assign_paste_test_mode`), as a single placement refuses it, and takes no seat from the
lines after it. The whole paste is refused once, not line by line, where the season
is no longer in a stage drivers are placed in or a division has left it.

**The check judges at the ask and again as the change starts**, in the league's log channel either
time. The guards that read templates and the test-mode flag run in the check alone, as they cannot
run inside a save; the save judges every line once more on its own connection, before the first
write, so that a seat taken or a stage left between the check and the write cannot slip past. A
refusal found there ends the change in its words, changing nothing and not stopping the lane.

**One save** then seats the drivers in list order (`PlacementService.assign_on`, with
`profile_for_placement_on` giving a member holding no profile one while the signup module is off,
the same member in two divisions holding the one), writes one `DRIVER_ASSIGN` audit entry per driver,
stamped with the queue's clock, and the one line that records the change. The placements are
unconfirmed, so no role is given and no lineup posted: the change has no job after its save. A
fault in the save stops the lane as a failed job does, and nothing of the paste is kept.
"""

from __future__ import annotations

import json
from typing import Any

import aiosqlite

from leaguebot.core.services.drivers.driver_changes import division_refusal, placing_refusal
from leaguebot.core.services.drivers.driver_service import (
    existing_profile_for_placement_on,
    profile_for_placement_on,
)
from leaguebot.core.services.drivers.placement_service import PlacementService
from leaguebot.core.utils.placement_paste import format_placement_errors
from leaguebot.framework.db.database import get_connection
from leaguebot.framework.models.change import (
    AuditRecord,
    LEAGUE_LANE,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.framework.models.refusal import Refused
from leaguebot.framework.services.queue.change_types import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.framework.services.switches.module_service import ModuleService
from leaguebot.framework.utils.log_lines import refusal_line, reply_reason

DRIVER_ASSIGN_BULK = "core.driver_assign_bulk"

#: The names of the jobs, as the stop notice, the outcome and the tests know them.
SAVE = "save"
CLOSE = "close"

#: `/driver assign-bulk` as the line names it.
ASSIGN_BULK_COMMAND = "/driver assign-bulk"

#: The same, as the log channel names what was refused.
ASSIGN_BULK_WHAT = "`/driver assign-bulk`"

#: The reply once a league admin has discarded the stopped save: nobody was placed.
NOTHING_ASSIGNED_BULK = (
    "Nothing was assigned: no driver of the pasted list is placed. Run `/driver assign-bulk` again."
)


def _line_number(fault: str) -> int:
    """The number of the line a fault ("Line N: …") names."""
    return int(fault.split(":", 1)[0].removeprefix("Line "))


async def _judged(
    db: aiosqlite.Connection,
    placement: PlacementService,
    modules: ModuleService,
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], bool]:
    """Judge the lines of *payload* on *db*: the lines that have what they need to be judged
    further (each with the profile it would be placed under), the faults found so far, as "Line N:
    …" in the order of the list, and whether the signup module is off.

    A line holding no profile is refused with the signup module on; with it off it is judged as a
    member asking for a seat, under the profile a placement ahead of this one on the queue has made
    since the ask, if any.
    """
    signup_off = not await modules.is_enabled_on(db, "signup")
    lines: list[dict[str, Any]] = []
    faults: dict[int, str] = {}
    for line in payload["lines"]:
        profile_id = line["driver_profile_id"]
        if profile_id is not None:
            cursor = await db.execute("SELECT 1 FROM driver_profiles WHERE id = ?", (profile_id,))
            if await cursor.fetchone() is None:
                profile_id = None
        elif signup_off:
            profile_id = await existing_profile_for_placement_on(
                db, str(line["discord_user_id"])
            )
        if profile_id is None and not signup_off:
            faults[line["line"]] = (
                f"Line {line['line']}: No driver profile found for **{line['driver_name']}**."
            )
            continue
        lines.append({**line, "driver_profile_id": profile_id})
    for fault in await placement.judge_assign_paste_on(
        db, lines, season_id=int(payload["season_id"]), signup_off=signup_off
    ):
        faults[_line_number(fault)] = fault
    sound = [line for line in lines if line["line"] not in faults]
    return sound, [faults[number] for number in sorted(faults)], signup_off


def driver_assign_bulk_change(
    *, placement: PlacementService, modules: ModuleService
) -> ChangeType:
    """The `core.driver_assign_bulk` change type, for the builder to register: *placement* is what
    the check judges the paste through and the save places the drivers through, and *modules* is
    read for the signup module's switch, on the connection the check or the save holds."""

    async def check(ctx: CheckContext) -> Verdict:
        """The refusals, in the order they come, each read from the database: of the whole paste,
        then every line's."""
        payload = ctx.payload
        season_id = int(payload["season_id"])
        divisions = sorted({(int(line["division_id"]), line["division"]) for line in payload["lines"]})
        refused = await placing_refusal(
            ctx.db_path, season_id, ASSIGN_BULK_COMMAND
        ) or await division_refusal(ctx.db_path, season_id, divisions)
        if refused is not None:
            return refused
        # Test mode first, as a single placement asks it, so that a real driver refused for it
        # takes no seat from the lines after.
        in_test_mode = await placement.guard_assign_paste_test_mode(payload["lines"])
        barred = {_line_number(fault) for fault in in_test_mode}
        asked = {**payload, "lines": [each for each in payload["lines"] if each["line"] not in barred]}
        async with get_connection(ctx.db_path) as db:
            sound, faults, _signup_off = await _judged(db, placement, modules, asked)
        faults = sorted(
            [*in_test_mode, *faults, *await placement.guard_assign_paste(season_id, sound)],
            key=_line_number,
        )
        if faults:
            return Verdict.refuse(format_placement_errors(faults))
        return Verdict.go()

    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The one save; see the module."""
        payload = ctx.payload
        season_id = int(payload["season_id"])
        sound, faults, signup_off = await _judged(db, placement, modules, payload)
        if faults:
            reply = format_placement_errors(faults)
            return StepResult(
                result={"refused": reply},
                lines=(refusal_line(ctx.named, ctx.what, reply_reason(reply)),),
            )
        placed: list[dict[str, Any]] = []
        audits: list[AuditRecord] = []
        for line in sound:
            profile_id = line["driver_profile_id"]
            if profile_id is None:
                profile_id = await profile_for_placement_on(db, str(line["discord_user_id"]))
            assigned = await placement.assign_on(
                db, driver_profile_id=profile_id, division_id=int(line["division_id"]),
                team_name=str(line["team_name"]), season_id=season_id, uncommitted_only=True,
                signup_off=signup_off,
            )
            audit = assigned["audit"]
            audits.append(
                AuditRecord(
                    "DRIVER_ASSIGN", audit["old_value"], audit["new_value"], audit["division_id"]
                )
            )
            placed.append(
                {
                    "line": line["line"], "driver_name": line["driver_name"],
                    "discord_user_id": line["discord_user_id"],
                    "team_full_name": line["team_full_name"],
                    "division_name": assigned["division_name"],
                }
            )
        return StepResult(result={"saved": True, "placed": placed}, audits=tuple(audits))

    async def close(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Write the line that records the change: the count, and each placement in list order."""
        saved = _saved(ctx)
        if saved is None:
            return StepResult(result={"closed": True})
        placed = saved["placed"]
        line = (
            f"{ctx.named} | {ASSIGN_BULK_COMMAND} | Success\n  placed: {len(placed)}"
            + "".join(
                f"\n  {each['driver_name']} (<@{each['discord_user_id']}>) → "
                f"{each['team_full_name']} in {each['division_name']}"
                for each in placed
            )
        )
        return StepResult(result={"closed": True}, lines=(line,))

    async def describe_close(ctx: StepContext) -> str:
        return f"recording `{ASSIGN_BULK_COMMAND}` of {len(ctx.payload['lines'])} driver(s)"

    def doing(payload: dict[str, Any]) -> str:
        return f"Assigning **{len(payload['lines'])}** driver(s) from a pasted list"

    def outcome(ctx: OutcomeContext) -> str:
        view = _view(ctx)
        refused = (view.result or {}).get("refused") if view is not None else None
        if refused:
            return str(refused)
        saved = _saved(ctx)
        if saved is None:
            return NOTHING_ASSIGNED_BULK
        return f"✅ Assigned **{len(saved['placed'])}** driver(s)."

    return ChangeType(
        kind=DRIVER_ASSIGN_BULK,
        opening=(PlannedStep(SAVE), PlannedStep(CLOSE)),
        steps={
            SAVE: Step(SAVE, StepKind.SAVE, save),
            CLOSE: Step(CLOSE, StepKind.SAVE, close, describe=describe_close),
        },
        check=check,
        key=lambda payload: f"{DRIVER_ASSIGN_BULK}|{json.dumps(payload, sort_keys=True)}",
        doing=doing,
        outcome=outcome,
        lane=lambda _payload: LEAGUE_LANE,
    )


def _view(ctx: OutcomeContext) -> Any:
    """The last save job."""
    return next((view for view in reversed(ctx.steps) if view.name == SAVE), None)


def _saved(ctx: OutcomeContext) -> dict[str, Any] | None:
    """What the save returned, or None where it did not go through: a league admin discarded it,
    or it found the paste refused."""
    view = _view(ctx)
    if view is None or not view.done or not view.result or not view.result.get("saved"):
        return None
    return view.result


__all__ = [
    "ASSIGN_BULK_COMMAND", "ASSIGN_BULK_WHAT", "DRIVER_ASSIGN_BULK", "NOTHING_ASSIGNED_BULK",
    "driver_assign_bulk_change",
]
