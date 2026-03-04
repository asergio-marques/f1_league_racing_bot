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
        }
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"
    finally:
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_run_migrations_idempotent() -> None:
    """Running migrations twice should not raise or duplicate entries."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        await run_migrations(db_path)
        await run_migrations(db_path)  # Second run — should be a no-op

        async with get_connection(db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM schema_migrations")
            (count,) = await cursor.fetchone()

        assert count == 1  # Only 001_initial.sql
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
