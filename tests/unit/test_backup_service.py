"""Saving and restoring the whole database.

The test that matters most is the WAL one. `bot.db` runs in WAL, so a committed
transaction sits in `bot.db-wal` until a checkpoint folds it in — and a backup taken with
a plain file copy would silently omit it, with nothing failing until someone restored and
found rounds missing. Every other test here guards a way the backup could be worse than
useless: a corrupt file restored over a working database, a half-written copy replacing a
good one, a restore that cannot be walked back.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services import backup_service as bs  # noqa: E402


def _database(path: Path, *, wal: bool = True, rows: int = 3) -> None:
    """A small database, in WAL by default, as the bot's own is.

    Every connection in this file is closed explicitly. A sqlite3 connection used as a
    context manager commits but does **not** close, and a WAL database with a live handle
    keeps `-wal` and `-shm` files beside it that Windows will not let anything rename over
    — so `with` here would fail the very operations under test, on the development machine
    only.
    """
    db = sqlite3.connect(str(path))
    try:
        if wal:
            db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE rounds (id INTEGER PRIMARY KEY, track TEXT)")
        db.executemany(
            "INSERT INTO rounds (track) VALUES (?)", [(f"track {n}",) for n in range(rows)]
        )
        db.commit()
    finally:
        db.close()


def _add_round(path: Path, track: str) -> None:
    """One more round, committed and the connection closed."""
    db = sqlite3.connect(str(path))
    try:
        db.execute("INSERT INTO rounds (track) VALUES (?)", (track,))
        db.commit()
    finally:
        db.close()


def _rows(path: Path) -> list[str]:
    """Closed explicitly: a sqlite3 connection used as a context manager commits but does
    not close, and a held handle stops the file being replaced on Windows."""
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [r[0] for r in db.execute("SELECT track FROM rounds ORDER BY id")]
    finally:
        db.close()


# ── Naming ────────────────────────────────────────────────────────────────


def test_the_backup_sits_beside_the_database_it_copies(tmp_path):
    assert bs.backup_path(tmp_path / "bot.db").name == "bot.bkup.db"
    assert bs.backup_path(tmp_path / "scheduler.db").name == "scheduler.bkup.db"


# ── The WAL case ──────────────────────────────────────────────────────────


def test_a_committed_row_still_in_the_wal_is_backed_up(tmp_path):
    """The reason this module uses SQLite's backup API and not `shutil.copyfile`.

    The row below is committed but not checkpointed, so it lives in `bot.db-wal` and not
    in `bot.db`. A file copy would produce a backup missing it, and nothing would say so
    until a restore came up short.
    """
    live = tmp_path / "bot.db"
    _database(live)
    connection = sqlite3.connect(str(live))  # held open, so no checkpoint runs
    try:
        connection.execute("INSERT INTO rounds (track) VALUES ('written into the wal')")
        connection.commit()

        bs.snapshot_database(live, bs.backup_path(live))

        assert "written into the wal" in _rows(bs.backup_path(live))
    finally:
        connection.close()


def test_a_plain_file_copy_would_have_missed_it(tmp_path):
    """Pins the premise of the test above, so it cannot quietly stop meaning anything.

    If a `shutil.copyfile` ever *does* carry the row — WAL turned off, a checkpoint
    slipped in — then the test above is passing for a reason other than the one it claims.
    """
    import shutil

    live = tmp_path / "bot.db"
    _database(live)
    connection = sqlite3.connect(str(live))
    try:
        connection.execute("INSERT INTO rounds (track) VALUES ('written into the wal')")
        connection.commit()

        naive = tmp_path / "naive.db"
        shutil.copyfile(live, naive)

        assert "written into the wal" not in _rows(naive)
    finally:
        connection.close()


# ── Saving ────────────────────────────────────────────────────────────────


def test_save_copies_both_databases(tmp_path):
    live, jobs = tmp_path / "bot.db", tmp_path / "scheduler.db"
    _database(live)
    _database(jobs, wal=False, rows=1)

    bs.save(live, jobs)

    assert bs.backup_path(live).is_file()
    assert bs.backup_path(jobs).is_file()


def test_save_overwrites_an_earlier_backup(tmp_path):
    live = tmp_path / "bot.db"
    _database(live, rows=1)
    bs.save(live, tmp_path / "scheduler.db")

    _add_round(live, "added later")
    bs.save(live, tmp_path / "scheduler.db")

    assert "added later" in _rows(bs.backup_path(live))


def test_a_missing_scheduler_database_is_not_a_fault(tmp_path):
    """A bot that has never scheduled anything has none, and is still worth backing up."""
    live = tmp_path / "bot.db"
    _database(live)

    bs.save(live, tmp_path / "scheduler.db")

    assert bs.backup_path(live).is_file()
    assert not bs.backup_path(tmp_path / "scheduler.db").exists()


def test_saving_a_database_that_is_not_there_is_refused(tmp_path):
    with pytest.raises(bs.BackupError, match="no database"):
        bs.save(tmp_path / "bot.db", tmp_path / "scheduler.db")


def test_a_failed_copy_leaves_the_previous_backup_standing(tmp_path):
    """Written to a `.part` and renamed: the one thing worse than no backup is a
    half-written one where a good one used to be."""
    live = tmp_path / "bot.db"
    _database(live, rows=1)
    bs.save(live, tmp_path / "scheduler.db")
    good = _rows(bs.backup_path(live))

    (tmp_path / "corrupt.db").write_bytes(b"not a database at all")
    with pytest.raises(bs.BackupError):
        bs.snapshot_database(tmp_path / "corrupt.db", bs.backup_path(live))

    assert _rows(bs.backup_path(live)) == good
    assert not bs.backup_path(live).with_name("bot.bkup.db.part").exists()


# ── The lock ──────────────────────────────────────────────────────────────


def test_a_locked_backup_refuses_a_save(tmp_path):
    live = tmp_path / "bot.db"
    _database(live, rows=1)
    bs.save(live, tmp_path / "scheduler.db")
    before = _rows(bs.backup_path(live))
    bs.set_lock(live, who="Manager")

    _add_round(live, "should not reach the backup")
    with pytest.raises(bs.BackupError, match="locked"):
        bs.save(live, tmp_path / "scheduler.db")

    assert _rows(bs.backup_path(live)) == before


def test_the_lock_toggles(tmp_path):
    """Unlocking by deleting a file over SSH is a poor thing to ask of a manager who
    locked one by accident."""
    live = tmp_path / "bot.db"

    assert bs.set_lock(live, who="Manager") is True
    assert bs.is_locked(live)
    assert bs.set_lock(live, who="Manager") is False
    assert not bs.is_locked(live)


def test_the_lock_records_who_set_it(tmp_path):
    live = tmp_path / "bot.db"
    bs.set_lock(live, who="Sérgio", now=datetime(2026, 9, 7, tzinfo=timezone.utc))

    assert "Sérgio" in bs.locked_by(live)
    assert "2026-09-07" in bs.locked_by(live)


# ── Reading a backup back ─────────────────────────────────────────────────


def test_a_real_database_reads_as_readable(tmp_path):
    live = tmp_path / "bot.db"
    _database(live)

    assert bs.is_readable_database(live)


@pytest.mark.parametrize(
    "content", [b"", b"not a database at all", b"SQLite format 3\x00 truncated"]
)
def test_rubbish_does_not_read_as_a_database(tmp_path, content):
    path = tmp_path / "bot.bkup.db"
    path.write_bytes(content)

    assert not bs.is_readable_database(path)


def test_a_file_that_is_not_there_does_not_read_as_a_database(tmp_path):
    assert not bs.is_readable_database(tmp_path / "nowhere.db")


# ── Staging a restore ─────────────────────────────────────────────────────


def test_staging_leaves_the_live_database_untouched(tmp_path):
    """Nothing is swapped until startup, where nothing holds a connection."""
    live, jobs = tmp_path / "bot.db", tmp_path / "scheduler.db"
    _database(live, rows=1)
    bs.save(live, jobs)
    _add_round(live, "live only")

    bs.stage_restore(live, jobs)

    assert "live only" in _rows(live), "the live database was replaced too early"
    assert bs.staged_path(live).is_file()


def test_staging_keeps_a_copy_of_what_was_live(tmp_path):
    """A restore nobody wanted is otherwise unrecoverable."""
    live, jobs = tmp_path / "bot.db", tmp_path / "scheduler.db"
    _database(live, rows=1)
    bs.save(live, jobs)
    _add_round(live, "about to be replaced")

    bs.stage_restore(live, jobs)

    assert "about to be replaced" in _rows(bs.prerestore_path(live))


def test_restoring_without_a_backup_is_refused(tmp_path):
    live = tmp_path / "bot.db"
    _database(live)

    with pytest.raises(bs.BackupError, match="no saved backup"):
        bs.stage_restore(live, tmp_path / "scheduler.db")


def test_a_corrupt_backup_is_refused_before_anything_is_staged(tmp_path):
    """Learning the backup is unusable while the working database is still there."""
    live = tmp_path / "bot.db"
    _database(live)
    bs.backup_path(live).write_bytes(b"not a database at all")

    with pytest.raises(bs.BackupError, match="not a readable database"):
        bs.stage_restore(live, tmp_path / "scheduler.db")

    assert not bs.staged_path(live).exists()


def test_a_corrupt_scheduler_backup_is_refused_too(tmp_path):
    live, jobs = tmp_path / "bot.db", tmp_path / "scheduler.db"
    _database(live)
    _database(jobs, wal=False, rows=1)
    bs.save(live, jobs)
    bs.backup_path(jobs).write_bytes(b"not a database at all")

    with pytest.raises(bs.BackupError, match="scheduler backup"):
        bs.stage_restore(live, jobs)

    assert not bs.staged_path(live).exists()


# ── Applying it at startup ────────────────────────────────────────────────


def test_the_staged_database_replaces_the_live_one(tmp_path):
    live, jobs = tmp_path / "bot.db", tmp_path / "scheduler.db"
    _database(live, rows=1)
    bs.save(live, jobs)
    _add_round(live, "added after the backup")
    bs.stage_restore(live, jobs)

    assert bs.apply_staged_restore(live, jobs) is True

    assert "added after the backup" not in _rows(live)
    assert not bs.staged_path(live).exists()


def test_nothing_staged_changes_nothing(tmp_path):
    live, jobs = tmp_path / "bot.db", tmp_path / "scheduler.db"
    _database(live, rows=2)

    assert bs.apply_staged_restore(live, jobs) is False
    assert len(_rows(live)) == 2


def test_the_replaced_database_s_wal_goes_with_it(tmp_path):
    """A stale `-wal` is not merely useless beside a different database: SQLite would try
    to recover it into the file it now sits next to."""
    live, jobs = tmp_path / "bot.db", tmp_path / "scheduler.db"
    _database(live, rows=1)
    bs.save(live, jobs)
    bs.stage_restore(live, jobs)
    Path(f"{live}-wal").write_bytes(b"stale wal")
    Path(f"{live}-shm").write_bytes(b"stale shm")

    bs.apply_staged_restore(live, jobs)

    assert not Path(f"{live}-wal").exists()
    assert not Path(f"{live}-shm").exists()


def test_a_staged_file_that_is_corrupt_is_discarded_not_applied(tmp_path):
    """The last line before a corrupt file replaces a working database."""
    live, jobs = tmp_path / "bot.db", tmp_path / "scheduler.db"
    _database(live, rows=2)
    bs.staged_path(live).write_bytes(b"not a database at all")

    assert bs.apply_staged_restore(live, jobs) is False

    assert len(_rows(live)) == 2, "a corrupt staged file replaced the live database"
    assert not bs.staged_path(live).exists(), "it would be retried on every restart"


# ── Status ────────────────────────────────────────────────────────────────


def test_status_with_no_backup(tmp_path):
    reported = bs.state(tmp_path / "bot.db")

    assert reported.exists is False
    assert reported.locked is False


def test_status_reports_a_saved_backup(tmp_path):
    live = tmp_path / "bot.db"
    _database(live)
    bs.save(live, tmp_path / "scheduler.db")

    reported = bs.state(live)

    assert reported.exists and reported.readable
    assert reported.size_bytes > 0
    assert reported.taken_at is not None


def test_status_reports_a_corrupt_backup_as_unreadable(tmp_path):
    """Without this, a manager learns their backup is rubbish at the restore."""
    live = tmp_path / "bot.db"
    _database(live)
    bs.backup_path(live).write_bytes(b"not a database at all")

    reported = bs.state(live)

    assert reported.exists and not reported.readable


def test_status_reports_the_lock(tmp_path):
    live = tmp_path / "bot.db"
    _database(live)
    bs.save(live, tmp_path / "scheduler.db")
    bs.set_lock(live, who="Manager")

    reported = bs.state(live)

    assert reported.locked
    assert "Manager" in reported.locked_by


# ── The round trip, against a database the bot would recognise ────────────


def test_a_real_migrated_database_survives_the_round_trip(tmp_path):
    """End to end on the real schema, not the toy table the rest of this file uses.

    Migrations, WAL, a row written and left in the write-ahead log, saved, staged and
    swapped — the sequence a manager actually performs, and the one that would expose an
    incompatibility between the backup API and whatever the schema has grown into.
    """
    import asyncio

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
    from db.database import run_migrations

    live = tmp_path / "bot.db"
    jobs = tmp_path / "scheduler.db"
    asyncio.run(run_migrations(str(live)))

    held = sqlite3.connect(str(live))  # held open, so the write stays in the WAL
    try:
        held.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (4242, 1, 2, 3)"
        )
        held.commit()
        bs.save(live, jobs)
    finally:
        held.close()

    bs.stage_restore(live, jobs)
    assert bs.apply_staged_restore(live, jobs) is True

    restored = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    try:
        row = restored.execute(
            "SELECT server_id FROM server_configs WHERE server_id = 4242"
        ).fetchone()
    finally:
        restored.close()
    assert row is not None, "the restored database lost a committed row"
