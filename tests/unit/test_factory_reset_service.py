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


# ── The Discord targets, gathered before the wipe ─────────────────────────


async def _seed_channels(db_path: str) -> None:
    """A past season and a current one, the bot posting in channels of both."""
    async with get_connection(db_path) as db:
        await db.executescript("""
            INSERT INTO seasons (id, start_date, status, season_number, stage)
            VALUES (1, '2025-01-01', 'COMPLETED', 1, 'COMPLETED'),
                   (2, '2026-01-01', 'ACTIVE', 2, 'ONGOING');
            INSERT INTO divisions (id, season_id, name, mention_role_id, forecast_channel_id,
                lineup_channel_id, calendar_channel_id)
            VALUES (1, 1, 'Old', 9, 10, 11, 12), (2, 2, 'New', 9, 20, 21, 22);
            INSERT INTO division_results_config (division_id, results_channel_id,
                standings_channel_id, penalty_channel_id) VALUES (2, 23, 24, '25');
            INSERT INTO attendance_division_config (division_id, rsvp_channel_id,
                attendance_channel_id) VALUES (2, '26', '27');
            INSERT INTO rounds (id, division_id, round_number, format, scheduled_at)
            VALUES (1, 2, 1, 'NORMAL', '2026-01-08T18:00:00');
            INSERT INTO rsvp_embed_messages (round_id, division_id, message_id, channel_id,
                posted_at) VALUES (1, 2, '1', '28', '2026-01-03');
            INSERT INTO round_submission_channels (round_id, channel_id, created_at)
            VALUES (1, 40, '2026-01-08');
            INSERT INTO round_amend_channels (round_id, channel_id, session_type, created_at)
            VALUES (1, 41, 'RACE', '2026-01-09');
            INSERT INTO signup_module_config (id, signup_channel_id) VALUES (1, 30);
            INSERT INTO signup_wizard_records (discord_user_id, signup_channel_id)
            VALUES ('900', 42), ('901', NULL);
            INSERT INTO pending_messages (channel_id, content, failure_reason, enqueued_at)
            VALUES (31, 'x', 'Forbidden', '2026-01-01');
            INSERT INTO season_review_prompts (id, season_id, channel_id, message_id,
                reviewer_id, posted_at) VALUES (1, 2, 32, 1, 1, '2026-01-01');
        """)
        await db.commit()


async def test_every_channel_the_bot_posts_to_is_gathered(db_path):
    await _seed_channels(db_path)

    targets = await factory_reset_service.gather_targets(db_path)

    # 2 and 3 are the interaction and log channels the fixture configures.
    assert targets.channels == (
        2, 3, 10, 11, 12, 20, 21, 22, 23, 24, 25, 26, 27, 28, 30, 31, 32,
    )


async def test_the_channels_the_bot_created_are_gathered_apart(db_path):
    await _seed_channels(db_path)

    targets = await factory_reset_service.gather_targets(db_path)

    assert targets.created == (40, 41, 42)
    assert not set(targets.created) & set(targets.channels)


async def test_a_fresh_install_has_nothing_to_clean(tmp_path):
    path = str(tmp_path / "bot.db")
    await run_migrations(path)

    targets = await factory_reset_service.gather_targets(path)

    assert targets == factory_reset_service.DiscordTargets(created=(), channels=())


# ── The wipe ──────────────────────────────────────────────────────────────


def _contents(path: str) -> dict[str, list]:
    """Every table's rows, but the record of when each migration was applied."""
    connection = sqlite3.connect(path)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        return {
            table: sorted(connection.execute(f"SELECT * FROM {table}").fetchall(), key=repr)
            for table in tables
            if table != "schema_migrations"
        }
    finally:
        connection.close()


async def test_the_wipe_leaves_exactly_a_fresh_install(db_path, tmp_path):
    from unittest.mock import MagicMock

    await _seed_channels(db_path)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE tracks SET gp_name = 'Renamed' WHERE id = 1")
        await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) "
            "VALUES ('900', 'NOT_SIGNED_UP')"
        )
        await db.commit()
    fresh = str(tmp_path / "fresh-for-comparison.db")
    await run_migrations(fresh)

    await factory_reset_service.wipe(db_path, MagicMock())

    assert _contents(db_path) == _contents(fresh)
    assert _contents(db_path)["tracks"], "the packaged circuits are kept"


async def test_the_wipe_frees_the_claim(db_path):
    from unittest.mock import MagicMock

    from services.config_service import ConfigService

    await factory_reset_service.wipe(db_path, MagicMock())

    assert await ConfigService(db_path).get_league_server_id() is None


async def test_the_wipe_clears_every_job_and_the_memory(db_path, monkeypatch):
    from unittest.mock import MagicMock

    cleared = []
    monkeypatch.setattr(
        factory_reset_service, "clear_in_memory_state", lambda bot: cleared.append(bot)
    )
    scheduler = MagicMock()
    bot = object()

    await factory_reset_service.wipe(db_path, scheduler, bot)

    scheduler.cancel_all.assert_called_once_with()
    assert cleared == [bot]


async def test_the_wipe_leaves_no_scratch_behind(db_path, tmp_path):
    from unittest.mock import MagicMock

    await factory_reset_service.wipe(db_path, MagicMock())

    assert sorted(p.name for p in tmp_path.iterdir() if "fresh" in p.name) == []
