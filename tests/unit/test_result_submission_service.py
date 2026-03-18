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
