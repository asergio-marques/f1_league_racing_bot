"""`core.division_delete`: `/division delete`, on the change queue.

A change type of its own on the league lane, registered by the builder, for a division of the
season being set up. One SAVE step deletes the division with its rounds, sessions, teams and seats
(`SeasonService.delete_division_on`), the delete hooks handing each module's rows of them to the
module that owns them, and writes its `DIVISION_DELETED` audit entry (the division as it stood,
and nothing in its place) and the log channel's line, stamped with the queue's clock, all on the
save's own connection. The kind declares no confinement of its save, the hooks writing other
modules' tables in it.

**A fault in the save stops the lane**, as a failed job does, for the reason `/season setup`'s does
(`season_setup_change.py`): a league admin exists to discard the job and clear the stop, and
nothing of the delete is kept.

**The division is judged by its id.** It is read from the name the member typed as the change is
asked for (`ChangeType.fill`) and judged by its id from then on, so that a division deleted
meanwhile is refused as it starts and a division given its name since is never deleted in its
place. Every refusal is the check's, judged at the ask and again as the change starts, in the
league's log channel either time: the season not in Placements, the division not found and a
driver placed in the division. The schema's foreign key from a placement to its seat stays behind
the last as the backstop.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import aiosqlite

from leaguebot.core.models.change import (
    AuditRecord,
    LEAGUE_LANE,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services.queue.change_types import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.division_edit_changes import (
    division_by_name,
    division_values,
    held_division,
    listed,
    listing,
    placed_division,
)
from leaguebot.core.services.round_add_changes import saved_result
from leaguebot.core.services.season_service import SeasonService
from leaguebot.core.utils.hooks import AwaitedHook

DIVISION_DELETE = "core.division_delete"

#: The name of the step that deletes the division and writes its audit entry and its line.
SAVE = "save"

#: `/division delete` as the line names it.
COMMAND = "/division delete"

#: The refusal of a division with a driver placed in it.
DRIVER_PLACED = (
    "❌ A division with a driver placed in it cannot be deleted. "
    "Unassign its drivers first with `/driver unassign`."
)

#: The reply once a league admin has discarded the stopped save: nothing was deleted.
NOTHING_DELETED = (
    "Nothing was deleted: division **{division}** stands as it was. "
    "Run `/division delete` again."
)


def _check(seasons: SeasonService) -> Callable[[CheckContext], Awaitable[Verdict]]:
    async def check(ctx: CheckContext) -> Verdict:
        """The refusals, in the order they come, each read from the database."""
        found = await placed_division(
            seasons, ctx.payload,
            f"❌ `{COMMAND}` can only be used while the season is in placements.",
        )
        if isinstance(found, Verdict):
            return found
        division, _held = found
        if await seasons.division_holds_a_placed_driver(division.id):
            return Verdict.refuse(DRIVER_PLACED)
        return Verdict.go()

    return check


def _save(
    seasons: SeasonService, rounds_deleted: AwaitedHook, divisions_deleted: AwaitedHook
) -> Callable[[aiosqlite.Connection, StepContext], Awaitable[StepResult]]:
    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Delete the division and write its audit entry and its line."""
        division = await held_division(seasons, db, ctx.payload)
        await SeasonService.delete_division_on(
            db, division.id, rounds_deleted=rounds_deleted, divisions_deleted=divisions_deleted
        )
        line = f"{ctx.named} | {COMMAND} | Success\n  division: {division.name}"
        return StepResult(
            result={
                "outcome": "deleted",
                "division": division.name,
                "divisions": listing(await seasons.get_divisions_on(db, division.season_id)),
            },
            audits=(
                AuditRecord(
                    "DIVISION_DELETED", division_values(division), {}, division_id=division.id
                ),
            ),
            lines=(line,),
        )

    return save


def _doing(payload: dict[str, Any]) -> str:
    return f"Deleting division **{payload['division']}**"


def _outcome(ctx: OutcomeContext) -> str:
    saved = saved_result(ctx)
    if saved.get("outcome") == "deleted":
        return f"✅ Division **{saved['division']}** deleted.\n\n" + listed(saved)
    return NOTHING_DELETED.format(division=ctx.payload["division"])


def division_delete_change(
    *, seasons: SeasonService, rounds_deleted: AwaitedHook, divisions_deleted: AwaitedHook
) -> ChangeType:
    """The `core.division_delete` change type, for the builder to register: *seasons* is what the
    fill and the check read the season being set up, its divisions and their placements through,
    and the save reads the division and deletes it through; *rounds_deleted* and
    *divisions_deleted* are the hooks each module's rows of them are deleted through."""

    async def fill(ctx: CheckContext) -> None:
        await division_by_name(seasons, ctx.payload, str(ctx.payload["name"]))

    return ChangeType(
        kind=DIVISION_DELETE,
        opening=(PlannedStep(SAVE),),
        steps={SAVE: Step(SAVE, StepKind.SAVE, _save(seasons, rounds_deleted, divisions_deleted))},
        check=_check(seasons),
        key=lambda payload: f"{DIVISION_DELETE}|{json.dumps(payload, sort_keys=True)}",
        doing=_doing,
        outcome=_outcome,
        fill=fill,
        lane=lambda _payload: LEAGUE_LANE,
    )


__all__ = ["DIVISION_DELETE", "DRIVER_PLACED", "NOTHING_DELETED", "division_delete_change"]
