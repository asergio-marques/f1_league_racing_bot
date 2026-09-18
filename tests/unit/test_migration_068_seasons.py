"""Migration 068: a season loses its server (issue #244).

seasons is the parent of twelve tables and the subject of four triggers, and a trigger on
another table reads it; the rebuild runs with enforcement off and sets that trigger aside.
Pinned here: nothing that named a season lost it, every trigger is back, and the league holds
at most one live season.
"""
from __future__ import annotations

import sqlite3

import pytest

from tests.support.migration_steps import apply, migrate_before

LEAGUE = 6801


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "pre_068.db")
    migrate_before(path, "068")
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


def _query(db_path, sql):
    db = sqlite3.connect(db_path)
    try:
        return db.execute(sql).fetchall()
    finally:
        db.close()


def test_every_season_is_carried_across_with_its_stage(db_path):
    apply(db_path, "068")

    assert _query(
        db_path, "SELECT id, status, season_number, game_edition, stage FROM seasons ORDER BY id"
    ) == [(1, "COMPLETED", 1, 25, "COMPLETED"), (2, "ACTIVE", 2, 26, "ONGOING")]


def test_nothing_that_named_a_season_lost_it(db_path):
    apply(db_path, "068")

    assert _query(db_path, "SELECT id, season_id FROM divisions") == [(20, 2)]
    assert _query(db_path, "PRAGMA foreign_key_check") == []


def test_every_trigger_is_back(db_path):
    before = {r[0] for r in _query(db_path, "SELECT name FROM sqlite_master WHERE type = 'trigger'")}

    apply(db_path, "068")

    after = {r[0] for r in _query(db_path, "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert after == before


def test_the_stage_triggers_still_hold(db_path):
    apply(db_path, "068")

    db = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError, match="stage does not match"):
        db.execute("UPDATE seasons SET stage = 'COMPLETED' WHERE id = 2")
    db.close()


def test_the_league_holds_one_live_season(db_path):
    apply(db_path, "068")

    db = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO seasons (start_date, status) VALUES ('2027-01-01', 'SETUP')")
    db.execute("INSERT INTO seasons (start_date, status) VALUES ('2024-01-01', 'CANCELLED')")
    db.close()


def test_no_server_id_is_left(db_path):
    apply(db_path, "068")

    columns = [c[1] for c in _query(db_path, "PRAGMA table_info(seasons)")]
    assert "server_id" not in columns
