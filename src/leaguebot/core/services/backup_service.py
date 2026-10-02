"""Saving and restoring the whole database, for driving a test season.

**This is a testing convenience, not disaster recovery.** The backups sit beside the live
files on the same disk — on the Pi, the same SD card — so they insure against a test run
that went somewhere unhelpful, or a migration worth undoing, and against nothing that
happens to the card. Say so wherever a league manager might read otherwise.

**The whole file is copied, not the test data within it.** `bot.db` holds the whole league,
and `test_mode_active` is a column of its one configuration row, so there is no "test data"
to separate at the file level. The guarantee that a real league is never touched comes from
the commands instead: they run only while the league is in test mode, which itself refuses
to switch on while a real driver stands in a live season (decided 2026-09-07).

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
from leaguebot.core.services.change_queue import empty_queue_in
from leaguebot.core.utils.league_bot import LeagueBot

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
    """What `/test-mode backup status` reports."""

    exists: bool
    taken_at: datetime | None
    size_bytes: int
    readable: bool
    locked: bool
    locked_by: str | None


class BackupError(Exception):
    """Anything that stops a save or a restore, in words a manager can act on.

    A locked backup, none saved and one that cannot be read are this class itself: the
    maintainer can act on each. A fault in the bot's own work is `BackupFault`.
    """


class BackupFault(BackupError):
    """A copy, a write or an integrity check that failed: a fault in the bot, not a refusal.

    The cogs tell it from its parent, catching it first, and record it as a failure whose
    reply does not name the error; the parent they record as a refusal with its text as
    the reason.
    """


# ── Copying a database ────────────────────────────────────────────────────


def snapshot_database(source: str | Path, target: str | Path) -> None:
    """Copy *source* to *target* through SQLite's own backup API.

    Correct against a database being written to, and against WAL — see the module
    docstring. Written to a temporary file beside the target and renamed over it, so an
    interruption leaves the previous backup standing rather than a half-written one where
    a good one used to be.
    """
    target = Path(target)
    temporary = _copy_to_temporary(source, target)
    try:
        os.replace(temporary, target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise BackupFault(f"{target.name} could not be written: {exc}") from exc


def _temporary_beside(target: Path, tag: str = "") -> Path:
    """The name a copy bound for *target* is written under first.

    *tag* keeps two copies bound for the same target apart, as a save whose two databases
    are one file would otherwise have them share a name.
    """
    return target.with_name(target.name + tag + ".part")


def _copy_to_temporary(source: str | Path, target: Path, tag: str = "") -> Path:
    """Copy *source* through the backup API to the temporary name beside *target*, and return it.

    Raises `BackupFault` where there is no database to copy or the copy fails, having removed
    what it wrote.
    """
    source = Path(source)
    if not source.is_file():
        raise BackupFault(f"there is no database at {source.name} to copy")

    temporary = _temporary_beside(target, tag)
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
        failure: sqlite3.Error | None = exc
    else:
        failure = None
    finally:
        for connection in (origin, copy):
            if connection is not None:
                connection.close()

    if failure is not None:
        # Removed only once both handles are shut, or Windows will not let it go.
        temporary.unlink(missing_ok=True)
        raise BackupFault(f"{source.name} could not be copied: {failure}") from failure
    return temporary


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
    """Back up both databases, overwriting whatever was there, both or neither.

    Refuses while the backup is locked. The caller pauses the scheduler around this.

    **All or nothing.** Both copies are taken to temporary files beside their targets first,
    and only once both have succeeded are the two backups replaced, so a copy that fails leaves
    the previous pair standing and no temporary behind. Such a failure is a `BackupFault`, and
    a caller may say the previous backup is unchanged. A failure in the renames themselves is
    not: the first rename that fails is a `BackupFault` too (nothing has been replaced yet), but
    one after the league backup has landed leaves a mismatched pair and raises the `OSError`
    as it is, for a caller to call a fault that may have been partly done.

    **No scheduler database means no scheduler backup.** A bot that has never scheduled
    anything has none, and the saved pair is then the league backup alone: an earlier
    scheduler backup is removed as the new pair replaces the old, so a restore never brings
    back jobs the league did not have when it was saved.
    """
    if is_locked(db_path):
        raise BackupError(
            "the saved backup is locked, so it will not be overwritten. "
            "Unlock it with `/test-mode backup lock` first."
        )
    league_target = backup_path(db_path)
    jobs_target = backup_path(jobstore_path)
    has_jobs = Path(jobstore_path).is_file()

    league_part = jobs_part = None
    try:
        league_part = _copy_to_temporary(db_path, league_target)
        if has_jobs:
            jobs_part = _copy_to_temporary(jobstore_path, jobs_target, ".jobs")
        try:
            os.replace(league_part, league_target)
        except OSError as exc:
            raise BackupFault(f"{league_target.name} could not be written: {exc}") from exc
        league_part = None
        # From here the pair no longer matches until the scheduler's half lands.
        if jobs_part is not None:
            os.replace(jobs_part, jobs_target)
            jobs_part = None
        else:
            jobs_target.unlink(missing_ok=True)
    finally:
        for part in (league_part, jobs_part):
            if part is not None:
                part.unlink(missing_ok=True)
    log.info("backup: saved %s and %s", league_target, jobs_target)


def discard(db_path: str | Path, jobstore_path: str | Path) -> bool:
    """Delete the saved backup, its scheduler half and the lock, and say whether one was there.

    A saved state belongs to the test season it was taken in: nothing restores it once that
    season is over, the backup commands running in test mode alone. So test mode leaving —
    by the toggle in Configuration, or by the season being completed — takes the backup with
    it rather than leaving a state nobody can reach (decided 2026-09-17).

    **The lock does not protect it.** The lock exists to stop a *save* overwriting a state a
    maintainer means to keep for the run they are in; it is not a reason to keep a state past
    the run itself. The lock file goes too, so the next test season saves freely.
    """
    removed = False
    for path in (backup_path(db_path), backup_path(jobstore_path), lock_path(db_path)):
        try:
            if Path(path).is_file():
                Path(path).unlink()
                removed = True
        except OSError:  # noqa: PERF203 — one file failing must not keep the others
            log.exception("backup: could not delete %s", path)
    if removed:
        log.info("backup: discarded the saved state beside %s", db_path)
    return removed


def stage_restore(db_path: str | Path, jobstore_path: str | Path) -> None:
    """Verify the backup, keep what is live, and stage the swap for the next startup.

    Nothing live is replaced here — see the module docstring on why the swap belongs to
    startup. What *is* done now is the checking, so a manager learns their backup is
    unusable while they still have the working database, rather than after it is gone.

    **Staging is all or nothing.** If a copy fails, both staged names are removed (whichever
    call wrote them, an earlier staging awaiting a restart included) and a `BackupFault` is
    raised, so the caller may say nothing was restored. Any other error reaches the caller as
    it is, including a staged file that cannot be removed.

    **A backup with no scheduler half stages an empty scheduler database** beside the league
    one, so the restart replaces the live scheduler with one holding no job, and the restored
    league runs with none it did not have when it was saved. The file is a real, non-empty
    one: only its header page is written (`PRAGMA user_version`), the connection closed
    explicitly so Windows can rename it, and the scheduler creates its table on it at start.
    The live scheduler is kept in its pre-restore copy first, as ever.
    """
    league_backup = backup_path(db_path)
    if not league_backup.is_file():
        raise BackupError(
            "there is no saved backup to restore. Take one with "
            "`/test-mode backup save`."
        )
    if not is_readable_database(league_backup):
        raise BackupError(
            f"the saved backup ({league_backup.name}) is not a readable database, so it "
            "will not be restored. Take a fresh one with `/test-mode backup save`."
        )

    jobstore_backup = backup_path(jobstore_path)
    if jobstore_backup.is_file() and not is_readable_database(jobstore_backup):
        raise BackupError(
            f"the saved scheduler backup ({jobstore_backup.name}) is not a readable "
            "database, so it will not be restored."
        )

    staged = (staged_path(db_path), staged_path(jobstore_path))
    try:
        # What is live now, kept before anything is staged: a restore nobody wanted is
        # otherwise unrecoverable, and this is the only copy of the state it replaced. Inside
        # the try, so a copy that fails here also leaves no earlier staging behind.
        if Path(db_path).is_file():
            snapshot_database(db_path, prerestore_path(db_path))
        if Path(jobstore_path).is_file():
            snapshot_database(jobstore_path, prerestore_path(jobstore_path))
        shutil.copyfile(league_backup, staged[0])
        if jobstore_backup.is_file():
            shutil.copyfile(jobstore_backup, staged[1])
        else:
            _write_empty_database(staged[1])
    except (OSError, sqlite3.Error, BackupFault) as exc:
        # Both staged names go, whichever call wrote them: an earlier staging still awaiting
        # a restart goes too, so the next start swaps nothing in. A file that cannot be removed
        # raises its own OSError, which is not a fault this function undid.
        for path in staged:
            path.unlink(missing_ok=True)
        if isinstance(exc, BackupFault):
            raise
        raise BackupFault(f"the restore could not be staged: {exc}") from exc
    log.info("backup: staged a restore of %s", league_backup)


def _write_empty_database(path: Path) -> None:
    """Write a database holding nothing but its header page to *path*."""
    path.unlink(missing_ok=True)
    connection = sqlite3.connect(str(path))
    try:
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
    finally:
        connection.close()


def apply_staged_restore(db_path: str | Path, jobstore_path: str | Path) -> bool:
    """Swap any staged files into place. Returns whether anything was swapped.

    **Called at startup, before a single service is constructed.** That is the one moment
    nothing holds a connection to either database, which is what makes the swap safe.

    The WAL and shared-memory files of the database being replaced are removed with it. A
    stale `-wal` beside a different database is not merely useless — SQLite would try to
    recover it into the file it now sits beside.

    **A restored state brings back no change waiting.** The league database's change queue is
    emptied in the staged file before it is swapped in: a change saved with the state would be
    carried out again against a server whose messages it no longer knows. A stop before the swap
    leaves the staged file, still to be emptied at the next start.
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
        if live == Path(db_path):
            empty_queue_in(staged)
        for residue in (f"{live}-wal", f"{live}-shm"):
            Path(residue).unlink(missing_ok=True)
        os.replace(staged, live)
        log.info("backup: restored %s from the saved backup", live.name)
        swapped = True
    return swapped


def state(db_path: str | Path) -> BackupState:
    """What the backup of the league database is, for `/test-mode backup status`."""
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

def jobstore_path_of(bot: LeagueBot) -> str:
    """Where the scheduler keeps its jobs, asked of the scheduler rather than guessed.

    Here rather than in a cog because two of them need it: the backup commands, and the
    approval that offers a backup before it commits a season.
    """
    scheduler = getattr(bot, "scheduler_service", None)
    path = getattr(scheduler, "_jobstore_path", None)
    if path:
        return str(path)
    from leaguebot.core.services.scheduler_service import default_jobstore_path

    return str(default_jobstore_path(bot.db_path))
