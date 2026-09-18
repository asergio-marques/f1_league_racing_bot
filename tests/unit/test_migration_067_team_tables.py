"""Migration 067: the league's team list and its team roles lose their server (issue #244)."""
from __future__ import annotations

import sqlite3

import pytest

from tests.support.migration_steps import apply, migrate_before

LEAGUE = 6701


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "pre_067.db")
    migrate_before(path, "067")
    db = sqlite3.connect(path)
    db.executescript(
        f"""
        INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id,
                                    log_channel_id) VALUES ({LEAGUE}, 1, 2, 3);
        INSERT INTO default_teams (id, server_id, name, max_seats, is_reserve)
            VALUES (3, {LEAGUE}, 'Alpha', 2, 0), (4, {LEAGUE}, 'Reserve', -1, 1);
        INSERT INTO team_role_configs (id, server_id, team_name, role_id, updated_at)
            VALUES (5, {LEAGUE}, 'Alpha', 4242, '2026-09-17');
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


def test_the_team_list_and_its_roles_are_carried_across(db_path):
    apply(db_path, "067")

    assert _query(
        db_path, "SELECT id, name, max_seats, is_reserve FROM default_teams ORDER BY id"
    ) == [(3, "Alpha", 2, 0), (4, "Reserve", -1, 1)]
    assert _query(db_path, "SELECT id, team_name, role_id, updated_at FROM team_role_configs") == [
        (5, "Alpha", 4242, "2026-09-17")
    ]


def test_a_team_name_is_unique_in_the_league(db_path):
    apply(db_path, "067")

    db = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO default_teams (name) VALUES ('Alpha')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO team_role_configs (team_name, role_id) VALUES ('Alpha', 1)")
    db.close()


def test_no_server_id_is_left(db_path):
    apply(db_path, "067")

    for table in ("default_teams", "team_role_configs"):
        columns = [c[1] for c in _query(db_path, f"PRAGMA table_info({table})")]
        assert "server_id" not in columns, table
