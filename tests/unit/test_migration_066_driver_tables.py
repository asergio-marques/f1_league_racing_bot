"""Migration 066: a driver, their accounts and their history lose their server (issue #244).

driver_profiles is the parent of seven tables, so the rebuild runs with enforcement off; what
is pinned here is that nothing that named a driver lost them, and that the triggers keeping a
driver's accounts still fire on the rebuilt table.
"""
from __future__ import annotations

import sqlite3

import pytest

from tests.support.migration_steps import apply, migrate_before

LEAGUE = 6601


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "pre_066.db")
    migrate_before(path, "066")
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        f"""
        INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id,
                                    log_channel_id) VALUES ({LEAGUE}, 1, 2, 3);
        INSERT INTO seasons (id, server_id, start_date, status, season_number)
            VALUES (1, {LEAGUE}, '2026-01-01', 'ACTIVE', 1);
        INSERT INTO divisions (id, season_id, name, mention_role_id) VALUES (10, 1, 'Pro', 5);
        INSERT INTO driver_profiles (id, server_id, discord_user_id, current_state,
                                     former_driver, is_test_driver)
            VALUES (7, {LEAGUE}, '1111', 'ASSIGNED', 1, 0);
        UPDATE driver_profiles SET discord_user_id = '2222' WHERE id = 7;
        INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id)
            VALUES (7, 1, 10);
        INSERT INTO driver_history_entries (server_id, discord_user_id, driver_profile_id,
                                            season_number, division_name)
            VALUES ({LEAGUE}, '1111', 7, 1, 'Pro');
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


def test_the_driver_and_every_account_they_held_are_carried_across(db_path):
    apply(db_path, "066")

    assert _query(
        db_path, "SELECT id, discord_user_id, current_state, former_driver FROM driver_profiles"
    ) == [(7, "2222", "ASSIGNED", 1)]
    assert _query(
        db_path,
        "SELECT driver_profile_id, discord_user_id FROM driver_accounts ORDER BY discord_user_id",
    ) == [(7, "1111"), (7, "2222")]


def test_nothing_that_named_the_driver_lost_them(db_path):
    apply(db_path, "066")

    assert _query(db_path, "SELECT driver_profile_id FROM driver_season_assignments") == [(7,)]
    assert _query(
        db_path, "SELECT driver_profile_id, discord_user_id FROM driver_history_entries"
    ) == [(7, "1111")]
    assert _query(db_path, "PRAGMA foreign_key_check") == []


def test_the_account_triggers_fire_on_the_rebuilt_table(db_path):
    apply(db_path, "066")

    db = sqlite3.connect(db_path)
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("INSERT INTO driver_profiles (id, discord_user_id, current_state) VALUES (8, '3333', 'UNASSIGNED')")
    db.execute("UPDATE driver_profiles SET discord_user_id = '4444' WHERE id = 8")
    db.commit()
    db.close()

    assert _query(
        db_path,
        "SELECT discord_user_id FROM driver_accounts WHERE driver_profile_id = 8 "
        "ORDER BY discord_user_id",
    ) == [("3333",), ("4444",)]


def test_an_account_belongs_to_one_driver_across_the_league(db_path):
    apply(db_path, "066")

    db = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO driver_profiles (discord_user_id, current_state) VALUES ('2222', 'UNASSIGNED')")
    db.close()


def test_no_server_id_is_left(db_path):
    apply(db_path, "066")

    for table in ("driver_profiles", "driver_accounts", "driver_history_entries"):
        columns = [c[1] for c in _query(db_path, f"PRAGMA table_info({table})")]
        assert "server_id" not in columns, table
