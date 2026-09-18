"""`migrate_before` builds each schema once per session and copies it — issue #252.

Thirty-five tests each raising the migration chain afresh took the Windows CI job past its
timeout: `executescript` commits every statement on its own, and a Windows runner pays for
each one. These pin the cache that removed that cost, and that a copy is the schema a fresh
build makes.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from tests.support import migration_steps
from tests.support.migration_steps import migrate_before


def _schema(db_path: str) -> list[tuple]:
    db = sqlite3.connect(db_path)
    try:
        return db.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name != 'schema_migrations' "
            "ORDER BY type, name"
        ).fetchall()
    finally:
        db.close()


def _applied(db_path: str) -> list[str]:
    db = sqlite3.connect(db_path)
    try:
        return [r[0] for r in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
    finally:
        db.close()


@pytest.fixture
def fresh_cache(monkeypatch):
    """An empty cache, so a test sees the first build rather than another test's."""
    monkeypatch.setattr(migration_steps, "_BEFORE", {})


def test_a_schema_before_a_migration_is_built_once_per_session(tmp_path, fresh_cache):
    real = migration_steps._build_before
    with patch.object(migration_steps, "_build_before", side_effect=real) as build:
        migrate_before(str(tmp_path / "a.db"), "049")
        migrate_before(str(tmp_path / "b.db"), "049")

    assert build.call_count == 1
    assert _schema(str(tmp_path / "a.db")) == _schema(str(tmp_path / "b.db"))


def test_a_copy_holds_the_same_schema_as_a_fresh_build(tmp_path, fresh_cache):
    migrate_before(str(tmp_path / "warm.db"), "061")
    migrate_before(str(tmp_path / "copy.db"), "061")
    files = [f for f in migration_steps._files() if f < "061"]
    migration_steps._build_before(str(tmp_path / "fresh.db"), "061", files)

    assert _schema(str(tmp_path / "copy.db")) == _schema(str(tmp_path / "fresh.db"))


def test_each_version_gets_a_schema_of_its_own(tmp_path, fresh_cache):
    migrate_before(str(tmp_path / "049.db"), "049")
    migrate_before(str(tmp_path / "061.db"), "061")

    assert _schema(str(tmp_path / "049.db")) != _schema(str(tmp_path / "061.db"))
    assert not any(v.startswith("061") for v in _applied(str(tmp_path / "061.db")))


def test_the_copy_records_what_it_applied(tmp_path, fresh_cache):
    """So `run_migrations_through` carries on from the copy rather than starting again."""
    migrate_before(str(tmp_path / "a.db"), "053")

    assert _applied(str(tmp_path / "a.db")) == [
        f for f in migration_steps._files() if f < "053"
    ]


def test_a_target_holding_data_is_refused(tmp_path):
    target = tmp_path / "seeded.db"
    db = sqlite3.connect(str(target))
    db.execute("CREATE TABLE kept (x)")
    db.commit()
    db.close()

    with pytest.raises(ValueError):
        migrate_before(str(target), "049")
