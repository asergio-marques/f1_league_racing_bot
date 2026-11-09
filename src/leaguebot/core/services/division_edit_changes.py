"""`core.division_rename`: `/division rename`, on the change queue.

A change type of its own on the league lane, registered by the builder, for a division of the season
being set up. One SAVE step changes the division's name and writes its `DIVISION_RENAMED` audit
entry (the division as it stood and as it stands) and the log channel's line, stamped with the
queue's clock, all on the save's own connection. A division given the name it already bears is
answered as such, recording that nothing was changed and writing no audit entry.

**A fault in the save stops the lane**, as a failed job does, for the reason `/season setup`'s does
(`season_setup_change.py`): a league admin exists to discard the job and clear the stop.

**The division is judged by its id.** It is read from the name the member typed as the change is
asked for (`ChangeType.fill`) and judged by its id from then on, so that a division deleted
meanwhile is refused as it starts and a division given its name since is never renamed in its
place. Every refusal is the check's, judged at the ask and again as the change starts, in the
league's log channel either time, in the order a manager has always met them: the season not in
Placements, the division not found, a name the league's rules refuse, and a name another division
holds (compared without regard to case, the division's own name excepted, so that correcting `Pro`
to `PRO` does not refuse itself). A name taken by a change asked just before is thus refused as it
starts, in the words it would have met at the ask, rather than reaching the schema's rule that holds
a name once per season (`001_baseline.sql`).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.core.models.change import (
    AuditRecord,
    LEAGUE_LANE,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.division import Division
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.season_service import (
    division_amendment_changes_nothing,
    validate_division_name,
)
from leaguebot.core.utils.formatting import format_division_list

if TYPE_CHECKING:
    from leaguebot.core.services.season_service import SeasonService

DIVISION_RENAME = "core.division_rename"

#: The name of the step that changes the division and writes its audit entry and its line.
SAVE = "save"

#: `/division rename` as the line names it.
RENAME = "/division rename"

#: The reply once a league admin has discarded the stopped save of a rename: nothing was renamed.
NOTHING_RENAMED = (
    "Nothing was renamed: division **{division}** stands as it was. "
    "Run `/division rename` again."
)


async def division_by_name(
    seasons: "SeasonService", payload: dict[str, Any], name: str
) -> None:
    """Fill *payload* with the division of the season being set up that *name* names, without
    regard to case: its own spelling as `division` and its id as `division_id`, which is None, and
    `division` the typed name, where there is none."""
    payload["division"] = name
    payload["division_id"] = None
    season = await seasons.get_setup_season()
    if season is None:
        return
    wanted = name.lower()
    division = next(
        (d for d in await seasons.get_divisions(season.id) if d.name.lower() == wanted), None
    )
    if division is not None:
        payload["division"] = division.name
        payload["division_id"] = division.id


async def placed_division(
    seasons: "SeasonService", payload: dict[str, Any], refusal_in_placements: str
) -> tuple[Division, list[Division]] | Verdict:
    """The division the request names by its id and the divisions of the season being set up, or
    the verdict refusing the request: *refusal_in_placements* where no season is being set up or
    it is not yet in Placements, and the division not found where it has gone."""
    season = await seasons.get_setup_season()
    if season is None or season.stage is not SeasonStage.PLACEMENTS:
        return Verdict.refuse(refusal_in_placements)
    held = await seasons.get_divisions(season.id)
    division = next((d for d in held if d.id == payload["division_id"]), None)
    if division is None:
        return Verdict.refuse(f"❌ Division `{payload['division']}` not found.")
    return division, held


def name_taken(name: str, division: Division, held: list[Division]) -> Verdict | None:
    """The verdict refusing *name* where a division other than *division* holds it, else None."""
    if any(d.name.lower() == name.lower() for d in held if d.id != division.id):
        return Verdict.refuse(f"❌ A division named **{name}** already exists.")
    return None


def division_values(division: Division) -> dict[str, Any]:
    """*division* as the audit entry holds it."""
    return {
        "name": division.name,
        "tier": division.tier,
        "mention_role_id": division.mention_role_id,
    }


async def held_division(
    seasons: "SeasonService", db: aiosqlite.Connection, payload: dict[str, Any]
) -> Division:
    """The division the request names by its id, as *db* holds it, or a LookupError where the
    check, made as the change started, has been overtaken."""
    season = await seasons.get_setup_season_on(db)
    if season is None:
        raise LookupError("no season is being set up")
    division = next(
        (d for d in await seasons.get_divisions_on(db, season.id)
         if d.id == payload["division_id"]),
        None,
    )
    if division is None:
        raise LookupError(f"no division named {payload['division']} is being set up")
    return division


def listing(divisions: list[Division]) -> list[dict[str, Any]]:
    """*divisions* as a result holds them, for the reply to list."""
    return [{"name": d.name, "tier": d.tier, "role_id": d.mention_role_id} for d in divisions]


def listed(saved: dict[str, Any]) -> str:
    """The divisions a saved result holds, as the reply lists them."""
    return format_division_list(
        [
            Division(
                id=0, season_id=0, name=d["name"], mention_role_id=d["role_id"],
                forecast_channel_id=None, tier=d["tier"],
            )
            for d in saved["divisions"]
        ]
    )


def saved_result(ctx: OutcomeContext) -> dict[str, Any]:
    """What the save recorded, or nothing where it did not run to its end."""
    return next(
        (view.result for view in ctx.steps if view.name == SAVE and view.result is not None), {}
    )


def _rename_check(seasons: "SeasonService") -> Callable[[CheckContext], Awaitable[Verdict]]:
    async def check(ctx: CheckContext) -> Verdict:
        """The refusals, in the order they come, each read from the database."""
        payload = ctx.payload
        found = await placed_division(
            seasons, payload,
            f"❌ `{RENAME}` can only be used while the season is in placements.",
        )
        if isinstance(found, Verdict):
            return found
        division, held = found
        new_name = str(payload["new_name"])
        refusal = validate_division_name(new_name)
        if refusal is not None:
            return Verdict.refuse(f"❌ {refusal}")
        return name_taken(new_name, division, held) or Verdict.go()

    return check


def _rename_save(
    seasons: "SeasonService",
) -> Callable[[aiosqlite.Connection, StepContext], Awaitable[StepResult]]:
    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Rename the division and write its audit entry and its line; or, where it already bears
        the name, record that nothing was changed."""
        payload = ctx.payload
        division = await held_division(seasons, db, payload)
        new_name = str(payload["new_name"])
        if division_amendment_changes_nothing(division, new_name=new_name):
            line = (
                f"{ctx.named} | {RENAME} | Nothing changed\n"
                f"  division: {division.name}\n"
                "  reason: it already bears that name"
            )
            return StepResult(
                result={"outcome": "unchanged", "division": division.name}, lines=(line,)
            )
        await db.execute("UPDATE divisions SET name = ? WHERE id = ?", (new_name, division.id))
        before = division_values(division)
        line = (
            f"{ctx.named} | {RENAME} | Success\n"
            f"  old_name: {division.name}\n"
            f"  new_name: {new_name}"
        )
        return StepResult(
            result={
                "outcome": "renamed",
                "old_name": division.name,
                "new_name": new_name,
                "divisions": listing(await seasons.get_divisions_on(db, division.season_id)),
            },
            audits=(
                AuditRecord(
                    "DIVISION_RENAMED", before, {**before, "name": new_name},
                    division_id=division.id,
                ),
            ),
            lines=(line,),
        )

    return save


def _rename_doing(payload: dict[str, Any]) -> str:
    return f"Renaming division **{payload['division']}** to **{payload['new_name']}**"


def _rename_outcome(ctx: OutcomeContext) -> str:
    saved = saved_result(ctx)
    match saved.get("outcome"):
        case "renamed":
            return (
                f"✅ Division **{saved['old_name']}** renamed to **{saved['new_name']}**.\n\n"
                + listed(saved)
            )
        case "unchanged":
            return (
                f"ℹ️ Division **{saved['division']}** is already named "
                f"**{saved['division']}**. Nothing was changed."
            )
        case _:
            return NOTHING_RENAMED.format(division=ctx.payload["division"])


def division_rename_change(*, seasons: "SeasonService") -> ChangeType:
    """The `core.division_rename` change type, for the builder to register: *seasons* is what the
    fill and the check read the season being set up and its divisions through, and the save reads
    and changes the division through."""

    async def fill(ctx: CheckContext) -> None:
        await division_by_name(seasons, ctx.payload, str(ctx.payload["current_name"]))

    return ChangeType(
        kind=DIVISION_RENAME,
        opening=(PlannedStep(SAVE),),
        steps={SAVE: Step(SAVE, StepKind.SAVE, _rename_save(seasons))},
        check=_rename_check(seasons),
        key=lambda payload: f"{DIVISION_RENAME}|{json.dumps(payload, sort_keys=True)}",
        doing=_rename_doing,
        outcome=_rename_outcome,
        fill=fill,
        lane=lambda _payload: LEAGUE_LANE,
        writes=("divisions",),
    )


__all__ = ["DIVISION_RENAME", "NOTHING_RENAMED", "division_rename_change"]
