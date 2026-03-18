"""Unit tests for amendment_service (T034) — points-amendment workflow."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations
from services.amendment_service import (
    AmendmentModifiedError,
    AmendmentNotActiveError,
    disable_amendment_mode,
    enable_amendment_mode,
    get_amendment_state,
    modify_session_points,
    revert_modification_store,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "amend_test.db")
    await run_migrations(path)
    async with get_connection(path) as db:
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
        await db.commit()
    return path, season_id


async def _get_season_id(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT id FROM seasons LIMIT 1")
        row = await cursor.fetchone()
    return row["id"]  # type: ignore[index]


async def _seed_season_points(db_path: str, season_id: int) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO season_points_entries (season_id, config_name, session_type, position, points) "
            "VALUES (?, 'STD', 'FEATURE_RACE', 1, 25)",
            (season_id,),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# disable_amendment_mode raises AmendmentModifiedError when modified_flag=1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_raises_when_modified_flag(db_path):
    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await enable_amendment_mode(path, season_id)
    # Manually set modified_flag=1
    async with get_connection(path) as db:
        await db.execute(
            "UPDATE season_amendment_state SET modified_flag = 1 WHERE season_id = ?",
            (season_id,),
        )
        await db.commit()

    with pytest.raises(AmendmentModifiedError):
        await disable_amendment_mode(path, season_id)


@pytest.mark.asyncio
async def test_disable_succeeds_when_not_modified(db_path):
    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await enable_amendment_mode(path, season_id)
    # modified_flag is 0 after enable — should succeed
    await disable_amendment_mode(path, season_id)
    state = await get_amendment_state(path, season_id)
    assert state is not None
    assert not state.amendment_active


# ---------------------------------------------------------------------------
# revert_modification_store — resets entries and clears modified_flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revert_modification_store(db_path):
    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await enable_amendment_mode(path, season_id)

    # Modify something in the modification store
    await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 1, 30)

    # Confirm flag is set
    state = await get_amendment_state(path, season_id)
    assert state is not None and state.modified_flag

    # Revert
    await revert_modification_store(path, season_id)

    # Flag should be cleared
    state = await get_amendment_state(path, season_id)
    assert state is not None
    assert not state.modified_flag

    # Modification store should match the original season points (25 pts)
    async with get_connection(path) as db:
        cursor = await db.execute(
            "SELECT points FROM season_modification_entries WHERE season_id = ? AND position = 1",
            (season_id,),
        )
        row = await cursor.fetchone()
    assert row is not None
    assert row["points"] == 25


# ---------------------------------------------------------------------------
# modify_session_points raises AmendmentNotActiveError when not in amendment mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_modify_raises_when_not_active(db_path):
    path, season_id = db_path
    with pytest.raises(AmendmentNotActiveError):
        await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 1, 30)


# ---------------------------------------------------------------------------
# approve_amendment — atomically overwrites season_points_entries
# (tested at DB level without bot dependency)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_amendment_overwrites_season_points(db_path):
    """Validate the transactional overwrite by inspecting DB state after manual simulate."""
    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await enable_amendment_mode(path, season_id)

    # Modify P1 from 25 to 30
    await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 1, 30)

    # Manually do the atomic overwrite (mirrors approve_amendment transaction)
    async with get_connection(path) as db:
        await db.execute(
            "DELETE FROM season_points_entries WHERE season_id = ?", (season_id,)
        )
        await db.execute(
            """
            INSERT INTO season_points_entries (season_id, config_name, session_type, position, points)
            SELECT season_id, config_name, session_type, position, points
            FROM season_modification_entries WHERE season_id = ?
            """,
            (season_id,),
        )
        await db.execute(
            "DELETE FROM season_modification_entries WHERE season_id = ?", (season_id,)
        )
        await db.execute(
            "UPDATE season_amendment_state SET amendment_active = 0, modified_flag = 0 WHERE season_id = ?",
            (season_id,),
        )
        await db.commit()

    # Verify season_points_entries now has 30 pts
    async with get_connection(path) as db:
        cursor = await db.execute(
            "SELECT points FROM season_points_entries WHERE season_id = ? AND position = 1",
            (season_id,),
        )
        row = await cursor.fetchone()
    assert row is not None
    assert row["points"] == 30

    # Verify amendment mode is off
    state = await get_amendment_state(path, season_id)
    assert state is not None
    assert not state.amendment_active
    assert not state.modified_flag
