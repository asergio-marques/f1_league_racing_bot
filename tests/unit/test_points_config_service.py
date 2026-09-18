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
    config_exists,
    create_config,
    get_config_entries,
    remove_config,
    setup_seasons_linking,
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
    store = await create_config(db_path, config_name="Standard")
    assert store.config_name == "Standard"
    assert store.id is not None


@pytest.mark.asyncio
async def test_create_config_duplicate_raises(db_path):
    await create_config(db_path, config_name="Dup")
    with pytest.raises(ConfigAlreadyExistsError):
        await create_config(db_path, config_name="Dup")


# ---------------------------------------------------------------------------
# remove_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_config_not_found_raises(db_path):
    with pytest.raises(ConfigNotFoundError):
        await remove_config(db_path, config_name="Ghost")


@pytest.mark.asyncio
async def test_remove_config_success(db_path):
    await create_config(db_path, config_name="Temp")
    await remove_config(db_path, config_name="Temp")
    # second removal should raise
    with pytest.raises(ConfigNotFoundError):
        await remove_config(db_path, config_name="Temp")


# ---------------------------------------------------------------------------
# set_fl_bonus — qualifying session type rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_fl_bonus_feature_qualifying_raises(db_path):
    await create_config(db_path, config_name="CFG")
    with pytest.raises(InvalidSessionTypeError):
        await set_fl_bonus(
            db_path, config_name="CFG",
            session_type=SessionType.FEATURE_QUALIFYING, fl_points=1,
        )


@pytest.mark.asyncio
async def test_set_fl_bonus_sprint_qualifying_raises(db_path):
    await create_config(db_path, config_name="CFG2")
    with pytest.raises(InvalidSessionTypeError):
        await set_fl_bonus(
            db_path, config_name="CFG2",
            session_type=SessionType.SPRINT_QUALIFYING, fl_points=1,
        )


# ---------------------------------------------------------------------------
# get_config_entries — round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_config_entries_round_trip(db_path):
    await create_config(db_path, config_name="R")
    await set_session_points(
        db_path, config_name="R",
        session_type=SessionType.FEATURE_RACE, position=1, points=25,
    )
    await set_session_points(
        db_path, config_name="R",
        session_type=SessionType.FEATURE_RACE, position=2, points=18,
    )
    await set_fl_bonus(
        db_path, config_name="R",
        session_type=SessionType.FEATURE_RACE, fl_points=1,
    )
    entries, fl_list = await get_config_entries(db_path, config_name="R")
    assert len(entries) == 2
    p1 = next(e for e in entries if e.position == 1)
    assert p1.points == 25
    assert len(fl_list) == 1
    assert fl_list[0].fl_points == 1


# ---------------------------------------------------------------------------
# remove_config and the seasons standing on it (#132)
# ---------------------------------------------------------------------------


async def _make_season(db_path: str, season_id: int, status: str, number: int = 1) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (?, '2026-01-01', ?, ?)",
            (season_id, status, number),
        )
        await db.commit()


async def _link(db_path: str, season_id: int, config_name: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
            (season_id, config_name),
        )
        await db.commit()


async def _links_of(db_path: str, season_id: int) -> list[str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT config_name FROM season_points_links WHERE season_id = ? "
            "ORDER BY config_name",
            (season_id,),
        )
        return [r["config_name"] for r in await cursor.fetchall()]


@pytest.mark.asyncio
async def test_remove_config_clears_links_from_setup_seasons(db_path):
    """#132's second way in: removing one left the season pointing at nothing at all."""
    await create_config(db_path, config_name="Standard")
    await _make_season(db_path, season_id=1, status="SETUP")
    await _link(db_path, 1, "Standard")

    await remove_config(db_path, config_name="Standard")

    assert await _links_of(db_path, 1) == []


@pytest.mark.asyncio
async def test_remove_config_leaves_an_approved_seasons_link_alone(db_path):
    """The scoping, and the reason it is not simply "delete every link".

    An approved season scores from its own copy, taken at approval, and the link is what
    offers that copy as a choice when results are submitted. Clearing it would take a
    running season's points configuration off the submission buttons.
    """
    await create_config(db_path, config_name="Standard")
    await _make_season(db_path, season_id=2, status="ACTIVE", number=2)
    await _link(db_path, 2, "Standard")

    await remove_config(db_path, config_name="Standard")

    assert await _links_of(db_path, 2) == ["Standard"]


@pytest.mark.asyncio
async def test_remove_config_clears_the_setup_link_and_keeps_the_finished_one(db_path):
    """Both rules at once, in the shape a league actually meets them.

    `idx_seasons_one_live_per_server` allows a server only one season in SETUP or ACTIVE,
    so the two never coexist live — the pairing that happens is last season, finished and
    keeping its history, beside next season being built.
    """
    await create_config(db_path, config_name="Standard")
    await _make_season(db_path, season_id=1, status="COMPLETED", number=1)
    await _make_season(db_path, season_id=2, status="SETUP", number=2)
    await _link(db_path, 1, "Standard")
    await _link(db_path, 2, "Standard")

    await remove_config(db_path, config_name="Standard")

    assert await _links_of(db_path, 2) == [], "the season being built loses the name"
    assert await _links_of(db_path, 1) == ["Standard"], "last season keeps its record"


@pytest.mark.asyncio
async def test_remove_config_leaves_other_configs_attached(db_path):
    """Only the name being removed goes; a season keeps the rest of its points."""
    await create_config(db_path, config_name="Standard")
    await create_config(db_path, config_name="Half Points")
    await _make_season(db_path, season_id=1, status="SETUP")
    await _link(db_path, 1, "Standard")
    await _link(db_path, 1, "Half Points")

    await remove_config(db_path, config_name="Standard")

    assert await _links_of(db_path, 1) == ["Half Points"]


@pytest.mark.asyncio
async def test_setup_seasons_linking_names_the_season(db_path):
    """What the confirmation prompt reads, so the manager is told what they are losing."""
    await create_config(db_path, config_name="Standard")
    await _make_season(db_path, season_id=1, status="SETUP", number=4)
    await _link(db_path, 1, "Standard")

    assert await setup_seasons_linking(db_path, "Standard") == [(1, 4)]


@pytest.mark.asyncio
async def test_setup_seasons_linking_ignores_an_approved_season(db_path):
    """Nothing is at stake there, so the command must not stop to ask about it."""
    await create_config(db_path, config_name="Standard")
    await _make_season(db_path, season_id=2, status="ACTIVE", number=2)
    await _link(db_path, 2, "Standard")

    assert await setup_seasons_linking(db_path, "Standard") == []


@pytest.mark.asyncio
async def test_setup_seasons_linking_is_empty_when_nothing_stands_on_it(db_path):
    await create_config(db_path, config_name="Standard")

    assert await setup_seasons_linking(db_path, "Standard") == []


@pytest.mark.asyncio
async def test_config_exists_answers_by_name(db_path):
    await create_config(db_path, config_name="Standard")

    assert await config_exists(db_path, "Standard") is True
    assert await config_exists(db_path, "Standrad") is False
