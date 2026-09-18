"""Migration 061: nothing but server_configs names a server any more.

The migration rebuilds some thirty tables in nine parts, one class here for each. Every part
seeds the shape before 061 and applies the whole migration: what each part must be shown to
do is carry its tables' rows across, keep what refers to them resolving, and keep the rules
their keys and triggers hold.
"""
from __future__ import annotations

import sqlite3

import pytest

from tests.support.migration_steps import apply, migrate_before

LEAGUE = 6101


def _seed(db_path, *rows, configured=(LEAGUE,)):
    db = sqlite3.connect(db_path)
    for server_id in configured:
        db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (server_id,),
        )
    db.executemany(
        "INSERT INTO weather_pipeline_config "
        "(server_id, phase_1_days, phase_2_days, phase_3_hours) VALUES (?, ?, ?, ?)",
        rows,
    )
    db.commit()
    db.close()


def _rows(db_path):
    db = sqlite3.connect(db_path)
    try:
        return db.execute(
            "SELECT id, phase_1_days, phase_2_days, phase_3_hours FROM weather_pipeline_config"
        ).fetchall()
    finally:
        db.close()


def _query(db_path, sql):
    db = sqlite3.connect(db_path)
    try:
        return db.execute(sql).fetchall()
    finally:
        db.close()


def _execute(db_path, *statements):
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA foreign_keys = ON")
    for sql, params in statements:
        db.execute(sql, params)
    db.commit()
    db.close()


class TestWeatherHorizons:
    """Part 1: the weather horizons become the league's one row."""

    @pytest.fixture
    def db_path(self, tmp_path):
        path = str(tmp_path / "pre_061.db")
        migrate_before(path, "061")
        return path

    def test_the_league_s_horizons_are_carried_across(self, db_path):
        _seed(db_path, (LEAGUE, 9, 4, 6))

        apply(db_path, "061")

        assert _rows(db_path) == [(1, 9, 4, 6)]

    def test_a_league_that_never_configured_them_still_has_none(self, db_path):
        _seed(db_path)

        apply(db_path, "061")

        assert _rows(db_path) == []

    def test_the_server_configuration_survives_the_rebuild(self, db_path):
        """The table was a child of server_configs; dropping it must not reach the parent."""
        _seed(db_path, (LEAGUE, 9, 4, 6))

        apply(db_path, "061")

        db = sqlite3.connect(db_path)
        assert db.execute("SELECT server_id FROM server_configs").fetchall() == [(LEAGUE,)]
        db.close()


class TestAttendanceConfiguration:
    """Part 2: the attendance configuration loses its server."""

    @pytest.fixture
    def db_path(self, tmp_path):
        path = str(tmp_path / "pre_061.db")
        migrate_before(path, "061")
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

    def test_the_league_s_configuration_is_carried_across(self, db_path):
        db = sqlite3.connect(db_path)
        db.execute(
            "INSERT INTO attendance_config (server_id, module_enabled, rsvp_notice_days, "
            "no_show_penalty, autosack_threshold) VALUES (?, 1, 6, 4, 9)",
            (LEAGUE,),
        )
        db.commit()
        db.close()

        apply(db_path, "061")

        assert _query(
            db_path,
            "SELECT id, module_enabled, rsvp_notice_days, no_show_penalty, autosack_threshold "
            "FROM attendance_config",
        ) == [(1, 1, 6, 4, 9)]

    def test_a_division_s_channels_are_carried_across(self, db_path):
        db = sqlite3.connect(db_path)
        db.execute(
            "INSERT INTO attendance_division_config (division_id, server_id, rsvp_channel_id, "
            "attendance_channel_id, attendance_message_id) VALUES (10, ?, '71', '72', '73')",
            (LEAGUE,),
        )
        db.commit()
        db.close()

        apply(db_path, "061")

        assert _query(
            db_path,
            "SELECT division_id, rsvp_channel_id, attendance_channel_id, attendance_message_id "
            "FROM attendance_division_config",
        ) == [(10, "71", "72", "73")]

    def test_a_division_s_channels_still_go_with_the_division(self, db_path):
        """The foreign key onto the division, and its cascade, survive the rebuild."""
        db = sqlite3.connect(db_path)
        db.execute(
            "INSERT INTO attendance_division_config (division_id, server_id, rsvp_channel_id) "
            "VALUES (10, ?, '71')",
            (LEAGUE,),
        )
        db.commit()
        db.close()
        apply(db_path, "061")

        db = sqlite3.connect(db_path)
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("DELETE FROM divisions WHERE id = 10")
        db.commit()
        db.close()

        assert _query(db_path, "SELECT * FROM attendance_division_config") == []


class TestImageTables:
    """Part 3: the image module's tables lose their server."""

    @pytest.fixture
    def db_path(self, tmp_path):
        path = str(tmp_path / "pre_061.db")
        migrate_before(path, "061")
        db = sqlite3.connect(path)
        db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (LEAGUE,),
        )
        db.commit()
        db.close()
        return path

    def test_the_configuration_and_its_toggles_are_carried_across(self, db_path):
        _execute(
            db_path,
            ("INSERT INTO image_config (server_id, module_enabled, time_zone, use_pfp, "
             "per_tier_colour_enabled, verdict_banner_template) VALUES (?, 1, 'Europe/Lisbon', "
             "1, 1, 'banner.svg')", (LEAGUE,)),
            ("INSERT INTO image_aspect_toggles (server_id, aspect, enabled) VALUES (?, 'lineup', 1)",
             (LEAGUE,)),
            ("INSERT INTO image_aspect_toggles (server_id, aspect, enabled) VALUES (?, 'rsvp', 0)",
             (LEAGUE,)),
        )

        apply(db_path, "061")

        assert _query(
            db_path,
            "SELECT id, module_enabled, time_zone, use_pfp, per_tier_colour_enabled, "
            "verdict_banner_template FROM image_config",
        ) == [(1, 1, "Europe/Lisbon", 1, 1, "banner.svg")]
        assert _query(db_path, "SELECT aspect, enabled FROM image_aspect_toggles ORDER BY aspect") == [
            ("lineup", 1),
            ("rsvp", 0),
        ]

    def test_every_configured_column_survives(self, db_path):
        """The rebuild names its columns; one it forgot would read back as the default."""
        before = [c[1] for c in _query(db_path, "PRAGMA table_info(image_config)")]
        apply(db_path, "061")
        after = [c[1] for c in _query(db_path, "PRAGMA table_info(image_config)")]

        assert sorted(set(before) - {"server_id"}) == sorted(set(after) - {"id"})

    def test_the_tier_colours_and_portraits_are_carried_across(self, db_path):
        _execute(
            db_path,
            ("INSERT INTO image_tier_colour (server_id, division_slug, slot, colour) "
             "VALUES (?, 'pro', 'accent', '#FFFFFF')", (LEAGUE,)),
            ("INSERT INTO driver_portraits (server_id, discord_user_id, avatar_key, fetched_at) "
             "VALUES (?, '11', 'k', '2026-01-01')", (LEAGUE,)),
        )

        apply(db_path, "061")

        assert _query(db_path, "SELECT division_slug, slot, colour FROM image_tier_colour") == [
            ("pro", "accent", "#FFFFFF")
        ]
        assert _query(db_path, "SELECT discord_user_id, avatar_key FROM driver_portraits") == [
            ("11", "k")
        ]


class TestResultsTables:
    """Part 4: the results module's tables lose their server.
    
    The points store is a parent: its entries and fastest-lap rows hang off its id by cascading
    foreign keys, which dropping it would fire. They are set aside and restored, and that is
    what is chiefly pinned here."""

    @pytest.fixture
    def db_path(self, tmp_path):
        path = str(tmp_path / "pre_061.db")
        migrate_before(path, "061")
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

    def test_the_module_flag_is_carried_across(self, db_path):
        apply(db_path, "061")

        assert _query(db_path, "SELECT id, module_enabled FROM results_module_config") == [(1, 1)]

    def test_a_configuration_keeps_its_id_and_name(self, db_path):
        apply(db_path, "061")

        assert _query(db_path, "SELECT id, config_name FROM points_config_store") == [(7, "Standard")]

    def test_a_configuration_keeps_its_points_through_the_rebuild(self, db_path):
        apply(db_path, "061")

        assert _query(
            db_path,
            "SELECT config_id, session_type, position, points FROM points_config_entries "
            "ORDER BY position",
        ) == [(7, "FEATURE_RACE", 1, 25), (7, "FEATURE_RACE", 2, 18)]
        assert _query(
            db_path, "SELECT config_id, fl_points, fl_position_limit FROM points_config_fl"
        ) == [(7, 1, 10)]

    def test_the_children_still_go_with_their_configuration(self, db_path):
        """The cascade onto the rebuilt store works: deleting a configuration takes its points."""
        apply(db_path, "061")

        db = sqlite3.connect(db_path)
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("DELETE FROM points_config_store WHERE id = 7")
        db.commit()
        db.close()

        assert _query(db_path, "SELECT * FROM points_config_entries") == []
        assert _query(db_path, "SELECT * FROM points_config_fl") == []

    def test_a_name_is_unique_on_its_own(self, db_path):
        apply(db_path, "061")

        db = sqlite3.connect(db_path)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO points_config_store (config_name) VALUES ('Standard')")
        db.close()


class TestSignupTables:
    """Part 5: the signup module's tables lose their server.
    
    signup_windows is a parent: signup_records.window_id refers to it ON DELETE SET NULL, which
    dropping it would fire. The records are set aside and restored, and that is chiefly what is
    pinned here: every record keeps the window it was made in."""

    @pytest.fixture
    def db_path(self, tmp_path):
        path = str(tmp_path / "pre_061.db")
        migrate_before(path, "061")
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

    def test_the_single_row_tables_are_carried_across(self, db_path):
        apply(db_path, "061")

        assert _query(
            db_path,
            "SELECT id, nationality_required, time_type, time_image_required "
            "FROM signup_module_settings",
        ) == [(1, 0, "SHORT_QUALIFYING", 0)]
        assert _query(
            db_path, "SELECT id, signup_channel_id, signups_open, close_at FROM signup_module_config"
        ) == [(1, 77, 1, "2026-02-01T20:00:00")]

    def test_a_record_keeps_the_window_it_was_made_in(self, db_path):
        apply(db_path, "061")

        assert _query(
            db_path,
            "SELECT id, season_id, window_id, discord_user_id, platform, approved "
            "FROM signup_records",
        ) == [(9, 1, 4, "11", "Steam", 1)]
        assert _query(db_path, "SELECT id, season_id FROM signup_windows") == [(4, 1)]

    def test_the_rest_are_carried_across(self, db_path):
        apply(db_path, "061")

        assert _query(db_path, "SELECT division_id FROM signup_division_config") == [(10,)]
        assert _query(
            db_path, "SELECT discord_user_id, wizard_state, signup_channel_id FROM signup_wizard_records"
        ) == [("11", "COLLECTING_PLATFORM", 88)]
        assert _query(db_path, "SELECT id, day_of_week, time_hhmm FROM signup_availability_slots") == [
            (3, 5, "21:00")
        ]

    def test_a_window_s_deletion_still_clears_its_records_window(self, db_path):
        """The SET NULL onto the rebuilt windows table still holds."""
        apply(db_path, "061")

        db = sqlite3.connect(db_path)
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("DELETE FROM signup_windows WHERE id = 4")
        db.commit()
        db.close()

        assert _query(db_path, "SELECT id, window_id FROM signup_records") == [(9, None)]

    def test_an_account_holds_one_wizard(self, db_path):
        apply(db_path, "061")

        db = sqlite3.connect(db_path)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO signup_wizard_records (discord_user_id) VALUES ('11')")
        db.close()


class TestDriverTables:
    """Part 6: a driver, their accounts and their history lose their server.
    
    driver_profiles is the parent of seven tables, so the rebuild runs with enforcement off; what
    is pinned here is that nothing that named a driver lost them, and that the triggers keeping a
    driver's accounts still fire on the rebuilt table."""

    @pytest.fixture
    def db_path(self, tmp_path):
        path = str(tmp_path / "pre_061.db")
        migrate_before(path, "061")
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

    def test_the_driver_and_every_account_they_held_are_carried_across(self, db_path):
        apply(db_path, "061")

        assert _query(
            db_path, "SELECT id, discord_user_id, current_state, former_driver FROM driver_profiles"
        ) == [(7, "2222", "ASSIGNED", 1)]
        assert _query(
            db_path,
            "SELECT driver_profile_id, discord_user_id FROM driver_accounts ORDER BY discord_user_id",
        ) == [(7, "1111"), (7, "2222")]

    def test_nothing_that_named_the_driver_lost_them(self, db_path):
        apply(db_path, "061")

        assert _query(db_path, "SELECT driver_profile_id FROM driver_season_assignments") == [(7,)]
        assert _query(
            db_path, "SELECT driver_profile_id, discord_user_id FROM driver_history_entries"
        ) == [(7, "1111")]
        assert _query(db_path, "PRAGMA foreign_key_check") == []

    def test_the_account_triggers_fire_on_the_rebuilt_table(self, db_path):
        apply(db_path, "061")

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

    def test_an_account_belongs_to_one_driver_across_the_league(self, db_path):
        apply(db_path, "061")

        db = sqlite3.connect(db_path)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO driver_profiles (discord_user_id, current_state) VALUES ('2222', 'UNASSIGNED')")
        db.close()


class TestTeamTables:
    """Part 7: the league's team list and its team roles lose their server."""

    @pytest.fixture
    def db_path(self, tmp_path):
        path = str(tmp_path / "pre_061.db")
        migrate_before(path, "061")
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

    def test_the_team_list_and_its_roles_are_carried_across(self, db_path):
        apply(db_path, "061")

        assert _query(
            db_path, "SELECT id, name, max_seats, is_reserve FROM default_teams ORDER BY id"
        ) == [(3, "Alpha", 2, 0), (4, "Reserve", -1, 1)]
        assert _query(db_path, "SELECT id, team_name, role_id, updated_at FROM team_role_configs") == [
            (5, "Alpha", 4242, "2026-09-17")
        ]

    def test_a_team_name_is_unique_in_the_league(self, db_path):
        apply(db_path, "061")

        db = sqlite3.connect(db_path)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO default_teams (name) VALUES ('Alpha')")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO team_role_configs (team_name, role_id) VALUES ('Alpha', 1)")
        db.close()


class TestSeasons:
    """Part 8: a season loses its server.
    
    seasons is the parent of twelve tables and the subject of four triggers, and a trigger on
    another table reads it; the rebuild runs with enforcement off and sets that trigger aside.
    Pinned here: nothing that named a season lost it, every trigger is back, and the league holds
    at most one live season."""

    @pytest.fixture
    def db_path(self, tmp_path):
        path = str(tmp_path / "pre_061.db")
        migrate_before(path, "061")
        db = sqlite3.connect(path)
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript(
            f"""
            INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id,
                                        log_channel_id) VALUES ({LEAGUE}, 1, 2, 3);
            INSERT INTO seasons (id, server_id, start_date, status, season_number, game_edition)
                VALUES (1, {LEAGUE}, '2025-01-01', 'COMPLETED', 1, 25),
                       (2, {LEAGUE}, '2026-01-01', 'ACTIVE', 2, 26);
            INSERT INTO divisions (id, season_id, name, mention_role_id) VALUES (20, 2, 'Pro', 5);
            """
        )
        db.commit()
        db.close()
        return path

    def test_every_season_is_carried_across_with_its_stage(self, db_path):
        apply(db_path, "061")

        assert _query(
            db_path, "SELECT id, status, season_number, game_edition, stage FROM seasons ORDER BY id"
        ) == [(1, "COMPLETED", 1, 25, "COMPLETED"), (2, "ACTIVE", 2, 26, "ONGOING")]

    def test_nothing_that_named_a_season_lost_it(self, db_path):
        apply(db_path, "061")

        assert _query(db_path, "SELECT id, season_id FROM divisions") == [(20, 2)]
        assert _query(db_path, "PRAGMA foreign_key_check") == []

    def test_every_trigger_is_back(self, db_path):
        before = {r[0] for r in _query(db_path, "SELECT name FROM sqlite_master WHERE type = 'trigger'")}

        apply(db_path, "061")

        after = {r[0] for r in _query(db_path, "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        assert after == before

    def test_the_stage_triggers_still_hold(self, db_path):
        apply(db_path, "061")

        db = sqlite3.connect(db_path)
        with pytest.raises(sqlite3.IntegrityError, match="stage does not match"):
            db.execute("UPDATE seasons SET stage = 'COMPLETED' WHERE id = 2")
        db.close()

    def test_the_league_holds_one_live_season(self, db_path):
        apply(db_path, "061")

        db = sqlite3.connect(db_path)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO seasons (start_date, status) VALUES ('2027-01-01', 'SETUP')")
        db.execute("INSERT INTO seasons (start_date, status) VALUES ('2024-01-01', 'CANCELLED')")
        db.close()


class TestAuditRetryAndReviewPrompt:
    """Part 9: the audit log, the retry queue and the review prompt lose their server."""

    @pytest.fixture
    def db_path(self, tmp_path):
        path = str(tmp_path / "pre_061.db")
        migrate_before(path, "061")
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

    def test_the_audit_log_is_carried_across(self, db_path):
        apply(db_path, "061")

        assert _query(
            db_path,
            "SELECT id, actor_id, actor_name, division_id, change_type, old_value, new_value, "
            "timestamp FROM audit_entries",
        ) == [(7, 42, "Toto", None, "SEASON_APPROVE", "SETUP", "ACTIVE", "2026-09-18T10:00:00")]

    def test_the_retry_queue_is_carried_across(self, db_path):
        apply(db_path, "061")

        assert _query(
            db_path,
            "SELECT id, channel_id, content, failure_reason, enqueued_at, retry_count, "
            "last_attempted_at FROM pending_messages",
        ) == [(8, 700, "hello", "503", "2026-09-18T11:00:00", 2, "2026-09-18T11:05:00")]

    def test_the_standing_review_prompt_becomes_the_leagues_one_row(self, db_path):
        apply(db_path, "061")

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

    def test_the_configuration_can_be_deleted_with_audit_rows_standing(self, db_path):
        """The foreign key onto server_configs went with the column."""
        apply(db_path, "061")

        db = sqlite3.connect(db_path)
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("DELETE FROM server_configs")
        db.commit()
        assert db.execute("SELECT COUNT(*) FROM audit_entries").fetchone() == (1,)
        db.close()

    def test_no_server_id_is_left_outside_server_configs(self, db_path):
        apply(db_path, "061")

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
