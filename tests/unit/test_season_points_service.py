"""Unit tests for season_points_service (T029)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations
from models.points_config import SessionType
from services import points_config_service, season_points_service
from services.season_points_service import (
    SeasonNotInSetupError,
    attach_config,
    get_season_points_view,
    validate_monotonic_ordering,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "sps_test.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 10, 20, 30)"
        )
        await db.commit()
    return path


async def _make_season(db_path: str, status: str = "SETUP") -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', ?, 1)",
            (status,),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def _make_config_with_entries(db_path: str, config_name: str) -> None:
    """Create a server config with Feature Race P1=25, P2=18, P3=15."""
    await points_config_service.create_config(db_path, server_id=1, config_name=config_name)
    for pos, pts in [(1, 25), (2, 18), (3, 15)]:
        await points_config_service.set_session_points(
            db_path, server_id=1, config_name=config_name,
            session_type=SessionType.FEATURE_RACE, position=pos, points=pts,
        )


# ---------------------------------------------------------------------------
# attach_config — blocked outside SETUP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attach_config_blocked_outside_setup(db_path):
    season_id = await _make_season(db_path, status="ACTIVE")
    await _make_config_with_entries(db_path, "CFG")
    with pytest.raises(SeasonNotInSetupError):
        await attach_config(db_path, season_id=season_id, config_name="CFG", season_status="ACTIVE")


@pytest.mark.asyncio
async def test_attach_config_success_in_setup(db_path):
    season_id = await _make_season(db_path, status="SETUP")
    await _make_config_with_entries(db_path, "CFG")
    await attach_config(db_path, season_id=season_id, config_name="CFG", season_status="SETUP")
    names = await season_points_service.get_attached_config_names(db_path, season_id)
    assert "CFG" in names


# ---------------------------------------------------------------------------
# validate_monotonic_ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_monotonic_valid(db_path):
    season_id = await _make_season(db_path)
    # Manually insert monotonic entries: P1=25, P2=18, P3=15
    async with get_connection(db_path) as db:
        for pos, pts in [(1, 25), (2, 18), (3, 15)]:
            await db.execute(
                "INSERT INTO season_points_entries (season_id, config_name, session_type, position, points) "
                "VALUES (?, 'X', 'FEATURE_RACE', ?, ?)",
                (season_id, pos, pts),
            )
        await db.commit()
    errors = await validate_monotonic_ordering(db_path, season_id)
    assert errors == []


@pytest.mark.asyncio
async def test_validate_monotonic_invalid(db_path):
    season_id = await _make_season(db_path)
    # P1=10, P2=18 — non-monotonic!
    async with get_connection(db_path) as db:
        for pos, pts in [(1, 10), (2, 18)]:
            await db.execute(
                "INSERT INTO season_points_entries (season_id, config_name, session_type, position, points) "
                "VALUES (?, 'BAD', 'FEATURE_RACE', ?, ?)",
                (season_id, pos, pts),
            )
        await db.commit()
    errors = await validate_monotonic_ordering(db_path, season_id)
    assert len(errors) >= 1
    assert "BAD" in errors[0]


@pytest.mark.asyncio
async def test_validate_monotonic_equal_nonzero(db_path):
    season_id = await _make_season(db_path)
    # P1=25, P2=25 — equal non-zero counts as a violation
    async with get_connection(db_path) as db:
        for pos, pts in [(1, 25), (2, 25)]:
            await db.execute(
                "INSERT INTO season_points_entries (season_id, config_name, session_type, position, points) "
                "VALUES (?, 'EQ', 'FEATURE_RACE', ?, ?)",
                (season_id, pos, pts),
            )
        await db.commit()
    errors = await validate_monotonic_ordering(db_path, season_id)
    assert len(errors) >= 1
    assert "EQ" in errors[0]


@pytest.mark.asyncio
async def test_validate_monotonic_trailing_zeros_ok(db_path):
    season_id = await _make_season(db_path)
    # P1=0, P2=0 — equal zeros are NOT a violation
    async with get_connection(db_path) as db:
        for pos, pts in [(1, 0), (2, 0)]:
            await db.execute(
                "INSERT INTO season_points_entries (season_id, config_name, session_type, position, points) "
                "VALUES (?, 'ZEROS', 'FEATURE_RACE', ?, ?)",
                (season_id, pos, pts),
            )
        await db.commit()
    errors = await validate_monotonic_ordering(db_path, season_id)
    assert errors == []


# ---------------------------------------------------------------------------
# get_season_points_view — trailing-zero collapse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_season_points_view_trailing_zero_collapse(db_path):
    season_id = await _make_season(db_path)
    # P1=25, P2=18, P3=0, P4=0
    async with get_connection(db_path) as db:
        for pos, pts in [(1, 25), (2, 18), (3, 0), (4, 0)]:
            await db.execute(
                "INSERT INTO season_points_entries (season_id, config_name, session_type, position, points) "
                "VALUES (?, 'TRAIL', 'FEATURE_RACE', ?, ?)",
                (season_id, pos, pts),
            )
        await db.commit()

    view = await get_season_points_view(db_path, season_id, "TRAIL")
    entries = view["FEATURE_RACE"]["entries"]
    # Should be: [("1", 25), ("2", 18), ("3+", 0)]
    labels = [label for label, _pts in entries]
    assert "1" in labels
    assert "2" in labels
    assert "3+" in labels
    # P4 should NOT appear separately since P3 was the first trailing zero
    assert "4" not in labels
    assert "4+" not in labels
