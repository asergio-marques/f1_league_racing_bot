"""Migration 065: the signup module's tables lose their server (issue #244).

signup_windows is a parent: signup_records.window_id refers to it ON DELETE SET NULL, which
dropping it would fire. The records are set aside and restored, and that is chiefly what is
pinned here: every record keeps the window it was made in.
"""
from __future__ import annotations

import sqlite3

import pytest

from tests.support.migration_steps import apply, migrate_before

LEAGUE = 6501


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "pre_065.db")
    migrate_before(path, "065")
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        f"""
        INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id,
                                    log_channel_id) VALUES ({LEAGUE}, 1, 2, 3);
        INSERT INTO seasons (id, server_id, start_date, status, season_number)
            VALUES (1, {LEAGUE}, '2026-01-01', 'ACTIVE', 1);
        INSERT INTO divisions (id, season_id, name, mention_role_id) VALUES (10, 1, 'Pro', 5);
        INSERT INTO signup_module_settings (server_id, nationality_required, time_type,
                                            time_image_required)
            VALUES ({LEAGUE}, 0, 'SHORT_QUALIFYING', 0);
        INSERT INTO signup_module_config (server_id, signup_channel_id, signups_open, close_at)
            VALUES ({LEAGUE}, 77, 1, '2026-02-01T20:00:00');
        INSERT INTO signup_division_config (server_id, division_id) VALUES ({LEAGUE}, 10);
        INSERT INTO signup_wizard_records (server_id, discord_user_id, wizard_state,
                                           signup_channel_id)
            VALUES ({LEAGUE}, '11', 'COLLECTING_PLATFORM', 88);
        INSERT INTO signup_availability_slots (id, server_id, day_of_week, time_hhmm)
            VALUES (3, {LEAGUE}, 5, '21:00');
        INSERT INTO signup_windows (id, server_id, season_id, selected_tracks_json)
            VALUES (4, {LEAGUE}, 1, '["1"]');
        INSERT INTO signup_records (id, server_id, season_id, window_id, discord_user_id,
                                    platform, approved)
            VALUES (9, {LEAGUE}, 1, 4, '11', 'Steam', 1);
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


def test_the_single_row_tables_are_carried_across(db_path):
    apply(db_path, "065")

    assert _query(
        db_path,
        "SELECT id, nationality_required, time_type, time_image_required "
        "FROM signup_module_settings",
    ) == [(1, 0, "SHORT_QUALIFYING", 0)]
    assert _query(
        db_path, "SELECT id, signup_channel_id, signups_open, close_at FROM signup_module_config"
    ) == [(1, 77, 1, "2026-02-01T20:00:00")]


def test_a_record_keeps_the_window_it_was_made_in(db_path):
    apply(db_path, "065")

    assert _query(
        db_path,
        "SELECT id, season_id, window_id, discord_user_id, platform, approved "
        "FROM signup_records",
    ) == [(9, 1, 4, "11", "Steam", 1)]
    assert _query(db_path, "SELECT id, season_id FROM signup_windows") == [(4, 1)]


def test_the_rest_are_carried_across(db_path):
    apply(db_path, "065")

    assert _query(db_path, "SELECT division_id FROM signup_division_config") == [(10,)]
    assert _query(
        db_path, "SELECT discord_user_id, wizard_state, signup_channel_id FROM signup_wizard_records"
    ) == [("11", "COLLECTING_PLATFORM", 88)]
    assert _query(db_path, "SELECT id, day_of_week, time_hhmm FROM signup_availability_slots") == [
        (3, 5, "21:00")
    ]


def test_a_window_s_deletion_still_clears_its_records_window(db_path):
    """The SET NULL onto the rebuilt windows table still holds."""
    apply(db_path, "065")

    db = sqlite3.connect(db_path)
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("DELETE FROM signup_windows WHERE id = 4")
    db.commit()
    db.close()

    assert _query(db_path, "SELECT id, window_id FROM signup_records") == [(9, None)]


def test_an_account_holds_one_wizard(db_path):
    apply(db_path, "065")

    db = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO signup_wizard_records (discord_user_id) VALUES ('11')")
    db.close()


def test_no_server_id_is_left(db_path):
    apply(db_path, "065")

    for table in (
        "signup_module_settings", "signup_module_config", "signup_division_config",
        "signup_wizard_records", "signup_availability_slots", "signup_windows",
        "signup_records",
    ):
        columns = [c[1] for c in _query(db_path, f"PRAGMA table_info({table})")]
        assert "server_id" not in columns, table
