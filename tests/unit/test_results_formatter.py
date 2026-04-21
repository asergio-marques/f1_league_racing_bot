"""Unit tests for results_formatter (T032)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.session_result import OutcomeModifier, QualifyingSessionResult, RaceSessionResult
from models.standings_snapshot import DriverStandingsSnapshot
from utils.results_formatter import _collapse_trailing_zeros, format_driver_standings, format_qualifying_table, format_race_table


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

