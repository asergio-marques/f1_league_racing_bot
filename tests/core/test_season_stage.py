"""A season's lifecycle stage, and how it stays in step with its coarse status (issue #220).

Migration 057 adds `seasons.stage` beside `seasons.status`. The status keeps the grain every
older reader was written against; the stage refines it into the ten states the core
specification names. These tests pin the mapping between the two, the migration's backfill
of rows already in the old shape, and the transitions `SeasonService.set_stage` permits.
"""
from __future__ import annotations

import sqlite3

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.season import (
    ALLOWED_STAGE_TRANSITIONS,
    STAGES_OF_STATUS,
    InvalidStageTransition,
    SeasonStage,
    SeasonStatus,
    status_of_stage,
)
from leaguebot.core.services.season_service import SeasonService

SERVER_ID = 2200


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "season_stage.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.commit()
    return path


async def _insert(db_path, status, stage=None):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number, stage) "
            "VALUES ('2026-09-17', ?, 1, ?)",
            (status, stage),
        )
        await db.commit()
        return cursor.lastrowid


async def _row(db_path, season_id):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT status, stage FROM seasons WHERE id = ?", (season_id,)
        )
        row = await cursor.fetchone()
    return row["status"], row["stage"]


# ── The mapping ─────────────────────────────────────────────────────────────────────


def test_season_stage_matches_status():
    """Every stage belongs to exactly one status, and every status admits at least one."""
    seen: list[SeasonStage] = []
    for status in SeasonStatus:
        assert STAGES_OF_STATUS[status], status
        seen.extend(STAGES_OF_STATUS[status])
    assert sorted(seen) == sorted(SeasonStage)
    for stage in SeasonStage:
        assert stage in STAGES_OF_STATUS[status_of_stage(stage)]


def test_every_stage_names_its_transitions():
    assert set(ALLOWED_STAGE_TRANSITIONS) == set(SeasonStage)
    assert ALLOWED_STAGE_TRANSITIONS[SeasonStage.COMPLETED] == frozenset()
    assert ALLOWED_STAGE_TRANSITIONS[SeasonStage.CANCELLED] == frozenset()


# ── The migration and its triggers ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status, stage",
    [("SETUP", "PLACEMENTS"), ("ACTIVE", "ONGOING"),
     ("COMPLETED", "COMPLETED"), ("CANCELLED", "CANCELLED")],
)
async def test_a_season_inserted_without_a_stage_takes_its_status_default(db_path, status, stage):
    season_id = await _insert(db_path, status)
    assert await _row(db_path, season_id) == (status, stage)


async def test_a_season_inserted_with_a_stage_keeps_it(db_path):
    season_id = await _insert(db_path, "SETUP", "CONFIGURATION")
    assert await _row(db_path, season_id) == ("SETUP", "CONFIGURATION")


async def test_a_stage_its_status_does_not_admit_is_refused_on_insert(db_path):
    with pytest.raises(sqlite3.IntegrityError, match="stage does not match"):
        await _insert(db_path, "SETUP", "ONGOING")


async def test_a_stage_its_status_does_not_admit_is_refused_on_update(db_path):
    season_id = await _insert(db_path, "SETUP", "CONFIGURATION")
    async with get_connection(db_path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="stage does not match"):
            await db.execute(
                "UPDATE seasons SET stage = 'ONGOING' WHERE id = ?", (season_id,)
            )


async def test_a_status_written_alone_carries_the_stage_with_it(db_path):
    """Older writers change the status alone; the stage follows."""
    season_id = await _insert(db_path, "SETUP", "PLACEMENTS")
    async with get_connection(db_path) as db:
        await db.execute("UPDATE seasons SET status = 'ACTIVE' WHERE id = ?", (season_id,))
        await db.commit()
    assert await _row(db_path, season_id) == ("ACTIVE", "ONGOING")

    async with get_connection(db_path) as db:
        await db.execute("UPDATE seasons SET status = 'COMPLETED' WHERE id = ?", (season_id,))
        await db.commit()
    assert await _row(db_path, season_id) == ("COMPLETED", "COMPLETED")


async def test_a_status_written_alone_keeps_a_stage_it_still_admits(db_path):
    season_id = await _insert(db_path, "ACTIVE", "ONGOING_SIGNUPS")
    async with get_connection(db_path) as db:
        await db.execute("UPDATE seasons SET status = 'ACTIVE' WHERE id = ?", (season_id,))
        await db.execute("UPDATE seasons SET status = 'CANCELLED' WHERE id = ?", (season_id,))
        await db.commit()
    assert await _row(db_path, season_id) == ("CANCELLED", "CANCELLED")


# ── SeasonService ───────────────────────────────────────────────────────────────────


async def test_set_stage_moves_the_season_and_its_status(db_path):
    svc = SeasonService(db_path)
    season_id = await _insert(db_path, "SETUP", "PLACEMENTS")

    await svc.set_stage(season_id, SeasonStage.ONGOING)

    assert await svc.get_stage(season_id) is SeasonStage.ONGOING
    assert await _row(db_path, season_id) == ("ACTIVE", "ONGOING")


async def test_set_stage_walks_the_whole_lifecycle(db_path):
    svc = SeasonService(db_path)
    season_id = await _insert(db_path, "SETUP", "CONFIGURATION")
    for stage in (
        SeasonStage.WAITING,
        SeasonStage.SIGNUPS,
        SeasonStage.PLACEMENTS,
        SeasonStage.ONGOING,
        SeasonStage.ONGOING_SIGNUPS,
        SeasonStage.ONGOING_PLACEMENTS,
        SeasonStage.ONGOING,
        SeasonStage.PENDING_COMPLETION,
        SeasonStage.COMPLETED,
    ):
        await svc.set_stage(season_id, stage)
        assert await svc.get_stage(season_id) is stage
    assert await _row(db_path, season_id) == ("COMPLETED", "COMPLETED")


@pytest.mark.parametrize(
    "start, target",
    [
        ("CONFIGURATION", "SIGNUPS"),
        ("WAITING", "PLACEMENTS"),
        ("PLACEMENTS", "CANCELLED"),
        ("ONGOING_PLACEMENTS", "ONGOING_SIGNUPS"),
        ("ONGOING_SIGNUPS", "PENDING_COMPLETION"),
        ("PENDING_COMPLETION", "CANCELLED"),
    ],
)
async def test_set_stage_refuses_a_transition_the_lifecycle_forbids(db_path, start, target):
    svc = SeasonService(db_path)
    status = status_of_stage(SeasonStage(start)).value
    season_id = await _insert(db_path, status, start)

    with pytest.raises(InvalidStageTransition):
        await svc.set_stage(season_id, SeasonStage(target))

    assert await svc.get_stage(season_id) is SeasonStage(start)


async def test_set_stage_refuses_a_season_that_does_not_exist(db_path):
    with pytest.raises(InvalidStageTransition):
        await SeasonService(db_path).set_stage(9999, SeasonStage.ONGOING)


async def test_a_season_read_carries_its_stage(db_path):
    svc = SeasonService(db_path)
    await _insert(db_path, "ACTIVE", "ONGOING_PLACEMENTS")
    season = await svc.get_confirmed_season()
    assert season is not None
    assert season.stage is SeasonStage.ONGOING_PLACEMENTS


async def test_a_status_the_lifecycle_does_not_know_is_left_without_a_stage(db_path):
    """The triggers never refuse a row for a status they have no stage for."""
    season_id = await _insert(db_path, "ARCHIVED")
    assert await _row(db_path, season_id) == ("ARCHIVED", None)


async def _freeze_stage(db_path):
    """A trigger that silently drops every stage write — as if another caller got there first."""
    async with get_connection(db_path) as db:
        await db.execute(
            "CREATE TRIGGER freeze_stage BEFORE UPDATE OF stage ON seasons "
            "BEGIN SELECT RAISE(IGNORE); END"
        )
        await db.commit()


async def test_set_stage_refuses_a_season_that_left_its_stage_meanwhile(db_path):
    """The write is conditioned on the stage read, so a lost race writes nothing and says so."""
    season_id = await _insert(db_path, "SETUP", "CONFIGURATION")
    await _freeze_stage(db_path)

    with pytest.raises(InvalidStageTransition, match="left CONFIGURATION before it could move"):
        await SeasonService(db_path).set_stage(season_id, SeasonStage.WAITING)


async def test_get_stage_is_none_for_a_season_without_one_or_no_season_at_all(db_path):
    svc = SeasonService(db_path)
    season_id = await _insert(db_path, "ARCHIVED")

    assert await svc.get_stage(season_id) is None
    assert await svc.get_stage(9999) is None
