"""Integration tests for database.py — migration runner and connection helper."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations


@pytest.mark.asyncio
async def test_run_migrations_creates_tables() -> None:
    """run_migrations() should create all 8 expected tables."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        await run_migrations(db_path)

        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = {row[0] for row in await cursor.fetchall()}

        expected = {
            "schema_migrations",
            "server_configs",
            "seasons",
            "divisions",
            "rounds",
            "sessions",
            "phase_results",
            "audit_entries",
            "tracks",
            "track_records",
            "lap_records",
        }
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"
        assert "track_rpc_params" not in tables, "track_rpc_params should have been dropped by migration 029"
    finally:
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_run_migrations_idempotent() -> None:
    """Running migrations twice should not raise or duplicate entries."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        await run_migrations(db_path)

        async with get_connection(db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM schema_migrations")
            (count_after_first,) = await cursor.fetchone()

        assert count_after_first >= 1  # at least one migration file recorded

        await run_migrations(db_path)  # Second run — should be a no-op

        async with get_connection(db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM schema_migrations")
            (count_after_second,) = await cursor.fetchone()

        assert count_after_second == count_after_first  # no duplicates
    finally:
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_foreign_keys_enabled() -> None:
    """get_connection should enable PRAGMA foreign_keys."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        async with get_connection(db_path) as db:
            cursor = await db.execute("PRAGMA foreign_keys")
            (fk,) = await cursor.fetchone()
        assert fk == 1
    finally:
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_migration_029_track_tables() -> None:
    """Migration 029 should seed 28 tracks, drop track_rpc_params, and create track_records/lap_records."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        await run_migrations(db_path)

        async with get_connection(db_path) as db:
            # tracks table exists and has 28 seed rows
            cursor = await db.execute("SELECT COUNT(*) FROM tracks")
            (track_count,) = await cursor.fetchone()
            assert track_count == 28, f"Expected 28 track rows, got {track_count}"

            # track_rpc_params was dropped
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='track_rpc_params'"
            )
            assert await cursor.fetchone() is None, "track_rpc_params should not exist after migration 029"

            # track_records and lap_records exist
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('track_records', 'lap_records')"
            )
            found = {row[0] for row in await cursor.fetchall()}
            assert found == {"track_records", "lap_records"}, f"Missing track tables: {found}"
    finally:
        os.unlink(db_path)
