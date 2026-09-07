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
hopeful (decided 2026-09-07).

Pure and synchronous — no `discord`, no database. The division, the team and the
nationality are carried through as written and resolved by the caller, which is what lets
these tests run without a fixture.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

#: The floor for a synthetic driver ID, mirroring `_SYNTHETIC_ID_BASE` in
#: `services/test_roster_service.py`. An ID below it is a real Discord snowflake — or a
#: mistyped one — and seating a real user's id as a mock driver is the one mistake this
#: import must not make quietly.
SYNTHETIC_ID_BASE = 9_000_000_000_000_000_000

#: The header the generator writes. Matched case-insensitively and only to decide whether
#: the first row is a header; a file without one is read from its first line.
HEADER_FIRST_FIELD = "id"

#: What every row must hold. Nationality is the only one a driver may lack — the signup
#: wizard drops the question when the switch is off, so a roster without it is legitimate.
EXPECTED_COLUMNS = 5


@dataclass(frozen=True)
class ParsedDriver:
    """One row of the roster, resolved no further than the file states it."""

    line: int                   # 1-based, counting the header, so it matches the box
    discord_user_id: int
    driver_name: str
    team_name: str
    division_name: str
    nationality: str | None


def parse_roster_csv(text: str) -> tuple[list[ParsedDriver], list[str]]:
    """Read *text* as the generator's `roster.csv`.

    Returns the drivers and **every** fault, rather than stopping at the first: a manager
    fixes one paste, not one row per attempt.
    """
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

        if len(row) != EXPECTED_COLUMNS:
            errors.append(
                f"Line {number}: expected {EXPECTED_COLUMNS} columns "
                f"(ID, driver, team, division, nationality) but found {len(row)}."
            )
            continue

        raw_id, name, team, division, nationality = (field.strip() for field in row)

        if not raw_id.isdigit():
            errors.append(f"Line {number}: `{raw_id}` is not a driver ID.")
            continue
        driver_id = int(raw_id)
        if driver_id < SYNTHETIC_ID_BASE:
            errors.append(
                f"Line {number}: {driver_id} is below the range test drivers use. "
                f"A real Discord account cannot be seated as a mock driver."
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
        # Two drivers of one name are indistinguishable in every listing the bot prints,
        # and in the results files the sibling scripts write against this roster.
        key = name.lower()
        if key in seen_names:
            errors.append(
                f"Line {number}: `{name}` is already named on line {seen_names[key]}."
            )
            continue
        seen_names[key] = number

        if not team:
            errors.append(f"Line {number}: `{name}` names no team.")
            continue
        if not division:
            errors.append(f"Line {number}: `{name}` names no division.")
            continue

        drivers.append(
            ParsedDriver(
                line=number,
                discord_user_id=driver_id,
                driver_name=name,
                team_name=team,
                division_name=division,
                nationality=nationality or None,
            )
        )

    if not drivers and not errors:
        errors.append("There were no drivers in what you pasted.")

    return drivers, errors


def divisions_named(drivers: list[ParsedDriver]) -> list[str]:
    """The divisions the roster touches, in the order they first appear.

    Ordered by appearance rather than sorted: the reply names them back in the order the
    manager wrote them, and a roster's divisions are written top-tier first by convention.
    """
    ordered: list[str] = []
    for driver in drivers:
        if driver.division_name not in ordered:
            ordered.append(driver.division_name)
    return ordered
