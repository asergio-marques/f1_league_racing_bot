"""`/bot factory-reset` — backup first, then the wipe, then the Discord clean-up (#247)."""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services import backup_service, factory_reset_service  # noqa: E402
from services.factory_reset_service import take_backup  # noqa: E402

NOW = datetime(2026, 9, 19, 10, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
async def db_path(tmp_path) -> str:
    path = str(tmp_path / "bot.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (4242, 1, 2, 3)"
        )
        await db.commit()
    return path


def _jobstore(tmp_path) -> str:
    path = str(tmp_path / "scheduler.db")
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE apscheduler_jobs (id TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO apscheduler_jobs VALUES ('pfp_daily')")
    connection.commit()
    connection.close()
    return path


def _read(path: Path, sql: str):
    connection = sqlite3.connect(str(path))
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


# ── The backup ────────────────────────────────────────────────────────────


async def test_the_backup_copies_both_databases_under_the_moment(db_path, tmp_path):
    jobstore = _jobstore(tmp_path)

    backup = take_backup(db_path, jobstore, now=NOW)

    assert backup.database == tmp_path / "bot.factory-20260919T101500Z.db"
    assert backup.jobstore == tmp_path / "scheduler.factory-20260919T101500Z.db"
    assert _read(backup.database, "SELECT server_id FROM server_configs") == [(4242,)]
    assert _read(backup.jobstore, "SELECT id FROM apscheduler_jobs") == [("pfp_daily",)]


async def test_the_backup_neither_overwrites_nor_heeds_the_test_mode_backup(db_path, tmp_path):
    jobstore = _jobstore(tmp_path)
    backup_service.save(db_path, jobstore)
    backup_service.set_lock(db_path, who="a maintainer", now=NOW)
    kept = backup_service.backup_path(db_path).read_bytes()

    backup = take_backup(db_path, jobstore, now=NOW)

    assert backup.database.is_file()
    assert backup_service.backup_path(db_path).read_bytes() == kept


async def test_a_second_backup_keeps_the_first(db_path, tmp_path):
    jobstore = _jobstore(tmp_path)
    first = take_backup(db_path, jobstore, now=NOW)

    second = take_backup(db_path, jobstore, now=NOW.replace(minute=16))

    assert first.database.is_file() and second.database.is_file()
    assert first.database != second.database


async def test_no_job_store_is_no_fault(db_path, tmp_path):
    backup = take_backup(db_path, str(tmp_path / "absent.db"), now=NOW)

    assert backup.jobstore is None
    assert backup.database.is_file()


async def test_a_copy_that_fails_its_check_is_refused_and_removed(db_path, tmp_path, monkeypatch):
    monkeypatch.setattr(backup_service, "is_readable_database", lambda _path: False)

    with pytest.raises(backup_service.BackupError):
        take_backup(db_path, _jobstore(tmp_path), now=NOW)

    assert not factory_reset_service.backup_path(db_path, "20260919T101500Z").exists()


async def test_a_database_that_cannot_be_copied_is_refused(tmp_path):
    with pytest.raises(backup_service.BackupError):
        take_backup(str(tmp_path / "missing.db"), _jobstore(tmp_path), now=NOW)
