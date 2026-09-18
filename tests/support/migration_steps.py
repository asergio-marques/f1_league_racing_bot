"""Stand a database just before one migration, so a test can seed the old shape and apply it.

The migrations that took `server_id` out of the schema (issue #244, from 061) each rebuild
tables holding data, and what they must be shown to do is carry that data across. Running
the real chain up to the step before builds the old shape exactly, where a hand-written copy
of it would drift (issue #233).

This deliberately bypasses `tests/conftest.py`'s schema template: that substitute always
migrates to the head, which is the one version these tests cannot use.
"""
from __future__ import annotations

import itertools
import os
import sqlite3

MIGRATIONS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "src", "db", "migrations"
)


def _files() -> list[str]:
    return sorted(
        f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql") and not f.startswith("__")
    )


#: Schemas built before a migration, by `(version, the files before it)`. See `migrate_before`.
_BEFORE: dict[tuple[str, tuple[str, ...]], str] = {}
_SCRATCH: str | None = None
_BUILT = itertools.count()


def _build_before(db_path: str, version: str, files: list[str]) -> None:
    """Apply *files* to a new database at *db_path*, recording each as `run_migrations` does."""
    db = sqlite3.connect(db_path)
    try:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        for name in files:
            with open(os.path.join(MIGRATIONS_DIR, name), encoding="utf-8") as fh:
                db.executescript(fh.read())
            db.execute(
                "INSERT OR IGNORE INTO schema_migrations VALUES (?, '2026-01-01')", (name,)
            )
        db.commit()
    finally:
        db.close()


def migrate_before(db_path: str, version: str) -> None:
    """Stand the schema as it was before the migration starting *version*, e.g. ``"061"``.

    **Built once per session and copied thereafter** (issue #252). Raising the chain costs a
    commit per statement — `executescript` commits before it runs and each statement is its
    own transaction — which Linux absorbs and a Windows runner does not: 35 tests building it
    afresh took the Windows job past its timeout. So the first call for a version builds it
    into scratch, and every later one copies the file, as `tests/conftest.py`'s template does
    for the full schema. The key carries the set of files before *version*, so a test that
    hides migrations from the directory still gets the schema those files make.

    ``schema_migrations`` records every file applied, as `run_migrations` would, so
    `run_migrations_through` carries on from a copy. The scratch directory carries the prefix
    `tests/conftest.py` sweeps at session start, so a killed run leaves nothing behind.

    *db_path* must not already hold data: a copy would destroy it.
    """
    import atexit
    import shutil
    import tempfile

    global _SCRATCH

    if os.path.exists(db_path) and os.path.getsize(db_path):
        raise ValueError(f"migrate_before would overwrite a database holding data: {db_path}")

    files = [name for name in _files() if name < version]
    key = (version, tuple(files))
    template = _BEFORE.get(key)
    if template is None:
        if _SCRATCH is None:
            _SCRATCH = tempfile.mkdtemp(prefix="f1-schema-before-")
            atexit.register(shutil.rmtree, _SCRATCH, ignore_errors=True)
        template = os.path.join(_SCRATCH, f"{next(_BUILT)}-before-{version}.db")
        _build_before(template, version, files)
        _BEFORE[key] = template
    shutil.copyfile(template, db_path)


def apply(db_path: str, version: str) -> None:
    """Apply the one migration whose file name starts with *version*, as the runner would:
    with foreign-key enforcement on, which is what `get_connection` opens every connection
    with."""
    [name] = [f for f in _files() if f.startswith(version)]
    db = sqlite3.connect(db_path)
    try:
        db.execute("PRAGMA foreign_keys = ON")
        with open(os.path.join(MIGRATIONS_DIR, name), encoding="utf-8") as fh:
            db.executescript(fh.read())
        db.commit()
    finally:
        db.close()


async def run_migrations_through(db_path: str, migration: str) -> None:
    """`run_migrations`, stopping after *migration* (a full file name).

    For a test of one historic migration: it applies that migration and nothing after it, so
    what it asserts about the schema is what that migration left, not what the head of the
    chain has since made of it. The later files are moved aside for the duration, which is
    the technique `test_migration_043` established for building the database before one.
    """
    import shutil
    import tempfile

    from db.database import run_migrations

    later = [f for f in _files() if f > migration]
    stash = tempfile.mkdtemp(prefix="stashed-migrations-")
    for name in later:
        shutil.move(os.path.join(MIGRATIONS_DIR, name), os.path.join(stash, name))
    try:
        await run_migrations(db_path)
    finally:
        for name in later:
            shutil.move(os.path.join(stash, name), os.path.join(MIGRATIONS_DIR, name))
        os.rmdir(stash)
