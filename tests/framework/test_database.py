"""Integration tests for database.py: the migration runner, each file applied whole with its keys
checked and its version recorded in the same save, and the connections it lends."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import sqlite3

import aiosqlite
import pytest

from leaguebot.core.db.database import get_connection, run_migrations, sole_row


def _stub_database(db_path: str) -> None:
    """Leave a small, valid, non-WAL database at *db_path*.

    `tests/conftest.py` swaps `run_migrations` for one that copies a prebuilt template
    whenever the target is absent or empty, and only delegates to the real migration runner
    when the target already holds something. A path under `tmp_path` holds nothing yet, so
    it takes the copy path — which means a test wanting to prove what the *real* runner does
    has to hand it a database that is already populated. This writes the smallest one that
    counts, without building the schema by hand.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("CREATE TABLE IF NOT EXISTS _stub (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_run_migrations_creates_tables(tmp_path) -> None:
    """run_migrations() should create the core tables, among the rest."""
    db_path = str(tmp_path / "bot.db")

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


@pytest.mark.asyncio
async def test_run_migrations_idempotent(tmp_path) -> None:
    """Running migrations twice should not raise or duplicate entries."""
    db_path = str(tmp_path / "bot.db")

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
    """#254 squashed the chain into one file, and until go-live a schema change edits it. What
    this test becomes at go-live is listed under "At go-live" in `run_migrations`' docstring."""
    from leaguebot.core.db import database

    files = sorted(
        f for f in os.listdir(database._MIGRATIONS_DIR)
        if f.endswith(".sql") and not f.startswith("__")
    )
    assert files == ["001_baseline.sql"], (
        "a migration beside the baseline: see \"At go-live\" in run_migrations' docstring"
    )


async def _migrated_with(tmp_path, monkeypatch, files: dict[str, str]) -> str:
    """A database holding the baseline, with the migrations pointed at a directory holding the
    baseline beside *files* (name to text), so the next `run_migrations` applies those alone.

    The target holds the baseline already, so `tests/conftest.py`'s substitute hands it to the
    real runner rather than copying its template over it.
    """
    from leaguebot.core.db import database

    db_path = str(tmp_path / "bot.db")
    await run_migrations(db_path)
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    shutil.copyfile(
        os.path.join(database._MIGRATIONS_DIR, "001_baseline.sql"), migrations / "001_baseline.sql"
    )
    for name, sql in files.items():
        (migrations / name).write_text(sql, encoding="utf-8")
    monkeypatch.setattr(database, "_MIGRATIONS_DIR", str(migrations))
    return db_path


def _tables_and_versions(db_path: str) -> tuple[set[str], set[str]]:
    """The test's own tables (named ``s4_...``) in *db_path*, and the versions it records."""
    db = sqlite3.connect(db_path)
    try:
        tables = {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name GLOB 's4_*'"
            )
        }
        versions = {row[0] for row in db.execute("SELECT version FROM schema_migrations")}
    finally:
        db.close()
    return tables, versions


_TWO_TABLES = [
    "CREATE TABLE s4_first (id INTEGER PRIMARY KEY);",
    "CREATE TABLE s4_second (id INTEGER PRIMARY KEY);",
    "INSERT INTO s4_first (id) VALUES (1);",
]
_FAILS = "INSERT INTO s4_nowhere (id) VALUES (1);"


@pytest.mark.parametrize(
    "failing",
    [
        pytest.param(0, id="at its first statement"),
        pytest.param(-1, id="at its last statement"),
    ],
)
async def test_a_migration_failing_part_way_leaves_nothing_and_no_record(
    tmp_path, monkeypatch, failing
) -> None:
    """A migration file is applied whole or not at all (architecture.md, "A database update is
    applied whole, or not at all"): a file failing at any statement leaves none of its tables and
    no record of itself, so the file corrected applies cleanly on the next start."""
    broken = list(_TWO_TABLES)
    broken[failing] = _FAILS
    db_path = await _migrated_with(tmp_path, monkeypatch, {"002_s4.sql": "\n".join(broken)})

    with pytest.raises(sqlite3.OperationalError, match="s4_nowhere"):
        await run_migrations(db_path)

    tables, versions = _tables_and_versions(db_path)
    assert tables == set()
    assert "002_s4.sql" not in versions

    (tmp_path / "migrations" / "002_s4.sql").write_text("\n".join(_TWO_TABLES), encoding="utf-8")
    await run_migrations(db_path)

    tables, versions = _tables_and_versions(db_path)
    assert tables == {"s4_first", "s4_second"}
    assert "002_s4.sql" in versions


async def test_a_migration_leaving_a_dangling_key_is_refused(tmp_path, monkeypatch) -> None:
    """A migration that leaves a row pointing at a parent that does not exist is refused by name,
    naming the table, and leaves nothing of itself applied or recorded. A file runs with the keys
    off, so the database itself takes the dangling insert; the runner's check before the commit
    is what refuses it."""
    from leaguebot.core.db.database import MigrationBreaksForeignKeys

    db_path = await _migrated_with(tmp_path, monkeypatch, {"002_s4.sql": """
        CREATE TABLE s4_parent (id INTEGER PRIMARY KEY);
        CREATE TABLE s4_child (
            id        INTEGER PRIMARY KEY,
            parent_id INTEGER NOT NULL REFERENCES s4_parent(id)
        );
        INSERT INTO s4_child (id, parent_id) VALUES (1, 7);
    """})

    with pytest.raises(MigrationBreaksForeignKeys, match="s4_child"):
        await run_migrations(db_path)

    tables, versions = _tables_and_versions(db_path)
    assert tables == set()
    assert "002_s4.sql" not in versions


async def test_rebuilding_a_parent_table_keeps_its_cascaded_children(tmp_path, monkeypatch) -> None:
    """A table rebuilt by SQLite's procedure (create the new table, copy the rows, drop the old,
    rename the new into place) keeps the rows of a table linked to it ``ON DELETE CASCADE``: the
    drop must not delete them."""
    db_path = await _migrated_with(tmp_path, monkeypatch, {"002_s4.sql": """
        CREATE TABLE s4_parent (id INTEGER PRIMARY KEY);
        CREATE TABLE s4_child (
            id        INTEGER PRIMARY KEY,
            parent_id INTEGER NOT NULL REFERENCES s4_parent(id) ON DELETE CASCADE
        );
        INSERT INTO s4_parent (id) VALUES (1), (2);
        INSERT INTO s4_child (id, parent_id) VALUES (10, 1), (11, 1), (20, 2);
    """})
    await run_migrations(db_path)
    (tmp_path / "migrations" / "003_s4_rebuild.sql").write_text("""
        CREATE TABLE s4_parent_new (id INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '');
        INSERT INTO s4_parent_new (id) SELECT id FROM s4_parent;
        DROP TABLE s4_parent;
        ALTER TABLE s4_parent_new RENAME TO s4_parent;
    """, encoding="utf-8")

    await run_migrations(db_path)

    db = sqlite3.connect(db_path)
    try:
        children = db.execute("SELECT id, parent_id FROM s4_child ORDER BY id").fetchall()
        parents = {row[2] for row in db.execute("PRAGMA foreign_key_list(s4_child)")}
    finally:
        db.close()
    assert children == [(10, 1), (11, 1), (20, 2)]
    assert parents == {"s4_parent"}
    assert "003_s4_rebuild.sql" in _tables_and_versions(db_path)[1]


@pytest.mark.asyncio
async def test_foreign_keys_enabled(tmp_path) -> None:
    """get_connection should enable PRAGMA foreign_keys."""
    db_path = str(tmp_path / "bot.db")

    async with get_connection(db_path) as db:
        cursor = await db.execute("PRAGMA foreign_keys")
        (fk,) = await cursor.fetchone()
    assert fk == 1


@pytest.mark.asyncio
async def test_the_baseline_seeds_every_track(tmp_path) -> None:
    """The schema seeds the 28 circuits a league can schedule."""
    db_path = str(tmp_path / "bot.db")

    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        # tracks table exists and has 28 seed rows
        cursor = await db.execute("SELECT COUNT(*) FROM tracks")
        (track_count,) = await cursor.fetchone()
        assert track_count == 28, f"Expected 28 track rows, got {track_count}"


# ── Journal mode, durability and the busy timeout ────────────────────
#
# Why these exist: under the default rollback journal a reader queues behind a writer, and
# on the Raspberry Pi's SD card that was measured at 2.45s for a read an autocomplete had
# three seconds to make. WAL drops the same read to 0.05s. See the plan for the numbers.


@pytest.mark.asyncio
async def test_migrations_leave_the_database_in_wal_mode(tmp_path) -> None:
    """The real migration runner puts the database into WAL and leaves it there."""
    db_path = str(tmp_path / "bot.db")

    # Populated first, so conftest's copy-a-template shortcut steps aside and the real
    # runner is what actually gets exercised.
    _stub_database(db_path)

    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        cursor = await db.execute("PRAGMA journal_mode")
        (mode,) = await cursor.fetchone()
    assert mode.lower() == "wal"


@pytest.mark.asyncio
async def test_the_schema_template_is_copied_in_wal_mode(tmp_path) -> None:
    """The copied template is in WAL too — which is what most fixtures actually get.

    Nearly every fixture in this suite hands `run_migrations` an empty path, so it receives
    a byte copy of a prebuilt template rather than a freshly migrated database. WAL lives in
    the file header, so it survives the copy, and a later, independent connection reads it
    without re-applying it; this pins that, because if it ever stopped being true the whole
    suite would quietly test the wrong journal mode.
    """
    db_path = str(tmp_path / "bot.db")

    await run_migrations(db_path)  # empty target -> template copy

    async with get_connection(db_path) as db:
        cursor = await db.execute("PRAGMA journal_mode")
        (mode,) = await cursor.fetchone()
    assert mode.lower() == "wal"


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
async def test_connections_carry_an_explicit_busy_timeout(tmp_path) -> None:
    """The lock wait is a named decision, not whatever sqlite3 happens to default to."""
    db_path = str(tmp_path / "bot.db")

    async with get_connection(db_path) as db:
        cursor = await db.execute("PRAGMA busy_timeout")
        (timeout_ms,) = await cursor.fetchone()
    assert timeout_ms == 5000


@pytest.mark.asyncio
async def test_a_shorter_busy_timeout_can_be_asked_for(tmp_path) -> None:
    """An autocomplete racing Discord's three seconds must be able to give up sooner."""
    db_path = str(tmp_path / "bot.db")

    async with get_connection(db_path, timeout=1.0) as db:
        cursor = await db.execute("PRAGMA busy_timeout")
        (timeout_ms,) = await cursor.fetchone()
    assert timeout_ms == 1000


@pytest.mark.asyncio
async def test_connections_keep_full_durability(monkeypatch, tmp_path) -> None:
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
    db_path = str(tmp_path / "bot.db")

    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        cursor = await db.execute("PRAGMA journal_mode")
        (mode,) = await cursor.fetchone()
        cursor = await db.execute("PRAGMA synchronous")
        (level,) = await cursor.fetchone()

    assert mode.lower() == "wal", "precondition: WAL is what removes the contention"
    assert level == 2, "expected FULL (2) — durability is preferred to commit latency"


# ── The suite's own databases (#256) ──────────────────────────────────
#
# The bot keeps FULL, above. The suite does not: `tests/conftest.py` opens every aiosqlite
# connection at `synchronous = OFF`, because the flushes FULL pays are what made a database
# test cost several times as much on Windows as on Linux. See the docstring there.

@pytest.mark.asyncio
async def test_the_suite_s_connections_skip_the_flush_to_disk(tmp_path) -> None:
    """A test's database is opened at OFF (0), and is still in WAL, on the bot's own kind of
    connection.

    OFF changes only what survives a power cut, which no test can observe. WAL changes which
    reads wait on which writes, which tests do, so the suite keeps the bot's journal mode. The
    bot's kind of connection records the cursors it closes before lending it again, so the
    suite keeps that too.
    """
    db_path = str(tmp_path / "suite.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        cursor = await db.execute("PRAGMA synchronous")
        (level,) = await cursor.fetchone()
        cursor = await db.execute("PRAGMA journal_mode")
        (mode,) = await cursor.fetchone()

        kinds = {kind.__module__ for kind in type(db._conn).__mro__}

    assert level == 0, "expected OFF (0) on the suite's own connections"
    assert mode.lower() == "wal", "the suite must still test the bot's journal mode"
    assert "leaguebot.core.db.database" in kinds, "the suite must keep the bot's connection"


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


# ── Lending connections again ─────────────────────────────────────────
#
# `get_connection` keeps a few connections idle once they are returned cleanly, and lends them
# to the next caller, which spares a thread and SQLite's re-read of the schema on every open.
# Most tests here switch the lending on through `lending` (`tests/conftest.py`), so that each
# holds whatever the cap is set to, and close what it left idle as they finish.


@pytest.mark.asyncio
async def test_migrations_hold_no_connection_open_afterwards(tmp_path, lending) -> None:
    """The migrations open a connection of their own and close it, whatever is lent.

    A factory reset migrates `bot.fresh.db` and then deletes it, which Windows refuses while
    anything holds the file open. The last connection to close checkpoints the write-ahead log
    and deletes it, so a `-wal` file left beside the database means a connection is still open.
    """
    db_path = str(tmp_path / "bot.db")
    _stub_database(db_path)  # populated, so the real runner migrates it

    await run_migrations(db_path)

    assert not os.path.exists(db_path + "-wal"), "a connection to the database is still open"


async def _a_database(tmp_path) -> str:
    """A migrated database under *tmp_path*, holding a table `t` of three rows."""
    db_path = str(tmp_path / "bot.db")
    await run_migrations(db_path)
    db = sqlite3.connect(db_path)
    try:
        db.execute("CREATE TABLE t (x INTEGER)")
        db.executemany("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
        db.commit()
    finally:
        db.close()
    return db_path


async def _refuses_queries(db: aiosqlite.Connection) -> bool:
    """Whether *db* has been closed, so that it runs nothing more."""
    try:
        await db.execute("SELECT 1")
    except ValueError:
        return True
    return False


@pytest.mark.asyncio
async def test_a_connection_returned_cleanly_is_lent_again(tmp_path, lending) -> None:
    """A block that ends with nothing pending gives its connection back, and the next caller
    for the same database gets that connection rather than a new one."""
    db_path = await _a_database(tmp_path)

    async with get_connection(db_path) as first:
        await (await first.execute("SELECT COUNT(*) FROM t")).fetchall()
    async with get_connection(db_path) as second:
        assert second is first


@pytest.mark.asyncio
async def test_two_connections_asked_for_at_once_are_two_connections(tmp_path, lending) -> None:
    """A caller asking while another holds a connection gets one of its own, at once: some code
    opens a second connection while its first is still open, as a service reading through
    another service inside its own block does, and a checkout that waited would never end."""
    db_path = await _a_database(tmp_path)

    async with get_connection(db_path) as outer:
        async with asyncio.timeout(5):
            async with get_connection(db_path) as inner:
                assert inner is not outer
                assert await (await inner.execute("SELECT COUNT(*) FROM t")).fetchall()


@pytest.mark.asyncio
async def test_a_connection_returned_mid_transaction_is_closed_not_lent(
    tmp_path, lending
) -> None:
    """A block that ends with a write uncommitted closes its connection, which rolls the write
    back, and the next caller gets a fresh connection that never sees it."""
    db_path = await _a_database(tmp_path)

    async with get_connection(db_path) as first:
        await first.execute("INSERT INTO t VALUES (4)")
        assert first.in_transaction
    async with get_connection(db_path) as second:
        assert second is not first
        (count,) = await sole_row(await second.execute("SELECT COUNT(*) FROM t"))
    assert count == 3
    assert await _refuses_queries(first)


@pytest.mark.asyncio
async def test_a_connection_left_by_an_exception_is_closed_not_lent(tmp_path, lending) -> None:
    """A block left by an exception closes its connection, whatever it had pending, and the
    next caller gets a fresh one."""
    db_path = await _a_database(tmp_path)

    with pytest.raises(RuntimeError, match="the step failed"):
        async with get_connection(db_path) as first:
            await (await first.execute("SELECT COUNT(*) FROM t")).fetchall()
            raise RuntimeError("the step failed")
    async with get_connection(db_path) as second:
        assert second is not first
    assert await _refuses_queries(first)


@pytest.mark.asyncio
async def test_a_confined_connection_is_never_lent_again(tmp_path, lending) -> None:
    """A connection that a conversation's save confined to its own tables is closed when the
    save returns it, so its refusal can never reach the next caller's writes."""
    from leaguebot.core.db.database import never_lend

    db_path = await _a_database(tmp_path)

    def refuse_every_write(action, *_args) -> int:
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    async with get_connection(db_path) as confined:
        await never_lend(confined, refuse_every_write)
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            await confined.execute("INSERT INTO t VALUES (4)")
    async with get_connection(db_path) as next_one:
        assert next_one is not confined
        await next_one.execute("INSERT INTO t VALUES (4)")
        await next_one.commit()
    assert await _refuses_queries(confined)


@pytest.mark.asyncio
async def test_a_lent_connection_has_its_foreign_keys_on_again(tmp_path, lending) -> None:
    """A caller that turned foreign keys off on its connection does not pass that on: the next
    caller gets the same connection with them on again."""
    db_path = await _a_database(tmp_path)

    async with get_connection(db_path) as first:
        await first.execute("PRAGMA foreign_keys = OFF")
    async with get_connection(db_path) as second:
        assert second is first
        (enforced,) = await sole_row(await second.execute("PRAGMA foreign_keys"))
    assert enforced == 1


@pytest.mark.asyncio
async def test_an_autocomplete_connection_is_never_lent_with_the_longer_timeout(
    tmp_path, lending
) -> None:
    """An autocomplete's connection, which gives up on a lock after one second, is lent only to
    another autocomplete; an ordinary caller never gets it, nor an autocomplete the ordinary
    caller's five seconds."""
    from leaguebot.core.db.database import AUTOCOMPLETE_TIMEOUT_SECONDS

    db_path = await _a_database(tmp_path)

    async with get_connection(db_path, timeout=AUTOCOMPLETE_TIMEOUT_SECONDS) as quick:
        pass
    async with get_connection(db_path) as ordinary:
        assert ordinary is not quick
        (ordinary_ms,) = await sole_row(await ordinary.execute("PRAGMA busy_timeout"))
    async with get_connection(db_path, timeout=AUTOCOMPLETE_TIMEOUT_SECONDS) as again:
        assert again is quick
        assert again is not ordinary
        (again_ms,) = await sole_row(await again.execute("PRAGMA busy_timeout"))
    assert (ordinary_ms, again_ms) == (5000, 1000)


@pytest.mark.asyncio
async def test_a_cursor_read_after_its_connection_is_returned_fails_as_before(
    tmp_path, lending
) -> None:
    """A cursor kept past its block cannot be read, even though its connection lives on: the
    cursors are closed as the connection is given back."""
    db_path = await _a_database(tmp_path)

    async with get_connection(db_path) as first:
        kept = await first.execute("SELECT x FROM t ORDER BY x")
    async with get_connection(db_path) as second:
        assert second is first
    with pytest.raises((ValueError, sqlite3.ProgrammingError)):
        await kept.fetchall()


@pytest.mark.asyncio
async def test_a_half_read_query_holds_no_snapshot_once_returned(tmp_path, lending) -> None:
    """A query read only in part, and left so, does not keep its view of the database into the
    next caller's work: the next caller sees a row written meanwhile."""
    db_path = await _a_database(tmp_path)

    async with get_connection(db_path) as first:
        half_read = await first.execute("SELECT x FROM t ORDER BY x")
        assert await half_read.fetchone() is not None
    writer = sqlite3.connect(db_path)
    try:
        writer.execute("INSERT INTO t VALUES (4)")
        writer.commit()
    finally:
        writer.close()
    async with get_connection(db_path) as second:
        assert second is first
        (count,) = await sole_row(await second.execute("SELECT COUNT(*) FROM t"))
    assert count == 4


@pytest.mark.asyncio
async def test_a_replaced_database_file_is_not_served_by_an_old_connection(
    tmp_path, lending, monkeypatch
) -> None:
    """A database file that has become another file between two callers is read afresh: the
    second caller gets a new connection, and the idle one, still holding the old file open, is
    closed.

    The swap is faked rather than made: from the second caller on, `os.stat` reports the file
    under another identity (another inode), as a file put in its place would carry. Windows
    will not replace a file a connection holds open, so a real swap could not run there, and
    the fake lets the test run on every host with no skip."""
    db_path = await _a_database(tmp_path)
    async with get_connection(db_path) as first:
        await (await first.execute("SELECT COUNT(*) FROM t")).fetchall()

    real_stat = os.stat
    replaced = os.path.abspath(db_path)

    def stat_as_replaced(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if not isinstance(path, int) and os.path.abspath(os.fspath(path)) == replaced:
            fields = list(result[:10])
            fields[1] = result.st_ino + 1
            return os.stat_result(fields)
        return result

    monkeypatch.setattr(os, "stat", stat_as_replaced)

    async with get_connection(db_path) as second:
        assert second is not first
        count = (await sole_row(await second.execute("SELECT COUNT(*) FROM t")))[0]
    assert count == 3
    assert await _refuses_queries(first)


@pytest.mark.asyncio
async def test_no_more_than_four_idle_connections_are_kept(tmp_path) -> None:
    """Six connections in use at once, all given back cleanly, leave four idle: six asked for
    again get those four and two new ones, and the other two were closed."""
    from leaguebot.core.db import database

    db_path = await _a_database(tmp_path)
    try:
        async with contextlib.AsyncExitStack() as stack:
            first = [await stack.enter_async_context(get_connection(db_path)) for _ in range(6)]
        async with contextlib.AsyncExitStack() as stack:
            second = [await stack.enter_async_context(get_connection(db_path)) for _ in range(6)]
        lent = [db for db in first if any(db is again for again in second)]
        not_kept = [db for db in first if not any(db is again for again in second)]
        assert len(lent) == 4
        assert [await _refuses_queries(db) for db in not_kept] == [True, True]
    finally:
        await database.close_connections()


@pytest.mark.asyncio
async def test_closing_the_connections_ends_every_idle_thread(tmp_path, lending) -> None:
    """Closing the connections, as the bot does when it stops, ends the thread of every idle
    one, so none keeps the process alive; the next caller gets a fresh connection."""
    from leaguebot.core.db.database import close_connections

    db_path = await _a_database(tmp_path)
    async with get_connection(db_path) as one:
        async with get_connection(db_path) as two:
            pass

    await close_connections()

    for idle in (one, two):
        idle._thread.join(timeout=5)
        assert not idle._thread.is_alive()
        assert await _refuses_queries(idle)
    async with get_connection(db_path) as fresh:
        assert fresh is not one and fresh is not two


@pytest.mark.asyncio
async def test_a_connection_given_back_while_the_connections_close_is_closed_too(
    tmp_path, lending
) -> None:
    """A block that ends cleanly while the connections are being closed, as the bot stops, has its
    connection closed too: nothing is left idle afterwards, and its thread has ended, so it cannot
    keep the process alive.

    One caller holds a connection while another is given back and left idle. The connections are
    then closed, and the idle one's close is held until the first caller has given its own back,
    so that the return certainly falls while the closing is under way."""
    from leaguebot.core.db.database import close_connections

    db_path = await _a_database(tmp_path)
    taken = asyncio.Event()
    release = asyncio.Event()

    async def hold() -> aiosqlite.Connection:
        async with get_connection(db_path) as held:
            taken.set()
            await release.wait()
        return held

    holder = asyncio.create_task(hold())
    await taken.wait()
    async with get_connection(db_path) as idle_one:
        pass
    real_close = idle_one.close

    async def close_once_the_other_is_back() -> None:
        release.set()
        await asyncio.wait([holder])
        await real_close()

    idle_one.close = close_once_the_other_is_back
    async with asyncio.timeout(10):
        await close_connections()
    returned = await holder

    assert await _refuses_queries(returned), "the connection given back meanwhile was kept idle"
    returned._thread.join(timeout=5)
    assert not returned._thread.is_alive()
    assert await _refuses_queries(idle_one)


@pytest.mark.asyncio
async def test_a_connection_whose_return_is_cancelled_is_closed(
    tmp_path, lending, monkeypatch
) -> None:
    """A block whose task is cancelled while its connection is being checked on its way back, as a
    Ctrl-C does to the work under way, has that connection closed rather than left open with its
    thread alive.

    The check that a returned connection holds nothing pending is made to wait, and the task is
    cancelled while it waits."""
    from leaguebot.core.db import database

    db_path = await _a_database(tmp_path)
    checking = asyncio.Event()

    async def check_that_waits(db: aiosqlite.Connection) -> bool:
        checking.set()
        await asyncio.Event().wait()
        return True

    monkeypatch.setattr(database, "_is_clean", check_that_waits)
    used: list[aiosqlite.Connection] = []

    async def use() -> None:
        async with get_connection(db_path) as db:
            used.append(db)

    task = asyncio.create_task(use())
    try:
        await checking.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        (cancelled,) = used
        assert await _refuses_queries(cancelled), "the connection was left open"
        cancelled._thread.join(timeout=5)
        assert not cancelled._thread.is_alive()
    finally:
        # Left open, its thread, which is not a daemon, would keep the test run from ending.
        for db in used:
            await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("closed_by", ["a replaced file", "closing the connections"])
async def test_a_cancelled_close_of_the_idle_connections_still_closes_the_rest(
    tmp_path, lending, monkeypatch, closed_by
) -> None:
    """Two idle connections being closed together, because their database file has been replaced
    or because the connections are being closed as the bot stops, are both closed even where the
    work closing them is cancelled while the first close is under way, as a Ctrl-C does: the
    cancellation reaches the caller, every one of them runs nothing more and has its thread
    ended, and none is left idle.

    The first close is made to wait, and the task closing them is cancelled while it waits. The
    file's replacement is faked as in the replaced-file test, by `os.stat` reporting another
    inode."""
    from leaguebot.core.db import database

    db_path = await _a_database(tmp_path)
    async with get_connection(db_path) as one:
        async with get_connection(db_path) as two:
            pass
    idle = [one, two]

    closing = asyncio.Event()
    closes_begun = 0

    def close_that_waits_first(db: aiosqlite.Connection):
        real_close = db.close

        async def close() -> None:
            nonlocal closes_begun
            closes_begun += 1
            if closes_begun == 1:
                closing.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    await real_close()
            else:
                await real_close()

        return close

    for db in idle:
        db.close = close_that_waits_first(db)

    if closed_by == "a replaced file":
        real_stat = os.stat
        replaced = os.path.abspath(db_path)

        def stat_as_replaced(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            if not isinstance(path, int) and os.path.abspath(os.fspath(path)) == replaced:
                fields = list(result[:10])
                fields[1] = result.st_ino + 1
                return os.stat_result(fields)
            return result

        monkeypatch.setattr(os, "stat", stat_as_replaced)

        async def work() -> None:
            async with get_connection(db_path):
                pass

    else:

        async def work() -> None:
            await database.close_connections()

    task = asyncio.create_task(work())
    try:
        await closing.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        for db in idle:
            assert await _refuses_queries(db), "a connection was left open"
            db._thread.join(timeout=5)
            assert not db._thread.is_alive()
        assert not any(database._idle.values())
    finally:
        # Left open, a thread, which is not a daemon, would keep the test run from ending.
        for db in idle:
            del db.close
            await db.close()
