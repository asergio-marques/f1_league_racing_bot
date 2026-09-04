"""A server holds at most one live season, enforced by the stored data.

A season is live while it is SETUP or ACTIVE. `/season setup` already refused to start a
second one, but nothing below that command enforced it, and a good deal of code assumes
it: `_get_active_season_id` and `get_setup_or_active_season` each selected
`WHERE status IN ('ACTIVE','SETUP')` and took a row with no ordering. On a server holding
two, those returned an arbitrary one — `/test-mode roster add` seated mock drivers in one
season's divisions while `/season review` drew the other's, giving a full roster listing
beside an empty lineup with neither command reporting a fault.

Migration 049 makes the state impossible rather than teaching each reader a precedence.
It also repairs a database that already holds the state, which is the case that matters
for a bot that has been running: an ACTIVE season is kept over a SETUP one, since a
season being *run* outranks a draft of the next and the draft can be rebuilt.

The archive is deliberately untouched — a league keeps every completed and cancelled
season it has ever had, and the constraint covers only the live rows.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402

SERVER_ID = 4242
OTHER_SERVER = 4343
MIGRATIONS = os.path.join(
    os.path.dirname(__file__), "..", "..", "src", "db", "migrations"
)


async def _seed_server(db_path, *server_ids):
    async with get_connection(db_path) as db:
        for server_id in server_ids:
            await db.execute(
                "INSERT OR IGNORE INTO server_configs (server_id, interaction_role_id, "
                "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
                (server_id,),
            )
        await db.commit()


async def _add_season(db_path, status, number, *, server_id=SERVER_ID):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, '2026-03-01', ?, ?)",
            (server_id, status, number),
        )
        await db.commit()
        return cursor.lastrowid


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "live.db")
    await run_migrations(path)
    await _seed_server(path, SERVER_ID, OTHER_SERVER)
    return path


# ── The constraint ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "first, second",
    [
        ("ACTIVE", "SETUP"),
        ("SETUP", "ACTIVE"),
        ("SETUP", "SETUP"),
        ("ACTIVE", "ACTIVE"),
    ],
)
async def test_a_second_live_season_is_refused(db_path, first, second):
    await _add_season(db_path, first, 1)

    with pytest.raises(aiosqlite.IntegrityError):
        await _add_season(db_path, second, 2)


@pytest.mark.parametrize("archived", ["COMPLETED", "CANCELLED"])
async def test_an_archived_season_does_not_block_a_live_one(db_path, archived):
    """A league starts its next season with its history behind it."""
    await _add_season(db_path, archived, 1)

    assert await _add_season(db_path, "SETUP", 2)


async def test_a_league_keeps_every_season_of_its_history(db_path):
    """The rule constrains the live season only — the archive is unbounded."""
    for number, status in enumerate(
        ["COMPLETED", "COMPLETED", "CANCELLED", "COMPLETED"], start=1
    ):
        await _add_season(db_path, status, number)
    await _add_season(db_path, "ACTIVE", 5)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM seasons WHERE server_id = ?", (SERVER_ID,)
        )
        assert (await cursor.fetchone())[0] == 5


async def test_each_server_has_its_own_live_season(db_path):
    """The constraint is per server, not global — the bot serves many leagues."""
    await _add_season(db_path, "ACTIVE", 1)

    assert await _add_season(db_path, "SETUP", 1, server_id=OTHER_SERVER)


# ── Repairing a database that already breaks the rule ──────────────────────


def _schema_before_049(path):
    """Build the schema as it stood before migration 049, and dirty it."""
    files = sorted(
        f
        for f in os.listdir(MIGRATIONS)
        if f.endswith(".sql") and not f.startswith(("__", "049"))
    )
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for name in files:
        with open(os.path.join(MIGRATIONS, name), encoding="utf-8") as fh:
            con.executescript(fh.read())
        con.execute(
            "INSERT OR IGNORE INTO schema_migrations VALUES (?, datetime('now'))",
            (name,),
        )
    con.commit()
    return con


@pytest.mark.parametrize(
    "rows, survivor",
    [
        # (id, status) pairs → the id expected to remain live.
        ([(10, "ACTIVE"), (11, "SETUP")], 10),   # the running season outranks the draft
        ([(10, "SETUP"), (11, "ACTIVE")], 11),   # whichever order they were written in
        ([(10, "SETUP"), (11, "SETUP")], 11),    # among equals, the newest
        ([(10, "ACTIVE"), (11, "ACTIVE")], 11),
    ],
)
async def test_the_migration_repairs_a_database_that_already_holds_two(
    tmp_path, rows, survivor
):
    path = str(tmp_path / "dirty.db")
    con = _schema_before_049(path)
    con.execute(
        "INSERT INTO server_configs (server_id, interaction_role_id, "
        "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
        (SERVER_ID,),
    )
    for season_id, status in rows:
        con.execute(
            "INSERT INTO seasons (id, server_id, start_date, status, season_number) "
            "VALUES (?, ?, '2026-03-01', ?, 1)",
            (season_id, SERVER_ID, status),
        )
    con.commit()
    con.close()

    await run_migrations(path)

    async with get_connection(path) as db:
        cursor = await db.execute(
            "SELECT id FROM seasons WHERE status IN ('SETUP', 'ACTIVE')"
        )
        live = [r[0] for r in await cursor.fetchall()]
        cursor = await db.execute("SELECT COUNT(*) FROM seasons")
        total = (await cursor.fetchone())[0]

    assert live == [survivor]
    # The losing season is cancelled, never deleted: rounds, teams and assignments hang
    # off it behind foreign keys that do not cascade.
    assert total == len(rows)


async def test_the_repair_leaves_the_archive_alone(tmp_path):
    path = str(tmp_path / "dirty_archive.db")
    con = _schema_before_049(path)
    con.execute(
        "INSERT INTO server_configs (server_id, interaction_role_id, "
        "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
        (SERVER_ID,),
    )
    for season_id, status in ((8, "COMPLETED"), (9, "CANCELLED"), (10, "ACTIVE"), (11, "SETUP")):
        con.execute(
            "INSERT INTO seasons (id, server_id, start_date, status, season_number) "
            "VALUES (?, ?, '2026-03-01', ?, 1)",
            (season_id, SERVER_ID, status),
        )
    con.commit()
    con.close()

    await run_migrations(path)

    async with get_connection(path) as db:
        cursor = await db.execute("SELECT id, status FROM seasons ORDER BY id")
        rows = {r[0]: r[1] for r in await cursor.fetchall()}

    assert rows[8] == "COMPLETED", "an archived season must not be touched"
    assert rows[9] == "CANCELLED"
    assert rows[10] == "ACTIVE", "the running season is the one kept"
    assert rows[11] == "CANCELLED"


async def test_the_migration_is_idempotent(tmp_path):
    """Startup applies migrations every run; a second pass must change nothing."""
    path = str(tmp_path / "twice.db")
    await run_migrations(path)
    await _seed_server(path, SERVER_ID)
    await _add_season(path, "ACTIVE", 1)

    await run_migrations(path)

    async with get_connection(path) as db:
        cursor = await db.execute(
            "SELECT status FROM seasons WHERE server_id = ?", (SERVER_ID,)
        )
        assert [r[0] for r in await cursor.fetchall()] == ["ACTIVE"]


# ── The readers agree ─────────────────────────────────────────────────────


async def test_every_reader_of_the_season_resolves_the_same_row(db_path):
    """The bug this exists to stop: two commands disagreeing about "the season".

    `_get_active_season_id` backs the `/test-mode roster` commands and
    `get_setup_or_active_season` backs several others. With one live season they cannot
    disagree, and both are ordered so they would still agree on a database that predates
    the constraint.
    """
    from services.season_service import SeasonService
    from services.test_roster_service import _get_active_season_id

    season_id = await _add_season(db_path, "SETUP", 1)

    roster_view = await _get_active_season_id(SERVER_ID, db_path)
    service = SeasonService(db_path)
    season_view = await service.get_setup_or_active_season(SERVER_ID)
    preview_view = await service.get_previewable_season(SERVER_ID)

    assert roster_view == season_id
    assert season_view.id == season_id
    assert preview_view.id == season_id
