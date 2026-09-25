"""The six-field row validators the result submission wizard reads.

Issue #208. `validate_qualifying_row` and `validate_race_row` — the eight-field forms a league
pastes in bulk — already have cover. Their six-field wizard counterparts,
`_validate_qualifying_row_wizard` and `_validate_race_row_wizard`, did not, and they are what a
league manager actually types a round's results into.

**They are the boundary between a league's typing and its championship.** Everything downstream
— the points, the standings, the graphics, the attendance record — is computed from whatever
these accept, so a row admitted wrongly is a wrong championship, and nothing later re-checks it.

**A refusal returns a string rather than raising**, and the string is shown to the manager
verbatim. So every refusal is asserted to quote the offending field back: a manager pasting
twenty rows needs to know which one, and "invalid input" for a twenty-row block is no help at
all. The tests read the message, not just the fact of refusal.

**The first row is special in a race and in qualifying alike, for different reasons.** First
place has no gap to quote and no delta to be behind, so a race's leading row must give an
absolute time — a delta there would be a gap to nobody — while a qualifying leader's gap field
is simply not checked. `test_the_leading_race_row_must_give_an_absolute_time` and
`test_a_qualifying_leader_s_gap_is_not_checked` sit on the two halves.

**A retirement suspends the rest of the row's rules.** A driver who did not start has no
fastest lap and no time, so `DNS`, `DNF` and `DSQ` in the time field stop the fastest-lap check
from running — asking a retired driver for a lap time would refuse every row a retirement
appears in.

**An empty tyre is an answer, not a gap** (XIV.13's per-field absent-datum rule): the
submission of a session does not oblige a compound, and its absence is a state the graphic
depicts rather than a gap it reports.
"""
from __future__ import annotations

import pytest

from leaguebot.results.services.result_submission_service import (
    _validate_qualifying_row_wizard,
    _validate_race_row_wizard,
)

DRIVER = "<@4242>"
TEAM = "<@&5353>"


def _qualifying(
    position: str = "1",
    driver: str = DRIVER,
    team: str = TEAM,
    tyre: str = "Soft",
    best_lap: str = "1:23.456",
    gap: str = "N/A",
) -> str:
    return f"{position}, {driver}, {team}, {tyre}, {best_lap}, {gap}"


def _race(
    position: str = "1",
    driver: str = DRIVER,
    team: str = TEAM,
    total_time: str = "1:23:45.678",
    fastest_lap: str = "1:23.456",
    penalties: str = "N/A",
) -> str:
    return f"{position}, {driver}, {team}, {total_time}, {fastest_lap}, {penalties}"


def _refused(result) -> bool:
    return isinstance(result, str)


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "validator,line",
    [
        (_validate_qualifying_row_wizard, "1, <@1>, <@&2>, Soft"),
        (_validate_qualifying_row_wizard, "1, <@1>, <@&2>, Soft, 1:23.456, N/A, extra"),
    ],
    ids=["too-few", "too-many"],
)
def test_a_qualifying_row_must_have_six_fields(validator, line):
    """The count is quoted back, because a manager pasting twenty rows needs to know which
    one and by how much."""
    result = validator(line)

    assert _refused(result)
    assert "6 comma-separated fields" in result


def test_a_race_row_must_have_six_fields():
    result = _validate_race_row_wizard("1, <@1>, <@&2>", is_first=True)

    assert _refused(result)
    assert "6 comma-separated fields" in result


def test_surrounding_whitespace_is_forgiven():
    """A pasted block carries it, and refusing over spacing would refuse most real input."""
    result = _validate_qualifying_row_wizard(
        "  1 ,  <@4242> ,  <@&5353> , Soft , 1:23.456 , N/A  "
    )

    assert not _refused(result)
    assert result.position == 1


# ---------------------------------------------------------------------------
# Position, driver and team
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("position", ["0x", "first", "-1", "1.5", ""])
def test_a_position_that_is_not_a_whole_number_is_refused(position):
    result = _validate_qualifying_row_wizard(_qualifying(position=position))

    assert _refused(result)
    assert "Position" in result


@pytest.mark.parametrize("driver", ["Lewis", "4242", "<@&4242>", ""])
def test_a_driver_that_is_not_a_member_mention_is_refused(driver):
    """A role mention where a member should be is the mistake this catches — the two look
    almost identical and a role would silently become a driver id."""
    result = _validate_qualifying_row_wizard(_qualifying(driver=driver))

    assert _refused(result)
    assert "Driver" in result


def test_an_empty_team_is_refused():
    result = _validate_qualifying_row_wizard(_qualifying(team=""))

    assert _refused(result)
    assert "Team" in result


@pytest.mark.parametrize("team", ["Alpha", "<@5353>"])
def test_a_team_that_is_not_a_role_mention_is_carried_as_typed(team):
    """A team may be typed by its shorthand (#381), and which team a text names is the
    division's business: `validate_submission_block` resolves it, and refuses what names none,
    `<@5353>` included."""
    result = _validate_qualifying_row_wizard(_qualifying(team=team))

    assert result.team_typed == team
    assert result.team_role_id is None


def test_a_valid_row_carries_the_ids_through():
    result = _validate_qualifying_row_wizard(_qualifying())

    assert result.driver_user_id == 4242
    assert result.team_role_id == 5353


# ---------------------------------------------------------------------------
# Tyres
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tyre", ["Soft", "soft", "SOFT"])
def test_a_tyre_is_stored_in_its_canonical_spelling(tyre):
    """One spelling is stored so the qualifying graphic finds its file on every row
    without the league supplying any tyre artwork."""
    result = _validate_qualifying_row_wizard(_qualifying(tyre=tyre))

    assert not _refused(result)
    assert result.tyre == _validate_qualifying_row_wizard(_qualifying(tyre="Soft")).tyre


def test_an_unknown_compound_is_refused_listing_the_set():
    """The five compounds are a closed set, so a submission naming something else names
    nothing the bot can draw — refused here rather than resolved to a placeholder six
    steps later."""
    result = _validate_qualifying_row_wizard(_qualifying(tyre="Rainbow"))

    assert _refused(result)
    assert "Rainbow" in result


@pytest.mark.parametrize("tyre", ["", "N/A", "n/a"])
def test_an_absent_tyre_is_an_answer_not_a_gap(tyre):
    """The submission of a session does not oblige a compound, and its absence is a state
    the graphic depicts rather than a gap it reports."""
    result = _validate_qualifying_row_wizard(_qualifying(tyre=tyre))

    assert not _refused(result)
    assert result.tyre is None


# ---------------------------------------------------------------------------
# Qualifying times
# ---------------------------------------------------------------------------


def test_a_qualifying_best_lap_must_be_a_time():
    result = _validate_qualifying_row_wizard(_qualifying(best_lap="quick"))

    assert _refused(result)
    assert "Best Lap" in result


@pytest.mark.parametrize("outcome", ["DNS", "DNF", "DSQ", "dnf"])
def test_a_retirement_may_stand_in_for_a_qualifying_time(outcome):
    """A driver who did not set a lap has no lap to give."""
    result = _validate_qualifying_row_wizard(_qualifying(best_lap=outcome))

    assert not _refused(result)


def test_a_qualifying_leader_s_gap_is_not_checked():
    """First place has nobody to be behind, so whatever sits in the field is ignored
    rather than refused — a league writing "—" or "Pole" there is not wrong."""
    result = _validate_qualifying_row_wizard(_qualifying(position="1", gap="anything"))

    assert not _refused(result)


def test_a_following_driver_s_gap_must_be_a_delta_a_time_or_absent():
    result = _validate_qualifying_row_wizard(_qualifying(position="2", gap="miles"))

    assert _refused(result)
    assert "Gap" in result


@pytest.mark.parametrize("gap", ["+0:01.234", "1:23.456", "N/A"])
def test_the_three_acceptable_gap_forms(gap):
    result = _validate_qualifying_row_wizard(_qualifying(position="2", gap=gap))

    assert not _refused(result)


# ---------------------------------------------------------------------------
# Race times
# ---------------------------------------------------------------------------


def test_the_leading_race_row_must_give_an_absolute_time():
    """A delta in first place would be a gap to nobody, and every following driver's gap
    is computed from this one."""
    result = _validate_race_row_wizard(_race(total_time="+0:01.234"), is_first=True)

    assert _refused(result)
    assert "absolute time" in result


def test_the_leading_race_row_accepts_an_absolute_time():
    result = _validate_race_row_wizard(_race(total_time="1:23:45.678"), is_first=True)

    assert not _refused(result)


@pytest.mark.parametrize(
    "total_time", ["+0:01.234", "1:23:45.678", "1 Lap", "2 Laps", "DNF", "DSQ", "DNS"]
)
def test_a_following_race_row_accepts_every_way_of_being_behind(total_time):
    """A delta, an absolute time, a lap gap, or a retirement — all four are real outcomes
    and refusing any of them would refuse a real race."""
    result = _validate_race_row_wizard(_race(position="2", total_time=total_time), is_first=False)

    assert not _refused(result)


def test_a_following_race_row_refuses_anything_else():
    result = _validate_race_row_wizard(_race(position="2", total_time="ages"), is_first=False)

    assert _refused(result)
    assert "Total Time" in result


def test_a_race_fastest_lap_must_be_a_time_or_absent():
    result = _validate_race_row_wizard(_race(fastest_lap="quick"), is_first=True)

    assert _refused(result)
    assert "Fastest Lap" in result


@pytest.mark.parametrize("fastest_lap", ["1:23.456", "N/A", "n/a"])
def test_the_two_acceptable_fastest_lap_forms(fastest_lap):
    result = _validate_race_row_wizard(_race(fastest_lap=fastest_lap), is_first=True)

    assert not _refused(result)


@pytest.mark.parametrize("outcome", ["DNF", "DNS", "DSQ"])
def test_a_retired_driver_is_not_asked_for_a_fastest_lap(outcome):
    """They did not finish, so there is nothing to give. Checking anyway would refuse
    every row a retirement appears in."""
    result = _validate_race_row_wizard(
        _race(position="2", total_time=outcome, fastest_lap="—"), is_first=False
    )

    assert not _refused(result)


def test_time_penalties_must_be_a_time_or_absent():
    result = _validate_race_row_wizard(_race(penalties="five seconds"), is_first=True)

    assert _refused(result)
    assert "Time Penalties" in result


@pytest.mark.parametrize("penalties", ["5.000", "0:05.000", "N/A"])
def test_the_acceptable_penalty_forms(penalties):
    result = _validate_race_row_wizard(_race(penalties=penalties), is_first=True)

    assert not _refused(result)


# ---------------------------------------------------------------------------
# What a wizard row leaves for later
# ---------------------------------------------------------------------------


def test_a_parsed_row_carries_no_later_penalty_at_all():
    """The six-field form is typed before any penalty is reviewed, and post-race and appeal
    penalties are applied by the review stages as records with a justification and an author.
    A parsed row has no field for them, so nothing typed can reach them (#345)."""
    qualifying = _validate_qualifying_row_wizard(_qualifying())
    race = _validate_race_row_wizard(_race(), is_first=True)

    for row in (qualifying, race):
        assert not hasattr(row, "postrace_penalty")
        assert not hasattr(row, "appeal_penalty")


@pytest.mark.parametrize("outcome", ["DNS", "DNF", "DSQ"])
def test_the_outcome_is_derived_from_the_time_field(outcome):
    """The manager types the retirement once, in the time field, rather than repeating it
    in a column of its own — so the two can never disagree."""
    race = _validate_race_row_wizard(
        _race(position="2", total_time=outcome), is_first=False
    )

    assert race.outcome.upper().endswith(outcome)
