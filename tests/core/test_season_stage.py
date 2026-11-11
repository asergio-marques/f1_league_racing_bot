"""A season's lifecycle stage, and how it stays in step with its coarse status (issue #220).

Migration 057 adds `seasons.stage` beside `seasons.status`. The status keeps the grain every
older reader was written against; the stage refines it into the ten states the core
specification names. These tests pin the mapping between the two, the migration's backfill
of rows already in the old shape, and the move the configuration's confirmation makes on its
save, `confirm_configuration_on`.
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
from leaguebot.core.services.seasons.season_service import SeasonService

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


@pytest.mark.parametrize(
    "status, stage",
    [(status, stage) for status in SeasonStatus for stage in SeasonStage],
    ids=lambda value: value.value,
)
async def test_every_status_and_stage_pair_is_held_by_the_triggers(db_path, status, stage):
    """A season written with a status and a stage keeps the pair where the status admits the
    stage (`STAGES_OF_STATUS`) and is refused otherwise, whether it is inserted with the pair or
    has its stage moved to it, so the triggers and the mapping cannot drift apart."""
    admitted = stage in STAGES_OF_STATUS[status]

    if admitted:
        season_id = await _insert(db_path, status.value, stage.value)
        assert await _row(db_path, season_id) == (status.value, stage.value)
        async with get_connection(db_path) as db:  # one live season at a time
            await db.execute("DELETE FROM seasons")
            await db.commit()
    else:
        with pytest.raises(sqlite3.IntegrityError, match="stage does not match"):
            await _insert(db_path, status.value, stage.value)

    season_id = await _insert(db_path, status.value, sorted(STAGES_OF_STATUS[status])[0].value)
    async with get_connection(db_path) as db:
        if admitted:
            await db.execute("UPDATE seasons SET stage = ? WHERE id = ?", (stage.value, season_id))
            await db.commit()
        else:
            with pytest.raises(sqlite3.IntegrityError, match="stage does not match"):
                await db.execute(
                    "UPDATE seasons SET stage = ? WHERE id = ?", (stage.value, season_id)
                )
    if admitted:
        assert await _row(db_path, season_id) == (status.value, stage.value)


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
    """Writing an ongoing season's status again leaves its stage where it was; writing a status
    that admits no stage it holds moves the stage with it."""
    season_id = await _insert(db_path, "ACTIVE", "ONGOING_SIGNUPS")
    async with get_connection(db_path) as db:
        await db.execute("UPDATE seasons SET status = 'ACTIVE' WHERE id = ?", (season_id,))
        await db.commit()
    assert await _row(db_path, season_id) == ("ACTIVE", "ONGOING_SIGNUPS")

    async with get_connection(db_path) as db:
        await db.execute("UPDATE seasons SET status = 'CANCELLED' WHERE id = ?", (season_id,))
        await db.commit()
    assert await _row(db_path, season_id) == ("CANCELLED", "CANCELLED")


# ── SeasonService ───────────────────────────────────────────────────────────────────


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


async def test_get_stage_is_none_for_a_season_without_one_or_no_season_at_all(db_path):
    svc = SeasonService(db_path)
    season_id = await _insert(db_path, "ARCHIVED")

    assert await svc.get_stage(season_id) is None
    assert await svc.get_stage(9999) is None


# ── The configuration confirmed, on the confirmation's save ─────────────────────────


@pytest.mark.parametrize("target", [SeasonStage.WAITING, SeasonStage.PLACEMENTS])
async def test_confirming_the_configuration_on_moves_the_stage_on_the_save_s_connection(
    db_path, target
):
    """A season being set up, in Configuration. `confirm_configuration_on`, handed a connection,
    the season and Waiting or Placements, moves the season to that stage on that connection, its
    status still SETUP, and commits nothing: rolled back, the season is in Configuration again."""
    from leaguebot.core.services.seasons.season_lifecycle_service import confirm_configuration_on

    season_id = await _insert(db_path, "SETUP", "CONFIGURATION")

    async with get_connection(db_path) as db:
        await confirm_configuration_on(db, season_id, target)

        cursor = await db.execute("SELECT status, stage FROM seasons WHERE id = ?", (season_id,))
        row = await cursor.fetchone()
        assert (row["status"], row["stage"]) == ("SETUP", target.value)
        assert db.in_transaction
        await db.rollback()

    assert await _row(db_path, season_id) == ("SETUP", "CONFIGURATION")


@pytest.mark.parametrize(
    "start, target",
    [
        ("WAITING", SeasonStage.WAITING),
        ("PLACEMENTS", SeasonStage.WAITING),
        (None, SeasonStage.WAITING),
        ("CONFIGURATION", SeasonStage.SIGNUPS),
    ],
    ids=["waiting", "placements", "no_such_season", "a_target_the_lifecycle_forbids"],
)
async def test_confirming_the_configuration_on_from_another_stage_raises_and_writes_nothing(
    db_path, start, target
):
    """A season that has left Configuration, for Waiting or Placements, or a season that does not
    exist, asked to move to Waiting; or a season in Configuration asked to move to Signups, which
    the lifecycle forbids from Configuration: `confirm_configuration_on` raises
    InvalidStageTransition, and the season, where there is one, is left where it stood."""
    from leaguebot.core.services.seasons.season_lifecycle_service import confirm_configuration_on

    season_id = await _insert(db_path, "SETUP", start) if start is not None else 9999

    async with get_connection(db_path) as db:
        with pytest.raises(InvalidStageTransition):
            await confirm_configuration_on(db, season_id, target)
        await db.commit()

    if start is not None:
        assert await _row(db_path, season_id) == ("SETUP", start)
