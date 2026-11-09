"""`core.round_amend`: `/round amend` of a round of the season being set up, on the change queue.

A change type of its own on the league lane, registered by the builder, beside the amendment of a
round of a season being raced (`round_amend_change`), which it shares nothing with: a setup round
has no timed work, no results and no confirmation. One SAVE step changes the round's track, moment
or format, clearing the track of a mystery round, renumbers its division by moment, and writes a
`ROUND_AMENDED` audit entry (the round as it stood and as it stands) and the log channel's line,
stamped with the queue's clock, all on the save's own connection. No other division's rounds are
touched, and a round given the values it already holds is answered as such, recording that nothing
was changed and writing no audit entry. An amendment adds no round, so the calendar's room is not
asked.

**A fault in the save stops the lane**, as a failed job does, for the reason `/season setup`'s does
(`season_setup_change.py`): a league admin exists to discard the job and clear the stop.

**The round is judged by its id.** The division and the round are read from what the member typed
as the change is asked for (`ChangeType.fill`), and judged by their ids from then on, so that a
round deleted meanwhile, which renumbers its division, is refused as it starts and the round that
now bears its number is never amended in its place. Every refusal is the check's, judged at the ask
and again as the change starts, in the league's log channel either time: no season being set up,
the division, the round, the track, the moment, the format, a mystery round needing no track, and
a moment another round of the division already holds (the round's own is not a clash).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    AuditRecord,
    LEAGUE_LANE,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.round import Round, RoundFormat
from leaguebot.core.services import track_service
from leaguebot.core.services.amendment_rules_service import amendment_changes_nothing
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.round_add_changes import (
    NO_SETUP,
    held_rounds,
    round_models,
    saved_result,
)
from leaguebot.core.services.round_amend_change import NOTHING_AMENDED
from leaguebot.core.services.season_service import SeasonService, renumber_rounds_on
from leaguebot.core.utils.formatting import discord_ts, format_round_list
from leaguebot.core.utils.input_validator import parse_datetime

ROUND_AMEND_SETUP = "core.round_amend"

#: The name of the step that changes the round, renumbers its division and writes the audit entry
#: and the line.
SAVE = "save"

#: `/round amend` of a setup round as the line names it.
COMMAND = "/round amend (pending)"

#: A field of a round as the command names its parameter, where its column is named otherwise.
_PARAMETER_OF = {"track_name": "track"}


def amend_payload(
    division_name: str, round_number: int, track: str, scheduled_at: str, format: str
) -> dict[str, Any]:
    """The request `/round amend` makes, as the member typed it: the check reads each part."""
    return {
        "division_name": division_name,
        "round_number": round_number,
        "track": track,
        "scheduled_at": scheduled_at,
        "format": format,
    }


def _field_text(value: object) -> str:
    """A round's field as a log line states it."""
    if value is None:
        return "none"
    return str(getattr(value, "value", value))


def _changed_values(before: dict[str, Any], after: dict[str, Any]) -> str:
    """Each field of *before* whose value *after* differs, from what to what, one per line,
    named as the command's parameter is, not as its column."""
    return "".join(
        f"\n  {_PARAMETER_OF.get(field, field)}: {_field_text(old)} → "
        f"{_field_text(after[field])}"
        for field, old in before.items()
        if after[field] != old
    )


def _standing(rnd: Round) -> dict[str, Any]:
    """The values of *rnd* an amendment may change, as the rules compare them."""
    return {"format": rnd.format, "scheduled_at": rnd.scheduled_at, "track_name": rnd.track_name}


async def _asked(
    db: aiosqlite.Connection, payload: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """The values asked for, by field, each as the round holds it, in the order the member meets
    the refusals; or the reply refusing what was typed, in which case the values are empty."""
    asked: dict[str, Any] = {}
    if payload["track"]:
        resolved = await track_service.resolve_track_name(db, payload["track"])
        if resolved is None:
            return {}, (
                f"❌ Unknown track `{payload['track']}`. Use autocomplete to pick a valid track."
            )
        asked["track_name"] = resolved
    if payload["scheduled_at"]:
        moment = parse_datetime(payload["scheduled_at"])
        if moment is None:
            return {}, "❌ Invalid datetime. Use `YYYY-MM-DDTHH:MM:SS`."
        asked["scheduled_at"] = moment
    if payload["format"]:
        try:
            asked["format"] = RoundFormat(str(payload["format"]).upper())
        except ValueError:
            return {}, (
                f"❌ Invalid format `{payload['format']}`. "
                "Use NORMAL, SPRINT, MYSTERY, or ENDURANCE."
            )
    return asked, None


def _values(rnd: Round) -> dict[str, Any]:
    """*rnd* as the audit entry holds it."""
    return {
        "round_id": rnd.id,
        "division_id": rnd.division_id,
        "round_number": rnd.round_number,
        "format": rnd.format.value,
        "track_name": rnd.track_name,
        "scheduled_at": rnd.scheduled_at.isoformat(),
    }


def _fill(seasons: SeasonService) -> Callable[[CheckContext], Awaitable[None]]:
    async def fill(ctx: CheckContext) -> None:
        """Name the division and the round the member typed by their ids, and the division by its
        own spelling, leaving each None where it is not found."""
        payload = ctx.payload
        payload["division"] = payload["division_name"]
        payload["division_id"] = payload["round_id"] = None
        season = await seasons.get_setup_season()
        if season is None:
            return
        wanted = str(payload["division_name"]).lower()
        division = next(
            (d for d in await seasons.get_divisions(season.id) if d.name.lower() == wanted), None
        )
        if division is None:
            return
        payload["division"] = division.name
        payload["division_id"] = division.id
        rnd = next(
            (
                r
                for r in await seasons.get_division_rounds(division.id)
                if r.round_number == payload["round_number"]
            ),
            None,
        )
        payload["round_id"] = rnd.id if rnd is not None else None

    return fill


def _check(seasons: SeasonService) -> Callable[[CheckContext], Awaitable[Verdict]]:
    async def check(ctx: CheckContext) -> Verdict:
        """The refusals, in the order they come, each read from the database."""
        payload = ctx.payload
        season = await seasons.get_setup_season()
        if season is None:
            return Verdict.refuse(NO_SETUP)
        division = next(
            (d for d in await seasons.get_divisions(season.id) if d.id == payload["division_id"]),
            None,
        )
        if division is None:
            return Verdict.refuse(
                f"❌ Division `{payload['division_name']}` not found in pending setup."
            )
        held = await seasons.get_division_rounds(division.id)
        rnd = next((r for r in held if r.id == payload["round_id"]), None)
        if rnd is None:
            return Verdict.refuse(
                f"❌ Round {payload['round_number']} not found in division "
                f"`{payload['division_name']}` of the pending setup."
            )

        async with get_connection(ctx.db_path) as db:
            asked, refusal = await _asked(db, payload)
        if refusal is not None:
            return Verdict.refuse(refusal)

        fmt = asked.get("format", rnd.format)
        if fmt is not RoundFormat.MYSTERY and not asked.get("track_name", rnd.track_name):
            return Verdict.refuse(
                f"❌ Format `{fmt.value}` requires a track. "
                "Supply a `track` value or change format to MYSTERY."
            )

        # Two rounds of one division may not share a moment: the confirmation of placements
        # refuses a season that holds such a pair, and it is refused here instead, at the change
        # that would cause it. The round's own moment, restated, is no clash.
        moment = asked.get("scheduled_at")
        if moment is not None:
            clash = next((r for r in held if r.id != rnd.id and r.scheduled_at == moment), None)
            if clash is not None:
                return Verdict.refuse(
                    f"❌ **{division.name}** already holds round {clash.round_number} at "
                    f"{discord_ts(moment)}. Two rounds of one division cannot share a moment "
                    "— the season could not be approved. The round was **not** amended."
                )
        return Verdict.go()

    return check


def _save(
    seasons: SeasonService,
) -> Callable[[aiosqlite.Connection, StepContext], Awaitable[StepResult]]:
    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Change the round, renumber its division, and write its audit entry and its line; or,
        where it already holds what was asked, record that nothing was changed."""
        payload = ctx.payload
        season = await seasons.get_setup_season_on(db)
        if season is None:
            raise LookupError("no season is being set up")
        division = next(
            (d for d in await seasons.get_divisions_on(db, season.id)
             if d.id == payload["division_id"]),
            None,
        )
        if division is None:
            raise LookupError(f"no division named {payload['division_name']} is being set up")
        rnd = next(
            (r for r in await seasons.get_division_rounds_on(db, division.id)
             if r.id == payload["round_id"]),
            None,
        )
        if rnd is None:
            raise LookupError(f"round {payload['round_number']} is not in {division.name}")
        asked, refusal = await _asked(db, payload)
        if refusal is not None:
            raise ValueError(refusal)

        before = _standing(rnd)
        if amendment_changes_nothing(before, asked):
            line = (
                f"{ctx.named} | {COMMAND} | Nothing changed\n"
                f"  division: {division.name}\n"
                f"  round: {rnd.round_number}\n"
                "  reason: the values given are the ones it holds"
            )
            return StepResult(
                result={
                    "outcome": "unchanged", "division": division.name, "number": rnd.round_number,
                },
                lines=(line,),
            )

        after = {**before, **asked}
        if after["format"] is RoundFormat.MYSTERY:
            after["track_name"] = None
        await db.execute(
            "UPDATE rounds SET format = ?, track_name = ?, scheduled_at = ? WHERE id = ?",
            (
                after["format"].value, after["track_name"], after["scheduled_at"].isoformat(),
                rnd.id,
            ),
        )
        await renumber_rounds_on(db, division.id)
        rounds = await seasons.get_division_rounds_on(db, division.id)
        placed = next(r for r in rounds if r.id == rnd.id)
        line = (
            f"{ctx.named} | {COMMAND} | Success\n"
            f"  division: {division.name}\n"
            f"  round: {placed.round_number}" + _changed_values(before, _standing(placed))
        )
        return StepResult(
            result={
                "outcome": "amended",
                "division": division.name,
                "number": placed.round_number,
                "rounds": held_rounds(rounds),
            },
            audits=(
                AuditRecord(
                    "ROUND_AMENDED", _values(rnd), _values(placed), division_id=division.id
                ),
            ),
            lines=(line,),
        )

    return save


def _doing(payload: dict[str, Any]) -> str:
    return f"Amending round {payload['round_number']} in **{payload['division']}**"


def _outcome(ctx: OutcomeContext) -> str:
    saved = saved_result(ctx)
    match saved.get("outcome"):
        case "amended":
            return (
                f"✅ Round {saved['number']} in **{saved['division']}** updated and saved to the "
                "season being set up. Its sessions are created when placements are confirmed "
                "from `/season placements-review`.\n\n"
                + format_round_list(round_models(saved["rounds"]))
            )
        case "unchanged":
            return (
                f"ℹ️ Round {saved['number']} in **{saved['division']}** already holds those "
                "values. Nothing was changed."
            )
        case _:
            return NOTHING_AMENDED.format(
                number=ctx.payload["round_number"], division=ctx.payload["division"]
            )


def setup_round_amend_change(*, seasons: SeasonService) -> ChangeType:
    """The `core.round_amend` change type, for the builder to register: *seasons* is what the
    fill and the check read the season being set up, its divisions and their rounds through, and
    the save changes the round and renumbers its division through."""
    return ChangeType(
        kind=ROUND_AMEND_SETUP,
        opening=(PlannedStep(SAVE),),
        steps={SAVE: Step(SAVE, StepKind.SAVE, _save(seasons))},
        check=_check(seasons),
        key=lambda payload: f"{ROUND_AMEND_SETUP}|{json.dumps(payload, sort_keys=True)}",
        doing=_doing,
        outcome=_outcome,
        fill=_fill(seasons),
        lane=lambda _payload: LEAGUE_LANE,
        writes=("rounds",),
    )


__all__ = ["ROUND_AMEND_SETUP", "amend_payload", "setup_round_amend_change"]
