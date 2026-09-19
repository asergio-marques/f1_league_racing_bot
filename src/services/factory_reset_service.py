"""factory_reset_service — return the bot to a fresh install (issue #247).

Three steps, in an order that is the whole of the design:

1. **A backup, always, first.** Both databases are copied beside the live files by the SQLite
   backup API, under a name carrying the moment, and the reset is refused if either copy
   fails or the league database's copy does not pass its own integrity check. Restoring one
   is the host's job, not a command's: a factory reset is the one act the bot offers that
   nothing inside it can undo, so the only undo is outside it.
2. **The wipe.** Everything the bot holds about the league goes — the database, the
   scheduled work and what is held in memory — and the claim on the server with it.
3. **The Discord clean-up**, run after the wipe and apart from the command. See
   `clean_discord`.

The backup is kept apart from the test-mode backup of `backup_service.save`: it neither
overwrites that one nor heeds its lock, and a second factory reset does not overwrite the
first's, each carrying its own moment in its name.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from services import backup_service

log = logging.getLogger(__name__)

#: Between the database's stem and the moment: `bot.db` -> `bot.factory-20260919T101500Z.db`.
BACKUP_INFIX = ".factory-"


@dataclass(frozen=True)
class FactoryBackup:
    """Where the backup was written. *jobstore* is None where there was no job store."""

    database: Path
    jobstore: Path | None


def backup_path(live_path: str | Path, stamp: str) -> Path:
    live = Path(live_path)
    return live.with_name(f"{live.stem}{BACKUP_INFIX}{stamp}.db")


def take_backup(
    db_path: str | Path, jobstore_path: str | Path, *, now: datetime | None = None
) -> FactoryBackup:
    """Copy both databases beside the live ones. Raises `backup_service.BackupError`.

    The caller pauses the scheduler around this, as `backup_service.save`'s callers do, so
    that no job is written to the job store mid-copy.
    """
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    database = backup_path(db_path, stamp)
    backup_service.snapshot_database(db_path, database)
    if not backup_service.is_readable_database(database):
        database.unlink(missing_ok=True)
        raise backup_service.BackupError(
            f"the copy of {Path(db_path).name} did not pass its integrity check"
        )

    jobstore: Path | None = None
    if Path(jobstore_path).is_file():
        jobstore = backup_path(jobstore_path, stamp)
        backup_service.copy_jobstore(jobstore_path, jobstore)

    log.info("factory reset: backed up to %s and %s", database, jobstore)
    return FactoryBackup(database=database, jobstore=jobstore)
