"""`core.round_delete`: `/round delete`, on the change queue.

A change type of its own on the league lane, registered by the builder, for a round of the season
being set up. One SAVE step deletes the round with its sessions and renumbers its division by
moment (`SeasonService.delete_round_on`), the delete hook handing each module's rows of the round
to the module that owns them, and writes its `ROUND_DELETED` audit entry (the round as it stood, and
nothing in its place) and the log channel's line, stamped with the queue's clock, all on the save's
own connection. The kind declares no confinement of its save, the hook writing other modules'
tables in it.

**A fault in the save stops the lane**, as a failed job does, for the reason `/season setup`'s does
(`season_setup_change.py`): a league admin exists to discard the job and clear the stop, and
nothing of the delete is kept.

**The round is judged by its id.** The division and the round are read from what the member typed
as the change is asked for (`ChangeType.fill`) and judged by their ids from then on, so that a
round deleted meanwhile, which renumbers its division, is refused as it starts and the round that
now bears its number is never deleted in its place. Every refusal is the check's, judged at the ask
and again as the change starts, in the league's log channel either time: the season not in
Placements, the division not found and the round not found.
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
from leaguebot.core.services.division_edit_changes import held_division, placed_division
from leaguebot.core.services.round_add_changes import held_rounds, round_models, saved_result
from leaguebot.core.services.seasons.season_service import SeasonService
from leaguebot.core.services.setup_round_amend_change import round_fill, round_values
from leaguebot.core.utils.formatting import format_round_list
from leaguebot.core.utils.hooks import AwaitedHook

ROUND_DELETE = "core.round_delete"

#: The name of the step that deletes the round, renumbers its division and writes the audit entry
#: and the line.
SAVE = "save"

#: `/round delete` as the line names it.
COMMAND = "/round delete"

#: The reply once a league admin has discarded the stopped save: nothing was deleted.
NOTHING_DELETED = (
    "Nothing was deleted: round {number} in **{division}** stands as it was. "
    "Run `/round delete` again."
)


def _check(seasons: SeasonService) -> Callable[[CheckContext], Awaitable[Verdict]]:
    async def check(ctx: CheckContext) -> Verdict:
        """The refusals, in the order they come, each read from the database."""
        payload = ctx.payload
        found = await placed_division(
            seasons, payload,
            f"❌ `{COMMAND}` can only be used while the season is in placements.",
        )
        if isinstance(found, Verdict):
            return found
        division, _held = found
        rounds = await seasons.get_division_rounds(division.id)
        if not any(r.id == payload["round_id"] for r in rounds):
            return Verdict.refuse(
                f"❌ Round {payload['round_number']} not found in division `{division.name}`."
            )
        return Verdict.go()

    return check


def _save(
    seasons: SeasonService, rounds_deleted: AwaitedHook
) -> Callable[[aiosqlite.Connection, StepContext], Awaitable[StepResult]]:
    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Delete the round, renumber its division and write its audit entry and its line."""
        payload = ctx.payload
        division = await held_division(seasons, db, payload)
        rnd = next(
            (r for r in await seasons.get_division_rounds_on(db, division.id)
             if r.id == payload["round_id"]),
            None,
        )
        if rnd is None:
            raise LookupError(f"round {payload['round_number']} is not in {division.name}")
        await SeasonService.delete_round_on(db, rnd.id, rounds_deleted=rounds_deleted)
        line = (
            f"{ctx.named} | {COMMAND} | Success\n"
            f"  division: {division.name}\n"
            f"  round: {rnd.round_number}"
        )
        return StepResult(
            result={
                "outcome": "deleted",
                "division": division.name,
                "number": rnd.round_number,
                "rounds": held_rounds(await seasons.get_division_rounds_on(db, division.id)),
            },
            audits=(
                AuditRecord("ROUND_DELETED", round_values(rnd), {}, division_id=division.id),
            ),
            lines=(line,),
        )

    return save


def _doing(payload: dict[str, Any]) -> str:
    return f"Deleting round {payload['round_number']} from **{payload['division']}**"


def _outcome(ctx: OutcomeContext) -> str:
    saved = saved_result(ctx)
    if saved.get("outcome") == "deleted":
        return (
            f"✅ Round **{saved['number']}** deleted from **{saved['division']}** and rounds "
            "renumbered.\n\n" + format_round_list(round_models(saved["rounds"]))
        )
    return NOTHING_DELETED.format(
        number=ctx.payload["round_number"], division=ctx.payload["division"]
    )


def round_delete_change(*, seasons: SeasonService, rounds_deleted: AwaitedHook) -> ChangeType:
    """The `core.round_delete` change type, for the builder to register: *seasons* is what the
    fill and the check read the season being set up, its divisions and their rounds through, and
    the save reads the round and deletes it through; *rounds_deleted* is the hook each module's
    rows of the round are deleted through."""
    return ChangeType(
        kind=ROUND_DELETE,
        opening=(PlannedStep(SAVE),),
        steps={SAVE: Step(SAVE, StepKind.SAVE, _save(seasons, rounds_deleted))},
        check=_check(seasons),
        key=lambda payload: f"{ROUND_DELETE}|{json.dumps(payload, sort_keys=True)}",
        doing=_doing,
        outcome=_outcome,
        fill=round_fill(seasons),
        lane=lambda _payload: LEAGUE_LANE,
    )


__all__ = ["NOTHING_DELETED", "ROUND_DELETE", "round_delete_change"]
