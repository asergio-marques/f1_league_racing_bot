"""Async SQLite connection management and schema migration runner."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import weakref
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Iterable, Sequence, TypeVar, overload

import aiosqlite

log = logging.getLogger(__name__)

_MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "migrations")

#: How long SQLite waits for a lock before giving up, in seconds.
#:
#: Sized for the longest *legitimate* write, not for Discord. Several commands hold the
#: write lock for the length of a whole transaction — the factory reset among them — and a
#: timeout shorter than the longest of those would turn a slow admin command into a failed one,
#: which is a worse fault than the one being fixed. Callers that genuinely cannot wait ask for
#: less; see `AUTOCOMPLETE_TIMEOUT_SECONDS`.
#:
#: This is passed to `aiosqlite.connect` rather than issued as a PRAGMA: it is a
#: connect-time argument, so it costs no round trip. Left unset, `sqlite3` defaults it to
#: 5.0 anyway — naming it here makes the value a decision rather than an accident. It is part
#: of the key connections are lent under, so a connection opened with another timeout is
#: never lent to a caller that asked for this one.
_BUSY_TIMEOUT_SECONDS = 5.0

#: The busy timeout for a Discord autocomplete callback.
#:
#: Discord gives autocomplete three seconds and provides no way to defer, so a reader that
#: waited the full `_BUSY_TIMEOUT_SECONDS` would answer into an interaction token that had
#: already expired. Failing fast and offering no choices is the better outcome: the manager
#: types another character and gets a fresh interaction.
AUTOCOMPLETE_TIMEOUT_SECONDS = 1.0


#: How many idle connections to one database (and one timeout) are kept to be lent again.
#:
#: Pinned by `test_no_more_than_four_idle_connections_are_kept`. Four covers the most that run
#: at once in practice (a step's save, a service it reads through inside that block, and a reader
#: or two); one asking beyond that opens its own and closes it on return. Zero switches lending
#: off. Lending is kept for what it saves: the change-queue tests of
#: `test_season_approval_change.py`, in one process on the Pi, take 55 s with four kept against
#: 161 s with none, the schema read by every fresh connection being most of the difference.
_IDLE_CONNECTIONS_KEPT = 4

#: Who has called ``never_lend``: connections that are closed on return, whatever else is true.
_NEVER_LENT: weakref.WeakSet[aiosqlite.Connection] = weakref.WeakSet()

#: What is told about a file to know it is the file a connection opened: its device and inode.
_Identity = tuple[int, int]

#: The idle connections, by the database path and the busy timeout they were opened with, each
#: with the identity of the file it opened. ``_idle_lock`` guards it, since a change run on a
#: worker thread (`season_review_changes`) can ask for a connection from a loop of its own.
#:
#: Module state rather than a pool object handed to every service: lending sits behind
#: `get_connection`, so every caller still passes just a path, and no pool has to be built at
#: start-up and handed to each plain-function service (architecture.md, "Services stay attached
#: to the bot"). It is empty at import and nothing is opened then.
_idle: dict[tuple[str, float], list[tuple[aiosqlite.Connection, _Identity]]] = {}
_idle_lock = threading.Lock()

#: How many calls of `close_connections` are under way. While any is, a connection given back is
#: closed rather than kept idle, so one returned in the middle of the bot stopping cannot be left
#: open with a thread that would keep the process alive. ``_idle_lock`` guards it.
_closing = 0

#: The signature of an SQLite authoriser callback.
Authoriser = Callable[[int, "str | None", "str | None", "str | None", "str | None"], int]


_CursorT = TypeVar("_CursorT", bound=sqlite3.Cursor)


class _Connection(sqlite3.Connection):
    """The bot's kind of connection: one that knows every cursor it has made.

    Closing the cursors is what gives a lent connection back clean. A cursor read only in part
    keeps its read snapshot open, and the next caller, lent the same connection, would see the
    database as it was when that cursor ran (`test_a_half_read_query_holds_no_snapshot_once_returned`).
    ``execute`` and ``executemany`` make their cursors without calling ``cursor`` (CPython does
    not), so each is overridden to record its own. ``executescript`` is not: it runs a whole script
    and leaves its cursor with nothing pending, and only the migrations use it.
    The cursors are held weakly: one nobody keeps is finished already.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._cursors: weakref.WeakSet[sqlite3.Cursor] = weakref.WeakSet()
        super().__init__(*args, **kwargs)

    @overload
    def cursor(self, factory: None = None) -> sqlite3.Cursor: ...

    @overload
    def cursor(self, factory: Callable[[sqlite3.Connection], _CursorT]) -> _CursorT: ...

    def cursor(self, factory: Callable[[sqlite3.Connection], sqlite3.Cursor] | None = None) -> sqlite3.Cursor:
        return self._recorded(super().cursor() if factory is None else super().cursor(factory))

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        return self._recorded(super().execute(sql, parameters))

    def executemany(self, sql: str, parameters: Iterable[Any], /) -> sqlite3.Cursor:
        return self._recorded(super().executemany(sql, parameters))

    def _recorded(self, cursor: sqlite3.Cursor) -> sqlite3.Cursor:
        self._cursors.add(cursor)
        return cursor

    def close_cursors(self) -> None:
        """Close every cursor made on this connection; runs on the connection's own thread."""
        for cursor in list(self._cursors):
            cursor.close()
        self._cursors.clear()


def _file_identity(db_path: str) -> _Identity | None:
    """The device and inode of the file at *db_path*, read afresh, or None where there is none
    (``:memory:``, a path not yet made, a file taken away)."""
    try:
        status = os.stat(db_path)
    except OSError:
        return None
    return (status.st_dev, status.st_ino)


@asynccontextmanager
async def get_connection(
    db_path: str, *, timeout: float | None = None
) -> AsyncIterator[aiosqlite.Connection]:
    """Yield an aiosqlite connection with foreign-key enforcement enabled.

    *timeout* overrides how long SQLite waits for a lock. Omit it for
    `_BUSY_TIMEOUT_SECONDS`; pass `AUTOCOMPLETE_TIMEOUT_SECONDS` on a path that is racing
    Discord's three-second budget.

    **Connections are lent again** rather than opened for every operation. A new connection
    starts a thread and makes SQLite read the whole schema before its first statement, and a
    change opens a great many. A connection given back cleanly is kept idle, and the next caller
    for the same database and timeout is lent it. Nothing about the call changes: the block has
    one connection to itself, a step's save has the one handed down to it, and nothing is
    handed back up.

    **Checking out never waits.** It takes an idle connection or opens one, and a caller never
    queues for another to finish: some code opens a second connection while its first is still
    open (a service reading through another service inside its own block), so two callers at once
    always get two connections
    (`test_two_connections_asked_for_at_once_are_two_connections`). That is why the pool is not
    capped by waiting. Before a connection is reused the database file's identity (device and
    inode) is read afresh with ``os.stat`` on the path and compared with the one the connection
    opened; a file put in its place empties that database's idle connections
    (`test_a_replaced_database_file_is_not_served_by_an_old_connection`). The row factory is set
    and ``PRAGMA foreign_keys = ON`` run on every checkout, so what a caller does to either does not
    pass on (`test_a_lent_connection_has_its_foreign_keys_on_again`). The timeout is part of the
    key, so an autocomplete's connection, which gives up on a lock after a second, is lent only to
    another autocomplete (`test_an_autocomplete_connection_is_never_lent_with_the_longer_timeout`).

    **A connection is kept only when it is clean:** the block ended without an exception (a
    cancellation counts as one), nothing is pending (``in_transaction`` false), the isolation level
    is the default, nobody has called `never_lend` on it, and fewer than `_IDLE_CONNECTIONS_KEPT`
    are kept for its key already. Any other is closed, which rolls back what it held
    (`test_a_connection_returned_mid_transaction_is_closed_not_lent`,
    `test_a_connection_left_by_an_exception_is_closed_not_lent`,
    `test_no_more_than_four_idle_connections_are_kept`).

    **Cursors are closed on return,** in one hop on the connection's thread, through aiosqlite's
    private ``_execute`` and the private ``_conn`` it reads the connection from, which the pin on
    aiosqlite holds in place. Most of the bot's queries are a
    bare ``cursor = await db.execute(...)`` that is never closed, and a half-read one would keep its
    read snapshot into the next caller's work
    (`test_a_half_read_query_holds_no_snapshot_once_returned`). A cursor read after the block fails,
    as it did when the connection closed (`test_a_cursor_read_after_its_connection_is_returned_fails_as_before`).

    The entry point calls `close_connections` as the bot stops: each idle connection holds a thread
    that is not a daemon.
    """
    effective = _BUSY_TIMEOUT_SECONDS if timeout is None else timeout
    key = (db_path, effective)
    db, identity = await _check_out(key)
    lend = False
    try:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        yield db
        lend = True
    finally:
        await _check_in(key, db, identity, lend)


async def _check_out(key: tuple[str, float]) -> tuple[aiosqlite.Connection, _Identity | None]:
    """An idle connection for *key* that still opens the file now there, or a new one, and the
    identity of the file it opened."""
    db_path, timeout = key
    with _idle_lock:
        waiting = bool(_idle.get(key))
    if waiting:
        current = _file_identity(db_path)
        stale: list[aiosqlite.Connection] = []
        found: tuple[aiosqlite.Connection, _Identity] | None = None
        with _idle_lock:
            idle = _idle.get(key)
            if idle:
                if idle[-1][1] == current:
                    found = idle.pop()
                else:
                    stale = [db for db, _ in idle]
                    idle.clear()
        await _close_all(stale)
        if found is not None:
            return found
    db = await aiosqlite.connect(db_path, timeout=timeout, factory=_Connection)
    return db, _file_identity(db_path)


async def _check_in(
    key: tuple[str, float], db: aiosqlite.Connection, identity: _Identity | None, lend: bool
) -> None:
    """Keep *db* idle if it was returned clean and there is room, else close it.

    A cancellation that arrives while *db* is being checked closes it too, and is raised again: a
    connection neither kept nor closed would keep its thread, which is not a daemon, alive
    (`test_a_connection_whose_return_is_cancelled_is_closed`). One returned while the connections
    are being closed is closed, not kept
    (`test_a_connection_given_back_while_the_connections_close_is_closed_too`).
    """
    if lend and identity is not None and _IDLE_CONNECTIONS_KEPT > 0 and db not in _NEVER_LENT:
        try:
            clean = await _is_clean(db)
        except BaseException:
            await db.close()
            raise
        if clean:
            with _idle_lock:
                idle = _idle.setdefault(key, [])
                if not _closing and len(idle) < _IDLE_CONNECTIONS_KEPT:
                    idle.append((db, identity))
                    return
    await db.close()


async def _is_clean(db: aiosqlite.Connection) -> bool:
    """Whether *db*, its cursors closed, holds nothing for the next caller to inherit."""
    connection = db._conn
    if not isinstance(connection, _Connection):
        return False
    try:
        await db._execute(connection.close_cursors)
    except (sqlite3.Error, ValueError):
        return False
    return not db.in_transaction and db.isolation_level == ""


async def _close_all(connections: list[aiosqlite.Connection]) -> None:
    """Close each of *connections*, one by one: a failure to close one is logged and does not
    keep the rest open."""
    for index, db in enumerate(connections):
        try:
            await db.close()
        except Exception:
            log.exception("A database connection would not close.")
        except BaseException:
            await _close_all(connections[index + 1 :])
            raise


async def close_connections(db_path: str | None = None) -> None:
    """Close the idle connections, of the database at *db_path* or of all, and end their threads.

    Called by the entry point as the bot stops, and by a caller about to replace the database
    file. A connection in use is not touched: it is closed as its block ends, never kept, if that
    is while this is under way (`test_a_connection_given_back_while_the_connections_close_is_closed_too`).
    """
    global _closing
    with _idle_lock:
        _closing += 1
        taken = [
            db
            for key in [k for k in _idle if db_path is None or k[0] == db_path]
            for db, _ in _idle.pop(key)
        ]
    try:
        await _close_all(taken)
    finally:
        with _idle_lock:
            _closing -= 1


async def never_lend(db: aiosqlite.Connection, authoriser: Authoriser) -> None:
    """Set *authoriser* on *db* and mark it never to be lent again, so it is closed when its block
    ends. The authoriser would otherwise be lent on with the connection and refuse the next
    caller's writes (`test_a_confined_connection_is_never_lent_again`). Outside this module, setting
    an authoriser on a connection is refused by a rule test."""
    _NEVER_LENT.add(db)
    await db.set_authorizer(authoriser)


@asynccontextmanager
async def _migration_connection(db_path: str) -> AsyncIterator[aiosqlite.Connection]:
    """A connection for `run_migrations` alone, opened and closed here and never lent.

    The migrations get a connection of their own, outside whatever `get_connection` keeps, so
    that when `run_migrations` returns nothing holds the database open. The factory reset
    migrates a fresh copy and then deletes it (`factory_reset_service.wipe`), and the last
    connection to close is the one that checkpoints the write-ahead log and removes it
    (`test_migrations_hold_no_connection_open_afterwards`).

    Its foreign keys are left off, which `run_migrations` states in its docstring.
    """
    async with aiosqlite.connect(db_path, timeout=_BUSY_TIMEOUT_SECONDS) as db:
        db.row_factory = aiosqlite.Row
        yield db


async def sole_row(cursor: aiosqlite.Cursor) -> aiosqlite.Row:
    """The one row a query certain to return one has returned.

    For an aggregate — ``COUNT``, ``MAX`` — which always returns a row, and for a lookup by a
    key the caller has just taken from the database, or just written. aiosqlite types every
    ``fetchone`` as optional; a None here means that certainty was wrong, and it is raised by
    name rather than indexed.
    """
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("a query certain to return a row returned none")
    return row


async def tuple_rows(db: aiosqlite.Connection, sql: str, *params: Any) -> list[tuple[Any, ...]]:
    """Every row *sql* returns on *db* as a plain tuple, so that a digest of the rows does not
    depend on the row factory the connection carries."""
    cursor = await db.execute(sql, params)
    return [tuple(row) for row in await cursor.fetchall()]


async def tuple_rows_in(
    db: aiosqlite.Connection, sql: str, ids: Sequence[int]
) -> list[tuple[Any, ...]]:
    """`tuple_rows` for *sql* whose `{marks}` stands for an `IN` list of *ids*, each bound as a
    parameter: one placeholder for each id."""
    return await tuple_rows(db, sql.format(marks=", ".join("?" for _ in ids)), *ids)


def inserted_id(cursor: aiosqlite.Cursor) -> int:
    """The id of the row *cursor*'s INSERT has just written.

    SQLite sets it after every INSERT into a rowid table; aiosqlite types it as optional because
    a cursor in general need not have run one. None here means the statement wrote no row, and
    that is raised by name rather than stored as an id.
    """
    row_id = cursor.lastrowid
    if row_id is None:
        raise RuntimeError("the statement wrote no row, so there is no id to read")
    return row_id


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


class DatabasePredatesBaselineError(RuntimeError):
    """The database records migrations the bot no longer carries. See `run_migrations`."""


class MigrationBreaksForeignKeys(RuntimeError):
    """A migration file left a row pointing at a missing parent. See `run_migrations`."""


#: How many dangling rows a `MigrationBreaksForeignKeys` names.
_DANGLING_ROWS_NAMED = 3


async def run_migrations(db_path: str) -> None:
    """Apply all pending SQL migration files in order.

    **The schema starts from one baseline**, ``001_baseline.sql``. Until go-live, which is
    release ``v1.0.0``, the baseline may be edited in place.

    **At go-live**, in one place so nothing is missed on the day:

    - The baseline is frozen. Every later schema change is a new migration numbered after it,
      with a test of its own, and no applied file is ever changed.
    - ``test_the_baseline_is_the_only_migration`` admits each numbered migration after the
      baseline.
    - A test pinning the SHA-256 of each migration file (read with universal newlines) is added,
      so an edited applied file fails the suite.

    **Each file is applied whole, or not at all** (architecture.md, "A database update is applied
    whole, or not at all"). For each pending file the foreign keys are switched off (a pragma
    SQLite ignores inside a transaction, so it is issued first; the migrations' own connection is
    never lent, so nothing has to switch them back), then the file runs in one transaction. Before
    it commits, ``PRAGMA foreign_key_check`` must find no row pointing at a missing parent, or
    :class:`MigrationBreaksForeignKeys` is raised naming the first few. The ``schema_migrations``
    row is written in the same save. Any fault rolls the whole file back, the row with it, and is
    raised again. This is SQLite's own procedure for changing a table: with the keys on, a rebuild
    would delete the rows a cascade links to the table dropped.

    **Three constraints on a migration file**, which that procedure puts on it:

    - It carries no transaction statement (``BEGIN TRANSACTION``, ``COMMIT``, ``ROLLBACK``,
      ``SAVEPOINT``; a trigger's ``BEGIN … END`` is not one) and no ``PRAGMA`` of its own; the
      runner owns both.
    - It does not record its own version; the runner does, in the same save.
    - A table rebuild follows SQLite's order: create the new table, copy the rows, drop the old
      one, and rename the new one into place. It never renames the old table away, which would
      repoint the keys that refer to it at the table renamed.

    **A database from before the baseline is refused, not migrated.** It records versions
    such as ``001_initial.sql`` that no longer exist, and the baseline applied over its tables
    would fail part-way with "table already exists". So any recorded version missing from
    the migrations directory raises :class:`DatabasePredatesBaselineError` before anything
    is written — the journal mode included — and the database has to be recreated. The same
    holds for one migrated by a branch that added a migration this checkout does not carry.

    **The migrations use a connection of their own,** opened and closed here and never one
    `get_connection` lends. When this returns nothing holds the database open, so the last
    connection to close has checkpointed the log and a factory reset can delete the fresh copy it
    migrated (`test_migrations_hold_no_connection_open_afterwards`). All the commits and scripts
    stay in this function and `_enable_wal`, which the rule on saves allows.
    """
    async with _migration_connection(db_path) as db:
        migration_files = sorted(
            f for f in os.listdir(_MIGRATIONS_DIR)
            if f.endswith(".sql") and not f.startswith("__")
        )
        cursor = await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        )
        if await cursor.fetchone() is not None:
            cursor = await db.execute("SELECT version FROM schema_migrations")
            unknown = sorted({row[0] for row in await cursor.fetchall()} - set(migration_files))
            if unknown:
                raise DatabasePredatesBaselineError(
                    f"{db_path} records migrations this bot does not carry "
                    f"({', '.join(unknown[:3])}{', …' if len(unknown) > 3 else ''}). It was "
                    "built before the schema was squashed into one baseline, or by a branch "
                    "with migrations of its own. Nothing was changed; recreate the database."
                )

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

        for filename in migration_files:
            version = filename  # e.g. "001_baseline.sql"
            if version in applied:
                continue

            filepath = os.path.join(_MIGRATIONS_DIR, filename)
            with open(filepath, encoding="utf-8") as fh:
                sql = fh.read()

            log.info("Applying migration: %s", filename)
            # The keys are switched off outside any transaction, where the pragma takes effect.
            # The script's first line opens the transaction and `executescript` leaves it open, so
            # the check and the version row fall inside it.
            await db.execute("PRAGMA foreign_keys = OFF")
            try:
                await db.executescript("BEGIN IMMEDIATE;\n" + sql)
                cursor = await db.execute("PRAGMA foreign_key_check")
                dangling = list(await cursor.fetchall())
                if dangling:
                    first = dangling[:_DANGLING_ROWS_NAMED]
                    named = ", ".join(f"{row[0]} row {row[1]} (parent {row[2]})" for row in first)
                    raise MigrationBreaksForeignKeys(
                        f"{version} leaves {len(dangling)} row(s) pointing at a missing parent: "
                        f"{named}{', …' if len(dangling) > _DANGLING_ROWS_NAMED else ''}. "
                        "Nothing was changed."
                    )
                await db.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, datetime.now(timezone.utc).isoformat()),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
            log.info("Migration applied: %s", filename)
