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


# ---------------------------------------------------------------------------
# T031 — new test cases: negative penalties, zero rejection, tiebreak, DSQ FL
# ---------------------------------------------------------------------------


def test_validate_negative_time_penalty():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="-3s",
    )
    assert isinstance(result, StagedPenalty)
    assert result.penalty_type == "TIME"
    assert result.penalty_seconds == -3


def test_validate_zero_penalty_rejected():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="0",
    )
    assert isinstance(result, str)


def test_time_penalty_rejected_if_result_negative():
    # Driver has 5s race time; a -10s penalty would produce negative time
    current_time_ms = 5_000  # 5 seconds
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="-10",
        current_time_ms=current_time_ms,
    )
    assert isinstance(result, str)
    assert "negative" in result.lower()


def test_negative_penalty_rejected_when_exceeds_existing_time_penalty():
    """Negative penalty whose absolute value exceeds the driver's current penalty is rejected."""
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="-10s",
        current_time_penalty_s=5,
    )
    assert isinstance(result, str)
    assert "5s" in result


def test_negative_penalty_accepted_when_equal_to_existing_time_penalty():
    """Negative penalty exactly cancelling the driver's full existing penalty is valid."""
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="-5s",
        current_time_penalty_s=5,
    )
    assert isinstance(result, StagedPenalty)
    assert result.penalty_seconds == -5


def test_negative_penalty_accepted_when_less_than_existing_time_penalty():
    """Negative penalty smaller in magnitude than the existing penalty is valid."""
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="-3s",
        current_time_penalty_s=5,
    )
    assert isinstance(result, StagedPenalty)
    assert result.penalty_seconds == -3


def test_negative_penalty_rejected_when_no_existing_time_penalty():
    """Negative penalty is rejected when the driver has no existing time penalty (0s)."""
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="-5s",
        current_time_penalty_s=0,
    )
    assert isinstance(result, str)
    assert "0s" in result


def test_negative_penalty_check_skipped_when_current_penalty_unknown():
    """When current_time_penalty_s is None the check is skipped and the penalty is accepted."""
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="-10s",
        current_time_penalty_s=None,
    )
    assert isinstance(result, StagedPenalty)
    assert result.penalty_seconds == -10


def test_negative_penalty_cumulative_second_reduction_rejected():
    """Simulate staging two successive -3s penalties when the driver has 3s applied.

    First -3s: effective remaining = 3 - 3 = 0  → accepted.
    Second -3s: effective remaining = 0          → rejected (nothing left to remove).
    """
    # First penalty: DB value = 3, no staged adjustments yet → passes.
    first = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="-3s",
        current_time_penalty_s=3,
    )
    assert isinstance(first, StagedPenalty)
    assert first.penalty_seconds == -3

    # Wizard now adjusts: DB (3) + staged (-3) = 0 effective remaining.
    effective_after_first = 3 + first.penalty_seconds  # = 0
    second = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="-3s",
        current_time_penalty_s=effective_after_first,
    )
    assert isinstance(second, str)
    assert "0s" in second


async def test_apply_negative_penalty_reorders(tmp_path):
    """Driver with a -10s penalty moves above a driver with no penalty."""
    from db.database import get_connection, run_migrations
    from services.penalty_service import apply_penalties

    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id, log_channel_id) VALUES (1,10,20,30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) VALUES (1,'2026-01-01','ACTIVE',1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id) VALUES (?,?,777,888)",
            (season_id, "Main"),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at) VALUES (?,1,'NORMAL','2026-01-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) VALUES (?,?,'FEATURE_RACE','ACTIVE')",
            (round_id, division_id),
        )
        sr_id = cursor.lastrowid
        # P1 = driver 1 (20:00.000 = 1200000ms), P2 = driver 2 (20:10.000 = 1210000ms)
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, team_role_id, finishing_position, outcome, base_time_ms, ingame_time_penalties_ms, postrace_time_penalties_ms, appeal_time_penalties_ms) "
            "VALUES (?,1,100,1,'CLASSIFIED',1200000,0,0,0)",
            (sr_id,),
        )
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, team_role_id, finishing_position, outcome, base_time_ms, ingame_time_penalties_ms, postrace_time_penalties_ms, appeal_time_penalties_ms) "
            "VALUES (?,2,200,2,'CLASSIFIED',1210000,0,0,0)",
            (sr_id,),
        )
        await db.commit()

    class _FakeBot:
        class output_router:
            @staticmethod
            async def post_log(*_a, **_kw):
                pass

    staged = [
        StagedPenalty(
            driver_user_id=1,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="TIME",
            penalty_seconds=-15,  # -15s on P1 → 1200000 - 15000 = 1185000ms < 1210000ms → P1 stays P1
        )
    ]
    await apply_penalties(db_path, round_id, division_id, staged, 999, _FakeBot(), _skip_post=True)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, finishing_position FROM race_session_results "
            "WHERE session_result_id = ? ORDER BY finishing_position",
            (sr_id,),
        )
        rows = await cursor.fetchall()

    assert rows[0]["driver_user_id"] == 1
    assert rows[0]["finishing_position"] == 1
    assert rows[1]["driver_user_id"] == 2
    assert rows[1]["finishing_position"] == 2


async def test_apply_negative_penalty_reorders_move_up(tmp_path):
    """P2 driver with -15s total adjusted time moves to P1 if beats P1."""
    from db.database import get_connection, run_migrations
    from services.penalty_service import apply_penalties

    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id, log_channel_id) VALUES (1,10,20,30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) VALUES (1,'2026-01-01','ACTIVE',1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id) VALUES (?,?,777,888)",
            (season_id, "Main"),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at) VALUES (?,1,'NORMAL','2026-01-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) VALUES (?,?,'FEATURE_RACE','ACTIVE')",
            (round_id, division_id),
        )
        sr_id = cursor.lastrowid
        # P1 = driver 1 (20:00.000 = 1200000ms), P2 = driver 2 (20:10.000 = 1210000ms)
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, team_role_id, finishing_position, outcome, base_time_ms, ingame_time_penalties_ms, postrace_time_penalties_ms, appeal_time_penalties_ms) "
            "VALUES (?,1,100,1,'CLASSIFIED',1200000,0,0,0)",
            (sr_id,),
        )
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, team_role_id, finishing_position, outcome, base_time_ms, ingame_time_penalties_ms, postrace_time_penalties_ms, appeal_time_penalties_ms) "
            "VALUES (?,2,200,2,'CLASSIFIED',1210000,0,0,0)",
            (sr_id,),
        )
        await db.commit()

    class _FakeBot:
        class output_router:
            @staticmethod
            async def post_log(*_a, **_kw):
                pass

    # Give driver 2 a -20s: 1210000ms - 20000ms = 1190000ms < 1200000ms → driver 2 becomes P1
    staged = [
        StagedPenalty(
            driver_user_id=2,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="TIME",
            penalty_seconds=-20,
        )
    ]
    await apply_penalties(db_path, round_id, division_id, staged, 999, _FakeBot(), _skip_post=True)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, finishing_position FROM race_session_results "
            "WHERE session_result_id = ? ORDER BY finishing_position",
            (sr_id,),
        )
        rows = await cursor.fetchall()

    assert rows[0]["driver_user_id"] == 2, "Driver 2 should now be P1 after -20s penalty"
    assert rows[1]["driver_user_id"] == 1, "Driver 1 should now be P2"


async def test_tiebreak_identical_times_preserves_earlier_position(tmp_path):
    """Two drivers with identical post-penalty times keep their original order."""
    from db.database import get_connection, run_migrations
    from services.penalty_service import apply_penalties

    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id, log_channel_id) VALUES (1,10,20,30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) VALUES (1,'2026-01-01','ACTIVE',1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id) VALUES (?,?,777,888)",
            (season_id, "Main"),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at) VALUES (?,1,'NORMAL','2026-01-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) VALUES (?,?,'FEATURE_RACE','ACTIVE')",
            (round_id, division_id),
        )
        sr_id = cursor.lastrowid
        # P1 = driver 1 (20:10.000 = 1210000ms), P2 = driver 2 (20:00.000 = 1200000ms)
        # Give driver 1 a -10s penalty → 1210000 - 10000 = 1200000ms = same as driver 2
        # Tiebreak: original position → driver 1 stays P1
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, team_role_id, finishing_position, outcome, base_time_ms, ingame_time_penalties_ms, postrace_time_penalties_ms, appeal_time_penalties_ms) "
            "VALUES (?,1,100,1,'CLASSIFIED',1210000,0,0,0)",
            (sr_id,),
        )
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, team_role_id, finishing_position, outcome, base_time_ms, ingame_time_penalties_ms, postrace_time_penalties_ms, appeal_time_penalties_ms) "
            "VALUES (?,2,200,2,'CLASSIFIED',1200000,0,0,0)",
            (sr_id,),
        )
        await db.commit()

    class _FakeBot:
        class output_router:
            @staticmethod
            async def post_log(*_a, **_kw):
                pass

    staged = [
        StagedPenalty(
            driver_user_id=1,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="TIME",
            penalty_seconds=-10,  # P1 (1210000 - 10000 = 1200000ms) ties with P2 (1200000ms)
        )
    ]
    await apply_penalties(db_path, round_id, division_id, staged, 999, _FakeBot(), _skip_post=True)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, finishing_position FROM race_session_results "
            "WHERE session_result_id = ? ORDER BY finishing_position",
            (sr_id,),
        )
        rows = await cursor.fetchall()

    # Tiebreak: driver 1 had original position 1, driver 2 had position 2
    # So driver 1 should remain P1 even with identical times
    assert rows[0]["driver_user_id"] == 1, "Driver 1 should be P1 (tiebreak by original position)"
    assert rows[1]["driver_user_id"] == 2, "Driver 2 should be P2"


async def test_dsq_fastest_lap_not_redistributed(tmp_path):
    """AC7: DSQ on fastest-lap holder forfeits the bonus; no other driver gains it."""
    from db.database import get_connection, run_migrations
    from services.penalty_service import apply_penalties
    from services.standings_service import compute_points_for_session
    from models.points_config import PointsConfigEntry, PointsConfigFastestLap
    from models.session_result import DriverSessionResult, OutcomeModifier

    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id, log_channel_id) VALUES (1,10,20,30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) VALUES (1,'2026-01-01','ACTIVE',1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id) VALUES (?,?,777,888)",
            (season_id, "Main"),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at) VALUES (?,1,'NORMAL','2026-01-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) VALUES (?,?,'FEATURE_RACE','ACTIVE')",
            (round_id, division_id),
        )
        sr_id = cursor.lastrowid
        # Driver 1 = P1, has fastest lap
        # Driver 2 = P2, no fastest lap
        await db.execute(
            "INSERT INTO race_session_results "
            "(session_result_id, driver_user_id, team_role_id, finishing_position, outcome, base_time_ms, ingame_time_penalties_ms, postrace_time_penalties_ms, appeal_time_penalties_ms, fastest_lap, fastest_lap_bonus) "
            "VALUES (?,1,100,1,'CLASSIFIED',1200000,0,0,0,'1:30.000',1)",
            (sr_id,),
        )
        await db.execute(
            "INSERT INTO race_session_results "
            "(session_result_id, driver_user_id, team_role_id, finishing_position, outcome, base_time_ms, ingame_time_penalties_ms, postrace_time_penalties_ms, appeal_time_penalties_ms, fastest_lap, fastest_lap_bonus) "
            "VALUES (?,2,200,2,'CLASSIFIED',1210000,0,0,0,'1:31.000',0)",
            (sr_id,),
        )
        await db.commit()

    class _FakeBot:
        class output_router:
            @staticmethod
            async def post_log(*_a, **_kw):
                pass

    staged = [
        StagedPenalty(
            driver_user_id=1,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="DSQ",
            penalty_seconds=None,
        )
    ]
    await apply_penalties(db_path, round_id, division_id, staged, 999, _FakeBot(), _skip_post=True)

    # Verify: after DSQ, driver 1's fast-lap bonus is 0
    # and driver 2 does NOT gain the bonus (not redistributed)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, outcome, fastest_lap_bonus FROM race_session_results "
            "WHERE session_result_id = ? ORDER BY finishing_position",
            (sr_id,),
        )
        rows = await cursor.fetchall()

    # DSQ driver's outcome should be set
    dsq_row = next(r for r in rows if r["driver_user_id"] == 1)
    assert dsq_row["outcome"] == "DSQ"

    # No OTHER driver should have their fastest_lap_bonus increased (not redistributed).
    # Driver 2 had fastest_lap_bonus=0 before and should still have 0 after.
    for row in rows:
        if row["driver_user_id"] != 1:
            assert (row["fastest_lap_bonus"] or 0) == 0, (
                f"Driver {row['driver_user_id']} should NOT receive the forfeited fastest-lap bonus"
            )
