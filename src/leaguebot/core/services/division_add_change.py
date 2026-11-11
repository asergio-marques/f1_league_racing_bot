"""`core.division_add`: `/division add`, on the change queue.

One SAVE step inserts the division into the season being set up, seeds its teams from the league's
list (the Reserve team included), and writes its `DIVISION_ADDED` audit entry and the log
channel's line, stamped with the queue's clock, all on the save's own connection. Only the
division is added: its rounds are the commands that add rounds'.

**A fault in the save stops the lane**, as a failed job does, for the reason `/season setup`'s does
(`season_setup_change.py`): a league admin exists to discard the job and clear the stop.

**Every refusal is the check's**, judged at the ask and again as the change starts, in the league's
log channel either time, in the order a manager has always met them: no season being set up, a
season not yet in Placements, a tier below 1, a name the league's rules refuse, a name the season
holds already (compared as Python lowercases it, so that an accented letter's case is judged too),
and a tier the season holds already. The second of two adds of one name or tier, asked together, is
thus refused as it starts, in the words it would have met at the ask. The schema holds the name
once per season without regard to ASCII case as the backstop (`001_baseline.sql`): SQLite folds no
other letter, so a name differing in an accented letter's case reaches the check alone.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import datetime
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
from leaguebot.core.services.queue.change_types import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.seasons.season_service import validate_division_name, validate_division_tier
from leaguebot.core.utils.formatting import format_division_list

if TYPE_CHECKING:
    from leaguebot.core.services.seasons.season_service import SeasonService
    from leaguebot.core.services.team_service import TeamService

KIND = "core.division_add"

#: The name of the step that inserts the division, seeds its teams and writes its audit entry and
#: its line.
SAVE = "save"

#: The command as the line names it.
COMMAND = "/division add"

#: `/division add` refused while no season is being set up, word for word.
NO_SETUP = "❌ No pending season setup. Run `/season setup` first."

#: `/division add` refused before Placements, word for word.
NOT_PLACEMENTS = (
    "⛔ Divisions can only be added while the season is in placements — "
    "once its configuration is confirmed and its signup window has closed."
)

#: The reply once a league admin has discarded the stopped save: nothing was added.
DISCARDED = "Nothing was added: no division was created. Run `/division add` again."


def _check(seasons: "SeasonService") -> Callable[[CheckContext], Awaitable[Verdict]]:
    async def check(ctx: CheckContext) -> Verdict:
        """The refusals, in the order they come, each read from the database."""
        season = await seasons.get_setup_season()
        if season is None:
            return Verdict.refuse(NO_SETUP)
        if season.stage is not SeasonStage.PLACEMENTS:
            return Verdict.refuse(NOT_PLACEMENTS)
        name, tier = str(ctx.payload["name"]), int(ctx.payload["tier"])
        refusal = validate_division_tier(tier, [])
        if refusal is not None:
            return Verdict.refuse(f"⛔ {refusal}")
        refusal = validate_division_name(name)
        if refusal is not None:
            return Verdict.refuse(f"❌ {refusal}")
        held = await seasons.get_divisions(season.id)
        if any(d.name.lower() == name.lower() for d in held):
            return Verdict.refuse(
                f"❌ A division named **{name}** already exists in this setup."
            )
        refusal = validate_division_tier(tier, [d.tier for d in held])
        if refusal is not None:
            return Verdict.refuse(f"⛔ {refusal}")
        return Verdict.go()

    return check


def _save(
    seasons: "SeasonService", teams: "TeamService"
) -> Callable[[aiosqlite.Connection, StepContext], Awaitable[StepResult]]:
    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Insert the division, seed its teams, and write its audit entry and its line, one save."""
        payload = ctx.payload
        division = await seasons.add_division_on(
            db, name=str(payload["name"]), role_id=int(payload["role_id"]),
            tier=int(payload["tier"]),
        )
        await teams.seed_division_teams_on(db, division.id)
        listed = await seasons.get_divisions_on(db, division.season_id)
        new = {
            "division_id": division.id,
            "season_id": division.season_id,
            "name": division.name,
            "tier": division.tier,
            "mention_role_id": division.mention_role_id,
        }
        line = (
            f"{ctx.named} | {COMMAND} | Success\n"
            f"  division: {division.name}, tier: {division.tier}"
        )
        return StepResult(
            result={
                "outcome": "added",
                **new,
                "divisions": [
                    {"name": d.name, "tier": d.tier, "role_id": d.mention_role_id} for d in listed
                ],
            },
            audits=(
                AuditRecord("DIVISION_ADDED", dict.fromkeys(new), new, division_id=division.id),
            ),
            lines=(line,),
        )

    return save


def _doing(payload: dict[str, Any]) -> str:
    return f"Adding division **{payload['name']}** (Tier {payload['tier']})"


def _outcome(ctx: OutcomeContext) -> str:
    saved = next(
        (view.result for view in ctx.steps if view.name == SAVE and view.result is not None), {}
    )
    if saved.get("outcome") != "added":
        return DISCARDED
    listed = [
        Division(
            id=0, season_id=0, name=d["name"], mention_role_id=d["role_id"],
            tier=d["tier"],
        )
        for d in saved["divisions"]
    ]
    return (
        f"✅ Division **{saved['name']}** (Tier {saved['tier']}) added.\n"
        f"Role: <@&{saved['mention_role_id']}>\n\n" + format_division_list(listed)
    )


def division_add_change(
    *, seasons: "SeasonService", teams: "TeamService"
) -> ChangeType:
    """The `core.division_add` change type, for the builder to register: *seasons* is what the
    check and the save read and write the season's divisions through, *teams* what the save seeds
    the division's teams through."""
    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(SAVE),),
        steps={SAVE: Step(SAVE, StepKind.SAVE, _save(seasons, teams))},
        check=_check(seasons),
        key=lambda payload: f"{KIND}|{json.dumps(payload, sort_keys=True)}",
        doing=_doing,
        outcome=_outcome,
        lane=lambda _payload: LEAGUE_LANE,
        writes=("divisions", "team_instances", "team_seats"),
    )


__all__ = ["DISCARDED", "KIND", "NO_SETUP", "NOT_PLACEMENTS", "division_add_change"]
