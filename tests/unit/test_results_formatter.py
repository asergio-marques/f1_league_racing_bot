"""Unit tests for results_formatter (T032)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.session_result import OutcomeModifier, QualifyingSessionResult, RaceSessionResult
from models.standings_snapshot import DriverStandingsSnapshot
from utils.results_formatter import (
    NOT_APPLICABLE,
    _collapse_trailing_zeros,
    build_qualifying_rows,
    build_race_rows,
    fastest_lap_holder,
    format_driver_standings,
    format_qualifying_table,
    format_race_table,
    render_gap,
    render_lap_time,
    render_time_penalty,
)


def _make_qual(
    position: int,
    driver_user_id: int = 100,
    team_role_id: int = 200,
    outcome: OutcomeModifier = OutcomeModifier.CLASSIFIED,
    best_lap: str | None = "1:23.456",
) -> QualifyingSessionResult:
    return QualifyingSessionResult(
        id=0,
        session_result_id=1,
        driver_user_id=driver_user_id,
        finishing_position=position,
        team_role_id=team_role_id,
        outcome=outcome,
        tyre="Soft",
        best_lap=best_lap,
        points_awarded=25,
    )


def _make_race(
    position: int,
    driver_user_id: int = 100,
    team_role_id: int = 200,
    outcome: OutcomeModifier = OutcomeModifier.CLASSIFIED,
    fastest_lap: str | None = "1:23.456",
) -> RaceSessionResult:
    return RaceSessionResult(
        id=0,
        session_result_id=1,
        driver_user_id=driver_user_id,
        finishing_position=position,
        team_role_id=team_role_id,
        outcome=outcome,
        base_time_ms=5025678,  # 1:23:45.678 in ms
        laps_behind=None,
        ingame_time_penalties_ms=0,
        postrace_time_penalties_ms=0,
        appeal_time_penalties_ms=0,
        fastest_lap=fastest_lap,
        fastest_lap_bonus=0,
        points_awarded=25,
    )


# ---------------------------------------------------------------------------
# _collapse_trailing_zeros
# ---------------------------------------------------------------------------


def test_collapse_all_zeros():
    result = _collapse_trailing_zeros([(1, 0), (2, 0), (3, 0)])
    assert len(result) == 1
    label, pts = result[0]
    assert pts == 0
    assert "+" in label


def test_collapse_mix_nonzero_then_zeros():
    result = _collapse_trailing_zeros([(1, 25), (2, 18), (3, 0), (4, 0)])
    labels = [label for label, _ in result]
    points = [pts for _, pts in result]
    assert "1" in labels
    assert "2" in labels
    assert "3+" in labels
    assert "4" not in labels
    assert "4+" not in labels
    # Points: 25, 18, 0
    assert points[0] == 25
    assert points[1] == 18
    assert points[2] == 0


def test_collapse_all_nonzero():
    result = _collapse_trailing_zeros([(1, 25), (2, 18), (3, 15)])
    # No trailing zeros — all positions returned as-is
    labels = [label for label, _ in result]
    assert labels == ["1", "2", "3"]
    points = [pts for _, pts in result]
    assert points == [25, 18, 15]


def test_collapse_empty():
    result = _collapse_trailing_zeros([])
    assert result == []


def test_collapse_single_zero():
    result = _collapse_trailing_zeros([(1, 0)])
    assert len(result) == 1
    label, pts = result[0]
    assert pts == 0
    assert "+" in label


def test_collapse_single_nonzero():
    result = _collapse_trailing_zeros([(1, 25)])
    assert result == [("1", 25)]


# ---------------------------------------------------------------------------
# format_qualifying_table — header row check
# ---------------------------------------------------------------------------


def test_format_qualifying_table_headers():
    rows = [_make_qual(1)]
    table = format_qualifying_table(
        rows,
        points_by_driver={100: 25},
        member_display={100: "Driver One"},
        team_display={200: "Red Bull"},
    )
    assert "Driver One" in table
    assert "Red Bull" in table
    assert "1." in table or "**1.**" in table


# ---------------------------------------------------------------------------
# format_race_table — header row check
# ---------------------------------------------------------------------------


def test_format_race_table_headers():
    rows = [_make_race(1)]
    table = format_race_table(
        rows,
        points_by_driver={100: 25},
        member_display={100: "Driver One"},
        team_display={200: "Red Bull"},
    )
    assert "Driver One" in table
    assert "Red Bull" in table
    assert "1." in table or "**1.**" in table


def test_format_race_table_fl_footer_shown_when_bonus():
    """When a driver has fastest_lap_bonus > 0, a FL footnote appears after the table."""
    row = _make_race(1, driver_user_id=100, fastest_lap="1:23.456")
    row.fastest_lap_bonus = 1
    table = format_race_table(
        [row],
        points_by_driver={100: 26},
    )
    assert "Fastest lap" in table
    assert "<@100>" in table
    assert "1:23.456" in table
    # Footer must appear after all driver lines
    last_driver_line = table.rindex("pts")
    footer_pos = table.index("Fastest lap")
    assert footer_pos > last_driver_line


def test_format_race_table_no_fl_footer_when_no_bonus():
    """No FL footnote when no driver has a fastest_lap_bonus."""
    row = _make_race(1, driver_user_id=100, fastest_lap="1:23.456")
    row.fastest_lap_bonus = 0
    table = format_race_table(
        [row],
        points_by_driver={100: 25},
    )
    assert "Fastest lap" not in table


# ---------------------------------------------------------------------------
# format_driver_standings — reserve filtering rules
# ---------------------------------------------------------------------------


def _make_snap(driver_user_id: int, position: int, total_points: int, race_participant: bool = False) -> DriverStandingsSnapshot:
    return DriverStandingsSnapshot(
        id=0,
        round_id=1,
        division_id=1,
        driver_user_id=driver_user_id,
        standing_position=position,
        total_points=total_points,
        finish_counts={},
        first_finish_rounds={},
        race_participant=race_participant,
    )


def test_driver_standings_non_reserve_always_shown():
    """Non-reserve drivers are always listed, even at 0 points."""
    snaps = [_make_snap(100, 1, 0)]
    result = format_driver_standings(snaps, reserve_user_ids=set(), show_reserves=True)
    assert "<@100>" in result


def test_driver_standings_reserve_with_points_shown_reserves_on():
    """Reserve with points is shown when show_reserves=True."""
    snaps = [_make_snap(200, 1, 5)]
    result = format_driver_standings(snaps, reserve_user_ids={200}, show_reserves=True)
    assert "<@200>" in result


def test_driver_standings_reserve_zero_pts_no_participation_hidden():
    """Reserve with 0 points and no race participation is never shown."""
    snaps = [_make_snap(200, 1, 0, race_participant=False)]
    result = format_driver_standings(snaps, reserve_user_ids={200}, show_reserves=True)
    assert "<@200>" not in result


def test_driver_standings_reserve_dnf_shown_when_reserves_on():
    """Reserve with 0 points but who participated (DNF) is shown when show_reserves=True."""
    snaps = [_make_snap(200, 1, 0, race_participant=True)]
    result = format_driver_standings(snaps, reserve_user_ids={200}, show_reserves=True)
    assert "<@200>" in result


def test_driver_standings_reserve_dnf_hidden_when_reserves_off():
    """Reserve with 0 points and participation is hidden when show_reserves=False."""
    snaps = [_make_snap(200, 1, 0, race_participant=True)]
    result = format_driver_standings(snaps, reserve_user_ids={200}, show_reserves=False)
    assert "<@200>" not in result


def test_driver_standings_reserve_with_points_hidden_reserves_off():
    """Reserve with points is hidden when show_reserves=False."""
    snaps = [_make_snap(200, 1, 5)]
    result = format_driver_standings(snaps, reserve_user_ids={200}, show_reserves=False)
    assert "<@200>" not in result



# ---------------------------------------------------------------------------
# The shared rendering layer (039, Constitution XIV.7)
#
# These cover the single derivation both the textual table and the results graphic
# draw from. A cell of None means "does not apply": the table renders NOT_APPLICABLE,
# the graphic empties the field.
# ---------------------------------------------------------------------------

def test_render_time_penalty_whole_seconds_carries_no_decimal():
    assert render_time_penalty(5000) == "+5s"


def test_render_time_penalty_fraction_is_three_decimal_places():
    """Five and a half seconds is "+5.500s" — never rounded to a whole second."""
    assert render_time_penalty(5500) == "+5.500s"


def test_render_time_penalty_sub_second_keeps_its_precision():
    assert render_time_penalty(750) == "+0.750s"


def test_render_time_penalty_negative_is_signed():
    assert render_time_penalty(-5000) == "-5s"
    assert render_time_penalty(-5500) == "-5.500s"


def test_render_time_penalty_none_where_no_penalty_applied():
    assert render_time_penalty(0) is None


def test_render_lap_time_shows_hours_only_where_there_are_any():
    assert render_lap_time(83456) == "1:23.456"
    assert render_lap_time(5025678) == "1:23:45.678"


def test_render_gap_shows_minutes_only_where_there_are_any():
    assert render_gap(456) == "+0.456"
    assert render_gap(83456) == "+1:23.456"


def test_qualifying_reference_lap_is_first_placed_entry():
    rows = build_qualifying_rows(
        [_make_qual(1, 100, best_lap="1:23.000"), _make_qual(2, 101, best_lap="1:23.500")],
        {100: 25, 101: 18},
    )
    assert rows[0].gap is None  # the entry holding the reference lap carries no gap
    assert rows[1].gap == "+0.500"


def test_qualifying_reference_falls_to_first_entry_holding_a_lap():
    """Where the first-placed entry set no lap, the reference is the first that did."""
    rows = build_qualifying_rows(
        [
            _make_qual(1, 100, outcome=OutcomeModifier.DNS, best_lap=None),
            _make_qual(2, 101, best_lap="1:23.000"),
            _make_qual(3, 102, best_lap="1:24.000"),
        ],
        {},
    )
    assert rows[1].gap == "+0.000"
    assert rows[2].gap == "+1.000"


def test_qualifying_gap_empty_for_every_entry_where_none_set_a_lap():
    rows = build_qualifying_rows(
        [
            _make_qual(1, 100, outcome=OutcomeModifier.DNS, best_lap=None),
            _make_qual(2, 101, outcome=OutcomeModifier.DNS, best_lap=None),
        ],
        {},
    )
    assert all(row.gap is None for row in rows)


def test_qualifying_outcome_literal_displaces_the_best_lap():
    rows = build_qualifying_rows([_make_qual(1, 100, outcome=OutcomeModifier.DSQ)], {})
    assert rows[0].best_lap == "DSQ"


def test_qualifying_sanctions_carry_dsq_from_the_phase_that_applied_it():
    rows = build_qualifying_rows(
        [_make_qual(1, 100), _make_qual(2, 101)],
        {},
        dsq_phase_map={0: "APPEAL"},
    )
    # Both fabricated rows share id 0, so both take the appeal DSQ; what matters is
    # that it lands in the appeal cell and not the penalty one.
    assert rows[0].appeal_penalty == "DSQ"
    assert rows[0].postrace_penalty is None


def test_race_first_placed_carries_total_time_and_others_an_interval():
    leader = _make_race(1, 100)
    follower = _make_race(2, 101)
    follower = RaceSessionResult(**{**follower.__dict__, "base_time_ms": 5026678})
    rows = build_race_rows([leader, follower], {})
    assert rows[0].time == "1:23:45.678"
    assert rows[1].time == "+1.000"


def test_race_laps_behind_is_singular_for_one_and_plural_beyond():
    one = RaceSessionResult(**{**_make_race(2, 101).__dict__, "laps_behind": 1})
    many = RaceSessionResult(**{**_make_race(3, 102).__dict__, "laps_behind": 3})
    rows = build_race_rows([_make_race(1, 100), one, many], {})
    assert rows[1].time == "+1 Lap"
    assert rows[2].time == "+3 Laps"


def test_race_outcome_literal_displaces_the_time():
    dnf = RaceSessionResult(
        **{**_make_race(2, 101).__dict__, "outcome": OutcomeModifier.DNF}
    )
    rows = build_race_rows([_make_race(1, 100), dnf], {})
    assert rows[1].time == "DNF"


def test_race_every_entry_carries_its_own_time_where_the_leader_records_none():
    leader = RaceSessionResult(**{**_make_race(1, 100).__dict__, "base_time_ms": None})
    follower = _make_race(2, 101)
    rows = build_race_rows([leader, follower], {})
    assert rows[0].time is None
    assert rows[1].time == "1:23:45.678"


def test_race_ingame_penalty_keeps_its_fraction():
    entry = RaceSessionResult(
        **{**_make_race(1, 100).__dict__, "ingame_time_penalties_ms": 5500}
    )
    rows = build_race_rows([entry], {})
    assert rows[0].ingame_penalty == "+5.500s"


def test_race_disqualified_twice_carries_dsq_on_appeal_and_the_penalty_below():
    entry = RaceSessionResult(
        **{**_make_race(1, 100).__dict__, "postrace_time_penalties_ms": 10000}
    )
    rows = build_race_rows([entry], {}, dsq_phase_map={0: "APPEAL"})
    assert rows[0].appeal_penalty == "DSQ"
    assert rows[0].postrace_penalty == "+10s"


def test_fastest_lap_holder_is_the_entry_the_bonus_was_conferred_on():
    holder = RaceSessionResult(**{**_make_race(2, 101).__dict__, "fastest_lap_bonus": 1})
    rows = build_race_rows([_make_race(1, 100), holder], {})
    found = fastest_lap_holder(rows)
    assert found is not None and found.driver_user_id == 101
    assert [row.holds_fastest_lap for row in rows] == [False, True]


def test_fastest_lap_holder_is_none_where_the_session_conferred_no_bonus():
    assert fastest_lap_holder(build_race_rows([_make_race(1, 100)], {})) is None


def test_text_table_renders_a_none_cell_as_the_placeholder():
    """The table's placeholder and the graphic's emptying are the same cell (FR-013)."""
    text = format_qualifying_table([_make_qual(1, 100)], {100: 25})
    assert NOT_APPLICABLE in text  # the first-placed entry's gap
