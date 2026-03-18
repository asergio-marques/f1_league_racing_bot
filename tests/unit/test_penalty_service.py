"""Unit tests for penalty_service (T033)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.points_config import SessionType
from services.penalty_service import StagedPenalty, validate_penalty_input


# ---------------------------------------------------------------------------
# validate_penalty_input
# ---------------------------------------------------------------------------


def test_validate_time_penalty_rejected_for_qualifying():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_QUALIFYING,
        penalty_value="+5",
    )
    assert isinstance(result, str)
    assert "qualifying" in result.lower() or "DSQ" in result


def test_validate_time_penalty_rejected_for_sprint_qualifying():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.SPRINT_QUALIFYING,
        penalty_value="5s",
    )
    assert isinstance(result, str)


def test_validate_dsq_accepted_for_qualifying():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_QUALIFYING,
        penalty_value="DSQ",
    )
    assert isinstance(result, StagedPenalty)
    assert result.penalty_type == "DSQ"
    assert result.penalty_seconds is None


def test_validate_time_penalty_accepted_for_race():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="+5",
    )
    assert isinstance(result, StagedPenalty)
    assert result.penalty_type == "TIME"
    assert result.penalty_seconds == 5


def test_validate_time_penalty_with_s_suffix():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="10s",
    )
    assert isinstance(result, StagedPenalty)
    assert result.penalty_seconds == 10


def test_validate_time_penalty_bare_integer():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="5",
    )
    assert isinstance(result, StagedPenalty)
    assert result.penalty_seconds == 5


def test_validate_dsq_accepted_for_race():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="DSQ",
    )
    assert isinstance(result, StagedPenalty)
    assert result.penalty_type == "DSQ"


def test_validate_invalid_penalty_value():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="notapenalty",
    )
    assert isinstance(result, str)


def test_validate_zero_not_accepted():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="0",
    )
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# DSQ supersedes TIME — logical reasoning test
# The StagedPenalty dataclass itself just records state; the "supersede" logic
# lives in the cog wizard. We verify the contract here by simulating how the
# wizard accumulates penalties: a DSQ for the same driver replaces a prior TIME.
# ---------------------------------------------------------------------------


def test_dsq_supersedes_time_in_staged_list():
    """Simulate wizard logic: adding DSQ after TIME for same driver/session."""
    staged: list[StagedPenalty] = []

    def _stage(driver_id: int, session: SessionType, value: str) -> None:
        result = validate_penalty_input(driver_id, session, value)
        assert isinstance(result, StagedPenalty)
        # Wizard logic: DSQ supersedes any existing penalty for same driver/session
        if result.penalty_type == "DSQ":
            staged[:] = [
                p for p in staged
                if not (p.driver_user_id == driver_id and p.session_type == session)
            ]
        staged.append(result)

    _stage(100, SessionType.FEATURE_RACE, "+5")
    assert len(staged) == 1
    assert staged[0].penalty_type == "TIME"

    # Now stage a DSQ — this should supersede the TIME
    _stage(100, SessionType.FEATURE_RACE, "DSQ")
    assert len(staged) == 1
    assert staged[0].penalty_type == "DSQ"
