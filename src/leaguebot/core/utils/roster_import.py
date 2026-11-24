"""Reading a test roster from the CSV the generator writes.

`tools/data-generator/test-roster/generate_test_roster.py --record` writes `roster.csv`,
and every sibling generator — results, check-ins — reads it back to know who is in which
team. Until now the roster reached the bot through `commands.txt`, one
`/test-mode roster add` per driver, fifty-one of them for a two-division league.

**The ID column is honoured, not predicted.** The generator's IDs "mirror the convention"
in `test_roster_service.py`, which allocates `MAX(existing) + 1` — so they line up only
when the import lands on a clean season. That is a fragile thing for the sibling scripts to
depend on, and they depend on it entirely: a results file naming driver
9000000000000000007 means nothing if the bot seated that driver as 9000000000000000009.
So the import writes the IDs the file states, and the CSV is authoritative rather than
hopeful.

A roster is two columns, `ID,Driver name`: a fake driver is created at Not Signed Up with no seat,
its division, team and seat decided as any driver's by `/driver assign` once its signup is
approved, and its nationality the one its signup gave.

`/test-mode roster sign-up-bulk` reads a third column, the nationality, which may be blank
(`parse_sign_up_csv`); every other rule of the two-column form holds for it, the one reader
judging both.

Pure and synchronous — no `discord`, no database, which is what lets these tests run without a
fixture.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from leaguebot.core.utils.input_validator import NAME, parse_nationality, parse_user_id

#: The floor for a synthetic driver ID, mirroring `_SYNTHETIC_ID_BASE` in
#: `core/services/test_mode/test_roster_service.py`. An ID below it is a real Discord snowflake — or a
#: mistyped one — and seating a real user's id as a mock driver is the one mistake this
#: import must not make quietly.
SYNTHETIC_ID_BASE = 9_000_000_000_000_000_000

#: The header the generator writes. Matched case-insensitively and only to decide whether
#: the first row is a header; a file without one is read from its first line.
HEADER_FIRST_FIELD = "id"

#: What every row of a roster must hold: the driver's ID and its name.
EXPECTED_COLUMNS = 2

#: What every row of a sign-up list must hold: the ID, the name and the nationality, which may be
#: blank.
SIGN_UP_COLUMNS = 3


@dataclass(frozen=True)
class ParsedDriver:
    """One row of the roster, resolved no further than the file states it."""

    line: int                   # 1-based, counting the header, so it matches the box
    discord_user_id: int
    driver_name: str
    nationality: str | None = None  # a sign-up list's, as the wizard reads it; none where blank


def parse_roster_csv(text: str) -> tuple[list[ParsedDriver], list[str]]:
    """Read *text* as the generator's `roster.csv`.

    Returns the drivers and **every** fault, rather than stopping at the first: a manager
    fixes one paste, not one row per attempt.
    """
    return _parse(text, EXPECTED_COLUMNS)


def parse_sign_up_csv(text: str) -> tuple[list[ParsedDriver], list[str]]:
    """Read *text* as `/test-mode roster sign-up-bulk` takes it: `ID,Driver name,Nationality`.

    The roster's reader, a third column added: the nationality, read as the signup wizard reads
    it and none where blank, a text the bot does not know a fault on its line. A row of an ID and
    a name alone is refused for its width, not read as a driver with no nationality: the blank
    column is written, as a comma.
    """
    return _parse(text, SIGN_UP_COLUMNS)


def _parse(text: str, columns: int) -> tuple[list[ParsedDriver], list[str]]:
    """The one reader of both forms: *columns* is two for a roster and three for a sign-up list."""
    drivers: list[ParsedDriver] = []
    errors: list[str] = []
    seen_ids: dict[int, int] = {}
    seen_names: dict[str, int] = {}

    rows = list(csv.reader(io.StringIO(text)))
    for number, row in enumerate(rows, start=1):
        # Blank lines are skipped but still counted, so the numbers reported match what
        # the manager is looking at rather than what survived a filter.
        if not row or not any(field.strip() for field in row):
            continue
        if number == 1 and row[0].strip().lower() == HEADER_FIRST_FIELD:
            continue

        if len(row) != columns:
            names = "ID, driver name, nationality" if columns == SIGN_UP_COLUMNS else "ID, driver name"
            errors.append(
                f"Line {number}: expected {columns} columns ({names}) but found {len(row)}."
            )
            continue

        raw_id, name, *rest = (field.strip() for field in row)

        driver_id = parse_user_id(raw_id)
        if driver_id is None:
            errors.append(f"Line {number}: `{raw_id}` is not a driver ID.")
            continue
        if driver_id < SYNTHETIC_ID_BASE:
            errors.append(
                f"Line {number}: {driver_id} is below the range test drivers use. "
                f"A real Discord account cannot be created as a fake driver."
            )
            continue
        if driver_id in seen_ids:
            errors.append(
                f"Line {number}: driver ID {driver_id} is already used on line "
                f"{seen_ids[driver_id]}."
            )
            continue
        seen_ids[driver_id] = number

        if not name:
            errors.append(f"Line {number}: the driver has no name.")
            continue
        refusal = NAME.check("driver name", name).refusal
        if refusal is not None:
            errors.append(f"Line {number}: {refusal}")
            continue
        # Two drivers of one name are indistinguishable in every listing the bot prints,
        # and in the results files the sibling scripts write against this roster.
        key = name.lower()
        if key in seen_names:
            errors.append(
                f"Line {number}: `{name}` is already named on line {seen_names[key]}."
            )
            continue
        seen_names[key] = number

        nationality = None
        if rest and rest[0]:
            nationality = parse_nationality(rest[0])
            if nationality is None:
                errors.append(f"Line {number}: `{rest[0]}` is not a nationality the bot knows.")
                continue

        drivers.append(
            ParsedDriver(
                line=number,
                discord_user_id=driver_id,
                driver_name=name,
                nationality=nationality,
            )
        )

    if not drivers and not errors:
        errors.append("There were no drivers in what you pasted.")

    return drivers, errors


def format_roster_errors(errors: list[str]) -> str:
    """One shape for every refusal, capped so a systematically wrong file still replies.

    A file whose every row is wrong produces one fault per row, and fifty-one of them
    exceed Discord's 2000-character limit — which refuses the whole message rather than
    truncating it, leaving the manager with no reply at all.
    """
    return _format(
        errors,
        f"⛔ The roster was not imported — {len(errors)} problem(s). **No drivers were added.**",
    )


def format_sign_up_errors(errors: list[str]) -> str:
    """`format_roster_errors` for a sign-up list: the same shape and cap, its headline saying
    nobody was signed up."""
    return _format(
        errors,
        f"⛔ The list was not signed up — {len(errors)} problem(s). **No drivers were added.**",
    )


def _format(errors: list[str], headline: str) -> str:
    shown = errors[:15]
    lines = [headline]
    lines += [f"• {problem}" for problem in shown]
    if len(errors) > len(shown):
        lines.append(f"…and {len(errors) - len(shown)} more.")
    return "\n".join(lines)
