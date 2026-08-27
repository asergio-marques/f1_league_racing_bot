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

#: How long SQLite waits for a lock before giving up, in seconds.
#:
#: Sized for the longest *legitimate* write, not for Discord. Several commands hold the
#: write lock for the length of a whole transaction — `replace_setup_season_snapshot` and
#: the factory reset run to well over a hundred lines each — and a timeout shorter than
#: those would turn a slow admin command into a failed one, which is a worse fault than
#: the one being fixed. Callers that genuinely cannot wait ask for less; see
#: `AUTOCOMPLETE_TIMEOUT_SECONDS`.
#:
#: This is passed to `aiosqlite.connect` rather than issued as a PRAGMA: it is a
#: connect-time argument, so it costs no round trip. Left unset, `sqlite3` defaults it to
#: 5.0 anyway — naming it here makes the value a decision rather than an accident.
_BUSY_TIMEOUT_SECONDS = 5.0

#: The busy timeout for a Discord autocomplete callback.
#:
#: Discord gives autocomplete three seconds and provides no way to defer, so a reader that
#: waited the full `_BUSY_TIMEOUT_SECONDS` would answer into an interaction token that had
#: already expired. Failing fast and offering no choices is the better outcome: the manager
#: types another character and gets a fresh interaction.
AUTOCOMPLETE_TIMEOUT_SECONDS = 1.0


@asynccontextmanager
async def get_connection(
    db_path: str, *, timeout: float | None = None
) -> AsyncIterator[aiosqlite.Connection]:
    """Yield an aiosqlite connection with foreign-key enforcement enabled.

    *timeout* overrides how long SQLite waits for a lock. Omit it for
    `_BUSY_TIMEOUT_SECONDS`; pass `AUTOCOMPLETE_TIMEOUT_SECONDS` on a path that is racing
    Discord's three-second budget.
    """
    async with aiosqlite.connect(
        db_path, timeout=_BUSY_TIMEOUT_SECONDS if timeout is None else timeout
    ) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        yield db


async def _enable_wal(db: aiosqlite.Connection) -> None:
    """Put the database into WAL, and say so once.

    WAL is the fix for readers queueing behind writers: under the default rollback journal
    a read can wait seconds for a write to finish, which overruns Discord's three-second
    autocomplete budget outright. The mode is written into the file header and persists, so
    this runs once at migration time and every later connection inherits it.

    `synchronous` is deliberately left at its default of FULL. WAL would make
    `synchronous=NORMAL` safe against corruption and it is markedly faster — 0.23 ms a
    commit against 6.21 ms on this host's SD card — but it leaves a window in which a sudden
    power cut loses the most recently committed transactions. A league's results are worth
    more than the milliseconds, so the slower, fully durable setting is kept on purpose.
    Do not "optimise" this without deciding that trade again.

    A database that will not take WAL is not a reason to refuse to start. `:memory:` reports
    `memory`, a read-only mount keeps its existing mode, and either way the bot works — just
    without the contention relief. Warn, naming what came back, and carry on.
    """
    # PRAGMA journal_mode cannot run inside a transaction, and the caller may have left one
    # open implicitly.
    await db.commit()
    cursor = await db.execute("PRAGMA journal_mode=WAL")
    row = await cursor.fetchone()
    mode = str(row[0]).lower() if row else "unknown"
    if mode != "wal":
        log.warning(
            "Could not enable WAL on this database — it reports journal_mode=%s. The bot "
            "will run, but reads may queue behind writes.",
            mode,
        )
        return
    log.info("Database journal mode: WAL")


async def run_migrations(db_path: str) -> None:
    """Apply all pending SQL migration files in order."""
    async with get_connection(db_path) as db:
        await _enable_wal(db)
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
