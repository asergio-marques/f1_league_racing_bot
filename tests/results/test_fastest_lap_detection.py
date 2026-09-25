"""Deciding who set the fastest lap, and whether they earn the bonus for it.

Issue #208. `standings_service.py` is otherwise well covered; what was left is the lap-time
parser inside `detect_fastest_lap` and the eligibility rules that decide whether the holder
actually gets the points.

**These are two separate questions and the code keeps them apart.** *Who* set the fastest lap
is a fact about the session; *whether they score for it* is a rule of the league's points
configuration. A driver can hold the fastest lap and earn nothing for it — because they finished
outside the eligible positions, or because they did not finish at all — and the standings still
have to know who set it.

**An unparseable lap time is infinity, not an error.** A malformed cell must not decide the
fastest lap and must not stop the standings being computed: every driver's time is compared, and
one bad row would otherwise take the whole round's scoring down. Infinity is the value that
loses every comparison, which is exactly the behaviour wanted.

**The parser takes three shapes.** `SS.mmm`, `M:SS.mmm` and `H:MM:SS.mmm` — a league writes
whichever the game gave them, and the same session can hold more than one. Getting the
three-part case wrong would make an hour-long time sort as a minute, handing the bonus to
somebody who did not earn it.

**The eligibility limit is inclusive.** "Top 10" means positions one to ten, and a driver
finishing tenth earns the bonus. The pair of tests either side of that bound is what pins it —
an off-by-one here silently moves a championship point every round.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from leaguebot.results.models.points_config import PointsConfigFastestLap, SessionType
from leaguebot.core.models.session_result import DriverSessionResult, OutcomeModifier
from leaguebot.results.services.standings_service import (
    compute_points_for_session,
    detect_fastest_lap,
)

DRIVER_A = 4001
DRIVER_B = 4002
DRIVER_C = 4003


def _row(user_id: int, fastest_lap: str):
    return SimpleNamespace(driver_user_id=user_id, fastest_lap=fastest_lap)


def _detect(rows):
    return detect_fastest_lap(rows, SessionType.FEATURE_RACE)


# ---------------------------------------------------------------------------
# Who set it
# ---------------------------------------------------------------------------


def test_the_quickest_lap_wins():
    result = _detect([_row(DRIVER_A, "1:23.456"), _row(DRIVER_B, "1:22.100")])

    assert result == DRIVER_B


def test_a_difference_of_one_millisecond_decides_it():
    """Lap times are recorded to the millisecond precisely so they can be separated."""
    result = _detect([_row(DRIVER_A, "1:23.456"), _row(DRIVER_B, "1:23.455")])

    assert result == DRIVER_B


def test_a_session_nobody_set_a_lap_in_has_no_holder():
    """Every time unparseable, so every comparison lost — which is the same as nobody
    having set one."""
    result = _detect([_row(DRIVER_A, "N/A"), _row(DRIVER_B, "")])

    assert result is None


def test_an_empty_session_has_no_holder():
    assert _detect([]) is None


# ---------------------------------------------------------------------------
# The three shapes a lap time comes in
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "quick,slow",
    [
        ("23.456", "24.000"),
        ("1:23.456", "1:24.000"),
        ("1:00:23.456", "1:00:24.000"),
    ],
    ids=["seconds", "minutes", "hours"],
)
def test_each_shape_is_compared_correctly_against_its_own_kind(quick, slow):
    result = _detect([_row(DRIVER_A, slow), _row(DRIVER_B, quick)])

    assert result == DRIVER_B


def test_shapes_are_comparable_against_each_other():
    """A league writes whichever the game gave them, and one session can hold more than
    one shape — so the parser has to put them on the same scale."""
    result = _detect([_row(DRIVER_A, "1:23.456"), _row(DRIVER_B, "23.456")])

    assert result == DRIVER_B


def test_an_hour_long_time_does_not_sort_as_a_minute():
    """The three-part case read as two parts would make an hour look like a minute and
    hand the bonus to somebody who did not earn it."""
    result = _detect([_row(DRIVER_A, "1:00:00.000"), _row(DRIVER_B, "1:30.000")])

    assert result == DRIVER_B


# ---------------------------------------------------------------------------
# What cannot be read
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["N/A", "", "quick", "-", "1:2:3:4.5", "abc.def"])
def test_an_unreadable_time_never_wins(bad):
    """Infinity loses every comparison, which is the behaviour wanted — a malformed cell
    must not decide the fastest lap."""
    result = _detect([_row(DRIVER_A, bad), _row(DRIVER_B, "9:59.999")])

    assert result == DRIVER_B


def test_one_unreadable_row_does_not_stop_the_others_being_compared():
    """Every driver's time is compared in one pass; raising on one bad row would take the
    whole round's scoring down."""
    result = _detect(
        [_row(DRIVER_A, "1:24.000"), _row(DRIVER_B, "nonsense"), _row(DRIVER_C, "1:23.000")]
    )

    assert result == DRIVER_C


def test_a_time_with_no_colon_is_read_as_seconds():
    """`SS.mmm` is what the game gives for a short lap, and reading it as minutes would
    make it sixty times too slow."""
    result = _detect([_row(DRIVER_A, "23.456"), _row(DRIVER_B, "1:00.000")])

    assert result == DRIVER_A


# ---------------------------------------------------------------------------
# Whether the holder scores for it
# ---------------------------------------------------------------------------
#
# Driven through `compute_points_for_session` rather than a local copy of the rule: a
# reimplementation here would test this file's understanding of the bonus, not the bot's,
# and would keep passing if the real rule changed underneath it.


def _result(
    user_id: int,
    position: int,
    *,
    outcome: OutcomeModifier = OutcomeModifier.CLASSIFIED,
    fastest_lap: str | None = "1:23.456",
) -> DriverSessionResult:
    return DriverSessionResult(
        id=user_id,
        session_result_id=1,
        driver_user_id=user_id,
        team_instance_id=0,
        finishing_position=position,
        outcome=outcome,
        tyre=None,
        best_lap=None,
        gap=None,
        total_time="1:30:00.000",
        fastest_lap=fastest_lap,
        time_penalties=None,
        post_steward_total_time=None,
        post_race_time_penalties=None,
        points_awarded=0,
        fastest_lap_bonus=0,
        is_superseded=False,
    )


def _fl_config(points: int = 1, limit: int | None = None) -> PointsConfigFastestLap:
    return PointsConfigFastestLap(
        id=1,
        config_id=1,
        session_type=SessionType.FEATURE_RACE,
        fl_points=points,
        fl_position_limit=limit,
    )


def _compute(rows, fl_config, *, entries=(), fl_override=None):
    return compute_points_for_session(
        rows,
        list(entries),
        fl_config,
        SessionType.FEATURE_RACE,
        fl_override=fl_override,
    )


def test_the_holder_earns_the_bonus():
    rows = _compute([_result(DRIVER_A, 1, fastest_lap="1:22.000")], _fl_config(points=1))

    assert rows[0].fastest_lap_bonus == 1


def test_nobody_else_earns_it():
    rows = _compute(
        [
            _result(DRIVER_A, 1, fastest_lap="1:22.000"),
            _result(DRIVER_B, 2, fastest_lap="1:23.000"),
        ],
        _fl_config(),
    )

    assert rows[0].fastest_lap_bonus == 1
    assert rows[1].fastest_lap_bonus == 0


def test_a_league_awarding_no_bonus_awards_none():
    """The configuration is optional, and a league that never set one must not have points
    invented for it."""
    rows = _compute([_result(DRIVER_A, 1)], None)

    assert rows[0].fastest_lap_bonus == 0


def test_a_driver_at_the_eligibility_limit_earns_it():
    """"Top 10" means positions one to ten — inclusive."""
    rows = _compute([_result(DRIVER_A, 10)], _fl_config(limit=10))

    assert rows[0].fastest_lap_bonus == 1


def test_a_driver_one_place_outside_the_limit_does_not():
    """Sits the other side of the bound from the test above. An off-by-one here silently
    moves a championship point every round."""
    rows = _compute([_result(DRIVER_A, 11)], _fl_config(limit=10))

    assert rows[0].fastest_lap_bonus == 0


def test_with_no_limit_any_position_earns_it():
    """A league that awards the bonus to anyone, which is a real configuration."""
    rows = _compute([_result(DRIVER_A, 20)], _fl_config(limit=None))

    assert rows[0].fastest_lap_bonus == 1


@pytest.mark.parametrize(
    "outcome", [OutcomeModifier.DNS, OutcomeModifier.DSQ], ids=["dns", "dsq"]
)
def test_a_driver_who_did_not_race_does_not_earn_it(outcome):
    """They cannot have set a lap, and a stray time against them must not score."""
    rows = _compute([_result(DRIVER_A, 1, outcome=outcome)], _fl_config())

    assert rows[0].fastest_lap_bonus == 0


def test_a_retirement_can_still_earn_the_bonus():
    """A driver may set the quickest lap and then retire, and the rule says DNF stays
    eligible — only the position points go."""
    rows = _compute([_result(DRIVER_A, 20, outcome=OutcomeModifier.DNF)], _fl_config())

    assert rows[0].fastest_lap_bonus == 1
    assert rows[0].points_awarded == 0


def test_an_override_designates_the_holder_directly():
    """A steward can name the holder by hand where the submitted times disagree with what
    the game showed, and the override bypasses detection entirely."""
    rows = _compute(
        [
            _result(DRIVER_A, 1, fastest_lap="1:22.000"),
            _result(DRIVER_B, 2, fastest_lap="1:23.000"),
        ],
        _fl_config(),
        fl_override=DRIVER_B,
    )

    assert rows[0].fastest_lap_bonus == 0
    assert rows[1].fastest_lap_bonus == 1


def test_qualifying_awards_no_fastest_lap_bonus():
    """There is no fastest lap to award in qualifying, and the detection is skipped for
    anything that is not a race."""
    rows = compute_points_for_session(
        [_result(DRIVER_A, 1)], [], _fl_config(), SessionType.FEATURE_QUALIFYING
    )

    assert rows[0].fastest_lap_bonus == 0


def test_holding_the_lap_and_scoring_for_it_are_separate_questions():
    """A driver outside the limit still holds the fastest lap; only the bonus is withheld.
    Conflating the two would lose the fact from the standings entirely."""
    holder = _detect([_row(DRIVER_A, "1:22.000"), _row(DRIVER_B, "1:23.000")])
    rows = _compute([_result(DRIVER_A, 15, fastest_lap="1:22.000")], _fl_config(limit=10))

    assert holder == DRIVER_A
    assert rows[0].fastest_lap_bonus == 0
