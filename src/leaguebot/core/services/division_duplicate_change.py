"""`core.division_duplicate`: `/division duplicate`, on the change queue.

One SAVE step inserts the copy into the season being set up, copies each round of the source into
it with its moment moved by the offset and renumbers the copies, seeds its teams from the league's
list (the Reserve team included), and writes its `DIVISION_DUPLICATED` audit entry and the log
channel's line, stamped with the queue's clock, all on the save's own connection, so that no
division is ever left with its rounds and no teams. The copy inherits none of the source's
channels.

**A fault in the save stops the lane**, as a failed job does, for the reason `/season setup`'s does
(`season_setup_change.py`): a league admin exists to discard the job and clear the stop.

**The source is judged by its id.** It is read from the name the member typed as the change is
asked for (`ChangeType.fill`) and named as it is stored from then on, so that a division deleted
meanwhile is refused as it starts and a division given its name since is never copied in its
place. Every refusal is the check's, judged at the ask and again as the change starts, in the
league's log channel either time, in the order a manager has always met them: the season not in
Placements, a tier below 1, the source not found, a name the league's rules refuse, a name the
season holds already and a tier it holds already. A name or a tier taken by a copy asked just
before is thus refused as it starts, in the words it would have met at the ask, rather than
reaching the schema's rule that holds each once per season (`001_baseline.sql`).

**The warnings are the reply's, never a refusal:** a shifted moment in the past, judged against the
queue's clock as the copy is saved, and rounds shifted onto one moment, each given once.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
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
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.division_edit_changes import (
    division_by_name,
    held_division,
    listed,
    listing,
)
from leaguebot.core.services.round_add_changes import saved_result
from leaguebot.core.services.season_service import validate_division_name, validate_division_tier
from leaguebot.core.utils.formatting import discord_ts, format_round_list

if TYPE_CHECKING:
    from leaguebot.core.services.season_service import SeasonService
    from leaguebot.core.services.team_service import TeamService

KIND = "core.division_duplicate"

#: The name of the step that copies the division, seeds its teams and writes its audit entry and
#: its line.
SAVE = "save"

#: The command as the line names it.
COMMAND = "/division duplicate"

#: The reply once a league admin has discarded the stopped save: nothing was duplicated.
DISCARDED = "Nothing was duplicated: no division was created. Run `/division duplicate` again."


def _offset(payload: dict[str, Any]) -> str:
    """The offset as the reply and the line give it."""
    return f"{payload['day_offset']:+}d {payload['hour_offset']:+}h"


def _check(seasons: "SeasonService") -> Callable[[CheckContext], Awaitable[Verdict]]:
    async def check(ctx: CheckContext) -> Verdict:
        """The refusals, in the order they come, each read from the database."""
        payload = ctx.payload
        season = await seasons.get_setup_season()
        if season is None or season.stage is not SeasonStage.PLACEMENTS:
            return Verdict.refuse(
                f"❌ `{COMMAND}` can only be used while the season is in placements."
            )
        tier, new_name = int(payload["tier"]), str(payload["new_name"])
        refusal = validate_division_tier(tier, [], within="season")
        if refusal is not None:
            return Verdict.refuse(f"⛔ {refusal}")
        held = await seasons.get_divisions(season.id)
        if all(d.id != payload["division_id"] for d in held):
            return Verdict.refuse(f"❌ Division `{payload['division']}` not found in pending setup.")
        refusal = validate_division_name(new_name)
        if refusal is not None:
            return Verdict.refuse(f"❌ {refusal}")
        if any(d.name.lower() == new_name.lower() for d in held):
            return Verdict.refuse(f"❌ A division named **{new_name}** already exists.")
        refusal = validate_division_tier(tier, [d.tier for d in held], within="season")
        if refusal is not None:
            return Verdict.refuse(f"⛔ {refusal}")
        return Verdict.go()

    return check


def _warnings(moments: list[datetime], now: datetime) -> list[str]:
    """The warnings the copy's *moments* earn, judged against *now*, each given once."""

    def aware(moment: datetime) -> datetime:
        return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment

    warnings: list[str] = []
    for moment in moments:
        if aware(moment) < now:
            warnings.append(
                f"⚠️ One or more shifted datetimes are in the past: {discord_ts(aware(moment))}"
            )
            break
    for moment, count in Counter(moments).items():
        if count > 1:
            warnings.append(
                f"⚠️ Multiple rounds share the same shifted datetime: {discord_ts(aware(moment))}"
            )
            break
    return warnings


def _save(
    seasons: "SeasonService", teams: "TeamService", now: Callable[[], datetime]
) -> Callable[[aiosqlite.Connection, StepContext], Awaitable[StepResult]]:
    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Copy the division, seed its teams, and write its audit entry and its line, one save."""
        payload = ctx.payload
        source = await held_division(seasons, db, payload)
        division = await seasons.duplicate_division_on(
            db, source.id, name=str(payload["new_name"]), role_id=int(payload["role_id"]),
            tier=int(payload["tier"]),
            offset=timedelta(days=payload["day_offset"], hours=payload["hour_offset"]),
        )
        await teams.seed_division_teams_on(db, division.id)
        rounds = await seasons.get_division_rounds_on(db, division.id)
        new = {
            "division_id": division.id,
            "season_id": division.season_id,
            "name": division.name,
            "tier": division.tier,
            "mention_role_id": division.mention_role_id,
            "source_division_id": source.id,
            "day_offset": payload["day_offset"],
            "hour_offset": payload["hour_offset"],
        }
        offset = _offset(payload)
        line = (
            f"{ctx.named} | {COMMAND} | Success\n"
            f"  source: {source.name}\n"
            f"  new_division: {division.name}, tier: {division.tier}\n"
            f"  offset: {offset}"
        )
        return StepResult(
            result={
                "outcome": "duplicated",
                "name": division.name,
                "tier": division.tier,
                "source": source.name,
                "offset": offset,
                "divisions": listing(await seasons.get_divisions_on(db, division.season_id)),
                "rounds": format_round_list(rounds),
                "warnings": _warnings([r.scheduled_at for r in rounds], now()),
            },
            audits=(
                AuditRecord(
                    "DIVISION_DUPLICATED", dict.fromkeys(new), new, division_id=division.id
                ),
            ),
            lines=(line,),
        )

    return save


def _doing(payload: dict[str, Any]) -> str:
    return (
        f"Duplicating division **{payload['division']}** as **{payload['new_name']}** "
        f"(Tier {payload['tier']})"
    )


def _outcome(ctx: OutcomeContext) -> str:
    saved = saved_result(ctx)
    if saved.get("outcome") != "duplicated":
        return DISCARDED
    warned = "".join(f"\n{warning}" for warning in saved["warnings"])
    return (
        f"✅ Division **{saved['name']}** (Tier {saved['tier']}) created from "
        f"**{saved['source']}** (offset: {saved['offset']}).\n\n"
        + listed(saved)
        + f"\n\n**{saved['name']} rounds:**\n{saved['rounds']}"
        + warned
    )


def division_duplicate_change(
    *, seasons: "SeasonService", teams: "TeamService", now: Callable[[], datetime]
) -> ChangeType:
    """The `core.division_duplicate` change type, for the builder to register: *seasons* is what
    the fill, the check and the save read and write the season's divisions and rounds through,
    *teams* what the save seeds the copy's teams through, and *now* the queue's clock, which the
    warnings are judged against."""

    async def fill(ctx: CheckContext) -> None:
        await division_by_name(seasons, ctx.payload, str(ctx.payload["source_name"]))

    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(SAVE),),
        steps={SAVE: Step(SAVE, StepKind.SAVE, _save(seasons, teams, now))},
        check=_check(seasons),
        key=lambda payload: f"{KIND}|{json.dumps(payload, sort_keys=True)}",
        doing=_doing,
        outcome=_outcome,
        fill=fill,
        lane=lambda _payload: LEAGUE_LANE,
        writes=("divisions", "rounds", "team_instances", "team_seats", "default_teams"),
    )


__all__ = ["DISCARDED", "KIND", "division_duplicate_change"]
