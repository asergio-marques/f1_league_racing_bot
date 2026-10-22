"""The amendment mode's writers, moved to results and run on the change queue (#439, slice 7).

`results/services/amendment_mode_service.py` writes amendment mode, its modification store and its
modified flag on the connection a settings change hands it, committing nothing, and returns what
it did: whether anything changed, and the audit entries the queue saves with it. Each test builds
season 1, under way and scoring on `Standard` (Feature Race P1 25, P2 18, a fastest-lap bonus of 1
to the top ten), on a database built by the migrations.
"""
from __future__ import annotations

import os
from typing import Any

import pytest

from leaguebot.core.db.database import get_connection, run_migrations

#: Why the tests here fail until the writers are built.
UNBUILT = "#439: results/services/amendment_mode_service.py has no writers on a connection yet"
SEASON_ID = 1
CONFIG = "Standard"


async def _season(tmp_path: Any, *, amend: bool = False, staged: bool = False) -> str:
    """Season 1 of the module docstring; with *amend*, amendment mode on, its store a copy of the
    season's points; with *staged*, P1 staged at 26 and the modified flag set."""
    os.makedirs(str(tmp_path), exist_ok=True)
    db_path = os.path.join(str(tmp_path), "amendment_mode.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status, stage) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE', 'ONGOING')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
            (SEASON_ID, CONFIG),
        )
        for table in ("season_points", "season_modification") if amend else ("season_points",):
            for position, points in ((1, 25), (2, 18)):
                await db.execute(
                    f"INSERT INTO {table}_entries (season_id, config_name, session_type, "
                    "position, points) VALUES (?, ?, 'FEATURE_RACE', ?, ?)",
                    (SEASON_ID, CONFIG, position, points),
                )
            await db.execute(
                f"INSERT INTO {table}_fl (season_id, config_name, session_type, fl_points, "
                "fl_position_limit) VALUES (?, ?, 'FEATURE_RACE', 1, 10)",
                (SEASON_ID, CONFIG),
            )
        if amend:
            await db.execute(
                "INSERT INTO season_amendment_state (season_id, amendment_active, modified_flag) "
                "VALUES (?, 1, ?)",
                (SEASON_ID, int(staged)),
            )
        if staged:
            await db.execute(
                "UPDATE season_modification_entries SET points = 26 WHERE position = 1"
            )
        await db.commit()
    return db_path


async def _rows(db_path: str, sql: str) -> list[tuple[Any, ...]]:
    async with get_connection(db_path) as db:
        return [tuple(row) for row in await (await db.execute(sql)).fetchall()]


async def _save(db_path: str, name: str, *args: Any, **kwargs: Any) -> Any:
    """Call the writer *name* on a connection of its own and commit, as the queue's save does."""
    from leaguebot.results.services import amendment_mode_service

    async with get_connection(db_path) as db:
        saved = await getattr(amendment_mode_service, name)(db, *args, **kwargs)
        await db.commit()
    return saved


MODE = "SELECT amendment_active, modified_flag FROM season_amendment_state"
STAGED = "SELECT position, points FROM season_modification_entries ORDER BY position"
STAGED_FL = "SELECT fl_points, fl_position_limit FROM season_modification_fl"


@pytest.mark.xfail(strict=True, reason=UNBUILT)
async def test_turning_the_mode_on_copies_the_season_s_points(tmp_path):
    """Turning amendment mode on for season 1 copies its positions and fastest-lap row into the
    modification store, the flag clear, with one `POINTS_AMENDMENT_MODE_SET` entry from off to
    on."""
    db_path = await _season(tmp_path)

    saved = await _save(db_path, "set_amendment_mode_on", SEASON_ID, True)

    assert saved.changed is True
    assert [(a.change_type, a.old_value, a.new_value) for a in saved.audits] == [
        ("POINTS_AMENDMENT_MODE_SET",
         {"season_id": SEASON_ID, "amendment_active": False},
         {"season_id": SEASON_ID, "amendment_active": True}),
    ]
    assert await _rows(db_path, MODE) == [(1, 0)]
    assert await _rows(db_path, STAGED) == [(1, 25), (2, 18)]
    assert await _rows(db_path, STAGED_FL) == [(1, 10)]


@pytest.mark.xfail(strict=True, reason=UNBUILT)
async def test_turning_it_off_empties_the_store(tmp_path):
    """Turning amendment mode off for season 1, nothing staged, empties the modification store
    and records one `POINTS_AMENDMENT_MODE_SET` entry from on to off."""
    db_path = await _season(tmp_path, amend=True)

    saved = await _save(db_path, "set_amendment_mode_on", SEASON_ID, False)

    assert saved.changed is True
    assert [(a.change_type, a.new_value) for a in saved.audits] == [
        ("POINTS_AMENDMENT_MODE_SET", {"season_id": SEASON_ID, "amendment_active": False}),
    ]
    assert await _rows(db_path, MODE) == [(0, 0)]
    assert await _rows(db_path, STAGED) == []
    assert await _rows(db_path, STAGED_FL) == []


@pytest.mark.xfail(strict=True, reason=UNBUILT)
async def test_reverting_names_each_differing_row_in_its_audit_value(tmp_path):
    """Reverting the store with P1 staged at 26 puts P1 back to 25 and clears the flag, with one
    `POINTS_AMENDMENT_REVERTED` entry whose old value lists the one row that differed and whose
    new value lists none."""
    db_path = await _season(tmp_path, amend=True, staged=True)

    saved = await _save(db_path, "revert_on", SEASON_ID)

    assert saved.changed is True
    [audit] = saved.audits
    assert audit.change_type == "POINTS_AMENDMENT_REVERTED"
    assert audit.old_value["season_id"] == SEASON_ID
    assert len(audit.old_value["staged"]) == 1
    assert audit.new_value == {"season_id": SEASON_ID, "staged": []}
    assert await _rows(db_path, STAGED) == [(1, 25), (2, 18)]
    assert await _rows(db_path, MODE) == [(1, 0)]


@pytest.mark.xfail(strict=True, reason=UNBUILT)
async def test_staging_the_held_points_changes_nothing(tmp_path):
    """Staging P1 at 25, which the store already holds, changes nothing: no audit entry, and the
    modified flag left clear."""
    db_path = await _season(tmp_path, amend=True)

    saved = await _save(db_path, "stage_points_on", SEASON_ID, CONFIG, "FEATURE_RACE", [(1, 25)])

    assert saved.changed is False
    assert tuple(saved.audits) == ()
    assert await _rows(db_path, MODE) == [(1, 0)]


@pytest.mark.xfail(strict=True, reason=UNBUILT)
async def test_staging_a_fastest_lap_keeps_the_other_column(tmp_path):
    """Staging the Feature Race fastest-lap bonus at 2 keeps the staged limit of 10, with one
    `POINTS_AMENDMENT_FL_STAGED` entry."""
    db_path = await _season(tmp_path, amend=True)

    saved = await _save(
        db_path, "stage_fastest_lap_on", SEASON_ID, CONFIG, "FEATURE_RACE", fl_points=2
    )

    assert saved.changed is True
    assert [a.change_type for a in saved.audits] == ["POINTS_AMENDMENT_FL_STAGED"]
    assert await _rows(db_path, STAGED_FL) == [(2, 10)]


@pytest.mark.xfail(strict=True, reason=UNBUILT)
async def test_the_mode_writers_commit_nothing(tmp_path):
    """Each writer, its connection closed without a commit, leaves the season as it was:
    turning the mode on, staging P1 at 27 and reverting a staged P1 of 26."""
    from leaguebot.results.services import amendment_mode_service as service

    off = await _season(tmp_path / "off")
    on = await _season(tmp_path / "on", amend=True, staged=True)
    before = {path: (await _rows(path, MODE), await _rows(path, STAGED)) for path in (off, on)}

    async with get_connection(off) as db:
        await service.set_amendment_mode_on(db, SEASON_ID, True)
    async with get_connection(on) as db:
        await service.stage_points_on(db, SEASON_ID, CONFIG, "FEATURE_RACE", [(1, 27)])
    async with get_connection(on) as db:
        await service.revert_on(db, SEASON_ID)

    for path in (off, on):
        assert (await _rows(path, MODE), await _rows(path, STAGED)) == before[path]
