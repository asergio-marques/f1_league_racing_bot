"""paste_reading.py — Reading and validating a pasted results block: each row of a
qualifying or race paste, the fastest-lap override line, and the checks against the
division's drivers, teams and accounts."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, NamedTuple, TypeVar

from leaguebot.core.db.database import get_connection
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.models.session_result import OutcomeModifier
from leaguebot.core.services.drivers.team_service import resolve_team_reference
from leaguebot.core.utils.input_validator import (
    USER_MENTION,
    parse_gap,
    parse_lap_gap,
    parse_role_mention,
    parse_time,
    parse_user_mention,
)
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.image.utils.tyre_compound import (
    canonicalise_tyre,
    records_no_tyre,
    tyre_compound_list,
)

if TYPE_CHECKING:
    from leaguebot.results.services.result_submission_service import CollectedSession


def _placeholders(items) -> str:
    """``?, ?, ?`` for binding *items* into an ``IN (...)`` clause."""
    return ", ".join("?" for _ in items)


# ---------------------------------------------------------------------------
# Validation — regex constants
# ---------------------------------------------------------------------------

# Times, gaps and lap gaps are read by the shared parsers (#362): one strict form across the
# bot, the one this paste always asked for.

# Optional fastest-lap override header:  FL: <@123>  (case-insensitive). The mention is the
# shared one (#362), so the header names a driver exactly as a row of the paste does.
_FL_OVERRIDE_RE = re.compile(rf"^FL:\s*{USER_MENTION}\s*$", re.IGNORECASE)


_OUTCOME_LITERALS: frozenset[str] = frozenset({"DNS", "DNF", "DSQ"})


def _parse_outcome(time_field: str) -> OutcomeModifier:
    """Infer OutcomeModifier from a race/qualifying time field value."""
    val = time_field.strip().upper()
    if val == "DNS":
        return OutcomeModifier.DNS
    if val == "DNF":
        return OutcomeModifier.DNF
    if val == "DSQ":
        return OutcomeModifier.DSQ
    return OutcomeModifier.CLASSIFIED


# ---------------------------------------------------------------------------
# Validation — parsed row types
# ---------------------------------------------------------------------------

@dataclass
class ParsedQualifyingRow:
    position: int
    driver_user_id: int
    #: The role the team column mentions, where it mentions one — which is refused, a team
    #: being typed by its shorthand (#381). None where the column holds anything else.
    team_role_id: int | None
    #: The canonical compound, or None where the submission recorded none (v7.8.0).
    tyre: str | None
    best_lap: str           # time string or DNS/DNF/DSQ (in-game result)
    gap: str                # delta string or "N/A"
    outcome: OutcomeModifier  # derived from best_lap
    #: The division's team *team_typed* names, set by ``validate_submission_block``. The
    #: shorthand is only what was typed; the team is what is stored (#375, #381).
    team_instance_id: int | None = None
    #: The team column as typed: the team's shorthand (#381). Echoed in a refusal.
    team_typed: str = ""


@dataclass
class ParsedRaceRow:
    position: int
    driver_user_id: int
    #: As on ParsedQualifyingRow.
    team_role_id: int | None
    total_time: str         # absolute time, delta, lap-gap, or outcome literal
    fastest_lap: str        # time string or "N/A"
    ingame_penalties: str   # time string (e.g. "5.000") or "N/A"
    outcome: OutcomeModifier  # derived from total_time
    # No post-race or appeal sanction: those are decided in the review stages, never pasted —
    # an amendment included, since #345 withdrew the two columns it used to take.
    #: As on ParsedQualifyingRow: the team the typed shorthand resolved to (#375, #381).
    team_instance_id: int | None = None
    #: As on ParsedQualifyingRow.
    team_typed: str = ""


# ---------------------------------------------------------------------------
# Validation — per-row functions
# ---------------------------------------------------------------------------

def _tyre_error(tyre: str) -> str | None:
    """The error a tyre field earns, or None where it is acceptable.

    The five compounds are a closed set the module defines (Constitution XIV.13, v7.8.0),
    so a submission naming something else names nothing the bot can draw, and is refused
    here rather than resolved to a placeholder six steps later. What is stored is the
    canonical spelling from :func:`canonicalise_tyre`, which is what lets the qualifying
    graphic find its file on every row without the league supplying any tyre artwork.

    An **empty** field is not an error and is stored as null: the submission of a session
    does not oblige a compound, and its absence is a state the graphic depicts rather than
    a gap it reports (XIV.13's per-field absent-datum declaration). `N/A` says the same
    thing and is accepted for it.
    """
    if records_no_tyre(tyre) or canonicalise_tyre(tyre) is not None:
        return None
    return f"Tyre must be one of {tyre_compound_list()}, got `{tyre}`"


#: What to tell somebody who pasted the amendment's old eight-column format (#345).
#:
#: Amending a round used to take two extra columns carrying the post-race and appeal penalties,
#: so that a re-inserted classification kept the sanctions already applied. The replay decides
#: those in its report and appeal stages instead, where they keep their justification and their
#: author — and where a disqualification can carry the mark that only a verdict record produces.
#: Pasting them now would apply each sanction twice, the stages adding to columns the paste had
#: already filled, so the format is refused rather than ignored.
_RETIRED_SANCTION_COLUMNS = (
    "Amending a round now takes the **same format as a first submission** — without the "
    "post-race and appeal penalty columns. Sanctions are no longer pasted: the amendment "
    "replays the round's report and appeal stages, where you keep or change each one with "
    "its justification intact."
)


def _field_count_error(parts: list[str], expected: int, line: str) -> str:
    """The error for a row of the wrong width, naming the retired columns where that is why."""
    got = len(parts)
    message = f"Expected {expected} comma-separated fields, got {got}: `{line.strip()}`"
    if got == expected + 2:
        message += f"\n{_RETIRED_SANCTION_COLUMNS}"
    return message


def _validate_qualifying_row_wizard(line: str) -> ParsedQualifyingRow | str:
    """Parse and validate a single qualifying-result line for the main submission wizard (6 fields).

    Fields: Position, Driver mention, Team (its shorthand), Tyre, Best Lap, Gap
    Postrace and appeal penalty fields default to N/A; outcome is derived from Best Lap.
    """
    parts = [p.strip() for p in line.strip().split(",")]
    if len(parts) != 6:
        return _field_count_error(parts, 6, line)

    pos_str, driver_str, team_str, tyre, best_lap, gap = parts

    if not pos_str.isdigit():
        return f"Position must be a positive integer, got `{pos_str}`"
    position = int(pos_str)

    driver_user_id = parse_user_mention(driver_str)
    if driver_user_id is None:
        return f"Driver must be a Discord member mention (<@user_id>), got `{driver_str}`"

    # A team is typed by its shorthand (#381), and which team a text names is the division's
    # business, settled by `validate_submission_block`. Only an empty field is refused here.
    if not team_str:
        return "Team must be named, by its shorthand."
    team_role_id = parse_role_mention(team_str)

    tyre_error = _tyre_error(tyre)
    if tyre_error is not None:
        return tyre_error

    best_lap_upper = best_lap.upper()
    if best_lap_upper not in _OUTCOME_LITERALS and parse_time(best_lap) is None:
        return f"Best Lap must be a time (e.g. 1:23.456) or DNS/DNF/DSQ, got `{best_lap}`"

    if position != 1:
        gap_upper = gap.upper()
        if (
            gap_upper != "N/A"
            and parse_gap(gap) is None
            and parse_time(gap) is None
        ):
            return f"Gap must be a delta time (e.g. +1:23.456), an absolute time, or N/A, got `{gap}`"

    outcome = _parse_outcome(best_lap)
    return ParsedQualifyingRow(
        position=position,
        driver_user_id=driver_user_id,
        team_role_id=team_role_id,
        team_typed=team_str,
        tyre=canonicalise_tyre(tyre),
        best_lap=best_lap,
        gap=gap,
        outcome=outcome,
    )


def _validate_race_row_wizard(line: str, is_first: bool) -> ParsedRaceRow | str:
    """Parse and validate a single race-result line for the main submission wizard (6 fields).

    Fields: Position, Driver mention, Team (its shorthand), Total Time, Fastest Lap,
    Ingame Penalties
    Postrace and appeal penalty fields default to N/A; outcome is derived from Total Time.
    """
    parts = [p.strip() for p in line.strip().split(",")]
    if len(parts) != 6:
        return _field_count_error(parts, 6, line)

    pos_str, driver_str, team_str, total_time, fastest_lap, ingame_penalties = parts

    if not pos_str.isdigit():
        return f"Position must be a positive integer, got `{pos_str}`"
    position = int(pos_str)

    driver_user_id = parse_user_mention(driver_str)
    if driver_user_id is None:
        return f"Driver must be a Discord member mention (<@user_id>), got `{driver_str}`"

    # A team is typed by its shorthand (#381), and which team a text names is the division's
    # business, settled by `validate_submission_block`. Only an empty field is refused here.
    if not team_str:
        return "Team must be named, by its shorthand."
    team_role_id = parse_role_mention(team_str)

    total_upper = total_time.upper()
    if is_first:
        if parse_time(total_time) is None:
            return (
                f"1st-place Total Time must be an absolute time (e.g. 1:23:45.678), "
                f"got `{total_time}`"
            )
    else:
        valid = (
            total_upper in _OUTCOME_LITERALS
            or parse_time(total_time) is not None
            or parse_gap(total_time) is not None
            or parse_lap_gap(total_time) is not None
        )
        if not valid:
            return (
                f"Total Time must be a time, delta (+M:SS.mmm), lap gap (x Laps), "
                f"or DNS/DNF/DSQ, got `{total_time}`"
            )

    fl_upper = fastest_lap.upper()
    if total_upper not in _OUTCOME_LITERALS:
        if fl_upper != "N/A" and parse_time(fastest_lap) is None:
            return f"Fastest Lap must be a time (e.g. 1:23.456) or N/A, got `{fastest_lap}`"

    ip_upper = ingame_penalties.upper()
    if ip_upper != "N/A" and parse_time(ingame_penalties) is None:
        return (
            f"Time Penalties must be a time (e.g. 5.000, 0:05.000) or N/A, "
            f"got `{ingame_penalties}`"
        )

    outcome = _parse_outcome(total_time)
    return ParsedRaceRow(
        position=position,
        driver_user_id=driver_user_id,
        team_role_id=team_role_id,
        team_typed=team_str,
        total_time=total_time,
        fastest_lap=fastest_lap,
        ingame_penalties=ingame_penalties,
        outcome=outcome,
    )


# ---------------------------------------------------------------------------
# Validation — block-level function
# ---------------------------------------------------------------------------

def extract_fl_override(lines: list[str]) -> tuple[int | None, list[str]]:
    """Strip an optional ``FL: <@user_id>`` header from the start of *lines*.

    Returns ``(fl_driver_id, remaining_lines)``.  If the first line does not
    match the header pattern, returns ``(None, lines)`` unchanged.
    Only meaningful for race sessions; callers should ignore the override for
    qualifying submissions.
    """
    if not lines:
        return None, lines
    m = _FL_OVERRIDE_RE.match(lines[0].strip())
    if m is None:
        return None, lines
    return int(m.group(1)), lines[1:]


def extract_current_fl_override(
    lines: list[str], session_type: SessionType, current_of: Mapping[int, int]
) -> tuple[int | None, list[str]]:
    """`extract_fl_override` for a race session, the holder moved onto their current account.

    The rows of the paste are moved onto current accounts by `validate_submission_block`, so
    the override has to be moved the same way or `FL: <@old>` would not be found among rows
    that now read `<@new>` (issue #243). A qualifying session carries no override.
    """
    if session_type.is_qualifying:
        return None, lines
    fl_override, lines = extract_fl_override(lines)
    if fl_override is not None:
        fl_override = current_of.get(fl_override, fl_override)
    return fl_override, lines


_Row = TypeVar("_Row", ParsedQualifyingRow, ParsedRaceRow)


def split_validation(
    result: list[ParsedQualifyingRow | ParsedRaceRow] | list[str],
) -> tuple[list[str], list[ParsedQualifyingRow | ParsedRaceRow]]:
    """`validate_submission_block`'s errors and its rows, of which one is always empty."""
    errors = [item for item in result if isinstance(item, str)]
    rows = [item for item in result if not isinstance(item, str)]
    return errors, rows


def _rows_of_kind(
    rows: list[ParsedQualifyingRow | ParsedRaceRow], kind: type[_Row]
) -> list[_Row]:
    """*rows*, every one of which the validator parsed as *kind* for this session.

    A session is qualifying or a race, and the validator parses each of its rows as that one
    kind; a row of the other kind here is raised by name rather than silently dropped (#228).
    """
    typed = [row for row in rows if isinstance(row, kind)]
    if len(typed) != len(rows):
        raise RuntimeError(f"a session of {kind.__name__}s held a row of another kind")
    return typed


def validate_submission_block(
    lines: list[str],
    session_type: SessionType,
    division_driver_ids: set[int],
    team_of_role: Mapping[int, int],
    reserve_team_role_id: int | None,
    driver_team_map: dict[int, int],
    reserve_driver_ids: set[int] | None = None,
    other_active_assignments: dict[int, tuple[int, str]] | None = None,
    current_of: Mapping[int, int] | None = None,
    team_names: Mapping[int, str] | None = None,
    team_of_shorthand: Mapping[str, int] | None = None,
) -> list[ParsedQualifyingRow | ParsedRaceRow] | list[str]:
    """Validate all result lines for a session.

    Returns a list of parsed rows on success, or a list of error strings on failure.
    Checks: field formats; sequential positions from 1; drivers in division; valid team
    roles; each driver assigned to their own team (reserves may sub for any team);
    max 2 drivers per team per session; and, where *other_active_assignments* is given, that
    no driver disagrees with another ACTIVE session of the same round about which team they
    raced for.

    **A team is compared as the division's team, never as its role** (#375). *team_of_role*
    maps each non-reserve team's role to that team's id, and *team_of_shorthand* each such
    team's shorthand, casefolded, to the same id. A team is typed by its shorthand alone
    (#381): a role mention in the team column is refused. Each row's team is resolved through
    ``resolve_team_reference`` once, onto ``team_instance_id``; *driver_team_map* and
    *other_active_assignments* name teams by the same id. A league may give a team another
    role mid-season, and the rounds recorded before stay the team's all the same. So a
    refusal names a team by its name, from *team_names*: the role a driver's team holds now is
    not necessarily the one a round was recorded under. What was typed is echoed as typed.

    *current_of* maps a driver's past accounts to their current one (issue #243). Any account
    names the driver, so each row is moved onto the current account before anything is
    checked: the seats are held under it, and the row is stored under it. Two rows naming
    one driver by two accounts are then the same driver twice, and refused as such.
    """
    if reserve_driver_ids is None:
        reserve_driver_ids = set()
    is_qualifying = session_type.is_qualifying
    non_empty = [ln for ln in lines if ln.strip()]

    if not non_empty:
        return ["No result lines found — please submit at least one driver line."]

    errors: list[str] = []
    # Collected by kind, so that what only one kind has is read off rows of that kind.
    qualifying_rows: list[ParsedQualifyingRow] = []
    race_rows: list[ParsedRaceRow] = []

    for i, line in enumerate(non_empty, start=1):
        if is_qualifying:
            qualifying = _validate_qualifying_row_wizard(line)
            if isinstance(qualifying, str):
                errors.append(f"Row {i}: {qualifying}")
            else:
                qualifying_rows.append(qualifying)
        else:
            race = _validate_race_row_wizard(line, is_first=(i == 1))
            if isinstance(race, str):
                errors.append(f"Row {i}: {race}")
            else:
                race_rows.append(race)
    parsed_rows: list[ParsedQualifyingRow | ParsedRaceRow] = [*qualifying_rows, *race_rows]

    if errors:
        return errors

    if current_of:
        for row in parsed_rows:
            row.driver_user_id = current_of.get(row.driver_user_id, row.driver_user_id)

    # Positions must be contiguous from 1
    positions = sorted(r.position for r in parsed_rows)
    for expected, actual in enumerate(positions, start=1):
        if actual != expected:
            errors.append(
                f"Position gap: expected position {expected}, got {actual}. "
                "All positions must be sequential starting from 1."
            )
            break

    if errors:
        return errors

    # Each driver must appear exactly once
    seen_driver_ids: set[int] = set()
    for row in parsed_rows:
        if row.driver_user_id in seen_driver_ids:
            errors.append(
                f"Row {row.position}: driver <@{row.driver_user_id}> "
                "appears more than once in this submission."
            )
        seen_driver_ids.add(row.driver_user_id)

    if errors:
        return errors

    # Each driver must be in the division
    for row in parsed_rows:
        if row.driver_user_id not in division_driver_ids:
            errors.append(
                f"Row {row.position}: driver <@{row.driver_user_id}> "
                "is not registered in this division."
            )

    # Each row's team must be a non-reserve team of the division, typed by its shorthand, and
    # is resolved here to that team (#375, #381). Reserves sub *into* a real team, so the
    # reserve team is never among those that may be named. Nor is a team with no role, as it
    # never has been (reconfirmed 2026-09-22): `team_of_shorthand` holds neither.
    role_of_team = {team: role for role, team in team_of_role.items()}
    namable = [
        {"id": team, "name": shorthand} for shorthand, team in (team_of_shorthand or {}).items()
    ]

    def _team(team_id: int) -> str:
        # By name where one is known, as every caller supplies; the team's role otherwise.
        name = (team_names or {}).get(team_id)
        return f"**{name}**" if name else f"<@&{role_of_team.get(team_id)}>"

    for row in parsed_rows:
        reference = resolve_team_reference(row.team_typed, namable, scope=" of this division")
        row.team_instance_id = reference.team["id"] if reference.team is not None else None
        if row.team_instance_id is None:
            errors.append(f"Row {row.position}: {reference.refusal}")

    # Driver must be assigned to the stated team — unless the driver is a reserve,
    # in which case they may sub for any valid non-reserve team.
    for row in parsed_rows:
        if row.driver_user_id in reserve_driver_ids:
            # Reserve driver: only check that the target team role is a real team
            # (already validated above); no further team-match restriction.
            continue
        mapped_team = driver_team_map.get(row.driver_user_id)
        if mapped_team is None:
            continue  # already reported above as "not in division"
        if row.team_instance_id != mapped_team:
            errors.append(
                f"Row {row.position}: driver <@{row.driver_user_id}> "
                f"submitted as {row.team_typed} "
                f"but is assigned to {_team(mapped_team)}."
            )

    # A driver already recorded under a different team by another ACTIVE session of this
    # round is rejected here too. The seat-based check above exempts a reserve, since a
    # reserve may legitimately sub for any team across different rounds — this closes the
    # gap it leaves within one round: a reserve (or anyone else) recorded for team A in one
    # session and team B in another of the same round is never legitimate.
    if other_active_assignments:
        for row in parsed_rows:
            existing = other_active_assignments.get(row.driver_user_id)
            if existing is not None and existing[0] != row.team_instance_id:
                existing_team, existing_session = existing
                existing_label = existing_session.replace("_", " ").title()
                errors.append(
                    f"Row {row.position}: driver <@{row.driver_user_id}> was recorded under "
                    f"{_team(existing_team)} in {existing_label} of this round, but is "
                    f"submitted here as {row.team_typed}."
                )

    # Max 2 drivers per team (counting reserve subs)
    team_driver_counts: dict[int, int] = {}
    for row in parsed_rows:
        if row.team_instance_id is not None:
            team_driver_counts[row.team_instance_id] = (
                team_driver_counts.get(row.team_instance_id, 0) + 1
            )
    for team, count in team_driver_counts.items():
        if count > 2:
            errors.append(
                f"Team {_team(team)} has {count} drivers submitted — maximum is 2."
            )

    if errors:
        return errors

    # Race ordering: positions must respect time-type hierarchy.
    # Lead-lap times (abs/delta) → lapped drivers (lap gap) → non-finishers (DNS/DNF/DSQ).
    # A lower position number must never have a higher category than a subsequent driver.
    if not is_qualifying:
        def _race_time_category(row: "ParsedRaceRow") -> int:
            # A postrace/appeal DSQ puts the driver in the DSQ group
            # regardless of what total_time says.
            if row.outcome == OutcomeModifier.DSQ:
                return 4
            tt = row.total_time.upper()
            if tt == "DNS":
                return 3
            if tt == "DNF":
                return 2
            if parse_lap_gap(row.total_time) is not None:
                return 1  # lapped finisher
            return 0      # lead-lap finisher (absolute or delta time)

        _CATEGORY_LABEL = {0: "lead-lap time", 1: "lap gap (+x Laps)", 2: "DNF", 3: "DNS", 4: "DSQ"}
        rows_by_pos = sorted(race_rows, key=lambda r: r.position)
        max_cat = 0
        for row in rows_by_pos:
            cat = _race_time_category(row)
            if cat < max_cat:
                errors.append(
                    f"Row {row.position}: driver <@{row.driver_user_id}> has a "
                    f"{_CATEGORY_LABEL[cat]} but appears after a "
                    f"{_CATEGORY_LABEL[max_cat]} entry. "
                    "Finishing order must be: lead-lap finishers first, "
                    "then lapped drivers (+x Laps), then DNS/DNF/DSQ."
                )
                break
            max_cat = max(max_cat, cat)

        # Within lapped drivers, lap counts must be non-decreasing with position.
        _LAP_INT_RE = re.compile(r"(\d+)")
        prev_lap_count: int | None = None
        for row in rows_by_pos:
            if parse_lap_gap(row.total_time) is not None:
                m = _LAP_INT_RE.search(row.total_time)
                if m:
                    lap_count = int(m.group(1))
                    if prev_lap_count is not None and lap_count < prev_lap_count:
                        errors.append(
                            f"Row {row.position}: driver <@{row.driver_user_id}> "
                            f"is {lap_count} lap(s) behind but appears after a driver "
                            f"who is {prev_lap_count} lap(s) behind. "
                            "Lap counts must be non-decreasing with position."
                        )
                        break
                    prev_lap_count = lap_count

    if errors:
        return errors

    # Qualifying ordering: CLASSIFIED → DNF → DNS → DSQ
    # Uses derived outcome so penalty-field DSQ is handled correctly.
    if is_qualifying:
        def _qual_outcome_category(row: "ParsedQualifyingRow") -> int:
            # 0=CLASSIFIED, 1=DNF, 2=DNS, 3=DSQ
            if row.outcome == OutcomeModifier.DSQ:
                return 3
            if row.outcome == OutcomeModifier.DNS:
                return 2
            if row.outcome == OutcomeModifier.DNF:
                return 1
            return 0

        _QUAL_CATEGORY_LABEL = {0: "classified", 1: "DNF", 2: "DNS", 3: "DSQ"}
        qualifying_by_pos = sorted(qualifying_rows, key=lambda r: r.position)
        max_qual_cat = 0
        for qualifying_row in qualifying_by_pos:
            cat = _qual_outcome_category(qualifying_row)
            if cat < max_qual_cat:
                errors.append(
                    f"Row {qualifying_row.position}: driver <@{qualifying_row.driver_user_id}> has outcome "
                    f"{_QUAL_CATEGORY_LABEL[cat]} but appears after a "
                    f"{_QUAL_CATEGORY_LABEL[max_qual_cat]} entry. "
                    "Qualifying order must be: classified first, then DNF, then DNS, then DSQ."
                )
                break
            max_qual_cat = max(max_qual_cat, cat)

    if errors:
        return errors

    # G1: derive Best Lap for qualifying DNF entries that have a valid gap
    if is_qualifying:
        p1_row = next((r for r in qualifying_rows if r.position == 1), None)
        if (
            p1_row is not None
            and p1_row.best_lap.upper() not in _OUTCOME_LITERALS
            and parse_time(p1_row.best_lap) is not None
        ):
            try:
                p1_ms = _parse_time_to_ms(p1_row.best_lap)
                for row in qualifying_rows:
                    if row.best_lap.upper() == "DNF" and (
                        parse_gap(row.gap) is not None or parse_time(row.gap) is not None
                    ):
                        gap_ms = _parse_time_to_ms(row.gap)
                        row.best_lap = _format_time_ms(p1_ms + gap_ms)
            except ValueError:
                pass  # parsing failed; leave best_lap unchanged

    return parsed_rows


# ---------------------------------------------------------------------------
# Time parsing helpers (for DNF best-lap derivation)
# ---------------------------------------------------------------------------

def _parse_time_to_ms(s: str) -> int:
    """A time, or a gap to the leader, in milliseconds — read by the shared parsers (#362)."""
    ms = parse_gap(s) if s.strip().startswith("+") else parse_time(s)
    if ms is None:
        raise ValueError(f"Cannot parse time string {s!r}")
    return ms


def _format_time_ms(total_ms: int) -> str:
    """Format milliseconds to 'M:SS.mmm' or 'SS.mmm'."""
    ms = total_ms % 1000
    total_s = total_ms // 1000
    mins = total_s // 60
    secs = total_s % 60
    if mins == 0:
        return f"{secs}.{ms:03d}"
    return f"{mins}:{secs:02d}.{ms:03d}"


class DivisionValidationData(NamedTuple):
    """What a division's seats say a submission may record, as ``validate_submission_block``
    reads it. Every team is named by its id in the division (#375)."""

    division_driver_ids: set[int]
    #: Each non-reserve team's Discord role -> that team's id: the one place a role typed in a
    #: submission is resolved to the team it names.
    team_of_role: dict[int, int]
    reserve_team_role_id: int | None
    #: Seated driver -> the id of the team they sit in.
    driver_team_map: dict[int, int]
    #: Drivers seated in the reserve team. They appear in ``division_driver_ids`` and
    #: ``driver_team_map`` too, but may stand in for any team.
    reserve_driver_ids: set[int]
    #: Team id -> its name, for the messages that name a team.
    team_names: dict[int, str]
    #: Each non-reserve team's shorthand, casefolded -> that team's id: how a submission names
    #: a team (#381). Only a team with a role is here, as in ``team_of_role``: a team with no
    #: role stays out of a submission (reconfirmed 2026-09-22).
    team_of_shorthand: dict[str, int]


async def _build_division_validation_data(division_id: int, bot: LeagueBot) -> DivisionValidationData:
    """Build validation structures for the given division.

    A team whose role is not mapped cannot be typed, and is left out. Its drivers are then
    outside ``division_driver_ids`` too, and refused as not in the division.
    """
    # Only drivers whose placements are confirmed may be scored (issue #220).
    div_teams = await bot.team_service.get_division_teams(division_id, committed_only=True)
    teams_with_roles = await bot.team_service.get_teams_with_roles()

    name_to_role: dict[str, int] = {
        t["name"]: t["role_id"]
        for t in teams_with_roles
        if t["role_id"] is not None
    }

    division_driver_ids: set[int] = set()
    team_of_role: dict[int, int] = {}
    reserve_team_role_id: int | None = None
    driver_team_map: dict[int, int] = {}
    reserve_driver_ids: set[int] = set()
    team_names: dict[int, str] = {}
    team_of_shorthand: dict[str, int] = {}

    for team in div_teams:
        role_id = name_to_role.get(team["name"])
        if role_id is None:
            continue
        team_names[team["id"]] = team["name"]
        if team["is_reserve"]:
            reserve_team_role_id = role_id
        else:
            team_of_role[role_id] = team["id"]
            team_of_shorthand[team["name"].casefold()] = team["id"]
        for seat in team["seats"]:
            uid_str = seat.get("discord_user_id")
            if uid_str is not None:
                uid = int(uid_str)
                division_driver_ids.add(uid)
                driver_team_map[uid] = team["id"]
                if team["is_reserve"]:
                    reserve_driver_ids.add(uid)

    return DivisionValidationData(
        division_driver_ids, team_of_role, reserve_team_role_id, driver_team_map,
        reserve_driver_ids, team_names, team_of_shorthand,
    )


async def other_active_team_assignments(
    db_path: str,
    round_id: int,
    exclude_session_type: SessionType,
    *,
    also_exclude: "tuple[SessionType, ...] | list[SessionType]" = (),
) -> dict[int, tuple[int, str]]:
    """driver_user_id -> (team_instance_id, session_type value) recorded by another ACTIVE
    session of *round_id*.

    Feeds the cross-session team check in ``validate_submission_block``: a driver's team
    must agree across every session of one round, and this is what the second session's
    validation reads to catch a disagreement the first session already recorded.

    *also_exclude* leaves out further sessions — those an amendment is replacing, whose recorded
    rows are about to go and must not be held against their own correction (#345).
    """
    excluded = [exclude_session_type.value, *(st.value for st in also_exclude)]
    marks = _placeholders(excluded)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"""
            SELECT sr.session_type, x.driver_user_id, x.team_instance_id
            FROM session_results sr
            JOIN qualifying_session_results x ON x.session_result_id = sr.id
            WHERE sr.round_id = ? AND sr.status = 'ACTIVE' AND sr.session_type NOT IN ({marks})
            UNION ALL
            SELECT sr.session_type, x.driver_user_id, x.team_instance_id
            FROM session_results sr
            JOIN race_session_results x ON x.session_result_id = sr.id
            WHERE sr.round_id = ? AND sr.status = 'ACTIVE' AND sr.session_type NOT IN ({marks})
            """,  # noqa: S608 — only placeholders are interpolated
            (round_id, *excluded, round_id, *excluded),
        )
        rows = await cursor.fetchall()

        # A session recorded before the driver changed account stands under the old one;
        # the check compares drivers, so both are read as the account in use (issue #243).
        from leaguebot.core.services.drivers.driver_service import current_account_map

        cursor = await db.execute(
            "SELECT 1 FROM rounds r JOIN divisions d ON d.id = r.division_id "
            "JOIN seasons s ON s.id = d.season_id WHERE r.id = ?",
            (round_id,),
        )
        server_row = await cursor.fetchone()
        current_of = (
            await current_account_map(db) if server_row else {}
        )

    result: dict[int, tuple[int, str]] = {}
    for row in rows:
        uid = current_of.get(row["driver_user_id"], row["driver_user_id"])
        result.setdefault(uid, (row["team_instance_id"], row["session_type"]))
    return result


async def current_accounts(db_path: str) -> dict[int, int]:
    """`driver_service.current_account_map` on a connection of its own, for the paste paths."""
    from leaguebot.core.services.drivers.driver_service import current_account_map

    async with get_connection(db_path) as db:
        return await current_account_map(db)


def collected_team_assignments(
    collected: list[CollectedSession],
    exclude_session_type: SessionType,
) -> dict[int, tuple[int, str]]:
    """What `other_active_team_assignments` answers, read from a resubmission in progress.

    driver_user_id -> (team_instance_id, session_type value) from every ACTIVE session collected so
    far other than *exclude_session_type*.
    """
    result: dict[int, tuple[int, str]] = {}
    for session in collected:
        if session.status != "ACTIVE" or session.session_type is exclude_session_type:
            continue
        for row in session.driver_rows:
            result.setdefault(
                row["driver_user_id"], (row["team_instance_id"], session.session_type.value)
            )
    return result
