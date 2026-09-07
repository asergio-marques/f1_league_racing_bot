"""Saving and restoring the whole database, for driving a test season.

**This is a testing convenience, not disaster recovery.** The backups sit beside the live
files on the same disk — on the Pi, the same SD card — so they insure against a test run
that went somewhere unhelpful, or a migration worth undoing, and against nothing that
happens to the card. Say so wherever a league manager might read otherwise.

**The whole file is copied, not the test data within it.** `bot.db` holds every server the
bot serves, and `test_mode_active` is a column of one server's row, so there is no
"test data" to separate at the file level. The guarantee that a real league is never
touched comes from the commands instead: they run only while the calling server is in test
mode, which itself refuses to switch on while a real driver stands in a live season
(decided 2026-09-07, the bot serving one server in practice).

**Why the SQLite backup API and not a file copy.** `bot.db` runs in WAL, so a committed
transaction lives in `bot.db-wal` until a checkpoint folds it into the main file. Copying
the `.db` alone yields a database missing its most recent writes — silently, with no error
at restore, discovered later as absent rounds. `Connection.backup` reads through the WAL
and produces a consistent snapshot of a live database, which is the whole reason it exists.

**Why restore stages rather than swaps.** Every service captures its path when the bot is
constructed and holds connections open; APScheduler holds the jobstore open on the event
loop. Replacing the files underneath all that leaves the process reading a database that no
longer exists. So restore stages the files and the swap happens at startup, before the
first service is built — the one moment nothing holds a connection. That works the same
whether the bot is run under systemd or from a terminal, neither of which this module
assumes.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

#: Suffixes for the three roles a file plays. A backup is what `save` writes; a staged file
#: is what `restore` leaves for the next startup; a pre-restore file is the copy taken of
#: what was live at the moment of a restore, so an unwanted one can be walked back.
BACKUP_SUFFIX = ".bkup.db"
STAGED_SUFFIX = ".staged.db"
PRERESTORE_SUFFIX = ".prerestore.db"

#: Sits beside the backups. Its presence refuses a save; its content records who locked it.
LOCK_NAME = "backup.lock"


def backup_path(live_path: str | Path) -> Path:
    """`bot.db` -> `bot.bkup.db`."""
    live = Path(live_path)
    return live.with_name(live.stem + BACKUP_SUFFIX)


def staged_path(live_path: str | Path) -> Path:
    live = Path(live_path)
    return live.with_name(live.stem + STAGED_SUFFIX)


def prerestore_path(live_path: str | Path) -> Path:
    live = Path(live_path)
    return live.with_name(live.stem + PRERESTORE_SUFFIX)


def lock_path(live_path: str | Path) -> Path:
    return Path(live_path).parent / LOCK_NAME


@dataclass(frozen=True)
class BackupState:
    """What `/backup status` reports."""

    exists: bool
    taken_at: datetime | None
    size_bytes: int
    readable: bool
    locked: bool
    locked_by: str | None


class BackupError(Exception):
    """Anything that stops a save or a restore, in words a manager can act on."""


# ── Copying a database ────────────────────────────────────────────────────


def snapshot_database(source: str | Path, target: str | Path) -> None:
    """Copy *source* to *target* through SQLite's own backup API.

    Correct against a database being written to, and against WAL — see the module
    docstring. Written to a temporary file beside the target and renamed over it, so an
    interruption leaves the previous backup standing rather than a half-written one where
    a good one used to be.
    """
    source, target = Path(source), Path(target)
    if not source.is_file():
        raise BackupError(f"there is no database at {source.name} to copy")

    temporary = target.with_name(target.name + ".part")
    temporary.unlink(missing_ok=True)
    origin = copy = None
    try:
        # Closed explicitly, in a `finally`, and not through `with`: a sqlite3 connection
        # used as a context manager commits on exit but does **not** close, and Windows
        # refuses to rename a file another handle still holds. That is a real failure of
        # the save, not a quirk of the tests — the bot's own deployment target is Linux,
        # but the development machine is not, and a save that works on one and not the
        # other is worse than one that works nowhere.
        origin = sqlite3.connect(str(source))
        copy = sqlite3.connect(str(temporary))
        origin.backup(copy)
    except sqlite3.Error as exc:
        failure = exc
    else:
        failure = None
    finally:
        for connection in (origin, copy):
            if connection is not None:
                connection.close()

    if failure is not None:
        # Removed only once both handles are shut, or Windows will not let it go.
        temporary.unlink(missing_ok=True)
        raise BackupError(f"{source.name} could not be copied: {failure}") from failure

    try:
        os.replace(temporary, target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise BackupError(f"{target.name} could not be written: {exc}") from exc


def copy_jobstore(source: str | Path, target: str | Path) -> None:
    """Copy the scheduler's database.

    Through the backup API for the same reason as the league database: `prepare_jobstore`
    puts this file into WAL too, so a plain copy would leave its most recent jobs behind
    in the `-wal`. The caller pauses the scheduler around this, which stops a job being
    written mid-copy; the API is what covers the WAL.

    A missing jobstore is not a fault. A bot that has never scheduled anything has none,
    and a backup of a database with no jobs is a legitimate thing to take.
    """
    source = Path(source)
    if not source.is_file():
        log.info("backup: no scheduler database at %s — nothing to copy", source)
        return
    snapshot_database(source, target)


def is_readable_database(path: str | Path) -> bool:
    """Whether *path* is a SQLite database that passes its own integrity check.

    Asked of a backup before a restore is staged. A truncated or corrupt file restored
    over a working database is the one failure this module must not allow, and it is
    exactly what an interrupted copy on a Pi would leave.
    """
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        return False
    # Closed explicitly, as in `snapshot_database`: a connection left open holds the file
    # against the rename and the delete that follow this check on Windows.
    connection = None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        row = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        log.warning("backup: %s is not a readable database: %s", path.name, exc)
        return False
    finally:
        if connection is not None:
            connection.close()
    return bool(row) and str(row[0]).lower() == "ok"


# ── The lock ──────────────────────────────────────────────────────────────


def is_locked(live_path: str | Path) -> bool:
    return lock_path(live_path).is_file()


def locked_by(live_path: str | Path) -> str | None:
    """The line the lock carries, or None where there is no lock."""
    path = lock_path(live_path)
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def set_lock(live_path: str | Path, *, who: str, now: datetime | None = None) -> bool:
    """Toggle the lock. Returns whether it is locked afterwards.

    A toggle rather than a one-way door: the alternative is unlocking by deleting a file
    over SSH, which is a poor thing to require of a manager who locked one by accident.
    """
    path = lock_path(live_path)
    if path.is_file():
        path.unlink(missing_ok=True)
        return False
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    path.write_text(f"locked by {who} at {stamp}", encoding="utf-8")
    return True


# ── The three operations ──────────────────────────────────────────────────


def save(db_path: str | Path, jobstore_path: str | Path) -> None:
    """Back up both databases, overwriting whatever was there.

    Refuses while the backup is locked. The caller pauses the scheduler around this.
    """
    if is_locked(db_path):
        raise BackupError(
            "the saved backup is locked, so it will not be overwritten. "
            "Unlock it with `/backup lock` first."
        )
    snapshot_database(db_path, backup_path(db_path))
    copy_jobstore(jobstore_path, backup_path(jobstore_path))
    log.info("backup: saved %s and %s", backup_path(db_path), backup_path(jobstore_path))


def stage_restore(db_path: str | Path, jobstore_path: str | Path) -> None:
    """Verify the backup, keep what is live, and stage the swap for the next startup.

    Nothing live is replaced here — see the module docstring on why the swap belongs to
    startup. What *is* done now is the checking, so a manager learns their backup is
    unusable while they still have the working database, rather than after it is gone.
    """
    league_backup = backup_path(db_path)
    if not league_backup.is_file():
        raise BackupError(
            "there is no saved backup to restore. Take one with `/backup save`."
        )
    if not is_readable_database(league_backup):
        raise BackupError(
            f"the saved backup ({league_backup.name}) is not a readable database, so it "
            "will not be restored. Take a fresh one with `/backup save`."
        )

    jobstore_backup = backup_path(jobstore_path)
    if jobstore_backup.is_file() and not is_readable_database(jobstore_backup):
        raise BackupError(
            f"the saved scheduler backup ({jobstore_backup.name}) is not a readable "
            "database, so it will not be restored."
        )

    # What is live now, kept before anything is staged: a restore nobody wanted is
    # otherwise unrecoverable, and this is the only copy of the state it replaced.
    if Path(db_path).is_file():
        snapshot_database(db_path, prerestore_path(db_path))
    if Path(jobstore_path).is_file():
        snapshot_database(jobstore_path, prerestore_path(jobstore_path))

    shutil.copyfile(league_backup, staged_path(db_path))
    if jobstore_backup.is_file():
        shutil.copyfile(jobstore_backup, staged_path(jobstore_path))
    log.info("backup: staged a restore of %s", league_backup)


def apply_staged_restore(db_path: str | Path, jobstore_path: str | Path) -> bool:
    """Swap any staged files into place. Returns whether anything was swapped.

    **Called at startup, before a single service is constructed.** That is the one moment
    nothing holds a connection to either database, which is what makes the swap safe.

    The WAL and shared-memory files of the database being replaced are removed with it. A
    stale `-wal` beside a different database is not merely useless — SQLite would try to
    recover it into the file it now sits beside.
    """
    swapped = False
    for live in (Path(db_path), Path(jobstore_path)):
        staged = staged_path(live)
        if not staged.is_file():
            continue
        if not is_readable_database(staged):
            log.error(
                "backup: the staged %s is not a readable database — leaving the live one "
                "alone and discarding it",
                staged.name,
            )
            staged.unlink(missing_ok=True)
            continue
        for residue in (f"{live}-wal", f"{live}-shm"):
            Path(residue).unlink(missing_ok=True)
        os.replace(staged, live)
        log.info("backup: restored %s from the saved backup", live.name)
        swapped = True
    return swapped


def state(db_path: str | Path) -> BackupState:
    """What the backup of the league database is, for `/backup status`."""
    path = backup_path(db_path)
    if not path.is_file():
        return BackupState(
            exists=False,
            taken_at=None,
            size_bytes=0,
            readable=False,
            locked=is_locked(db_path),
            locked_by=locked_by(db_path),
        )
    stat = path.stat()
    return BackupState(
        exists=True,
        taken_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        size_bytes=stat.st_size,
        readable=is_readable_database(path),
        locked=is_locked(db_path),
        locked_by=locked_by(db_path),
    )
