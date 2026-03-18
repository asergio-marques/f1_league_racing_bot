"""Unit tests for results_formatter (T032)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.session_result import DriverSessionResult, OutcomeModifier
from utils.results_formatter import _collapse_trailing_zeros, format_qualifying_table, format_race_table


def _make_dsr(
    position: int,
    driver_user_id: int = 100,
    team_role_id: int = 200,
    outcome: OutcomeModifier = OutcomeModifier.CLASSIFIED,
    fastest_lap: str | None = "1:23.456",
) -> DriverSessionResult:
    return DriverSessionResult(
        id=0,
        session_result_id=1,
        driver_user_id=driver_user_id,
        finishing_position=position,
        team_role_id=team_role_id,
        tyre="Soft",
        best_lap="1:23.456",
        gap="N/A",
        total_time="1:23:45.678",
        fastest_lap=fastest_lap,
        time_penalties=None,
        outcome=outcome,
        points_awarded=25,
        fastest_lap_bonus=0,
        post_steward_total_time=None,
        post_race_time_penalties=None,
        is_superseded=False,
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
    rows = [_make_dsr(1)]
    table = format_qualifying_table(
        rows,
        points_by_driver={100: 25},
        member_display={100: "Driver One"},
        team_display={200: "Red Bull"},
    )
    table_lower = table.lower()
    assert "pos" in table_lower
    assert "driver" in table_lower
    assert "```" in table


# ---------------------------------------------------------------------------
# format_race_table — header row check
# ---------------------------------------------------------------------------


def test_format_race_table_headers():
    rows = [_make_dsr(1)]
    table = format_race_table(
        rows,
        points_by_driver={100: 25},
        member_display={100: "Driver One"},
        team_display={200: "Red Bull"},
    )
    table_lower = table.lower()
    assert "pos" in table_lower
    assert "```" in table
