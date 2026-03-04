"""Async SQLite connection management and schema migration runner."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

import aiosqlite

log = logging.getLogger(__name__)

_MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "migrations")


@asynccontextmanager
async def get_connection(db_path: str) -> AsyncIterator[aiosqlite.Connection]:
    """Yield an aiosqlite connection with foreign-key enforcement enabled."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        yield db


async def run_migrations(db_path: str) -> None:
    """Apply all pending SQL migration files in order."""
    async with get_connection(db_path) as db:
        # Ensure the tracking table exists first
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        await db.commit()

        cursor = await db.execute("SELECT version FROM schema_migrations")
        applied = {row[0] for row in await cursor.fetchall()}

        # Collect and sort migration files
        migration_files = sorted(
            f for f in os.listdir(_MIGRATIONS_DIR)
            if f.endswith(".sql") and not f.startswith("__")
        )

        for filename in migration_files:
            version = filename  # e.g. "001_initial.sql"
            if version in applied:
                continue

            filepath = os.path.join(_MIGRATIONS_DIR, filename)
            with open(filepath, encoding="utf-8") as fh:
                sql = fh.read()

            log.info("Applying migration: %s", filename)
            await db.executescript(sql)
            await db.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
            log.info("Migration applied: %s", filename)
