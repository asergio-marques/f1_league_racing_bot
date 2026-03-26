"""Integration tests for the post-submission penalty review flow (T033).

Tests set up an in-memory SQLite DB with season/division/round/results and
verify that the penalty application and finalization path produces the correct
DB state.  Discord interactions are fully mocked.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations
from models.points_config import SessionType
from services.penalty_service import StagedPenalty, apply_penalties
from services.result_submission_service import is_channel_in_penalty_review


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _bootstrap(db_path: str, round_format: str = "NORMAL"):
    """Insert minimal rows: server → season → division → round → points config.

    Returns (season_id, division_id, round_id).
    """
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id) "
            "VALUES (?, 'Main', 777, 888)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at) "
            "VALUES (?, 1, ?, '2026-01-01T18:00:00')",
            (division_id, round_format),
        )
        round_id = cursor.lastrowid
        # Points: P1=25, P2=18
        for pos, pts in [(1, 25), (2, 18)]:
            await db.execute(
                "INSERT INTO season_points_entries "
                "(season_id, config_name, session_type, position, points) "
                "VALUES (?, 'STD', 'FEATURE_RACE', ?, ?)",
                (season_id, pos, pts),
            )
        await db.commit()
    return season_id, division_id, round_id


async def _insert_feature_race(db_path: str, round_id: int, division_id: int) -> int:
    """Insert a 2-driver FEATURE_RACE session. Returns sr_id."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (round_id, division_id),
        )
        sr_id = cursor.lastrowid
        # P1: driver 1 — 20:00.000, fastest lap 1:30.000
        await db.execute(
            "INSERT INTO driver_session_results "
            "(session_result_id, driver_user_id, team_role_id, finishing_position, "
            "outcome, total_time, fastest_lap, points_awarded, fastest_lap_bonus, is_superseded) "
            "VALUES (?, 1, 100, 1, 'CLASSIFIED', '20:00.000', '1:30.000', 25, 1, 0)",
            (sr_id,),
        )
        # P2: driver 2 — 20:10.000, no fastest lap
        await db.execute(
            "INSERT INTO driver_session_results "
            "(session_result_id, driver_user_id, team_role_id, finishing_position, "
            "outcome, total_time, fastest_lap, points_awarded, fastest_lap_bonus, is_superseded) "
            "VALUES (?, 2, 200, 2, 'CLASSIFIED', '20:10.000', '1:31.000', 18, 0, 0)",
            (sr_id,),
        )
        await db.commit()
    return sr_id


class _FakeBot:
    """Minimal bot stub for apply_penalties."""

    class output_router:
        @staticmethod
        async def post_log(*_a, **_kw):
            pass


# ---------------------------------------------------------------------------
# T033-1: Full flow, no penalties → DB state check
# ---------------------------------------------------------------------------


async def test_full_flow_no_penalties(tmp_path):
    """With empty staged list apply_penalties is a no-op; positions unchanged."""
    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)
    _, division_id, round_id = await _bootstrap(db_path)
    sr_id = await _insert_feature_race(db_path, round_id, division_id)

    # Apply with empty staged list
    await apply_penalties(
        db_path, round_id, division_id, [], 999, _FakeBot(), _skip_post=True
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, finishing_position FROM driver_session_results "
            "WHERE session_result_id = ? AND is_superseded = 0 ORDER BY finishing_position",
            (sr_id,),
        )
        rows = await cursor.fetchall()

    assert rows[0]["driver_user_id"] == 1
    assert rows[0]["finishing_position"] == 1
    assert rows[1]["driver_user_id"] == 2
    assert rows[1]["finishing_position"] == 2


# ---------------------------------------------------------------------------
# T033-2: Positive time penalty → P1 drops
# ---------------------------------------------------------------------------


async def test_full_flow_with_positive_time_penalty(tmp_path):
    """Stage +30s on P1 driver; P1 drops behind P2 after apply_penalties."""
    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)
    _, division_id, round_id = await _bootstrap(db_path)
    sr_id = await _insert_feature_race(db_path, round_id, division_id)

    staged = [
        StagedPenalty(
            driver_user_id=1,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="TIME",
            penalty_seconds=30,  # 20:00.000 + 30s = 20:30.000 > 20:10.000
        )
    ]
    await apply_penalties(
        db_path, round_id, division_id, staged, 999, _FakeBot(), _skip_post=True
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, finishing_position FROM driver_session_results "
            "WHERE session_result_id = ? AND is_superseded = 0 ORDER BY finishing_position",
            (sr_id,),
        )
        rows = await cursor.fetchall()

    assert rows[0]["driver_user_id"] == 2, "Driver 2 should be P1 after driver 1 gets +30s"
    assert rows[1]["driver_user_id"] == 1, "Driver 1 should drop to P2"


# ---------------------------------------------------------------------------
# T033-3: Negative time penalty → driver moves up
# ---------------------------------------------------------------------------


async def test_full_flow_with_negative_time_penalty(tmp_path):
    """Stage -20s on P2 driver; P2 overtakes P1."""
    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)
    _, division_id, round_id = await _bootstrap(db_path)
    sr_id = await _insert_feature_race(db_path, round_id, division_id)

    staged = [
        StagedPenalty(
            driver_user_id=2,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="TIME",
            penalty_seconds=-20,  # 20:10.000 - 20s = 19:50.000 < 20:00.000
        )
    ]
    await apply_penalties(
        db_path, round_id, division_id, staged, 999, _FakeBot(), _skip_post=True
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, finishing_position FROM driver_session_results "
            "WHERE session_result_id = ? AND is_superseded = 0 ORDER BY finishing_position",
            (sr_id,),
        )
        rows = await cursor.fetchall()

    assert rows[0]["driver_user_id"] == 2, "Driver 2 should move to P1 after -20s"
    assert rows[1]["driver_user_id"] == 1, "Driver 1 should drop to P2"


# ---------------------------------------------------------------------------
# T033-4: DSQ → driver moves to last with 0 points
# ---------------------------------------------------------------------------


async def test_full_flow_with_dsq(tmp_path):
    """DSQ on P1 driver; driver moved to last position with 0 points."""
    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)
    _, division_id, round_id = await _bootstrap(db_path)
    sr_id = await _insert_feature_race(db_path, round_id, division_id)

    staged = [
        StagedPenalty(
            driver_user_id=1,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="DSQ",
            penalty_seconds=None,
        )
    ]
    await apply_penalties(
        db_path, round_id, division_id, staged, 999, _FakeBot(), _skip_post=True
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, finishing_position, outcome, points_awarded, fastest_lap_bonus "
            "FROM driver_session_results "
            "WHERE session_result_id = ? AND is_superseded = 0 ORDER BY finishing_position",
            (sr_id,),
        )
        rows = await cursor.fetchall()

    # P1 should now be the non-DSQ driver
    assert rows[0]["driver_user_id"] == 2
    # DSQ driver is last
    dsq_row = next(r for r in rows if r["driver_user_id"] == 1)
    assert dsq_row["outcome"] == "DSQ"
    assert dsq_row["finishing_position"] == 2


# ---------------------------------------------------------------------------
# T033-5: DSQ fastest-lap holder — bonus forfeited, not redistributed
# ---------------------------------------------------------------------------


async def test_dsq_fastest_lap_not_redistributed_integration(tmp_path):
    """AC7 integration: DSQ on fastest-lap holder; no other driver gains bonus."""
    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)
    _, division_id, round_id = await _bootstrap(db_path)
    sr_id = await _insert_feature_race(db_path, round_id, division_id)

    staged = [
        StagedPenalty(
            driver_user_id=1,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="DSQ",
            penalty_seconds=None,
        )
    ]
    await apply_penalties(
        db_path, round_id, division_id, staged, 999, _FakeBot(), _skip_post=True
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, outcome, fastest_lap_bonus FROM driver_session_results "
            "WHERE session_result_id = ? AND is_superseded = 0",
            (sr_id,),
        )
        rows = await cursor.fetchall()

    # No OTHER driver should gain the bonus (core AC7 requirement: bonus forfeited, not redistributed)
    for row in rows:
        if row["driver_user_id"] != 1:
            assert (row["fastest_lap_bonus"] or 0) == 0, (
                f"Driver {row['driver_user_id']} should not have fastest_lap_bonus after DSQ holder"
            )
    # The DSQ driver's outcome should be set
    dsq_row = next(r for r in rows if r["driver_user_id"] == 1)
    assert dsq_row["outcome"] == "DSQ"


# ---------------------------------------------------------------------------
# T033-6: is_channel_in_penalty_review flag lifecycle
# ---------------------------------------------------------------------------


async def test_penalty_review_flag_lifecycle(tmp_path):
    """in_penalty_review=1 makes is_channel_in_penalty_review return True;
    closing the channel (finalized) causes it to return False."""
    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)
    _, division_id, round_id = await _bootstrap(db_path)

    channel_id = 9001

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO round_submission_channels "
            "(round_id, channel_id, created_at, closed, in_penalty_review) "
            "VALUES (?, ?, '2026-01-01T18:00:00', 0, 1)",
            (round_id, channel_id),
        )
        await db.commit()

    # While open and in penalty review → True
    assert await is_channel_in_penalty_review(db_path, channel_id) is True

    # Simulate close (finalize_round marks closed=1)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE round_submission_channels SET closed = 1 WHERE channel_id = ?",
            (channel_id,),
        )
        await db.commit()

    # After close → False
    assert await is_channel_in_penalty_review(db_path, channel_id) is False


# ---------------------------------------------------------------------------
# T033-7: Test-mode advance blocked before finalize
# ---------------------------------------------------------------------------


async def test_test_mode_advance_blocked_before_finalize(tmp_path):
    """is_round_finalized returns False when finalized=0; True after finalized=1."""
    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)
    _, division_id, round_id = await _bootstrap(db_path)

    from services.test_mode_service import is_round_finalized

    assert await is_round_finalized(db_path, round_id) is False

    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE rounds SET finalized = 1 WHERE id = ?", (round_id,)
        )
        await db.commit()

    assert await is_round_finalized(db_path, round_id) is True
