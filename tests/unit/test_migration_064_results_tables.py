"""Migration 064: the results module's tables lose their server (issue #244).

The points store is a parent: its entries and fastest-lap rows hang off its id by cascading
foreign keys, which dropping it would fire. They are set aside and restored, and that is
what is chiefly pinned here.
"""
from __future__ import annotations

import sqlite3

import pytest

from tests.support.migration_steps import apply, migrate_before

LEAGUE = 6401


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "pre_064.db")
    migrate_before(path, "064")
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys = ON")
    db.execute(
        "INSERT INTO server_configs (server_id, interaction_role_id, "
        "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
        (LEAGUE,),
    )
    db.execute("INSERT INTO results_module_config (server_id, module_enabled) VALUES (?, 1)", (LEAGUE,))
    db.execute(
        "INSERT INTO points_config_store (id, server_id, config_name) VALUES (7, ?, 'Standard')",
        (LEAGUE,),
    )
    db.execute(
        "INSERT INTO points_config_entries (config_id, session_type, position, points) "
        "VALUES (7, 'FEATURE_RACE', 1, 25), (7, 'FEATURE_RACE', 2, 18)"
    )
    db.execute(
        "INSERT INTO points_config_fl (config_id, session_type, fl_points, fl_position_limit) "
        "VALUES (7, 'FEATURE_RACE', 1, 10)"
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


def test_the_module_flag_is_carried_across(db_path):
    apply(db_path, "064")

    assert _query(db_path, "SELECT id, module_enabled FROM results_module_config") == [(1, 1)]


def test_a_configuration_keeps_its_id_and_name(db_path):
    apply(db_path, "064")

    assert _query(db_path, "SELECT id, config_name FROM points_config_store") == [(7, "Standard")]


def test_a_configuration_keeps_its_points_through_the_rebuild(db_path):
    apply(db_path, "064")

    assert _query(
        db_path,
        "SELECT config_id, session_type, position, points FROM points_config_entries "
        "ORDER BY position",
    ) == [(7, "FEATURE_RACE", 1, 25), (7, "FEATURE_RACE", 2, 18)]
    assert _query(
        db_path, "SELECT config_id, fl_points, fl_position_limit FROM points_config_fl"
    ) == [(7, 1, 10)]


def test_the_children_still_go_with_their_configuration(db_path):
    """The cascade onto the rebuilt store works: deleting a configuration takes its points."""
    apply(db_path, "064")

    db = sqlite3.connect(db_path)
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("DELETE FROM points_config_store WHERE id = 7")
    db.commit()
    db.close()

    assert _query(db_path, "SELECT * FROM points_config_entries") == []
    assert _query(db_path, "SELECT * FROM points_config_fl") == []


def test_a_name_is_unique_on_its_own(db_path):
    apply(db_path, "064")

    db = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO points_config_store (config_name) VALUES ('Standard')")
    db.close()


def test_no_server_id_is_left(db_path):
    apply(db_path, "064")

    for table in ("results_module_config", "points_config_store", "round_amend_channels"):
        columns = [c[1] for c in _query(db_path, f"PRAGMA table_info({table})")]
        assert "server_id" not in columns, table
