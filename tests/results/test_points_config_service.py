"""Unit tests for points_config_service (T028)."""
from __future__ import annotations

import json

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.points_config_service import (
    config_exists,
    get_config_entries,
    setup_seasons_linking,
)
from tests.support.saves import create_config, remove_config, set_fl_bonus, set_session_points


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
# remove_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_config_success(db_path):
    """A configuration removed is gone from the store. A name that is not there is the
    `config_remove` setting's check to refuse
    (`test_removing_a_configuration_that_does_not_exist_says_so`)."""
    await create_config(db_path, config_name="Temp")

    await remove_config(db_path, config_name="Temp")

    assert await config_exists(db_path, "Temp") is False


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
    """A configuration added is in the store under its name. A name already taken is the
    `config_add` setting's check to refuse, before anything is written
    (`test_results_config_refusals.py::test_every_config_refusal_is_recorded`, its
    `add-duplicate-no-season` case)."""
    await create_config(db_path, config_name="Standard")

    assert await config_exists(db_path, "Standard") is True
    assert await config_exists(db_path, "Standrad") is False


# ---------------------------------------------------------------------------
# config_points_on — a bulk paste, written whole on the save's connection (#442, #439)
# ---------------------------------------------------------------------------


async def test_set_session_points_many_writes_every_pair_in_one_transaction(db_path):
    """Every pair lands on the connection the save hands it, and each position it changed is
    answered as an audit entry, from what it was to what it became; the queue stamps who and
    when and writes them, so the writer itself writes no audit row and commits nothing."""
    from leaguebot.results.services.points_config_service import config_points_on

    await create_config(db_path, config_name="Standard")
    await set_session_points(db_path, "Standard", SessionType.FEATURE_RACE, 1, 20)

    async with get_connection(db_path) as db:
        saved = await config_points_on(
            db, "Standard", SessionType.FEATURE_RACE, [(1, 25), (2, 18), (3, 15)]
        )
        await db.commit()

    entries, _fl = await get_config_entries(db_path, config_name="Standard")
    assert {e.position: e.points for e in entries} == {1: 25, 2: 18, 3: 15}
    assert saved.changed is True
    assert len(saved.audits) == 3
    assert {a.change_type for a in saved.audits} == {"POINTS_CONFIG_SESSION_SET"}
    first = next(a for a in saved.audits if "25" in json.dumps(a.new_value))
    assert "20" in json.dumps(first.old_value)
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) AS n FROM audit_entries")
        assert (await cursor.fetchone())["n"] == 0


@pytest.mark.parametrize(
    ("given", "refusal"),
    [
        ({"position": 0}, "❌ Position must be >= 1, got 0."),
        ({"position": 1}, None),
        ({"points": -1}, "❌ Points must be >= 0, got -1."),
        ({"points": 0}, None),
        ({"limit": 0}, "❌ Position limit must be >= 1, got 0."),
        ({"limit": 1}, None),
        ({"position": 0, "points": -1}, "❌ Position must be >= 1, got 0."),
    ],
    ids=["position-0", "position-1", "points-minus-1", "points-0", "limit-0", "limit-1",
         "position-first"],
)
def test_points_value_refusal_follows_the_bulk_rule(given, refusal):
    """A single position, points value or fastest-lap limit is held to the bulk paste's rule: a
    position or a limit below 1, or points below 0, is refused in the paste's words, the position
    judged before the points; anything else passes."""
    from leaguebot.results.services.points_config_service import points_value_refusal

    assert points_value_refusal(**given) == refusal
