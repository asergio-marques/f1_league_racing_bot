"""Migration 062: the attendance configuration loses its server (issue #244)."""
from __future__ import annotations

import sqlite3

import pytest

from tests.support.migration_steps import apply, migrate_before

LEAGUE = 6201


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "pre_062.db")
    migrate_before(path, "062")
    db = sqlite3.connect(path)
    db.execute(
        "INSERT INTO server_configs (server_id, interaction_role_id, "
        "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
        (LEAGUE,),
    )
    db.execute(
        "INSERT INTO seasons (id, server_id, start_date, status, season_number) "
        "VALUES (1, ?, '2026-01-01', 'ACTIVE', 1)",
        (LEAGUE,),
    )
    db.execute(
        "INSERT INTO divisions (id, season_id, name, mention_role_id) VALUES (10, 1, 'Pro', 5)"
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


def test_the_league_s_configuration_is_carried_across(db_path):
    db = sqlite3.connect(db_path)
    db.execute(
        "INSERT INTO attendance_config (server_id, module_enabled, rsvp_notice_days, "
        "no_show_penalty, autosack_threshold) VALUES (?, 1, 6, 4, 9)",
        (LEAGUE,),
    )
    db.commit()
    db.close()

    apply(db_path, "062")

    assert _query(
        db_path,
        "SELECT id, module_enabled, rsvp_notice_days, no_show_penalty, autosack_threshold "
        "FROM attendance_config",
    ) == [(1, 1, 6, 4, 9)]


def test_a_division_s_channels_are_carried_across(db_path):
    db = sqlite3.connect(db_path)
    db.execute(
        "INSERT INTO attendance_division_config (division_id, server_id, rsvp_channel_id, "
        "attendance_channel_id, attendance_message_id) VALUES (10, ?, '71', '72', '73')",
        (LEAGUE,),
    )
    db.commit()
    db.close()

    apply(db_path, "062")

    assert _query(
        db_path,
        "SELECT division_id, rsvp_channel_id, attendance_channel_id, attendance_message_id "
        "FROM attendance_division_config",
    ) == [(10, "71", "72", "73")]


def test_a_division_s_channels_still_go_with_the_division(db_path):
    """The foreign key onto the division, and its cascade, survive the rebuild."""
    db = sqlite3.connect(db_path)
    db.execute(
        "INSERT INTO attendance_division_config (division_id, server_id, rsvp_channel_id) "
        "VALUES (10, ?, '71')",
        (LEAGUE,),
    )
    db.commit()
    db.close()
    apply(db_path, "062")

    db = sqlite3.connect(db_path)
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("DELETE FROM divisions WHERE id = 10")
    db.commit()
    db.close()

    assert _query(db_path, "SELECT * FROM attendance_division_config") == []


def test_no_server_id_is_left(db_path):
    apply(db_path, "062")

    for table in ("attendance_config", "attendance_division_config"):
        columns = [c[1] for c in _query(db_path, f"PRAGMA table_info({table})")]
        assert "server_id" not in columns, table
