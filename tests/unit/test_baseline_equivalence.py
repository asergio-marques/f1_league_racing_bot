"""The baseline builds the database the 61 migrations build — issue #254.

**Temporary.** This is the proof that squashing the chain lost nothing, and it can only run
while both exist: it is removed in the commit that deletes the chain.

Compared: every schema object, by type, name, table and SQL text, and every row of every
table. `sqlite_sequence` is left out — the chain's rebuilds leave counters of 0 behind, which
change nothing — and so is `schema_migrations`, which records the files applied and so must
differ.
"""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import run_migrations  # noqa: E402

BASELINE = os.path.join(
    os.path.dirname(__file__), "..", "..", "src", "db", "migrations",
    "001_baseline.sql.candidate",
)
SKIPPED = ("sqlite_sequence", "schema_migrations")


def _schema(db: sqlite3.Connection) -> list[tuple]:
    return db.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        f"WHERE tbl_name NOT IN {SKIPPED} ORDER BY type, name"
    ).fetchall()


def _contents(db: sqlite3.Connection) -> dict[str, list[tuple]]:
    tables = [
        r[0] for r in db.execute(
            f"SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT IN {SKIPPED}"
        )
    ]
    return {t: sorted(db.execute(f'SELECT * FROM "{t}"').fetchall(), key=repr) for t in tables}


async def test_the_baseline_builds_the_database_the_chain_builds(tmp_path):
    chain_path = str(tmp_path / "chain.db")
    await run_migrations(chain_path)

    baseline_path = str(tmp_path / "baseline.db")
    built = sqlite3.connect(baseline_path)
    built.execute("PRAGMA foreign_keys = ON")
    with open(BASELINE, encoding="utf-8") as fh:
        built.executescript(fh.read())
    built.commit()

    chain = sqlite3.connect(chain_path)
    try:
        assert _schema(built) == _schema(chain)
        assert _contents(built) == _contents(chain)
        assert len(_contents(built)["tracks"]) == 28
    finally:
        chain.close()
        built.close()
