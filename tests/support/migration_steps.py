"""Stand a database just before one migration, so a test can seed the old shape and apply it.

The migrations that took `server_id` out of the schema (issue #244, from 061) each rebuild
tables holding data, and what they must be shown to do is carry that data across. Running
the real chain up to the step before builds the old shape exactly, where a hand-written copy
of it would drift (issue #233).

This deliberately bypasses `tests/conftest.py`'s schema template: that substitute always
migrates to the head, which is the one version these tests cannot use.
"""
from __future__ import annotations

import os
import sqlite3

MIGRATIONS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "src", "db", "migrations"
)


def _files() -> list[str]:
    return sorted(
        f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql") and not f.startswith("__")
    )


def migrate_before(db_path: str, version: str) -> None:
    """Apply every migration whose file name sorts before *version*, e.g. ``"061"``."""
    db = sqlite3.connect(db_path)
    try:
        db.execute("PRAGMA foreign_keys = ON")
        for name in _files():
            if name >= version:
                break
            with open(os.path.join(MIGRATIONS_DIR, name), encoding="utf-8") as fh:
                db.executescript(fh.read())
        db.commit()
    finally:
        db.close()


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
