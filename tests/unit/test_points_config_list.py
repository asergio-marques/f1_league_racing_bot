"""Unit tests for listing the points configurations a server or season holds (#200)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations
from models.points_config import SessionType
from services.points_config_service import (
    create_config,
    list_configs,
    set_session_points,
)


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "pcl_test.db")
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
# list_configs — the bare names
# ---------------------------------------------------------------------------


async def test_list_configs_returns_bare_names(db_path):
    """The server store's names, which nothing called before #200."""
    await create_config(db_path, "Standard")
    await create_config(db_path, "Half Points")

    configs = await list_configs(db_path)

    assert [c.config_name for c in configs] == ["Half Points", "Standard"]


async def test_list_configs_is_empty_on_a_fresh_server(db_path):
    assert await list_configs(db_path) == []


async def test_list_configs_orders_by_name(db_path):
    """Ordered by name, so a manager reads the same list twice running.

    Never assert on insertion order here — the ordering is the point.
    """
    for name in ("Zeta", "Alpha", "Mid"):
        await create_config(db_path, name)

    configs = await list_configs(db_path)

    assert [c.config_name for c in configs] == ["Alpha", "Mid", "Zeta"]


async def test_list_configs_names_a_config_that_carries_no_entries(db_path):
    """A configuration created and never filled is still one the server holds."""
    await create_config(db_path, "Empty")
    await create_config(db_path, "Filled")
    await set_session_points(db_path, "Filled", SessionType.FEATURE_RACE, 1, 25)

    names = [c.config_name for c in await list_configs(db_path)]

    assert names == ["Empty", "Filled"]
