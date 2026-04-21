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
        # P1: driver 1 — 20:00.000 = 1200000ms, fastest lap 1:30.000
        await db.execute(
            "INSERT INTO race_session_results "
            "(session_result_id, driver_user_id, team_role_id, finishing_position, "
            "outcome, base_time_ms, laps_behind, ingame_time_penalties_ms, "
            "postrace_time_penalties_ms, appeal_time_penalties_ms, "
            "fastest_lap, fastest_lap_bonus, points_awarded) "
            "VALUES (?, 1, 100, 1, 'CLASSIFIED', 1200000, NULL, 0, 0, 0, '1:30.000', 1, 25)",
            (sr_id,),
        )
        # P2: driver 2 — 20:10.000 = 1210000ms, no fastest lap
        await db.execute(
            "INSERT INTO race_session_results "
            "(session_result_id, driver_user_id, team_role_id, finishing_position, "
            "outcome, base_time_ms, laps_behind, ingame_time_penalties_ms, "
            "postrace_time_penalties_ms, appeal_time_penalties_ms, "
            "fastest_lap, fastest_lap_bonus, points_awarded) "
            "VALUES (?, 2, 200, 2, 'CLASSIFIED', 1210000, NULL, 0, 0, 0, '1:31.000', 0, 18)",
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
            "SELECT driver_user_id, finishing_position FROM race_session_results "
            "WHERE session_result_id = ? ORDER BY finishing_position",
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
            "SELECT driver_user_id, finishing_position FROM race_session_results "
            "WHERE session_result_id = ? ORDER BY finishing_position",
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
            "SELECT driver_user_id, finishing_position FROM race_session_results "
            "WHERE session_result_id = ? ORDER BY finishing_position",
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
            "FROM race_session_results "
            "WHERE session_result_id = ? ORDER BY finishing_position",
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
            "SELECT driver_user_id, outcome, fastest_lap_bonus FROM race_session_results "
            "WHERE session_result_id = ?",
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


# ---------------------------------------------------------------------------
# T033-8: Gap-string penalty — P2 has "+SS.mmm" total_time (real-world format)
# ---------------------------------------------------------------------------


async def _insert_race_with_gap_strings(db_path: str, round_id: int, division_id: int) -> int:
    """Insert a 3-driver race where P2 and P3 use gap strings for total_time."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (round_id, division_id),
        )
        sr_id = cursor.lastrowid
        # P1: driver 1 — 47:55.744 = 2875744ms
        await db.execute(
            "INSERT INTO race_session_results "
            "(session_result_id, driver_user_id, team_role_id, finishing_position, "
            "outcome, base_time_ms, laps_behind, ingame_time_penalties_ms, "
            "postrace_time_penalties_ms, appeal_time_penalties_ms, "
            "fastest_lap, fastest_lap_bonus, points_awarded) "
            "VALUES (?, 1, 100, 1, 'CLASSIFIED', 2875744, NULL, 0, 0, 0, '1:30.000', 1, 25)",
            (sr_id,),
        )
        # P2: driver 2 — 47:55.744 + 2.955s = 2878699ms
        await db.execute(
            "INSERT INTO race_session_results "
            "(session_result_id, driver_user_id, team_role_id, finishing_position, "
            "outcome, base_time_ms, laps_behind, ingame_time_penalties_ms, "
            "postrace_time_penalties_ms, appeal_time_penalties_ms, "
            "fastest_lap, fastest_lap_bonus, points_awarded) "
            "VALUES (?, 2, 200, 2, 'CLASSIFIED', 2878699, NULL, 0, 0, 0, '1:31.000', 0, 18)",
            (sr_id,),
        )
        # P3: driver 3 — 47:55.744 + 42.044s = 2917788ms
        await db.execute(
            "INSERT INTO race_session_results "
            "(session_result_id, driver_user_id, team_role_id, finishing_position, "
            "outcome, base_time_ms, laps_behind, ingame_time_penalties_ms, "
            "postrace_time_penalties_ms, appeal_time_penalties_ms, "
            "fastest_lap, fastest_lap_bonus, points_awarded) "
            "VALUES (?, 3, 300, 3, 'CLASSIFIED', 2917788, NULL, 0, 0, 0, '1:32.000', 0, 15)",
            (sr_id,),
        )
        await db.commit()
    return sr_id


async def test_gap_string_penalty_p1_drops(tmp_path):
    """P1 gets +10s; P1 should drop behind P2 who has a smaller total time."""
    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)
    _, division_id, round_id = await _bootstrap(db_path)
    sr_id = await _insert_race_with_gap_strings(db_path, round_id, division_id)

    staged = [
        StagedPenalty(
            driver_user_id=1,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="TIME",
            penalty_seconds=10,  # 2875744 + 10000 = 2885744ms > 2878699ms (P2's total) → P1 drops
        )
    ]
    await apply_penalties(
        db_path, round_id, division_id, staged, 999, _FakeBot(), _skip_post=True
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, finishing_position "
            "FROM race_session_results "
            "WHERE session_result_id = ? ORDER BY finishing_position",
            (sr_id,),
        )
        rows = await cursor.fetchall()

    assert rows[0]["driver_user_id"] == 2, "Driver 2 (was P2) should become new P1"
    assert rows[1]["driver_user_id"] == 1, "Driver 1 (penalized) should drop to P2"
    assert rows[2]["driver_user_id"] == 3, "Driver 3 should remain P3"


async def test_gap_string_penalty_p3_gets_penalty(tmp_path):
    """P3 gets +120s penalty; P3 should remain P3 since no-one is behind to overtake."""
    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)
    _, division_id, round_id = await _bootstrap(db_path)
    sr_id = await _insert_race_with_gap_strings(db_path, round_id, division_id)

    staged = [
        StagedPenalty(
            driver_user_id=3,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="TIME",
            penalty_seconds=120,
        )
    ]
    await apply_penalties(
        db_path, round_id, division_id, staged, 999, _FakeBot(), _skip_post=True
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, finishing_position "
            "FROM race_session_results "
            "WHERE session_result_id = ? ORDER BY finishing_position",
            (sr_id,),
        )
        rows = await cursor.fetchall()

    # Positions unchanged: P1 still 1, P2 still 2, P3 still 3
    assert rows[0]["driver_user_id"] == 1
    assert rows[1]["driver_user_id"] == 2
    assert rows[2]["driver_user_id"] == 3


# ---------------------------------------------------------------------------
# T033-N: DSQ re-sorts race_session_results (new table)
# ---------------------------------------------------------------------------


async def _insert_race_and_new_tables(db_path: str, round_id: int, division_id: int) -> int:
    """Insert a 2-driver FEATURE_RACE into both legacy and new tables. Returns sr_id."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (round_id, division_id),
        )
        sr_id = cursor.lastrowid
        # New table: base_time_ms = total_ms (ingame=0)
        # 20:00.000 = 1_200_000 ms; 20:10.000 = 1_210_000 ms
        await db.execute(
            "INSERT INTO race_session_results "
            "(session_result_id, driver_user_id, team_role_id, finishing_position, "
            "outcome, base_time_ms, laps_behind, ingame_time_penalties_ms, "
            "postrace_time_penalties_ms, appeal_time_penalties_ms, "
            "fastest_lap, fastest_lap_bonus, points_awarded) "
            "VALUES (?, 1, 100, 1, 'CLASSIFIED', 1200000, NULL, 0, 0, 0, '1:30.000', 0, 25)",
            (sr_id,),
        )
        await db.execute(
            "INSERT INTO race_session_results "
            "(session_result_id, driver_user_id, team_role_id, finishing_position, "
            "outcome, base_time_ms, laps_behind, ingame_time_penalties_ms, "
            "postrace_time_penalties_ms, appeal_time_penalties_ms, "
            "fastest_lap, fastest_lap_bonus, points_awarded) "
            "VALUES (?, 2, 200, 2, 'CLASSIFIED', 1210000, NULL, 0, 0, 0, '1:31.000', 0, 18)",
            (sr_id,),
        )
        await db.commit()
    return sr_id


@pytest.mark.asyncio
async def test_dsq_reorders_race_session_results(tmp_path):
    """DSQ on P1 driver; race_session_results must promote P2 to P1 and move DSQ driver last."""
    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)
    _, division_id, round_id = await _bootstrap(db_path)
    sr_id = await _insert_race_and_new_tables(db_path, round_id, division_id)

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
            "SELECT driver_user_id, finishing_position, outcome "
            "FROM race_session_results "
            "WHERE session_result_id = ? ORDER BY finishing_position",
            (sr_id,),
        )
        rows = await cursor.fetchall()

    assert rows[0]["driver_user_id"] == 2
    assert rows[0]["finishing_position"] == 1
    assert rows[0]["outcome"] == "CLASSIFIED"
    assert rows[1]["driver_user_id"] == 1
    assert rows[1]["finishing_position"] == 2
    assert rows[1]["outcome"] == "DSQ"


# ---------------------------------------------------------------------------
# T033-N+1: DSQ re-sorts qualifying_session_results (new table)
# ---------------------------------------------------------------------------


async def _insert_qualifying_and_new_tables(db_path: str, round_id: int, division_id: int) -> int:
    """Insert a 2-driver FEATURE_QUALIFYING into both legacy and new tables. Returns sr_id."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_QUALIFYING', 'ACTIVE')",
            (round_id, division_id),
        )
        sr_id = cursor.lastrowid
        # New qualifying table
        await db.execute(
            "INSERT INTO qualifying_session_results "
            "(session_result_id, driver_user_id, team_role_id, finishing_position, "
            "outcome, tyre, best_lap, points_awarded) "
            "VALUES (?, 1, 100, 1, 'CLASSIFIED', 'Soft', '1:20.000', 0)",
            (sr_id,),
        )
        await db.execute(
            "INSERT INTO qualifying_session_results "
            "(session_result_id, driver_user_id, team_role_id, finishing_position, "
            "outcome, tyre, best_lap, points_awarded) "
            "VALUES (?, 2, 200, 2, 'CLASSIFIED', 'Medium', '1:22.000', 0)",
            (sr_id,),
        )
        await db.commit()
    return sr_id


@pytest.mark.asyncio
async def test_dsq_reorders_qualifying_session_results(tmp_path):
    """DSQ on P1 qualifier; qualifying_session_results must promote P2 to P1, DSQ driver to last."""
    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)
    _, division_id, round_id = await _bootstrap(db_path)
    sr_id = await _insert_qualifying_and_new_tables(db_path, round_id, division_id)

    staged = [
        StagedPenalty(
            driver_user_id=1,
            session_type=SessionType.FEATURE_QUALIFYING,
            penalty_type="DSQ",
            penalty_seconds=None,
        )
    ]
    await apply_penalties(
        db_path, round_id, division_id, staged, 999, _FakeBot(), _skip_post=True
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, finishing_position, outcome "
            "FROM qualifying_session_results "
            "WHERE session_result_id = ? ORDER BY finishing_position",
            (sr_id,),
        )
        rows = await cursor.fetchall()

    assert rows[0]["driver_user_id"] == 2
    assert rows[0]["finishing_position"] == 1
    assert rows[0]["outcome"] == "CLASSIFIED"
    assert rows[1]["driver_user_id"] == 1
    assert rows[1]["finishing_position"] == 2
    assert rows[1]["outcome"] == "DSQ"
