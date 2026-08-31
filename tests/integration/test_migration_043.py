"""Migration 043 — the seven asset directories default to `resources/league/`.

The bot ships its artwork under `resources/defaults/` and a league puts its own under
`resources/league/`, which is gitignored and survives an update. Until now both were the
same path, so a league had to run `/images config <class>-directory` seven times before the
folders shipped for its own artwork were looked at. Two-tier resolution makes that
unnecessary: a miss in the configured directory falls through to the packaged one, so the
default moves and dropping a file in is the whole job.

Two things this migration must get right, and both are pinned here:

* **Only the default changes.** Every existing row is copied verbatim — a league that named
  a directory keeps it, and so does one still sitting on the old default. Changing a value
  a league is currently rendering from is not a migration's business.
* **The aspect toggles survive.** `image_aspect_toggles` carries a foreign key onto
  `image_config(server_id) ON DELETE CASCADE`, and dropping a parent table with foreign-key
  enforcement on performs an implicit DELETE FROM that fires the cascade. The migration
  sets them aside and restores them rather than relying on the pragma state the runner
  happens to connect with.

`template_directory` deliberately does not move: templates have no packaged second tier.
"""
from __future__ import annotations

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import db.database as database  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402

MIGRATION = "043_league_asset_directories.sql"

_SEED_SERVER = (
    "INSERT INTO server_configs "
    "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
    "VALUES (?, 1, 2, 3)"
)

ASSET_COLUMNS = (
    "track_image_directory",
    "team_image_directory",
    "flag_directory",
    "driver_image_directory",
    "marker_directory",
    "weather_icon_directory",
    "tyre_directory",
)


@pytest.fixture()
async def pre_migration_db(tmp_path):
    """A database migrated up to 042 — the schema as it stood before this change.

    The migrations are moved aside rather than filtered out of the run, because the runner
    discovers them by listing the directory; hiding the files is the only way to stop at 042
    without reimplementing that discovery here.

    **Every migration from 043 onwards is stashed, not 043 alone.** Stopping at 042 means
    stopping there: leaving a later migration in place lets it apply to the pre-043 schema and
    then run again, out of order, when the fixture restores 043 — which is not a state any
    deployment reaches, and which broke this fixture the moment a 044 was added. Listing the
    directory rather than naming files keeps that true for every migration still to come.
    """
    path = str(tmp_path / "pre.db")
    stash = tmp_path / "stashed-migrations"
    stash.mkdir()

    later = sorted(
        name
        for name in os.listdir(database._MIGRATIONS_DIR)
        if name.endswith(".sql") and name >= MIGRATION
    )
    for name in later:
        shutil.move(os.path.join(database._MIGRATIONS_DIR, name), str(stash / name))
    try:
        await run_migrations(path)
    finally:
        for name in later:
            shutil.move(str(stash / name), os.path.join(database._MIGRATIONS_DIR, name))
    return path


async def test_a_new_server_gets_the_league_folders(pre_migration_db):
    await run_migrations(pre_migration_db)

    async with get_connection(pre_migration_db) as db:
        await db.execute(_SEED_SERVER, (1,))
        await db.execute("INSERT INTO image_config (server_id) VALUES (1)")
        await db.commit()

        cursor = await db.execute(
            f"SELECT {', '.join(ASSET_COLUMNS)} FROM image_config WHERE server_id = 1"
        )
        row = await cursor.fetchone()

    for column, value in zip(ASSET_COLUMNS, tuple(row)):
        assert value.startswith("resources/league/"), f"{column}: {value}"


async def test_the_template_directory_does_not_move(pre_migration_db):
    """It has no packaged second tier: pointing it at an empty folder would leave a fresh
    install unable to render anything at all."""
    await run_migrations(pre_migration_db)

    async with get_connection(pre_migration_db) as db:
        await db.execute(_SEED_SERVER, (1,))
        await db.execute("INSERT INTO image_config (server_id) VALUES (1)")
        await db.commit()

        cursor = await db.execute(
            "SELECT template_directory FROM image_config WHERE server_id = 1"
        )
        assert (await cursor.fetchone())[0] == "resources/defaults/templates"


async def test_an_existing_row_is_carried_over_verbatim(pre_migration_db):
    """A league that named a directory keeps it, and one still on the old default keeps
    that too — the value it renders from today does not change under it."""
    async with get_connection(pre_migration_db) as db:
        await db.execute(_SEED_SERVER, (7,))
        await db.execute("INSERT INTO image_config (server_id) VALUES (7)")
        await db.execute(
            "UPDATE image_config SET flag_directory = 'resources/mine/flags', "
            "module_enabled = 1, time_zone = 'Europe/Lisbon' WHERE server_id = 7"
        )
        await db.commit()

    await run_migrations(pre_migration_db)

    async with get_connection(pre_migration_db) as db:
        cursor = await db.execute(
            "SELECT flag_directory, track_image_directory, module_enabled, time_zone "
            "FROM image_config WHERE server_id = 7"
        )
        flag, track, enabled, zone = tuple(await cursor.fetchone())

    assert flag == "resources/mine/flags", "a directory the league named was rewritten"
    assert track == "resources/defaults/tracks", "an untouched row was rewritten"
    assert enabled == 1
    assert zone == "Europe/Lisbon"


async def test_the_aspect_toggles_survive_the_rebuild(pre_migration_db):
    """The cascade this migration works around: dropping the parent table would take them."""
    async with get_connection(pre_migration_db) as db:
        await db.execute(_SEED_SERVER, (7,))
        await db.execute("INSERT INTO image_config (server_id) VALUES (7)")
        await db.executemany(
            "INSERT INTO image_aspect_toggles (server_id, aspect, enabled) VALUES (?, ?, ?)",
            [(7, "calendar", 1), (7, "results", 1), (7, "lineup", 0)],
        )
        await db.commit()

    await run_migrations(pre_migration_db)

    async with get_connection(pre_migration_db) as db:
        cursor = await db.execute(
            "SELECT aspect, enabled FROM image_aspect_toggles "
            "WHERE server_id = 7 ORDER BY aspect"
        )
        rows = [tuple(row) for row in await cursor.fetchall()]

    assert rows == [("calendar", 1), ("lineup", 0), ("results", 1)]


async def test_the_rebuilt_table_keeps_its_own_foreign_key(pre_migration_db):
    """The rebuild must not quietly drop the cascade from `server_configs`."""
    await run_migrations(pre_migration_db)

    async with get_connection(pre_migration_db) as db:
        await db.execute(_SEED_SERVER, (7,))
        await db.execute("INSERT INTO image_config (server_id) VALUES (7)")
        await db.commit()

        await db.execute("DELETE FROM server_configs WHERE server_id = 7")
        await db.commit()

        cursor = await db.execute(
            "SELECT COUNT(*) FROM image_config WHERE server_id = 7"
        )
        assert (await cursor.fetchone())[0] == 0

        cursor = await db.execute("PRAGMA foreign_key_check")
        assert await cursor.fetchall() == []


async def test_the_defaults_in_the_schema_match_the_constants(pre_migration_db):
    """One table in code, one set of column defaults in SQL. They are written twice and
    must not drift: a row created by `create_with_defaults` carries the SQL defaults, and
    everything else in the module reads the constants."""
    from models.image_constants import ASSET_DIRECTORIES

    await run_migrations(pre_migration_db)

    async with get_connection(pre_migration_db) as db:
        await db.execute(_SEED_SERVER, (1,))
        await db.execute("INSERT INTO image_config (server_id) VALUES (1)")
        await db.commit()

        cursor = await db.execute("SELECT * FROM image_config WHERE server_id = 1")
        row = await cursor.fetchone()

    for column, (_command, default, _packaged) in ASSET_DIRECTORIES.items():
        assert row[column] == default, column
