"""Reading the generator's `roster.csv`.

No fixtures: the parser resolves nothing against the database, which is the whole reason
it is a module of its own. What it must do is refuse a file that would seat the wrong
people — an ID below the synthetic range is a real Discord account — and report every
fault at once, so a manager fixes one paste rather than one row per attempt.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.roster_import import (  # noqa: E402
    SYNTHETIC_ID_BASE,
    divisions_named,
    parse_roster_csv,
)

HEADER = "ID,Driver name,Team,Division,Nationality"


def _row(offset: int = 1, name="Quicksilver", team="Alpine", div="Elite", nat="Serbian"):
    return f"{SYNTHETIC_ID_BASE + offset},{name},{team},{div},{nat}"


def _csv(*rows: str) -> str:
    return "\n".join([HEADER, *rows])


# ── The happy path ────────────────────────────────────────────────────────


def test_a_row_is_read_whole():
    drivers, errors = parse_roster_csv(_csv(_row()))

    assert errors == []
    assert len(drivers) == 1
    driver = drivers[0]
    assert driver.discord_user_id == SYNTHETIC_ID_BASE + 1
    assert driver.driver_name == "Quicksilver"
    assert driver.team_name == "Alpine"
    assert driver.division_name == "Elite"
    assert driver.nationality == "Serbian"


def test_the_header_is_skipped():
    drivers, errors = parse_roster_csv(_csv(_row(), _row(2, name="Badger")))

    assert errors == []
    assert [d.driver_name for d in drivers] == ["Quicksilver", "Badger"]


def test_a_file_without_a_header_is_read_from_its_first_line():
    drivers, errors = parse_roster_csv(_row())

    assert errors == []
    assert len(drivers) == 1


def test_a_driver_with_no_nationality_is_accepted():
    """The signup wizard drops the question when the switch is off, so a roster without
    one is legitimate and is drawn without a flag."""
    drivers, errors = parse_roster_csv(_csv(_row(nat="")))

    assert errors == []
    assert drivers[0].nationality is None


def test_surrounding_spaces_are_trimmed():
    drivers, _ = parse_roster_csv(
        _csv(f"  {SYNTHETIC_ID_BASE + 1} , Quicksilver , Alpine , Elite , Serbian ")
    )

    assert drivers[0].driver_name == "Quicksilver"
    assert drivers[0].team_name == "Alpine"


def test_a_team_name_holding_a_comma_survives():
    """Read as CSV rather than split on commas, so a quoted field holds one."""
    drivers, errors = parse_roster_csv(
        _csv(f'{SYNTHETIC_ID_BASE + 1},Kestrel,"Haas, Inc",Elite,British')
    )

    assert errors == []
    assert drivers[0].team_name == "Haas, Inc"


# ── The ID, which the sibling scripts depend on ───────────────────────────


def test_an_id_below_the_synthetic_range_is_refused():
    """The one mistake this import must not make quietly: below the base is a real
    Discord snowflake, and seating a real account as a mock driver is not recoverable by
    turning test mode off."""
    drivers, errors = parse_roster_csv(_csv(f"123456789012345678,Quicksilver,Alpine,Elite,Serbian"))

    assert drivers == []
    assert "below the range" in errors[0]


def test_the_base_itself_is_below_the_range():
    """`_next_synthetic_id` hands out BASE + 1, so BASE is never a driver."""
    drivers, errors = parse_roster_csv(
        _csv(f"{SYNTHETIC_ID_BASE - 1},Quicksilver,Alpine,Elite,Serbian")
    )

    assert drivers == []
    assert errors


def test_something_that_is_not_a_number_is_refused():
    drivers, errors = parse_roster_csv(_csv("not-an-id,Quicksilver,Alpine,Elite,Serbian"))

    assert drivers == []
    assert "is not a driver ID" in errors[0]


def test_two_rows_sharing_an_id_are_refused():
    """The sibling scripts key on the ID; two drivers behind one is unresolvable."""
    drivers, errors = parse_roster_csv(_csv(_row(), _row(1, name="Badger")))

    assert len(drivers) == 1
    assert "already used on line 2" in errors[0]


# ── Rows that are wrong ───────────────────────────────────────────────────


def test_a_row_of_the_wrong_width_is_refused():
    """A malformed paste must fail loudly rather than read the name as a team."""
    drivers, errors = parse_roster_csv(_csv(f"{SYNTHETIC_ID_BASE + 1},Quicksilver,Alpine"))

    assert drivers == []
    assert "expected 5 columns" in errors[0]


@pytest.mark.parametrize(
    "row,expected",
    [
        (f"{SYNTHETIC_ID_BASE + 1},,Alpine,Elite,Serbian", "no name"),
        (f"{SYNTHETIC_ID_BASE + 1},Quicksilver,,Elite,Serbian", "names no team"),
        (f"{SYNTHETIC_ID_BASE + 1},Quicksilver,Alpine,,Serbian", "names no division"),
    ],
)
def test_a_missing_field_is_named(row, expected):
    drivers, errors = parse_roster_csv(_csv(row))

    assert drivers == []
    assert expected in errors[0]


def test_two_drivers_of_one_name_are_refused():
    """Indistinguishable in every listing the bot prints, and in the results files the
    sibling scripts write against this roster."""
    drivers, errors = parse_roster_csv(_csv(_row(), _row(2)))

    assert len(drivers) == 1
    assert "already named on line 2" in errors[0]


def test_the_name_clash_ignores_case():
    drivers, errors = parse_roster_csv(_csv(_row(), _row(2, name="QUICKSILVER")))

    assert len(drivers) == 1
    assert errors


# ── Reporting ─────────────────────────────────────────────────────────────


def test_every_fault_is_reported_not_only_the_first():
    """A manager fixes one paste, not one row per attempt."""
    drivers, errors = parse_roster_csv(
        _csv(
            "not-an-id,Quicksilver,Alpine,Elite,Serbian",
            f"{SYNTHETIC_ID_BASE + 2},,Alpine,Elite,Greek",
            f"{SYNTHETIC_ID_BASE + 3},Kestrel,Aston Martin",
        )
    )

    assert drivers == []
    assert len(errors) == 3


def test_line_numbers_count_the_header_and_blank_lines():
    """So they match what the manager sees in the box rather than what survived a filter."""
    drivers, errors = parse_roster_csv(
        "\n".join([HEADER, "", "not-an-id,Quicksilver,Alpine,Elite,Serbian"])
    )

    assert drivers == []
    assert "Line 3" in errors[0]


def test_an_empty_paste_is_reported_rather_than_silently_accepted():
    drivers, errors = parse_roster_csv("")

    assert drivers == []
    assert errors == ["There were no drivers in what you pasted."]


def test_a_header_alone_is_reported_too():
    drivers, errors = parse_roster_csv(HEADER)

    assert drivers == []
    assert errors


# ── The divisions a roster names ──────────────────────────────────────────


def test_the_divisions_come_back_in_the_order_they_appear():
    """Rosters are written top-tier first by convention, and the reply reads better for
    saying them back that way."""
    drivers, _ = parse_roster_csv(
        _csv(_row(1, div="Elite"), _row(2, name="B", div="Challenger"), _row(3, name="C", div="Elite"))
    )

    assert divisions_named(drivers) == ["Elite", "Challenger"]


# ── The real file ─────────────────────────────────────────────────────────


def test_the_generator_s_own_roster_parses():
    """The file this import exists for, read exactly as it ships.

    A change to the generator's columns would otherwise be found by pasting fifty-one
    drivers into a modal and being refused.
    """
    csv_path = Path(__file__).resolve().parents[2] / "tools" / "data-generator" / "roster.csv"
    if not csv_path.is_file():
        pytest.skip("the generator has not been run with --record on this host")

    drivers, errors = parse_roster_csv(csv_path.read_text(encoding="utf-8"))

    assert errors == []
    assert drivers, "the shipped roster.csv parsed to nothing"
    assert all(d.discord_user_id >= SYNTHETIC_ID_BASE for d in drivers)
