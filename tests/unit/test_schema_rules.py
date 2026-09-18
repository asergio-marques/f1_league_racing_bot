"""The rules the schema itself holds: keys, cascades, uniqueness and triggers.

Issue #254 squashed the migration chain into one baseline and removed the tests that proved
each historic migration carried data across. Several of those tests also pinned a rule of the
schema as it stands — a cascade, a unique key, a trigger — and those rules are kept here,
against the schema `run_migrations` raises today, so that removing the history took none of
them with it.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import run_migrations  # noqa: E402

LEAGUE = 2540


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "schema.db")
    await run_migrations(path)
    db = sqlite3.connect(path)
    db.executescript(
        f"""
        INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id,
                                    log_channel_id) VALUES ({LEAGUE}, 1, 2, 3);
        INSERT INTO seasons (id, start_date, status, season_number)
            VALUES (1, '2026-01-01', 'ACTIVE', 1);
        INSERT INTO divisions (id, season_id, name, mention_role_id) VALUES (10, 1, 'Pro', 5);
        """
    )
    db.commit()
    db.close()
    return path


def _connect(db_path: str) -> sqlite3.Connection:
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA foreign_keys = ON")
    return db


def _query(db_path: str, sql: str) -> list[tuple]:
    db = sqlite3.connect(db_path)
    try:
        return db.execute(sql).fetchall()
    finally:
        db.close()


# ── One league ────────────────────────────────────────────────────────────────


async def test_no_server_id_is_left_outside_server_configs(db_path):
    """One bot serves one league (#244): the server lives in `server_configs` and nowhere else."""
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


async def test_the_configuration_can_be_deleted_with_audit_rows_standing(db_path):
    db = _connect(db_path)
    db.execute(
        "INSERT INTO audit_entries (actor_id, actor_name, change_type, old_value, new_value, "
        "timestamp) VALUES (42, 'Toto', 'SEASON_APPROVE', 'SETUP', 'ACTIVE', '2026-09-18')"
    )
    db.execute("DELETE FROM server_configs")
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM audit_entries").fetchone() == (1,)
    db.close()


def _single_row_tables() -> list[str]:
    """Every table the schema holds to one row, read from the baseline rather than listed."""
    import asyncio
    import tempfile

    with tempfile.TemporaryDirectory() as scratch:
        path = os.path.join(scratch, "probe.db")
        asyncio.run(run_migrations(path))
        rows = _query(path, "SELECT name, sql FROM sqlite_master WHERE type = 'table'")
    return sorted(name for name, sql in rows if sql and "CHECK (id = 1)" in sql)


@pytest.mark.parametrize("table", _single_row_tables())
async def test_a_single_row_table_refuses_a_second_row(db_path, table):
    """The refusal must be the `id = 1` check itself, so every column that would otherwise
    refuse the row first — NOT NULL with no default — is given a value."""
    db = _connect(db_path)
    required = [
        c[1] for c in db.execute(f"PRAGMA table_info({table})")
        if c[3] and c[4] is None and c[1] != "id"
    ]
    columns = ", ".join(["id", *required])
    values = ", ".join(["2", *("0" for _ in required)])
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed: id = 1"):
        db.execute(f"INSERT INTO {table} ({columns}) VALUES ({values})")
    db.close()


# ── Cascades ──────────────────────────────────────────────────────────────────


async def test_a_division_s_attendance_channels_go_with_the_division(db_path):
    db = _connect(db_path)
    db.execute(
        "INSERT INTO attendance_division_config (division_id, rsvp_channel_id) VALUES (10, '71')"
    )
    db.execute("DELETE FROM divisions WHERE id = 10")
    db.commit()
    db.close()

    assert _query(db_path, "SELECT * FROM attendance_division_config") == []


async def test_a_points_configuration_takes_its_points_with_it(db_path):
    db = _connect(db_path)
    db.executescript(
        """
        PRAGMA foreign_keys = ON;
        INSERT INTO points_config_store (id, config_name) VALUES (7, 'Standard');
        INSERT INTO points_config_entries (config_id, session_type, position, points)
            VALUES (7, 'FEATURE_RACE', 1, 25);
        INSERT INTO points_config_fl (config_id, session_type, fl_points, fl_position_limit)
            VALUES (7, 'FEATURE_RACE', 1, 10);
        DELETE FROM points_config_store WHERE id = 7;
        """
    )
    db.commit()
    db.close()

    assert _query(db_path, "SELECT * FROM points_config_entries") == []
    assert _query(db_path, "SELECT * FROM points_config_fl") == []


async def test_a_signup_window_s_deletion_clears_its_records_window(db_path):
    db = _connect(db_path)
    db.execute(
        "INSERT INTO signup_windows (id, season_id, selected_tracks_json) VALUES (4, 1, '[\"1\"]')"
    )
    db.execute(
        "INSERT INTO signup_records (id, season_id, window_id, discord_user_id, platform) "
        "VALUES (9, 1, 4, '11', 'Steam')"
    )
    db.execute("DELETE FROM signup_windows WHERE id = 4")
    db.commit()
    db.close()

    assert _query(db_path, "SELECT id, window_id FROM signup_records") == [(9, None)]


# ── Uniqueness ────────────────────────────────────────────────────────────────


async def test_a_points_configuration_name_is_unique(db_path):
    db = _connect(db_path)
    db.execute("INSERT INTO points_config_store (config_name) VALUES ('Standard')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO points_config_store (config_name) VALUES ('Standard')")
    db.close()


async def test_an_account_holds_one_signup_wizard(db_path):
    db = _connect(db_path)
    db.execute("INSERT INTO signup_wizard_records (discord_user_id) VALUES ('11')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO signup_wizard_records (discord_user_id) VALUES ('11')")
    db.close()


async def test_a_team_name_is_unique_in_the_league(db_path):
    db = _connect(db_path)
    db.execute("INSERT INTO default_teams (name) VALUES ('Alpha')")
    db.execute("INSERT INTO team_role_configs (team_name, role_id) VALUES ('Alpha', 1)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO default_teams (name) VALUES ('Alpha')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO team_role_configs (team_name, role_id) VALUES ('Alpha', 2)")
    db.close()


async def test_an_account_belongs_to_one_driver_across_the_league(db_path):
    db = _connect(db_path)
    db.execute(
        "INSERT INTO driver_profiles (discord_user_id, current_state) VALUES ('2222', 'UNASSIGNED')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) "
            "VALUES ('2222', 'UNASSIGNED')"
        )
    db.close()


# ── Triggers ──────────────────────────────────────────────────────────────────


async def test_a_driver_s_accounts_are_recorded_as_they_change(db_path):
    db = _connect(db_path)
    db.execute(
        "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
        "VALUES (8, '3333', 'UNASSIGNED')"
    )
    db.execute("UPDATE driver_profiles SET discord_user_id = '4444' WHERE id = 8")
    db.commit()
    db.close()

    assert _query(
        db_path,
        "SELECT discord_user_id FROM driver_accounts WHERE driver_profile_id = 8 "
        "ORDER BY discord_user_id",
    ) == [("3333",), ("4444",)]


async def test_a_season_s_stage_must_match_its_status(db_path):
    db = _connect(db_path)
    with pytest.raises(sqlite3.IntegrityError, match="stage does not match"):
        db.execute("UPDATE seasons SET stage = 'COMPLETED' WHERE id = 1")
    db.close()


async def test_the_league_holds_one_live_season(db_path):
    db = _connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO seasons (start_date, status) VALUES ('2027-01-01', 'SETUP')")
    db.execute("INSERT INTO seasons (start_date, status) VALUES ('2024-01-01', 'CANCELLED')")
    db.close()


# ── Image module defaults ─────────────────────────────────────────────────────


async def test_the_image_directory_defaults_match_the_constants(db_path):
    """Written twice — as column defaults in SQL and in `ASSET_DIRECTORIES` — and must not drift:
    a row created by `create_with_defaults` carries the SQL defaults, and the rest of the module
    reads the constants."""
    from models.image_constants import ASSET_DIRECTORIES

    db = _connect(db_path)
    db.execute("INSERT INTO image_config (id) VALUES (1)")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM image_config").fetchone()
    db.close()

    for column, (_command, default, _packaged) in ASSET_DIRECTORIES.items():
        assert row[column] == default, column
        assert default.startswith("resources/league/"), column


async def test_the_template_directory_stays_with_the_packaged_templates(db_path):
    """Templates have no packaged second tier, so a fresh install that pointed it at the
    league's empty folder could render nothing at all."""
    db = _connect(db_path)
    db.execute("INSERT INTO image_config (id) VALUES (1)")
    assert db.execute("SELECT template_directory FROM image_config").fetchone() == (
        "resources/defaults/templates",
    )
    db.close()


async def test_per_tier_colours_start_switched_off(db_path):
    db = _connect(db_path)
    db.execute("INSERT INTO image_config (id) VALUES (1)")
    assert db.execute("SELECT per_tier_colour_enabled FROM image_config").fetchone() == (0,)
    db.close()


async def test_a_tier_holds_one_colour_per_slot(db_path):
    """The primary key is what makes a second colour replace the first rather than add to it."""
    db = _connect(db_path)
    for colour in ("#111111", "#222222"):
        db.execute(
            "INSERT INTO image_tier_colour (division_slug, slot, colour) "
            "VALUES ('division_1', 'accent', ?) "
            "ON CONFLICT(division_slug, slot) DO UPDATE SET colour = excluded.colour",
            (colour,),
        )
    db.execute("INSERT INTO image_tier_colour VALUES ('division_2', 'accent', '#A78BFA')")
    db.commit()
    rows = db.execute(
        "SELECT division_slug, slot, colour FROM image_tier_colour ORDER BY division_slug"
    ).fetchall()
    db.close()

    assert rows == [("division_1", "accent", "#222222"), ("division_2", "accent", "#A78BFA")]
