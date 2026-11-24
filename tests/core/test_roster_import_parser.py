"""Reading the generator's `roster.csv`.

No fixtures: the parser resolves nothing against the database, which is the whole reason
it is a module of its own. What it must do is refuse a file that would seat the wrong
people — an ID below the synthetic range is a real Discord account — and report every
fault at once, so a manager fixes one paste rather than one row per attempt.
"""
from __future__ import annotations

import pytest

from leaguebot.core.utils.roster_import import SYNTHETIC_ID_BASE, parse_roster_csv

#: A roster is `ID,Driver name` alone: a fake driver's division, team and seat are decided by
#: `/driver assign` after its signup is approved, and its nationality by its signup.
HEADER = "ID,Driver name"


def _row(offset: int = 1, name="Quicksilver"):
    return f"{SYNTHETIC_ID_BASE + offset},{name}"


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


def test_the_lowest_id_of_the_range_may_be_imported():
    """The range reserved for test drivers begins at its base, which is itself within it."""
    drivers, errors = parse_roster_csv(_csv(_row(offset=0)))

    assert errors == []
    assert [driver.discord_user_id for driver in drivers] == [SYNTHETIC_ID_BASE]


def test_the_header_is_skipped():
    drivers, errors = parse_roster_csv(_csv(_row(), _row(2, name="Badger")))

    assert errors == []
    assert [d.driver_name for d in drivers] == ["Quicksilver", "Badger"]


def test_a_file_without_a_header_is_read_from_its_first_line():
    drivers, errors = parse_roster_csv(_row())

    assert errors == []
    assert len(drivers) == 1


def test_surrounding_spaces_are_trimmed():
    drivers, _ = parse_roster_csv(_csv(f"  {SYNTHETIC_ID_BASE + 1} , Quicksilver "))

    assert drivers[0].discord_user_id == SYNTHETIC_ID_BASE + 1
    assert drivers[0].driver_name == "Quicksilver"


# ── The ID, which the sibling scripts depend on ───────────────────────────


@pytest.mark.parametrize(
    "driver_id",
    [123456789012345678, SYNTHETIC_ID_BASE - 1],
    ids=["a real snowflake", "one below the base"],
)
def test_an_id_below_the_synthetic_range_is_refused(driver_id):
    """The one mistake this import must not make quietly: below the base is a real
    Discord snowflake, and seating a real account as a mock driver is not recoverable by
    turning test mode off. The base itself is in the range and may be imported, though
    `_next_synthetic_id` hands out BASE + 1 onwards; the boundary case is the id one below
    it."""
    drivers, errors = parse_roster_csv(_csv(f"{driver_id},Quicksilver"))

    assert drivers == []
    assert errors[0] == (
        f"Line 2: {driver_id} is below the range test drivers use. "
        "A real Discord account cannot be created as a fake driver."
    )


def test_something_that_is_not_a_number_is_refused():
    drivers, errors = parse_roster_csv(_csv("not-an-id,Quicksilver"))

    assert drivers == []
    assert "is not a driver ID" in errors[0]


def test_two_rows_sharing_an_id_are_refused():
    """The sibling scripts key on the ID; two drivers behind one is unresolvable."""
    drivers, errors = parse_roster_csv(_csv(_row(), _row(1, name="Badger")))

    assert len(drivers) == 1
    assert "already used on line 2" in errors[0]


# ── Rows that are wrong ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "row",
    [
        f"{SYNTHETIC_ID_BASE + 1}",
        f"{SYNTHETIC_ID_BASE + 1},Quicksilver,Alpine",
        f"{SYNTHETIC_ID_BASE + 1},Quicksilver,Alpine,Elite,Serbian",
    ],
    ids=["the ID alone", "three columns", "the five columns rosters once took"],
)
def test_a_row_of_the_wrong_width_is_refused(row):
    """A malformed paste must fail loudly rather than read part of it as a name. A row in the
    five columns a roster once took is refused like any other width."""
    drivers, errors = parse_roster_csv(_csv(row))

    assert drivers == []
    assert "expected 2 columns" in errors[0]


def test_a_missing_name_is_named():
    drivers, errors = parse_roster_csv(_csv(f"{SYNTHETIC_ID_BASE + 1},"))

    assert drivers == []
    assert "no name" in errors[0]


@pytest.mark.parametrize("second", ["Quicksilver", "QUICKSILVER"],
                         ids=["the same name", "the name in another case"])
def test_two_drivers_of_one_name_are_refused(second):
    """Indistinguishable in every listing the bot prints, and in the results files the
    sibling scripts write against this roster. The clash ignores case."""
    drivers, errors = parse_roster_csv(_csv(_row(), _row(2, name=second)))

    assert len(drivers) == 1
    assert "already named on line 2" in errors[0]


# ── Reporting ─────────────────────────────────────────────────────────────


def test_every_fault_is_reported_not_only_the_first():
    """A manager fixes one paste, not one row per attempt."""
    drivers, errors = parse_roster_csv(
        _csv(
            "not-an-id,Quicksilver",
            f"{SYNTHETIC_ID_BASE + 2},",
            f"{SYNTHETIC_ID_BASE + 3},Kestrel,Aston Martin",
        )
    )

    assert drivers == []
    assert len(errors) == 3


def test_line_numbers_count_the_header_and_blank_lines():
    """So they match what the manager sees in the box rather than what survived a filter."""
    drivers, errors = parse_roster_csv(
        "\n".join([HEADER, "", "not-an-id,Quicksilver"])
    )

    assert drivers == []
    assert "Line 3" in errors[0]


@pytest.mark.parametrize("text", ["", HEADER], ids=["nothing", "a header alone"])
def test_an_empty_paste_is_reported_rather_than_silently_accepted(text):
    drivers, errors = parse_roster_csv(text)

    assert drivers == []
    assert errors == ["There were no drivers in what you pasted."]


def test_a_roster_line_naming_a_driver_with_markup_is_refused():
    """Held to the rules every typed name is (#362); the line is named, as for any fault."""
    drivers, errors = parse_roster_csv(_csv(_row(name="**Quicksilver**")))

    assert drivers == []
    assert any(e.startswith("Line 2:") and "driver name" in e for e in errors)


def test_a_roster_line_naming_a_driver_with_a_member_mention_is_refused():
    """A mock driver's name is held to every rule `roster add` holds it to (#388)."""
    drivers, errors = parse_roster_csv(_csv(_row(name="<@123456789012345678>")))

    assert drivers == []
    assert any(e.startswith("Line 2:") and "member" in e for e in errors)


# ── The three-column form `/test-mode roster sign-up-bulk` takes (#600) ───

#: Why each test of the three-column form fails until the parser is written.
_SIGN_UP_PARSER = "#600: the three-column sign-up-bulk parser is not yet written"

#: The form sign-up-bulk takes: a fake driver's ID, its name and its nationality, which may be
#: blank.
SIGN_UP_HEADER = "ID,Driver name,Nationality"


def _parse_sign_up(text: str):
    from leaguebot.core.utils.roster_import import parse_sign_up_csv

    return parse_sign_up_csv(text)


@pytest.mark.xfail(strict=True, reason=_SIGN_UP_PARSER)
@pytest.mark.parametrize("header", [True, False], ids=["with the header", "without it"])
def test_a_sign_up_list_is_read_with_each_driver_s_nationality(header):
    """A row naming a nationality the bot knows is read with it, as the signup wizard reads the
    answer; a row whose nationality is blank is read with none. The header is skipped, and a
    list without one is read from its first line, its line numbers counting the header."""
    from leaguebot.core.utils.input_validator import parse_nationality

    rows = [_row(1) + ",British", _row(2, name="Badger") + ","]
    drivers, errors = _parse_sign_up("\n".join([SIGN_UP_HEADER, *rows] if header else rows))

    assert errors == []
    assert [
        (d.line, d.discord_user_id, d.driver_name, d.nationality) for d in drivers
    ] == [
        (2 if header else 1, SYNTHETIC_ID_BASE + 1, "Quicksilver", parse_nationality("British")),
        (3 if header else 2, SYNTHETIC_ID_BASE + 2, "Badger", None),
    ]


@pytest.mark.xfail(strict=True, reason=_SIGN_UP_PARSER)
@pytest.mark.parametrize(
    "row",
    [f"{SYNTHETIC_ID_BASE + 1}", f"{SYNTHETIC_ID_BASE + 1},Quicksilver,British,Alpine"],
    ids=["the ID alone", "four columns"],
)
def test_a_sign_up_row_of_the_wrong_width_is_refused(row):
    """A row that is not `ID,Driver name,Nationality` is refused, the three columns named."""
    drivers, errors = _parse_sign_up("\n".join([SIGN_UP_HEADER, row]))

    assert drivers == []
    assert errors[0].startswith("Line 2: expected 3 columns")


@pytest.mark.xfail(strict=True, reason=_SIGN_UP_PARSER)
def test_a_sign_up_row_naming_a_nationality_the_bot_does_not_know_is_refused():
    """A nationality the wizard would refuse is refused on its line, the text named."""
    drivers, errors = _parse_sign_up("\n".join([SIGN_UP_HEADER, _row() + ",Martian"]))

    assert drivers == []
    assert len(errors) == 1
    assert errors[0].startswith("Line 2:")
    assert "Martian" in errors[0] and "nationality" in errors[0].lower()


@pytest.mark.xfail(strict=True, reason=_SIGN_UP_PARSER)
@pytest.mark.parametrize(
    "rows",
    [
        [f"{SYNTHETIC_ID_BASE - 1},Quicksilver"],
        ["not-an-id,Quicksilver"],
        [_row(), _row(1, name="Badger")],
        [_row(), _row(2, name="QUICKSILVER")],
        [f"{SYNTHETIC_ID_BASE + 1},"],
        [_row(name="**Quicksilver**")],
    ],
    ids=["an ID below the range", "not an ID", "a repeated ID", "a repeated name",
         "no name", "a name breaking the name rule"],
)
def test_a_sign_up_list_is_refused_for_each_fault_a_roster_is(rows):
    """Each fault the text shows in a roster's ID or name is refused in a sign-up list in the same
    words, on the same line, a blank nationality column added to every row."""
    roster_drivers, roster_errors = parse_roster_csv(_csv(*rows))
    drivers, errors = _parse_sign_up("\n".join([SIGN_UP_HEADER, *(f"{row}," for row in rows)]))

    assert roster_errors
    assert errors == roster_errors
    assert [d.discord_user_id for d in drivers] == [d.discord_user_id for d in roster_drivers]
