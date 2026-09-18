"""Migration 069: the audit log, the retry queue and the review prompt lose their server (#244)."""
from __future__ import annotations

import sqlite3

import pytest

from tests.support.migration_steps import apply, migrate_before

LEAGUE = 6901


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "pre_069.db")
    migrate_before(path, "069")
    db = sqlite3.connect(path)
    db.executescript(
        f"""
        INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id,
                                    log_channel_id) VALUES ({LEAGUE}, 1, 2, 3);
        INSERT INTO audit_entries (id, server_id, actor_id, actor_name, division_id,
                                   change_type, old_value, new_value, timestamp)
            VALUES (7, {LEAGUE}, 42, 'Toto', NULL, 'SEASON_APPROVE', 'SETUP', 'ACTIVE',
                    '2026-09-18T10:00:00');
        INSERT INTO pending_messages (id, server_id, channel_id, content, failure_reason,
                                      enqueued_at, retry_count, last_attempted_at)
            VALUES (8, {LEAGUE}, 700, 'hello', '503', '2026-09-18T11:00:00', 2,
                    '2026-09-18T11:05:00');
        INSERT INTO season_review_prompts (server_id, season_id, channel_id, message_id,
                                           reviewer_id, posted_at)
            VALUES ({LEAGUE}, 3, 700, 800, 4242, '2026-09-18T12:00:00');
        """
    )
    db.commit()
    db.close()
    return path


def _query(db_path, sql):
    db = sqlite3.connect(db_path)
    try:
        return db.execute(sql).fetchall()
    finally:
        db.close()


def test_the_audit_log_is_carried_across(db_path):
    apply(db_path, "069")

    assert _query(
        db_path,
        "SELECT id, actor_id, actor_name, division_id, change_type, old_value, new_value, "
        "timestamp FROM audit_entries",
    ) == [(7, 42, "Toto", None, "SEASON_APPROVE", "SETUP", "ACTIVE", "2026-09-18T10:00:00")]


def test_the_retry_queue_is_carried_across(db_path):
    apply(db_path, "069")

    assert _query(
        db_path,
        "SELECT id, channel_id, content, failure_reason, enqueued_at, retry_count, "
        "last_attempted_at FROM pending_messages",
    ) == [(8, 700, "hello", "503", "2026-09-18T11:00:00", 2, "2026-09-18T11:05:00")]


def test_the_standing_review_prompt_becomes_the_leagues_one_row(db_path):
    apply(db_path, "069")

    assert _query(
        db_path,
        "SELECT id, season_id, channel_id, message_id, reviewer_id, posted_at "
        "FROM season_review_prompts",
    ) == [(1, 3, 700, 800, 4242, "2026-09-18T12:00:00")]

    db = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO season_review_prompts "
            "(id, season_id, channel_id, message_id, reviewer_id, posted_at) "
            "VALUES (2, 4, 1, 1, 1, 'now')"
        )
    db.close()


def test_the_configuration_can_be_deleted_with_audit_rows_standing(db_path):
    """The foreign key onto server_configs went with the column."""
    apply(db_path, "069")

    db = sqlite3.connect(db_path)
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("DELETE FROM server_configs")
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM audit_entries").fetchone() == (1,)
    db.close()


def test_no_server_id_is_left_outside_server_configs(db_path):
    apply(db_path, "069")

    tables = [
        row[0]
        for row in _query(
            db_path,
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' AND name != 'server_configs'",
        )
    ]
    for table in tables:
        columns = [c[1] for c in _query(db_path, f"PRAGMA table_info({table})")]
        assert "server_id" not in columns, table
