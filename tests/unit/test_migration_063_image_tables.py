"""Migration 063: the image module's tables lose their server (issue #244)."""
from __future__ import annotations

import sqlite3

import pytest

from tests.support.migration_steps import apply, migrate_before

LEAGUE = 6301


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "pre_063.db")
    migrate_before(path, "063")
    db = sqlite3.connect(path)
    db.execute(
        "INSERT INTO server_configs (server_id, interaction_role_id, "
        "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
        (LEAGUE,),
    )
    db.commit()
    db.close()
    return path


def _execute(db_path, *statements):
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA foreign_keys = ON")
    for sql, params in statements:
        db.execute(sql, params)
    db.commit()
    db.close()


def _query(db_path, sql):
    db = sqlite3.connect(db_path)
    try:
        return db.execute(sql).fetchall()
    finally:
        db.close()


def test_the_configuration_and_its_toggles_are_carried_across(db_path):
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

    apply(db_path, "063")

    assert _query(
        db_path,
        "SELECT id, module_enabled, time_zone, use_pfp, per_tier_colour_enabled, "
        "verdict_banner_template FROM image_config",
    ) == [(1, 1, "Europe/Lisbon", 1, 1, "banner.svg")]
    assert _query(db_path, "SELECT aspect, enabled FROM image_aspect_toggles ORDER BY aspect") == [
        ("lineup", 1),
        ("rsvp", 0),
    ]


def test_every_configured_column_survives(db_path):
    """The rebuild names its columns; one it forgot would read back as the default."""
    before = [c[1] for c in _query(db_path, "PRAGMA table_info(image_config)")]
    apply(db_path, "063")
    after = [c[1] for c in _query(db_path, "PRAGMA table_info(image_config)")]

    assert sorted(set(before) - {"server_id"}) == sorted(set(after) - {"id"})


def test_the_tier_colours_and_portraits_are_carried_across(db_path):
    _execute(
        db_path,
        ("INSERT INTO image_tier_colour (server_id, division_slug, slot, colour) "
         "VALUES (?, 'pro', 'accent', '#FFFFFF')", (LEAGUE,)),
        ("INSERT INTO driver_portraits (server_id, discord_user_id, avatar_key, fetched_at) "
         "VALUES (?, '11', 'k', '2026-01-01')", (LEAGUE,)),
    )

    apply(db_path, "063")

    assert _query(db_path, "SELECT division_slug, slot, colour FROM image_tier_colour") == [
        ("pro", "accent", "#FFFFFF")
    ]
    assert _query(db_path, "SELECT discord_user_id, avatar_key FROM driver_portraits") == [
        ("11", "k")
    ]


def test_no_server_id_is_left(db_path):
    apply(db_path, "063")

    for table in ("image_config", "image_aspect_toggles", "image_tier_colour", "driver_portraits"):
        columns = [c[1] for c in _query(db_path, f"PRAGMA table_info({table})")]
        assert "server_id" not in columns, table
