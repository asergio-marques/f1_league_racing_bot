"""Migration 051 — a tier's own colours, and the toggle that turns them on.

A `CREATE TABLE` and a plain `ALTER TABLE ... ADD COLUMN`, following 044 and 048 rather than
043's table rebuild. Three things it must get right, and all three are pinned here.

* **A league already configured keeps everything it configured.** The column arrives on rows
  that already exist, carrying its default, and nothing else on those rows moves.
* **The feature arrives off.** A league that upgrades and does nothing sees no change at all
  — which is the whole promise of an optional feature, and the default is the only thing
  enforcing it.
* **The table is keyed so one tier holds one colour per slot.** Setting the same slot twice
  replaces rather than accumulates, and that is the primary key's job, not the service's.
"""
from __future__ import annotations

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import db.database as database  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402

MIGRATION = "051_per_tier_colours.sql"
COLUMN = "per_tier_colour_enabled"

_SEED_SERVER = (
    "INSERT INTO server_configs "
    "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
    "VALUES (?, 1, 2, 3)"
)


@pytest.fixture()
async def pre_migration_db(tmp_path):
    """A database migrated up to 050 — the schema as it stood before this change.

    Every migration from 051 onwards is stashed, not 051 alone, for the reason
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


async def test_a_new_server_has_the_feature_off(pre_migration_db):
    await run_migrations(pre_migration_db)

    async with get_connection(pre_migration_db) as db:
        await db.execute(_SEED_SERVER, (1,))
        await db.execute("INSERT INTO image_config (server_id) VALUES (1)")
        await db.commit()
        row = await (
            await db.execute(
                f"SELECT {COLUMN} FROM image_config WHERE server_id = 1"
            )
        ).fetchone()

    assert row[COLUMN] == 0


async def test_a_league_already_configured_keeps_what_it_configured(pre_migration_db):
    """The column arrives on an existing row without disturbing it, and arrives off."""
    async with get_connection(pre_migration_db) as db:
        await db.execute(_SEED_SERVER, (7,))
        await db.execute(
            "INSERT INTO image_config (server_id, template_directory, fastest_lap_colour) "
            "VALUES (7, 'resources/league/templates', '#123456')"
        )
        await db.commit()

    await run_migrations(pre_migration_db)

    async with get_connection(pre_migration_db) as db:
        row = await (
            await db.execute(
                f"SELECT template_directory, fastest_lap_colour, {COLUMN} "
                "FROM image_config WHERE server_id = 7"
            )
        ).fetchone()

    assert row["template_directory"] == "resources/league/templates"
    assert row["fastest_lap_colour"] == "#123456"
    assert row[COLUMN] == 0


async def test_one_tier_holds_one_colour_per_slot(pre_migration_db):
    """The primary key is what makes a second set replace the first rather than add to it."""
    await run_migrations(pre_migration_db)

    async with get_connection(pre_migration_db) as db:
        await db.execute(_SEED_SERVER, (1,))
        for colour in ("#111111", "#222222"):
            await db.execute(
                "INSERT INTO image_tier_colour (server_id, division_slug, slot, colour) "
                "VALUES (1, 'division_1', 'accent', ?) "
                "ON CONFLICT(server_id, division_slug, slot) "
                "DO UPDATE SET colour = excluded.colour",
                (colour,),
            )
        await db.commit()
        rows = await (
            await db.execute("SELECT slot, colour FROM image_tier_colour")
        ).fetchall()

    assert [(r["slot"], r["colour"]) for r in rows] == [("accent", "#222222")]


async def test_two_tiers_hold_the_same_slot_independently(pre_migration_db):
    await run_migrations(pre_migration_db)

    async with get_connection(pre_migration_db) as db:
        await db.execute(_SEED_SERVER, (1,))
        await db.execute(
            "INSERT INTO image_tier_colour VALUES (1, 'division_1', 'accent', '#3DD6F5')"
        )
        await db.execute(
            "INSERT INTO image_tier_colour VALUES (1, 'division_2', 'accent', '#A78BFA')"
        )
        await db.commit()
        rows = await (
            await db.execute(
                "SELECT division_slug, colour FROM image_tier_colour ORDER BY division_slug"
            )
        ).fetchall()

    assert [(r["division_slug"], r["colour"]) for r in rows] == [
        ("division_1", "#3DD6F5"),
        ("division_2", "#A78BFA"),
    ]


async def test_the_rows_go_when_the_server_does(pre_migration_db):
    """A tier's colours are configuration of that server and cascade with it."""
    await run_migrations(pre_migration_db)

    async with get_connection(pre_migration_db) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute(_SEED_SERVER, (1,))
        await db.execute(
            "INSERT INTO image_tier_colour VALUES (1, 'division_1', 'accent', '#3DD6F5')"
        )
        await db.commit()
        await db.execute("DELETE FROM server_configs WHERE server_id = 1")
        await db.commit()
        left = await (
            await db.execute("SELECT COUNT(*) AS n FROM image_tier_colour")
        ).fetchone()

    assert left["n"] == 0
