"""Integration tests for the results submission → standings flow (T035).

Sets up an in-memory SQLite DB, inserts season/division/round/points config,
calls save_session_result to store driver results, and checks that
compute_driver_standings + persist_snapshots produce correct totals.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations
from models.points_config import PointsConfigEntry, PointsConfigFastestLap, SessionType
from models.session_result import OutcomeModifier
from services.result_submission_service import save_session_result
from services.standings_service import (
    compute_driver_standings,
    compute_points_for_session,
    persist_snapshots,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "flow_test.db")
    await run_migrations(path)
    return path


async def _bootstrap(db_path: str):
    """Create server → season → division → round → points config snapshot.
    
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
            "VALUES (?, 1, 'NORMAL', '2026-01-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        # Points config snapshot: P1=25, P2=18, P3=15
        for pos, pts in [(1, 25), (2, 18), (3, 15)]:
            await db.execute(
                "INSERT INTO season_points_entries (season_id, config_name, session_type, position, points) "
                "VALUES (?, 'STD', 'FEATURE_RACE', ?, ?)",
                (season_id, pos, pts),
            )
        await db.commit()
    return season_id, division_id, round_id


# ---------------------------------------------------------------------------
# Test: session_results and race_session_results rows are created
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_session_result_creates_rows(db_path):
    _, division_id, round_id = await _bootstrap(db_path)

    driver_rows = [
        {
            "driver_user_id": 111,
            "team_role_id": 999,
            "finishing_position": 1,
            "outcome": OutcomeModifier.CLASSIFIED.value,
            "total_time": "1:23:45.678",
            "fastest_lap": "1:23.456",
            "time_penalties": None,
            "points_awarded": 25,
            "fastest_lap_bonus": 0,
        },
        {
            "driver_user_id": 222,
            "team_role_id": 998,
            "finishing_position": 2,
            "outcome": OutcomeModifier.CLASSIFIED.value,
            "total_time": "+0:01.234",
            "fastest_lap": "1:24.000",
            "time_penalties": None,
            "points_awarded": 18,
            "fastest_lap_bonus": 0,
        },
    ]

    session_result_id = await save_session_result(
        db_path,
        round_id=round_id,
        division_id=division_id,
        session_type=SessionType.FEATURE_RACE,
        status="ACTIVE",
        config_name="STD",
        submitted_by=999_999,
        driver_rows=driver_rows,
    )
    assert session_result_id is not None

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS c FROM race_session_results WHERE session_result_id = ?",
            (session_result_id,),
        )
        row = await cursor.fetchone()
    assert row["c"] == 2


# ---------------------------------------------------------------------------
# Test: compute_driver_standings returns correct totals and positions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_driver_standings_correct_totals(db_path):
    _, division_id, round_id = await _bootstrap(db_path)

    driver_rows = [
        {
            "driver_user_id": 111,
            "team_role_id": 999,
            "finishing_position": 1,
            "outcome": OutcomeModifier.CLASSIFIED.value,
            "total_time": "1:23:45.678",
            "fastest_lap": None,
            "time_penalties": None,
            "points_awarded": 25,
            "fastest_lap_bonus": 0,
        },
        {
            "driver_user_id": 222,
            "team_role_id": 998,
            "finishing_position": 2,
            "outcome": OutcomeModifier.CLASSIFIED.value,
            "total_time": "+0:01.234",
            "fastest_lap": None,
            "time_penalties": None,
            "points_awarded": 18,
            "fastest_lap_bonus": 0,
        },
        {
            "driver_user_id": 333,
            "team_role_id": 997,
            "finishing_position": 3,
            "outcome": OutcomeModifier.DNF.value,
            "total_time": "DNF",
            "fastest_lap": None,
            "time_penalties": None,
            "points_awarded": 0,
            "fastest_lap_bonus": 0,
        },
    ]

    await save_session_result(
        db_path,
        round_id=round_id,
        division_id=division_id,
        session_type=SessionType.FEATURE_RACE,
        status="ACTIVE",
        config_name="STD",
        submitted_by=999_999,
        driver_rows=driver_rows,
    )

    snapshots = await compute_driver_standings(db_path, division_id=division_id, up_to_round_id=round_id)

    pts_map = {s.driver_user_id: s.total_points for s in snapshots}
    pos_map = {s.driver_user_id: s.standing_position for s in snapshots}

    # Driver 111: 25 pts → P1; Driver 222: 18 pts → P2; Driver 333: 0 pts → P3
    assert pts_map[111] == 25
    assert pts_map[222] == 18
    assert pts_map[333] == 0
    assert pos_map[111] == 1
    assert pos_map[222] == 2
    assert pos_map[333] == 3


# ---------------------------------------------------------------------------
# Test: persist_snapshots writes DriverStandingsSnapshot rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_snapshots_writes_rows(db_path):
    _, division_id, round_id = await _bootstrap(db_path)

    driver_rows = [
        {
            "driver_user_id": 111,
            "team_role_id": 999,
            "finishing_position": 1,
            "outcome": OutcomeModifier.CLASSIFIED.value,
            "total_time": "1:23:45.678",
            "fastest_lap": None,
            "time_penalties": None,
            "points_awarded": 25,
            "fastest_lap_bonus": 0,
        },
    ]

    await save_session_result(
        db_path,
        round_id=round_id,
        division_id=division_id,
        session_type=SessionType.FEATURE_RACE,
        status="ACTIVE",
        config_name="STD",
        submitted_by=999_999,
        driver_rows=driver_rows,
    )

    snapshots = await compute_driver_standings(db_path, division_id=division_id, up_to_round_id=round_id)
    await persist_snapshots(db_path, snapshots, team_snaps=[])

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS c FROM driver_standings_snapshots WHERE round_id = ? AND division_id = ?",
            (round_id, division_id),
        )
        row = await cursor.fetchone()
    assert row["c"] == 1
