"""Unit tests for points_config_service (T028)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations
from models.points_config import SessionType
from services.points_config_service import (
    ConfigAlreadyExistsError,
    ConfigNotFoundError,
    InvalidSessionTypeError,
    create_config,
    get_config_entries,
    remove_config,
    set_fl_bonus,
    set_session_points,
)


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "pcs_test.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 10, 20, 30)"
        )
        await db.commit()
    return path


# ---------------------------------------------------------------------------
# create_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_config_success(db_path):
    store = await create_config(db_path, server_id=1, config_name="Standard")
    assert store.config_name == "Standard"
    assert store.server_id == 1
    assert store.id is not None


@pytest.mark.asyncio
async def test_create_config_duplicate_raises(db_path):
    await create_config(db_path, server_id=1, config_name="Dup")
    with pytest.raises(ConfigAlreadyExistsError):
        await create_config(db_path, server_id=1, config_name="Dup")


# ---------------------------------------------------------------------------
# remove_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_config_not_found_raises(db_path):
    with pytest.raises(ConfigNotFoundError):
        await remove_config(db_path, server_id=1, config_name="Ghost")


@pytest.mark.asyncio
async def test_remove_config_success(db_path):
    await create_config(db_path, server_id=1, config_name="Temp")
    await remove_config(db_path, server_id=1, config_name="Temp")
    # second removal should raise
    with pytest.raises(ConfigNotFoundError):
        await remove_config(db_path, server_id=1, config_name="Temp")


# ---------------------------------------------------------------------------
# set_fl_bonus — qualifying session type rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_fl_bonus_feature_qualifying_raises(db_path):
    await create_config(db_path, server_id=1, config_name="CFG")
    with pytest.raises(InvalidSessionTypeError):
        await set_fl_bonus(
            db_path, server_id=1, config_name="CFG",
            session_type=SessionType.FEATURE_QUALIFYING, fl_points=1,
        )


@pytest.mark.asyncio
async def test_set_fl_bonus_sprint_qualifying_raises(db_path):
    await create_config(db_path, server_id=1, config_name="CFG2")
    with pytest.raises(InvalidSessionTypeError):
        await set_fl_bonus(
            db_path, server_id=1, config_name="CFG2",
            session_type=SessionType.SPRINT_QUALIFYING, fl_points=1,
        )


# ---------------------------------------------------------------------------
# get_config_entries — round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_config_entries_round_trip(db_path):
    await create_config(db_path, server_id=1, config_name="R")
    await set_session_points(
        db_path, server_id=1, config_name="R",
        session_type=SessionType.FEATURE_RACE, position=1, points=25,
    )
    await set_session_points(
        db_path, server_id=1, config_name="R",
        session_type=SessionType.FEATURE_RACE, position=2, points=18,
    )
    await set_fl_bonus(
        db_path, server_id=1, config_name="R",
        session_type=SessionType.FEATURE_RACE, fl_points=1,
    )
    entries, fl_list = await get_config_entries(db_path, server_id=1, config_name="R")
    assert len(entries) == 2
    p1 = next(e for e in entries if e.position == 1)
    assert p1.points == 25
    assert len(fl_list) == 1
    assert fl_list[0].fl_points == 1
