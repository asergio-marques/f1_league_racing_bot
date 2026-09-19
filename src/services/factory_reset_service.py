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
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from db.database import get_connection, run_migrations
from services import backup_service
from services.channel_registry_service import DIVISION_SOURCES, SERVER_SOURCES
from services.in_memory_state import clear_in_memory_state

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


# ── What to clean in Discord, gathered before the wipe ────────────────────


@dataclass(frozen=True)
class DiscordTargets:
    """The channels the clean-up visits, read out of the database before it is wiped.

    *created* are the channels the bot made for itself — a signup wizard's, a round's results
    submission and amendment channels — and are deleted outright. *channels* are every other
    channel the bot posts to or reads from, in any season, from which only the bot's own
    messages are deleted. Each is sorted, so the clean-up visits them in a stable order and a
    report of where it stopped means the same thing on a second run.
    """

    created: tuple[int, ...]
    channels: tuple[int, ...]


#: Channels the bot creates, as (table, column).
_CREATED_SOURCES: tuple[tuple[str, str], ...] = (
    ("signup_wizard_records", "signup_channel_id"),
    ("round_submission_channels", "channel_id"),
    ("round_amend_channels", "channel_id"),
)

#: Channels the bot posts to that no setting names, as (table, column): where a check-in
#: call was posted, a review prompt waits, or an undelivered message was bound.
_OTHER_SOURCES: tuple[tuple[str, str], ...] = (
    ("rsvp_embed_messages", "channel_id"),
    ("season_review_prompts", "channel_id"),
    ("pending_messages", "channel_id"),
)


async def gather_targets(db_path: str) -> DiscordTargets:
    """Every channel the clean-up must visit. Read before the wipe, which forgets them all."""
    async def ids(db, pairs) -> set[int]:
        found: set[int] = set()
        for table, column in pairs:
            cursor = await db.execute(
                f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"
            )
            for row in await cursor.fetchall():
                try:
                    found.add(int(row[0]))
                except (TypeError, ValueError):
                    log.warning("factory reset: %s.%s holds %r", table, column, row[0])
        return found

    async with get_connection(db_path) as db:
        created = await ids(db, _CREATED_SOURCES)
        # Every season's divisions, not only the current one's: the bot posted in all of them.
        settings = [
            (table, column) for _key, table, column in (*SERVER_SOURCES, *DIVISION_SOURCES)
        ]
        channels = await ids(db, [*settings, *_OTHER_SOURCES])
    return DiscordTargets(
        created=tuple(sorted(created)), channels=tuple(sorted(channels - created))
    )


# ── The wipe ──────────────────────────────────────────────────────────────


async def wipe(db_path: str, scheduler_service, bot=None) -> None:
    """Return the database, the scheduled work and the memory to a fresh install.

    **The database is replaced by a freshly migrated one, not emptied table by table.** A
    new database is built beside the live one and copied over it through SQLite's backup
    API, so the result is exactly what a first start produces — the packaged circuits and
    nothing else, the counters reset, with no list of tables here to fall out of step with
    the schema. The backup API writes through SQLite's own locking, so it is safe while the
    bot's short-lived connections come and go.
    """
    scheduler_service.cancel_all()
    if bot is not None:
        clear_in_memory_state(bot)

    live = Path(db_path)
    fresh = live.with_name(f"{live.stem}.fresh.db")
    _remove_database(fresh)
    try:
        await run_migrations(str(fresh))
        origin = copy = None
        try:
            # Closed explicitly, as `backup_service.snapshot_database` explains.
            origin = sqlite3.connect(str(fresh))
            copy = sqlite3.connect(str(live))
            origin.backup(copy)
        finally:
            for connection in (origin, copy):
                if connection is not None:
                    connection.close()
    finally:
        _remove_database(fresh)
    log.info("factory reset: %s replaced by a fresh database", live)


def _remove_database(path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
