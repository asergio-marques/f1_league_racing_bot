"""Unit tests for result_submission_service (T030)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.points_config import SessionType
from models.round import RoundFormat
from services.result_submission_service import (
    ParsedQualifyingRow,
    ParsedRaceRow,
    _format_time_ms,
    _parse_time_to_ms,
    get_sessions_for_format,
    validate_qualifying_row,
    validate_race_row,
    validate_submission_block,
)


# ---------------------------------------------------------------------------
# get_sessions_for_format
# ---------------------------------------------------------------------------


def test_get_sessions_for_format_normal():
    sessions = get_sessions_for_format(RoundFormat.NORMAL)
    session_names = {s.value for s in sessions}
    assert "FEATURE_QUALIFYING" in session_names
    assert "FEATURE_RACE" in session_names


def test_get_sessions_for_format_sprint():
    sessions = get_sessions_for_format(RoundFormat.SPRINT)
    session_names = {s.value for s in sessions}
    assert "SPRINT_QUALIFYING" in session_names
    assert "SPRINT_RACE" in session_names
    assert "FEATURE_RACE" in session_names


def test_get_sessions_for_format_endurance():
    sessions = get_sessions_for_format(RoundFormat.ENDURANCE)
    session_names = {s.value for s in sessions}
    assert "FEATURE_RACE" in session_names


# ---------------------------------------------------------------------------
# validate_qualifying_row
# ---------------------------------------------------------------------------


def test_validate_qualifying_row_rejects_wrong_position():
    line = "abc, <@123>, <@&456>, Soft, 1:23.456, N/A"
    result = validate_qualifying_row(line)
    assert isinstance(result, str)
    assert "Position" in result


def test_validate_qualifying_row_rejects_invalid_mention():
    line = "1, notamention, <@&456>, Soft, 1:23.456, N/A"
    result = validate_qualifying_row(line)
    assert isinstance(result, str)
    assert "Discord member mention" in result


def test_validate_qualifying_row_rejects_invalid_time():
    line = "1, <@123>, <@&456>, Soft, badtime, N/A"
    result = validate_qualifying_row(line)
    assert isinstance(result, str)
    assert "Best Lap" in result


def test_validate_qualifying_row_success():
    line = "1, <@123>, <@&456>, Soft, 1:23.456, N/A"
    result = validate_qualifying_row(line)
    assert isinstance(result, ParsedQualifyingRow)
    assert result.position == 1
    assert result.driver_user_id == 123
    assert result.team_role_id == 456


def test_validate_qualifying_row_dns():
    line = "1, <@100>, <@&200>, N/A, DNS, N/A"
    result = validate_qualifying_row(line)
    assert isinstance(result, ParsedQualifyingRow)


# ---------------------------------------------------------------------------
# validate_race_row
# ---------------------------------------------------------------------------


def test_validate_race_row_accepts_delta_format():
    line = "2, <@200>, <@&300>, +1:23.456, 1:23.456, N/A"
    result = validate_race_row(line, is_first=False)
    assert isinstance(result, ParsedRaceRow)
    assert result.position == 2


def test_validate_race_row_dnf():
    line = "3, <@300>, <@&400>, DNF, N/A, N/A"
    result = validate_race_row(line, is_first=False)
    assert isinstance(result, ParsedRaceRow)


def test_validate_race_row_dns():
    line = "4, <@400>, <@&500>, DNS, N/A, N/A"
    result = validate_race_row(line, is_first=False)
    assert isinstance(result, ParsedRaceRow)


def test_validate_race_row_first_place_must_be_absolute():
    line = "1, <@100>, <@&200>, +0:00.000, 1:23.456, N/A"
    result = validate_race_row(line, is_first=True)
    assert isinstance(result, str)
    assert "absolute" in result.lower() or "1st" in result.lower()


def test_validate_race_row_first_place_success():
    line = "1, <@100>, <@&200>, 1:23:45.678, 1:23.456, N/A"
    result = validate_race_row(line, is_first=True)
    assert isinstance(result, ParsedRaceRow)
    assert result.position == 1


# ---------------------------------------------------------------------------
# validate_submission_block — position gaps and wrong team
# ---------------------------------------------------------------------------


def _make_qual_block(lines: list[str]) -> list[ParsedQualifyingRow | ParsedRaceRow] | list[str]:
    return validate_submission_block(
        lines,
        session_type=SessionType.FEATURE_QUALIFYING,
        division_driver_ids={100, 200},
        team_role_ids={300, 400},
        reserve_team_role_id=None,
        driver_team_map={100: 300, 200: 400},
    )


def test_validate_submission_block_position_gap():
    lines = [
        "1, <@100>, <@&300>, Soft, 1:23.456, N/A",
        "3, <@200>, <@&400>, Soft, 1:24.000, +0:00.544",  # gap: no position 2
    ]
    result = _make_qual_block(lines)
    assert isinstance(result, list)
    assert all(isinstance(r, str) for r in result)
    combined = " ".join(result)
    assert "gap" in combined.lower() or "position" in combined.lower()


def test_validate_submission_block_wrong_team_driver():
    # Driver 100 is assigned to team 300, but submits team 400
    lines = [
        "1, <@100>, <@&400>, Soft, 1:23.456, N/A",
        "2, <@200>, <@&400>, Soft, 1:24.000, +0:00.544",
    ]
    result = _make_qual_block(lines)
    assert isinstance(result, list)
    assert all(isinstance(r, str) for r in result)
    combined = " ".join(result)
    assert "assigned to" in combined.lower() or "team" in combined.lower() or "submitted as" in combined.lower()


def test_validate_submission_block_success():
    lines = [
        "1, <@100>, <@&300>, Soft, 1:23.456, N/A",
        "2, <@200>, <@&400>, Soft, 1:24.000, +0:00.544",
    ]
    result = _make_qual_block(lines)
    assert isinstance(result, list)
    assert all(isinstance(r, ParsedQualifyingRow) for r in result)


# ---------------------------------------------------------------------------
# G2 — regex accepts sub-10-second values
# ---------------------------------------------------------------------------


def test_validate_qualifying_row_accepts_sub10s_gap():
    """G2: +0.039 must be accepted (was rejected by old \\d{2} pattern)."""
    line = "2, <@200>, <@&400>, Soft, 1:23.456, +0.039"
    result = validate_qualifying_row(line)
    assert isinstance(result, ParsedQualifyingRow)


def test_validate_qualifying_row_accepts_sub10s_best_lap():
    """G2: A best lap of 9.456 (9 seconds) must be accepted."""
    line = "1, <@100>, <@&300>, Soft, 9.456, N/A"
    result = validate_qualifying_row(line)
    assert isinstance(result, ParsedQualifyingRow)


def test_validate_race_row_accepts_sub10s_gap():
    """G2: +0.202 delta must be accepted for race Total Time."""
    line = "2, <@200>, <@&300>, +0.202, 1:14.532, 0.000"
    result = validate_race_row(line, is_first=False)
    assert isinstance(result, ParsedRaceRow)


# ---------------------------------------------------------------------------
# C2 — P1 gap input ignored entirely
# ---------------------------------------------------------------------------


def test_validate_qualifying_row_p1_gap_ignored():
    """C2: P1 gap with any value (even invalid format) must be accepted."""
    line = "1, <@100>, <@&300>, Soft, 1:23.456, WHATEVER_IGNORED"
    result = validate_qualifying_row(line)
    assert isinstance(result, ParsedQualifyingRow)


def test_validate_qualifying_row_p2_gap_validated():
    """C2: P2+ gap is still validated — bad format must fail."""
    line = "2, <@200>, <@&400>, Soft, 1:24.000, INVALID"
    result = validate_qualifying_row(line)
    assert isinstance(result, str)
    assert "Gap" in result


# ---------------------------------------------------------------------------
# C3 — Fastest Lap validation skipped when Total Time is outcome literal
# ---------------------------------------------------------------------------


def test_validate_race_row_dsq_fl_skipped():
    """C3: DSQ Total Time — Fastest Lap validation skipped (N/A allowed)."""
    line = "2, <@200>, <@&300>, DSQ, N/A, 0.000"
    result = validate_race_row(line, is_first=False)
    assert isinstance(result, ParsedRaceRow)


def test_validate_race_row_dnf_fl_skipped():
    """C3: DNF Total Time — Fastest Lap validation skipped."""
    line = "3, <@300>, <@&400>, DNF, N/A, 0.000"
    result = validate_race_row(line, is_first=False)
    assert isinstance(result, ParsedRaceRow)


def test_validate_race_row_dns_fl_skipped():
    """C3: DNS Total Time — Fastest Lap validation skipped."""
    line = "4, <@400>, <@&500>, DNS, N/A, 0.000"
    result = validate_race_row(line, is_first=False)
    assert isinstance(result, ParsedRaceRow)


def test_validate_race_row_normal_fl_validated():
    """C3: Normal Total Time — Fastest Lap must still be valid."""
    line = "2, <@200>, <@&300>, +5.321, BADLAP, 0.000"
    result = validate_race_row(line, is_first=False)
    assert isinstance(result, str)
    assert "Fastest Lap" in result


# ---------------------------------------------------------------------------
# G1 — DNF best-lap derivation in validate_submission_block
# ---------------------------------------------------------------------------


def _make_qual_block_3(lines):
    return validate_submission_block(
        lines,
        session_type=SessionType.FEATURE_QUALIFYING,
        division_driver_ids={100, 200, 300},
        team_role_ids={400, 500, 600},
        reserve_team_role_id=None,
        driver_team_map={100: 400, 200: 500, 300: 600},
    )


def test_dnf_best_lap_derived_from_gap():
    """G1: DNF + valid gap → best_lap computed as P1_best_lap + gap."""
    lines = [
        "1, <@100>, <@&400>, Soft, 1:11.606, N/A",   # P1 best lap = 71606ms
        "2, <@200>, <@&500>, Soft, 1:11.645, +0.039", # normal
        "3, <@300>, <@&600>, Soft, DNF, +0.202",       # DNF + gap → derived
    ]
    result = _make_qual_block_3(lines)
    assert not isinstance(result[0], str), f"Expected parsed rows, got errors: {result}"
    p3 = next(r for r in result if r.position == 3)
    # P1 best lap 1:11.606 + 0.202 = 1:11.808
    assert p3.best_lap == "1:11.808"


def test_dnf_best_lap_not_derived_without_valid_gap():
    """G1: DNF with N/A gap → best_lap stays as DNF."""
    lines = [
        "1, <@100>, <@&400>, Soft, 1:11.606, N/A",
        "2, <@200>, <@&500>, Soft, DNF, N/A",         # DNF, gap = N/A → no derivation
    ]
    result = validate_submission_block(
        lines,
        session_type=SessionType.FEATURE_QUALIFYING,
        division_driver_ids={100, 200},
        team_role_ids={400, 500},
        reserve_team_role_id=None,
        driver_team_map={100: 400, 200: 500},
    )
    assert not isinstance(result[0], str)
    p2 = next(r for r in result if r.position == 2)
    assert p2.best_lap == "DNF"


# ---------------------------------------------------------------------------
# _parse_time_to_ms / _format_time_ms helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("time_str,expected_ms", [
    ("0.039", 39),
    ("9.456", 9456),
    ("1:11.606", 71606),
    ("1:23.456", 83456),
    ("1:23:45.678", 5025678),
    ("+0.039", 39),
    ("+1:11.606", 71606),
])
def test_parse_time_to_ms(time_str, expected_ms):
    assert _parse_time_to_ms(time_str) == expected_ms


@pytest.mark.parametrize("ms,expected_str", [
    (39, "0.039"),
    (9456, "9.456"),
    (71606, "1:11.606"),
    (83456, "1:23.456"),
])
def test_format_time_ms(ms, expected_str):
    assert _format_time_ms(ms) == expected_str

