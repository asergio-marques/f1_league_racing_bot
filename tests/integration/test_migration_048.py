"""Migration 048 — an eighth asset directory, for a division's own logo.

A plain `ALTER TABLE ... ADD COLUMN`, which is the whole point: nothing about the existing
columns changes and there is no value to migrate, so 044's precedent applies rather than
043's table rebuild. Two things it must get right, and both are pinned here.

* **A league already configured keeps everything it configured.** The column arrives on rows
  that already exist, carrying its default, and nothing else on those rows moves. A rebuild
  is where that goes wrong, which is the reason not to do one.
* **The default is the league folder, not the packaged one.** 043's rule: a league drops a
  file into `resources/league/division-logos/` and it is drawn with no command run at all,
  while the packaged blank answers every division that has none.
"""
from __future__ import annotations

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import db.database as database  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402

MIGRATION = "048_division_logo_directory.sql"
COLUMN = "division_logo_directory"
DEFAULT = "resources/league/division-logos"

_SEED_SERVER = (
    "INSERT INTO server_configs "
    "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
    "VALUES (?, 1, 2, 3)"
)


@pytest.fixture()
async def pre_migration_db(tmp_path):
    """A database migrated up to 047 — the schema as it stood before this change.

    Every migration from 048 onwards is stashed, not 048 alone, for the reason
    `test_migration_043` records: leaving a later one in place lets it apply out of order when
    the fixture restores this one.
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


async def test_a_new_server_gets_the_league_folder(pre_migration_db):
    await run_migrations(pre_migration_db)

    async with get_connection(pre_migration_db) as db:
        await db.execute(_SEED_SERVER, (1,))
        await db.execute("INSERT INTO image_config (server_id) VALUES (1)")
        await db.commit()

        cursor = await db.execute(
            f"SELECT {COLUMN} FROM image_config WHERE server_id = 1"
        )
        row = await cursor.fetchone()

    assert row[0] == DEFAULT


async def test_a_league_already_configured_keeps_what_it_configured(pre_migration_db):
    """The column arrives on an existing row and nothing else on that row moves.

    This is what an ADD COLUMN buys over a rebuild, and it is worth pinning rather than
    assuming: a league rendering from directories it named must not have them replaced by a
    schema change it never asked for.
    """
    async with get_connection(pre_migration_db) as db:
        await db.execute(_SEED_SERVER, (1,))
        await db.execute(
            "INSERT INTO image_config (server_id, flag_directory, tyre_directory) "
            "VALUES (1, ?, ?)",
            ("resources/league/my-own-flags", "resources/league/my-own-tyres"),
        )
        await db.commit()

    await run_migrations(pre_migration_db)

    async with get_connection(pre_migration_db) as db:
        cursor = await db.execute(
            f"SELECT flag_directory, tyre_directory, {COLUMN} "
            "FROM image_config WHERE server_id = 1"
        )
        flags, tyres, logos = await cursor.fetchone()

    assert flags == "resources/league/my-own-flags"
    assert tyres == "resources/league/my-own-tyres"
    assert logos == DEFAULT


async def test_the_schema_default_matches_the_constant(pre_migration_db):
    """Written twice — once in SQL, once in `ASSET_DIRECTORIES` — and must not drift."""
    from models.image_constants import ASSET_DIRECTORIES

    await run_migrations(pre_migration_db)

    async with get_connection(pre_migration_db) as db:
        await db.execute(_SEED_SERVER, (1,))
        await db.execute("INSERT INTO image_config (server_id) VALUES (1)")
        await db.commit()

        cursor = await db.execute(
            f"SELECT {COLUMN} FROM image_config WHERE server_id = 1"
        )
        stored = (await cursor.fetchone())[0]

    _command, default, _packaged = ASSET_DIRECTORIES[COLUMN]
    assert stored == default
