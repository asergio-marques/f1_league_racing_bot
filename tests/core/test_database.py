"""Integration tests for database.py — migration runner and connection helper."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
import tempfile

import aiosqlite
import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations


def _remove_database(db_path: str) -> None:
    """Delete a database and the sidecars WAL leaves beside it.

    A clean close checkpoints `-wal` and `-shm` away, so usually there is nothing to
    remove — but an ungraceful teardown does leave them, and on Windows a leftover sidecar
    is not merely litter: the suite runs on `windows-latest`, where an open file cannot be
    deleted and a stray sidecar turns into a failure that never reproduces on Linux.
    """
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.unlink(db_path + suffix)
        except OSError:
            pass


def _stub_database(db_path: str) -> None:
    """Leave a small, valid, non-WAL database at *db_path*.

    `tests/conftest.py` swaps `run_migrations` for one that copies a prebuilt template
    whenever the target is absent or empty, and only delegates to the real migration runner
    when the target already holds something. A `NamedTemporaryFile` is empty, so it takes
    the copy path — which means a test wanting to prove what the *real* runner does has to
    hand it a database that is already populated. This writes the smallest one that counts,
    without building the schema by hand.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("CREATE TABLE IF NOT EXISTS _stub (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_run_migrations_creates_tables() -> None:
    """run_migrations() should create the core tables, among the rest."""
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
        }
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"
    finally:
        _remove_database(db_path)


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
        _remove_database(db_path)


@pytest.mark.asyncio
async def test_a_database_from_before_the_baseline_is_refused_untouched(tmp_path) -> None:
    """#254. A database the 61-file chain built records `001_initial.sql` and the rest;
    applying the baseline over it would fail part-way. It is refused before anything is
    written, the journal mode included, and names what it found."""
    from leaguebot.core.db.database import DatabasePredatesBaselineError

    db_path = str(tmp_path / "old.db")
    db = sqlite3.connect(db_path)
    db.executescript(
        """
        CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
        INSERT INTO schema_migrations VALUES ('001_initial.sql', '2026-04-21');
        CREATE TABLE server_configs (server_id INTEGER PRIMARY KEY);
        """
    )
    db.commit()
    before = db.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    mode = db.execute("PRAGMA journal_mode").fetchone()
    db.close()

    with pytest.raises(DatabasePredatesBaselineError, match="001_initial.sql"):
        await run_migrations(db_path)

    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall() == before
        assert db.execute("PRAGMA journal_mode").fetchone() == mode
    finally:
        db.close()


def test_the_baseline_is_the_only_migration() -> None:
    """#254 squashed the chain into one file. Until go-live a schema change edits it; from
    go-live on this test is updated to admit each new, numbered migration after it."""
    from leaguebot.core.db import database

    files = sorted(
        f for f in os.listdir(database._MIGRATIONS_DIR)
        if f.endswith(".sql") and not f.startswith("__")
    )
    assert files == ["001_baseline.sql"]


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
        _remove_database(db_path)


@pytest.mark.asyncio
async def test_the_baseline_seeds_every_track() -> None:
    """The schema seeds the 28 circuits a league can schedule."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        await run_migrations(db_path)

        async with get_connection(db_path) as db:
            # tracks table exists and has 28 seed rows
            cursor = await db.execute("SELECT COUNT(*) FROM tracks")
            (track_count,) = await cursor.fetchone()
            assert track_count == 28, f"Expected 28 track rows, got {track_count}"
    finally:
        _remove_database(db_path)


# ── Journal mode, durability and the busy timeout ────────────────────
#
# Why these exist: under the default rollback journal a reader queues behind a writer, and
# on the Raspberry Pi's SD card that was measured at 2.45s for a read an autocomplete had
# three seconds to make. WAL drops the same read to 0.05s. See the plan for the numbers.


@pytest.mark.asyncio
async def test_migrations_leave_the_database_in_wal_mode() -> None:
    """The real migration runner puts the database into WAL and leaves it there."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        # Populated first, so conftest's copy-a-template shortcut steps aside and the real
        # runner is what actually gets exercised.
        _stub_database(db_path)

        await run_migrations(db_path)

        async with get_connection(db_path) as db:
            cursor = await db.execute("PRAGMA journal_mode")
            (mode,) = await cursor.fetchone()
        assert mode.lower() == "wal"
    finally:
        _remove_database(db_path)


@pytest.mark.asyncio
async def test_the_schema_template_is_copied_in_wal_mode() -> None:
    """The copied template is in WAL too — which is what most fixtures actually get.

    Nearly every fixture in this suite hands `run_migrations` an empty path, so it receives
    a byte copy of a prebuilt template rather than a freshly migrated database. WAL lives in
    the file header, so it survives the copy; this pins that, because if it ever stopped
    being true the whole suite would quietly test the wrong journal mode.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        await run_migrations(db_path)  # empty target -> template copy

        async with get_connection(db_path) as db:
            cursor = await db.execute("PRAGMA journal_mode")
            (mode,) = await cursor.fetchone()
        assert mode.lower() == "wal"
    finally:
        _remove_database(db_path)


@pytest.mark.asyncio
async def test_a_later_connection_inherits_wal_from_the_file() -> None:
    """WAL is a property of the file, so nothing has to re-apply it per connection."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        await run_migrations(db_path)

        # A second, entirely independent connection, setting nothing itself.
        async with get_connection(db_path) as db:
            cursor = await db.execute("PRAGMA journal_mode")
            (mode,) = await cursor.fetchone()
        assert mode.lower() == "wal"
    finally:
        _remove_database(db_path)


@pytest.mark.asyncio
async def test_migrations_survive_a_database_that_cannot_take_wal(caplog) -> None:
    """A database that will not take WAL warns and carries on, rather than refusing to start.

    An in-memory database reports `memory` and can never be WAL. That is not a reason to
    fail startup — the bot works without the contention relief, just more slowly.
    """
    with caplog.at_level(logging.WARNING, logger="leaguebot.core.db.database"):
        await run_migrations(":memory:")  # must not raise

    assert any(
        "could not enable wal" in record.message.lower() for record in caplog.records
    ), f"expected a WAL warning, got: {[r.message for r in caplog.records]}"


@pytest.mark.asyncio
async def test_connections_carry_an_explicit_busy_timeout() -> None:
    """The lock wait is a named decision, not whatever sqlite3 happens to default to."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        async with get_connection(db_path) as db:
            cursor = await db.execute("PRAGMA busy_timeout")
            (timeout_ms,) = await cursor.fetchone()
        assert timeout_ms == 5000
    finally:
        _remove_database(db_path)


@pytest.mark.asyncio
async def test_a_shorter_busy_timeout_can_be_asked_for() -> None:
    """An autocomplete racing Discord's three seconds must be able to give up sooner."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        async with get_connection(db_path, timeout=1.0) as db:
            cursor = await db.execute("PRAGMA busy_timeout")
            (timeout_ms,) = await cursor.fetchone()
        assert timeout_ms == 1000
    finally:
        _remove_database(db_path)


@pytest.mark.asyncio
async def test_connections_keep_full_durability(monkeypatch) -> None:
    """`synchronous` stays at FULL (2), and that is a decision rather than an oversight.

    WAL would make `synchronous=NORMAL` safe against corruption, and it is dramatically
    faster on this host's SD card — 0.23 ms a commit against 6.21 ms. It was measured,
    considered and **declined** on 2026-08-27: NORMAL leaves a window in which a sudden
    power cut loses the most recently committed transactions, and a league's results are
    worth more than the milliseconds.

    This test exists so that trade is re-decided deliberately rather than tuned away.

    **It opens the connection the way the bot does, not the way the suite does.**
    `tests/conftest.py` turns the flush off on every aiosqlite connection the suite opens
    (#256), so `get_connection` is run here against aiosqlite's own `connect` — which is
    what production calls — to see what the bot itself asks for.
    """
    monkeypatch.setattr(aiosqlite, "connect", aiosqlite.core.connect)
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        await run_migrations(db_path)

        async with get_connection(db_path) as db:
            cursor = await db.execute("PRAGMA journal_mode")
            (mode,) = await cursor.fetchone()
            cursor = await db.execute("PRAGMA synchronous")
            (level,) = await cursor.fetchone()

        assert mode.lower() == "wal", "precondition: WAL is what removes the contention"
        assert level == 2, "expected FULL (2) — durability is preferred to commit latency"
    finally:
        _remove_database(db_path)


# ── The suite's own databases (#256) ──────────────────────────────────
#
# The bot keeps FULL, above. The suite does not: `tests/conftest.py` opens every aiosqlite
# connection at `synchronous = OFF`, because the flushes FULL pays are what made a database
# test cost several times as much on Windows as on Linux. See the docstring there.

@pytest.mark.asyncio
async def test_the_suite_s_connections_skip_the_flush_to_disk(tmp_path) -> None:
    """A test's database is opened at OFF (0), and is still in WAL.

    OFF changes only what survives a power cut, which no test can observe. WAL changes which
    reads wait on which writes, which tests do, so the suite keeps the bot's journal mode.
    """
    db_path = str(tmp_path / "suite.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        cursor = await db.execute("PRAGMA synchronous")
        (level,) = await cursor.fetchone()
        cursor = await db.execute("PRAGMA journal_mode")
        (mode,) = await cursor.fetchone()

    assert level == 0, "expected OFF (0) on the suite's own connections"
    assert mode.lower() == "wal", "the suite must still test the bot's journal mode"


@pytest.mark.asyncio
async def test_a_seed_through_aiosqlite_skips_the_flush_too(tmp_path) -> None:
    """A fixture that seeds through `aiosqlite.connect` directly, not `get_connection`, is
    covered as well: some twenty test files do, each with a connection that writes."""
    db_path = str(tmp_path / "seed.db")
    await run_migrations(db_path)

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("PRAGMA synchronous")
        (level,) = await cursor.fetchone()

    assert level == 0


@pytest.mark.asyncio
async def test_a_caller_s_own_factory_is_left_alone(tmp_path) -> None:
    """A connection asked for with a factory of its own gets that factory, and with it
    SQLite's own default of FULL — the suite's setting steps aside rather than overriding."""
    opened = []

    class OwnConnection(sqlite3.Connection):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            opened.append(self)

    async with aiosqlite.connect(str(tmp_path / "own.db"), factory=OwnConnection) as db:
        cursor = await db.execute("PRAGMA synchronous")
        (level,) = await cursor.fetchone()

    assert len(opened) == 1
    assert level == 2
