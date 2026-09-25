"""Unit tests for listing the points configurations a server or season holds (#200)."""
from __future__ import annotations

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.points_config_service import (
    create_config,
    list_configs,
    list_configs_with_sessions,
    set_session_points,
)
from leaguebot.results.services.season_points_service import (
    attach_config,
    list_season_configs_with_sessions,
    snapshot_configs_to_season,
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


# ---------------------------------------------------------------------------
# list_configs_with_sessions — the server store
# ---------------------------------------------------------------------------


async def test_list_configs_with_sessions_names_only_sessions_that_carry_entries(db_path):
    """A configuration reports the session types it actually holds points for."""
    await create_config(db_path, "Standard")
    await set_session_points(db_path, "Standard", SessionType.FEATURE_RACE, 1, 25)
    await set_session_points(db_path, "Standard", SessionType.FEATURE_RACE, 2, 18)
    await set_session_points(db_path, "Standard", SessionType.SPRINT_RACE, 1, 8)

    rows = await list_configs_with_sessions(db_path)

    assert rows == [
        ("Standard", [SessionType.SPRINT_RACE, SessionType.FEATURE_RACE]),
    ]


async def test_list_configs_with_sessions_reports_an_empty_config_as_empty(db_path):
    """The trap #200 names: an unfilled config is present, with nothing in it.

    It must not vanish from the listing — a league needs to see that it exists and
    that it would snapshot as empty points.
    """
    await create_config(db_path, "Never Filled")

    rows = await list_configs_with_sessions(db_path)

    assert rows == [("Never Filled", [])]


async def test_list_configs_with_sessions_orders_by_name(db_path):
    for name in ("Zeta", "Alpha", "Mid"):
        await create_config(db_path, name)
    await set_session_points(db_path, "Mid", SessionType.FEATURE_RACE, 1, 25)

    rows = await list_configs_with_sessions(db_path)

    assert [name for name, _ in rows] == ["Alpha", "Mid", "Zeta"]


async def test_list_configs_with_sessions_is_empty_on_a_fresh_server(db_path):
    assert await list_configs_with_sessions(db_path) == []


async def test_list_configs_with_sessions_does_not_repeat_a_session_type(db_path):
    """Many positions in one session collapse to that session named once."""
    await create_config(db_path, "Standard")
    for position, points in ((1, 25), (2, 18), (3, 15)):
        await set_session_points(db_path, "Standard", SessionType.FEATURE_RACE, position, points)

    rows = await list_configs_with_sessions(db_path)

    assert rows == [("Standard", [SessionType.FEATURE_RACE])]


# ---------------------------------------------------------------------------
# list_season_configs_with_sessions — the season's own store
# ---------------------------------------------------------------------------


SEASON_ID = 1


async def _season_in_setup(path: str) -> None:
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'SETUP')",
            (SEASON_ID,),
        )
        await db.commit()


async def test_list_season_configs_with_sessions_reads_the_seasons_own_store(db_path):
    await _season_in_setup(db_path)
    await create_config(db_path, "Standard")
    await set_session_points(db_path, "Standard", SessionType.FEATURE_RACE, 1, 25)
    await attach_config(db_path, SEASON_ID, "Standard", "SETUP")
    await snapshot_configs_to_season(db_path, SEASON_ID)

    rows = await list_season_configs_with_sessions(db_path, SEASON_ID)

    assert rows == [("Standard", [SessionType.FEATURE_RACE])]


async def test_list_season_configs_reports_an_attached_but_unfilled_config(db_path):
    """Attached and empty must read as attached, not vanish — as on the server side."""
    await _season_in_setup(db_path)
    await create_config(db_path, "Never Filled")
    await attach_config(db_path, SEASON_ID, "Never Filled", "SETUP")
    await snapshot_configs_to_season(db_path, SEASON_ID)

    rows = await list_season_configs_with_sessions(db_path, SEASON_ID)

    assert rows == [("Never Filled", [])]


async def test_list_configs_reads_the_season_store_after_snapshot_diverges(db_path):
    """The reason `/results config list` makes the manager name the store (#200).

    Once a season has snapshotted, editing the server's configuration no longer
    changes what the season scores by. The two scopes must therefore report
    different things, and this pins that they do.
    """
    await _season_in_setup(db_path)
    await create_config(db_path, "Standard")
    await set_session_points(db_path, "Standard", SessionType.FEATURE_RACE, 1, 25)
    await attach_config(db_path, SEASON_ID, "Standard", "SETUP")
    await snapshot_configs_to_season(db_path, SEASON_ID)

    # The server's copy gains a session the season's snapshot never took.
    await set_session_points(db_path, "Standard", SessionType.SPRINT_RACE, 1, 8)

    server_rows = await list_configs_with_sessions(db_path)
    season_rows = await list_season_configs_with_sessions(db_path, SEASON_ID)

    assert server_rows == [
        ("Standard", [SessionType.SPRINT_RACE, SessionType.FEATURE_RACE])
    ]
    assert season_rows == [("Standard", [SessionType.FEATURE_RACE])]
    assert server_rows != season_rows


async def test_list_season_configs_is_empty_when_nothing_is_attached(db_path):
    await _season_in_setup(db_path)

    assert await list_season_configs_with_sessions(db_path, SEASON_ID) == []
