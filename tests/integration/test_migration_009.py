"""Integration test for migration 009_module_system.sql — T033.

Verifies that the full migration chain (001–009) applies cleanly,
that all expected new tables exist, that forecast_channel_id is nullable
on divisions, and that server_configs has the new boolean columns defaulting to 0.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


@pytest.mark.asyncio
async def test_migration_009_applies_cleanly() -> None:
    """Migration chain through 009 must apply without error."""
    from db.database import run_migrations, get_connection

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        await run_migrations(db_path)

        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = {row[0] for row in await cursor.fetchall()}

        # New tables must exist
        expected_new = {
            "signup_module_config",
            "signup_module_settings",
            "signup_availability_slots",
        }
        assert expected_new.issubset(tables), (
            f"Missing tables after migration 009: {expected_new - tables}"
        )
    finally:
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_forecast_channel_id_is_nullable() -> None:
    """divisions.forecast_channel_id must accept NULL after migration."""
    from db.database import run_migrations, get_connection

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        await run_migrations(db_path)

        async with get_connection(db_path) as db:
            # Insert a server config and season first (FK chain)
            await db.execute(
                "INSERT INTO server_configs (server_id, interaction_role_id, "
                "interaction_channel_id, log_channel_id) VALUES (1, 0, 0, 0)"
            )
            await db.execute(
                "INSERT INTO seasons (server_id, season_number, status, start_date) "
                "VALUES (1, 1, 'SETUP', '2025-01-01T00:00:00')"
            )
            cursor = await db.execute("SELECT last_insert_rowid()")
            (season_id,) = await cursor.fetchone()

            # Insert division with NULL forecast_channel_id — must not raise
            await db.execute(
                "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id, tier) "
                "VALUES (?, 'Div A', 0, NULL, 1)",
                (season_id,),
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT forecast_channel_id FROM divisions LIMIT 1"
            )
            row = await cursor.fetchone()
            assert row[0] is None, "forecast_channel_id should be NULL"
    finally:
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_module_flag_columns_default_to_zero() -> None:
    """server_configs must have weather_module_enabled and signup_module_enabled defaulting to 0."""
    from db.database import run_migrations, get_connection

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        await run_migrations(db_path)

        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO server_configs (server_id, interaction_role_id, "
                "interaction_channel_id, log_channel_id) VALUES (2, 0, 0, 0)"
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT weather_module_enabled, signup_module_enabled "
                "FROM server_configs WHERE server_id = 2"
            )
            row = await cursor.fetchone()

        assert row[0] == 0, "weather_module_enabled should default to 0"
        assert row[1] == 0, "signup_module_enabled should default to 0"
    finally:
        os.unlink(db_path)
