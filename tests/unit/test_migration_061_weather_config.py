"""Migration 061: the weather horizons become the league's one row (issue #244)."""
from __future__ import annotations

import sqlite3

import pytest

from tests.support.migration_steps import apply, migrate_before

LEAGUE = 6101


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "pre_061.db")
    migrate_before(path, "061")
    return path


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


def test_the_league_s_horizons_are_carried_across(db_path):
    _seed(db_path, (LEAGUE, 9, 4, 6))

    apply(db_path, "061")

    assert _rows(db_path) == [(1, 9, 4, 6)]


def test_a_league_that_never_configured_them_still_has_none(db_path):
    _seed(db_path)

    apply(db_path, "061")

    assert _rows(db_path) == []


def test_the_server_configuration_survives_the_rebuild(db_path):
    """The table was a child of server_configs; dropping it must not reach the parent."""
    _seed(db_path, (LEAGUE, 9, 4, 6))

    apply(db_path, "061")

    db = sqlite3.connect(db_path)
    assert db.execute("SELECT server_id FROM server_configs").fetchall() == [(LEAGUE,)]
    db.close()


def test_no_server_id_is_left(db_path):
    apply(db_path, "061")

    db = sqlite3.connect(db_path)
    columns = [c[1] for c in db.execute("PRAGMA table_info(weather_pipeline_config)")]
    db.close()
    assert "server_id" not in columns
