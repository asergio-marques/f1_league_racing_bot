"""`core.round_add` and `core.round_import`: `/round add`, `/round add-bulk` and `/round add-xml`,
on the change queue.

Each is a change type of its own on the league lane, registered by the builder. One SAVE step adds
the round or rounds to their divisions of the season being set up, renumbers each division it
touches by the rounds' moments, and writes a `ROUND_ADDED` audit entry for each round (the
division it is on, the round replacing nothing) and the log channel's line, stamped with the
queue's clock, all on the save's own connection. No other division's rounds are touched, and none
is deleted. `/round add-bulk` and `/round add-xml` are one change type, as the paste and the XML
differ only in how they are read, and the whole import is one save: all of it lands or none.

**A fault in the save stops the lane**, as a failed job does, for the reason `/season setup`'s does
(`season_setup_change.py`): a league admin exists to discard the job and clear the stop.

**Every refusal is the check's**, judged at the ask and again as the change starts, in the league's
log channel either time, so that a round added or a division deleted meanwhile is caught as the
change starts, in the words it would have met at the ask. No stage is checked: before Placements
there is no division to add a round to, and the division is not found. A division, a round's format,
its track and its moment are read from what the member typed, in the order a manager has always met
them: no season being set up, the format, a track where the format needs one, the track, the
moment, the division, a round of the division already at that moment, and the calendar's room. An
import is refused whole, with every fault it holds listed.

**The calendar's room is asked by `calendar_round_refusal`,** for the rounds the division would
hold: one catch, a stated exception (architecture.md, "A catch-all error handler"), stands aside
where the guard itself cannot run, so that a round is refused for a real over-capacity alone.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

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
from leaguebot.core.models.division import Division
from leaguebot.core.models.round import Round, RoundFormat
from leaguebot.core.services import track_service
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.season_service import renumber_rounds_on
from leaguebot.core.utils.formatting import discord_ts, format_round_list
from leaguebot.core.utils.input_validator import parse_datetime
from leaguebot.core.utils.round_import import ParsedDivisionRounds, format_import_errors

if TYPE_CHECKING:
    from leaguebot.core.services.image_drawing import ImageDrawing
    from leaguebot.core.services.season_service import SeasonService

log = logging.getLogger(__name__)

ADD = "core.round_add"
IMPORT = "core.round_import"

#: The name of the step that inserts the rounds, renumbers their divisions and writes the audit
#: entries and the line.
SAVE = "save"

#: `/round add` as the line names it.
COMMAND = "/round add"

#: A round refused while no season is being set up, word for word.
NO_SETUP = "❌ No pending season setup. Run `/season setup` first."

#: The reply once a league admin has discarded the stopped save of `/round add`: nothing was added.
ADD_DISCARDED = "Nothing was added: no round was created. Run `/round add` again."

_FORMATS = ", ".join(fmt.value for fmt in RoundFormat)


async def calendar_round_refusal(drawing: "ImageDrawing", would_hold: int) -> str | None:
    """The refusal where a division holding *would_hold* rounds would outgrow the calendar
    template, in core's words around the image module's problem and remedy; None where the guard
    does not apply (the module off, the aspect off, no valid template) or there is room to spare.

    A guard that cannot run lets the round through, the error going to the host's log in full:
    only a genuine over-capacity refuses a round. Separate from the placement guard of seated
    drivers, which counts another thing at another command.
    """
    try:
        problem = await drawing.round_capacity_problem(would_hold)
    except Exception as exc:  # noqa: BLE001 — a stated exception, see the docstring
        log.error("round add: calendar capacity check could not run: %s", exc, exc_info=True)
        return None
    if problem is None:
        return None
    return f"❌ {problem.problem}. The round was **not** added.\n{problem.remedy}"


def round_models(rounds: list[dict[str, Any]]) -> list[Round]:
    """The rounds a result holds, as the list a reply formats."""
    return [
        Round(
            id=0, division_id=0, round_number=r["number"], format=RoundFormat(r["format"]),
            track_name=r["track"], scheduled_at=datetime.fromisoformat(r["at"]),
        )
        for r in rounds
    ]


def held_rounds(rounds: list[Round]) -> list[dict[str, Any]]:
    """The rounds a division holds, as a result keeps them."""
    return [
        {
            "number": r.round_number, "format": r.format.value, "track": r.track_name,
            "at": r.scheduled_at.isoformat(),
        }
        for r in rounds
    ]


def saved_result(ctx: OutcomeContext) -> dict[str, Any]:
    """What the save returned, or nothing where it did not finish."""
    return next(
        (view.result for view in ctx.steps if view.name == SAVE and view.result is not None), {}
    )


def _audit(placed: Round, division: Division) -> AuditRecord:
    """The audit entry of *placed*, a round added to *division*, as it stands once numbered."""
    new = {
        "round_id": placed.id,
        "division_id": division.id,
        "round_number": placed.round_number,
        "format": placed.format.value,
        "track_name": placed.track_name,
        "scheduled_at": placed.scheduled_at.isoformat(),
    }
    return AuditRecord("ROUND_ADDED", dict.fromkeys(new), new, division_id=division.id)


# ---------------------------------------------------------------------------
# /round add
# ---------------------------------------------------------------------------


def _add_check(
    seasons: "SeasonService", drawing: "ImageDrawing"
) -> Callable[[CheckContext], Awaitable[Verdict]]:
    async def check(ctx: CheckContext) -> Verdict:
        """The refusals, in the order they come, each read from the database."""
        payload = ctx.payload
        season = await seasons.get_setup_season()
        if season is None:
            return Verdict.refuse(NO_SETUP)

        try:
            fmt = RoundFormat(str(payload["format"]).upper())
        except ValueError:
            return Verdict.refuse(
                f"❌ Invalid format `{payload['format']}`. Choose from: {_FORMATS}."
            )
        track_name = str(payload["track"]).strip() or None
        if fmt is not RoundFormat.MYSTERY and not track_name:
            return Verdict.refuse(
                f"❌ A track is required for `{fmt.value}` rounds. "
                "Leave track blank only for `MYSTERY` rounds."
            )
        if track_name:
            async with get_connection(ctx.db_path) as db:
                resolved = await track_service.resolve_track_name(db, track_name)
            if resolved is None:
                return Verdict.refuse(
                    f"❌ Unknown track `{track_name}`.\n"
                    "Use `/round add` and type a number or name — autocomplete will guide you."
                )
        moment = parse_datetime(str(payload["scheduled_at"]))
        if moment is None:
            return Verdict.refuse("❌ Invalid datetime. Use ISO format: `YYYY-MM-DDTHH:MM:SS`")

        wanted = str(payload["division_name"]).lower()
        division = next(
            (d for d in await seasons.get_divisions(season.id) if d.name.lower() == wanted),
            None,
        )
        if division is None:
            return Verdict.refuse(
                f"❌ Division `{payload['division_name']}` not found in pending setup."
            )
        held = await seasons.get_division_rounds(division.id)

        # Two rounds of one division may not share a moment: the confirmation of placements
        # refuses a season that holds such a pair, and it is refused here instead, at the change
        # that would cause it, rather than at an approval days later.
        clash = next((r for r in held if r.scheduled_at == moment), None)
        if clash is not None:
            return Verdict.refuse(
                f"❌ **{division.name}** already holds round {clash.round_number} at "
                f"{discord_ts(moment)}. Two rounds of one division cannot share a moment "
                f"— the season could not be approved. The round was **not** added."
            )

        # Overflow is refused at the earliest moment it can be detected, which for rounds is the
        # change that would cause it, not a render days later.
        overflow = await calendar_round_refusal(drawing, len(held) + 1)
        if overflow is not None:
            return Verdict.refuse(overflow)
        return Verdict.go()

    return check


def _add_save(
    seasons: "SeasonService",
) -> Callable[[aiosqlite.Connection, StepContext], Awaitable[StepResult]]:
    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Insert the round, renumber its division, and write its audit entry and its line."""
        payload = ctx.payload
        season = await seasons.get_setup_season_on(db)
        if season is None:
            raise LookupError("no season is being set up")
        wanted = str(payload["division_name"]).lower()
        division = next(
            (d for d in await seasons.get_divisions_on(db, season.id) if d.name.lower() == wanted),
            None,
        )
        if division is None:
            raise LookupError(f"no division named {payload['division_name']} is being set up")
        fmt = RoundFormat(str(payload["format"]).upper())
        track_name = str(payload["track"]).strip() or None
        if track_name:
            track_name = await track_service.resolve_track_name(db, track_name)
        moment = parse_datetime(str(payload["scheduled_at"]))
        if moment is None:
            raise ValueError(f"not a moment: {payload['scheduled_at']}")

        round_id = await seasons.add_round_on(
            db, division.id, fmt=fmt, track_name=track_name, scheduled_at=moment
        )
        await renumber_rounds_on(db, division.id)
        rounds = await seasons.get_division_rounds_on(db, division.id)
        placed = next(r for r in rounds if r.id == round_id)
        line = (
            f"{ctx.named} | {COMMAND} | Success\n"
            f"  division: {division.name}\n"
            f"  round: {placed.round_number}, format: {fmt.value}\n"
            f"  track: {track_name or 'Mystery'}\n"
            f"  scheduled_at: {moment.isoformat()} UTC"
        )
        return StepResult(
            result={
                "outcome": "added",
                "division": division.name,
                "number": placed.round_number,
                "format": fmt.value,
                "track": track_name,
                "at": moment.isoformat(),
                "rounds": held_rounds(rounds),
            },
            audits=(_audit(placed, division),),
            lines=(line,),
        )

    return save


def _add_doing(payload: dict[str, Any]) -> str:
    moment = parse_datetime(str(payload["scheduled_at"]))
    at = discord_ts(moment) if moment is not None else payload["scheduled_at"]
    return f"Adding a round to **{payload['division_name']}** at {at}"


def _add_outcome(ctx: OutcomeContext) -> str:
    saved = saved_result(ctx)
    if saved.get("outcome") != "added":
        return ADD_DISCARDED
    return (
        f"✅ Round **{saved['number']}** added to **{saved['division']}**.\n"
        f"Format: {saved['format']} | Track: {saved['track'] or 'Mystery'} | "
        f"{discord_ts(datetime.fromisoformat(saved['at']))}\n\n"
        + format_round_list(round_models(saved["rounds"]))
    )


def round_add_change(*, seasons: "SeasonService", drawing: "ImageDrawing") -> ChangeType:
    """The `core.round_add` change type, for the builder to register: *seasons* is what the check
    reads the season being set up, its divisions and their rounds through, and the save writes
    the round and renumbers its division through; *drawing* is the image module's, which the
    check asks whether the calendar would hold the round."""
    return ChangeType(
        kind=ADD,
        opening=(PlannedStep(SAVE),),
        steps={SAVE: Step(SAVE, StepKind.SAVE, _add_save(seasons))},
        check=_add_check(seasons, drawing),
        key=lambda payload: f"{ADD}|{json.dumps(payload, sort_keys=True)}",
        doing=_add_doing,
        outcome=_add_outcome,
        lane=lambda _payload: LEAGUE_LANE,
        writes=("rounds",),
    )


def add_payload(
    division_name: str, format: str, scheduled_at: str, track: str
) -> dict[str, Any]:
    """The request `/round add` makes, as the member typed it: the check reads each part."""
    return {
        "division_name": division_name,
        "format": format,
        "scheduled_at": scheduled_at,
        "track": track,
    }


# ---------------------------------------------------------------------------
# /round add-bulk and /round add-xml
# ---------------------------------------------------------------------------


def import_payload(source: str, parsed: list[ParsedDivisionRounds]) -> dict[str, Any]:
    """The request an import form makes: *source* is the command it came from, *parsed* the
    divisions and rounds it named, each round with where in the paste it stood."""
    return {
        "source": source,
        "divisions": [
            {
                "name": entry.division_name.strip(),
                "rounds": [
                    {
                        "location": r.location,
                        "scheduled_at": r.scheduled_at.isoformat(),
                        "format": r.format.value,
                        "track": r.track_raw,
                    }
                    for r in entry.rounds
                ],
            }
            for entry in parsed
        ],
    }


def _duplicate_datetime_errors(
    existing: list[datetime], incoming: list[dict[str, Any]]
) -> list[str]:
    """Every incoming round whose datetime is already taken, within the batch or before it.

    The confirmation of placements refuses a season holding two rounds of one division at the same
    moment, and it does so long after the calendar has been built and possibly posted. A repeated
    line is the likeliest fault in a pasted calendar, so it is caught here instead, at the first
    moment the whole batch can be seen.
    """
    errors: list[str] = []
    seen: dict[datetime, str] = {at: "a round already in this division" for at in existing}
    for parsed in incoming:
        at = datetime.fromisoformat(parsed["scheduled_at"])
        previous = seen.get(at)
        if previous is not None:
            errors.append(
                f"{parsed['location']}: duplicate datetime {at.isoformat()} — already used "
                f"by {previous}."
            )
            continue
        seen[at] = parsed["location"]
    return errors


def _import_check(
    seasons: "SeasonService", drawing: "ImageDrawing"
) -> Callable[[CheckContext], Awaitable[Verdict]]:
    async def check(ctx: CheckContext) -> Verdict:
        """The import, or every fault it holds: each division validated in full, so that a manager
        fixes the paste and submits the same text again rather than working out which half of it
        landed.

        Rounds are *merged* with those a division already holds, never substituted for them:
        these are add commands, and a calendar too long for one modal is imported in two passes.
        """
        season = await seasons.get_setup_season()
        if season is None:
            return Verdict.refuse(NO_SETUP)
        divisions = await seasons.get_divisions(season.id)

        errors: list[str] = []
        seen: set[str] = set()
        # One connection for every track in the payload, and for every division's rounds.
        async with get_connection(ctx.db_path) as db:
            for entry in ctx.payload["divisions"]:
                wanted = str(entry["name"]).strip().casefold()
                if wanted in seen:
                    errors.append(f"[{entry['name']}]: named more than once in this import.")
                    continue
                seen.add(wanted)

                division = next(
                    (d for d in divisions if d.name.strip().casefold() == wanted), None
                )
                if division is None:
                    errors.append(
                        f"[{entry['name']}]: no division of that name in the pending setup."
                    )
                    continue
                held = await seasons.get_division_rounds_on(db, division.id)

                added = 0
                for parsed in entry["rounds"]:
                    if parsed["track"]:
                        resolved = await track_service.resolve_track_name(db, parsed["track"])
                        if resolved is None:
                            errors.append(
                                f"{parsed['location']}: unknown track `{parsed['track']}`."
                            )
                            continue
                    added += 1

                errors += _duplicate_datetime_errors(
                    [r.scheduled_at for r in held], entry["rounds"]
                )

                # Last, because it loads and parses the calendar template. Asked of the whole
                # batch: each round of a dozen can clear a `+ 1` check while the dozen together
                # outgrow the template.
                overflow = await calendar_round_refusal(drawing, len(held) + added)
                if overflow is not None:
                    errors.append(f"[{division.name}]: {overflow}")

        if errors:
            return Verdict.refuse(format_import_errors(errors), "\n".join(errors))
        return Verdict.go()

    return check


def _import_save(
    seasons: "SeasonService",
) -> Callable[[aiosqlite.Connection, StepContext], Awaitable[StepResult]]:
    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Insert every round of the import, renumber each division it touches, and write an audit
        entry for each round and the line, one save."""
        season = await seasons.get_setup_season_on(db)
        if season is None:
            raise LookupError("no season is being set up")
        divisions = await seasons.get_divisions_on(db, season.id)

        audits: list[AuditRecord] = []
        held: list[dict[str, Any]] = []
        for entry in ctx.payload["divisions"]:
            wanted = str(entry["name"]).strip().casefold()
            division = next((d for d in divisions if d.name.strip().casefold() == wanted), None)
            if division is None:
                raise LookupError(f"no division named {entry['name']} is being set up")
            added: list[int] = []
            for parsed in entry["rounds"]:
                track_name = None
                if parsed["track"]:
                    track_name = await track_service.resolve_track_name(db, parsed["track"])
                added.append(
                    await seasons.add_round_on(
                        db,
                        division.id,
                        fmt=RoundFormat(parsed["format"]),
                        track_name=track_name,
                        scheduled_at=datetime.fromisoformat(parsed["scheduled_at"]),
                    )
                )
            await renumber_rounds_on(db, division.id)
            rounds = await seasons.get_division_rounds_on(db, division.id)
            audits += [_audit(r, division) for r in rounds if r.id in added]
            held.append({"division": division.name, "rounds": held_rounds(rounds)})

        source = str(ctx.payload["source"])
        line = f"{ctx.named} | {source} | Success\n" + "\n".join(
            f"  division: {d['division']}, now holds {len(d['rounds'])} round(s)" for d in held
        )
        return StepResult(
            result={"outcome": "added", "divisions": held},
            audits=tuple(audits),
            lines=(line,),
        )

    return save


def _import_doing(payload: dict[str, Any]) -> str:
    count = sum(len(entry["rounds"]) for entry in payload["divisions"])
    names = [f"**{entry['name']}**" for entry in payload["divisions"]]
    named = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
    return f"Adding {count} round{'' if count == 1 else 's'} to {named}"


def _import_outcome(ctx: OutcomeContext) -> str:
    saved = saved_result(ctx)
    if saved.get("outcome") != "added":
        return (
            "Nothing was added: no round was imported. "
            f"Run `{ctx.payload['source']}` again."
        )
    divisions = saved["divisions"]
    lines = [f"✅ Added rounds to {len(divisions)} division(s)."]
    for d in divisions:
        lines.append(f"  **{d['division']}**: {len(d['rounds'])} round(s) in total")
    if len(divisions) == 1:
        lines.append("")
        lines.append(format_round_list(round_models(divisions[0]["rounds"])))
    return "\n".join(lines)


def round_import_change(*, seasons: "SeasonService", drawing: "ImageDrawing") -> ChangeType:
    """The `core.round_import` change type, for the builder to register: *seasons* is what the
    check reads the season being set up, its divisions and their rounds through, and the save
    writes the rounds and renumbers their divisions through; *drawing* is the image module's,
    which the check asks whether the calendar would hold the rounds."""
    return ChangeType(
        kind=IMPORT,
        opening=(PlannedStep(SAVE),),
        steps={SAVE: Step(SAVE, StepKind.SAVE, _import_save(seasons))},
        check=_import_check(seasons, drawing),
        key=lambda payload: f"{IMPORT}|{json.dumps(payload, sort_keys=True)}",
        doing=_import_doing,
        outcome=_import_outcome,
        lane=lambda _payload: LEAGUE_LANE,
        writes=("rounds",),
    )


__all__ = [
    "ADD",
    "ADD_DISCARDED",
    "IMPORT",
    "NO_SETUP",
    "add_payload",
    "calendar_round_refusal",
    "held_rounds",
    "import_payload",
    "round_add_change",
    "round_import_change",
    "round_models",
    "saved_result",
]
