"""What `/bot-reset full:True` deletes beyond the seasons, pinned table by table.

Until issue #244 a full reset reached these tables through `ON DELETE CASCADE` from
`server_configs`: deleting the configuration row took each module's configuration with it.
The rebuilds that take `server_id` out of the schema take those foreign keys with it, so
`reset_service` deletes the tables by name instead. This list is what the cascade reached on
2026-09-18, derived from the schema then; a table leaving it is a change to what a full reset
means, not a tidy-up.

The rows are seeded generically — every NOT NULL column without a default given a value of
its type — so that the test survives the rebuilds changing each table's columns.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import run_migrations  # noqa: E402
from services.reset_service import reset_server_data  # noqa: E402

SERVER_ID = 2440

#: Every table a full reset empties besides the season data, in dependency order (a parent
#: before the rows that name it).
WIPED_BY_A_FULL_RESET = (
    "attendance_config",
    "image_config",
    "image_aspect_toggles",
    "image_tier_colour",
    "points_config_store",
    "points_config_entries",
    "points_config_fl",
    "results_module_config",
    "signup_availability_slots",
    "signup_division_config",
    "signup_module_config",
    "signup_module_settings",
    "signup_windows",
    "signup_records",
    "signup_wizard_records",
    "weather_pipeline_config",
)


def _value(declared_type: str, column: str):
    if column == "server_id":
        return SERVER_ID
    kind = declared_type.upper()
    if "INT" in kind or "REAL" in kind or "NUM" in kind:
        return 1
    return "x"


def _seed(db_path: str) -> None:
    """One row in each table, with enforcement off: the parents are not the subject here."""
    db = sqlite3.connect(db_path)
    try:
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        for table in WIPED_BY_A_FULL_RESET:
            columns = db.execute(f"PRAGMA table_info({table})").fetchall()
            # cid, name, type, notnull, default, pk
            chosen = [
                c for c in columns
                if c[1] == "server_id" or (c[3] and c[4] is None) or c[5] == 1
            ]
            names = ", ".join(c[1] for c in chosen)
            marks = ", ".join("?" for _ in chosen)
            db.execute(
                f"INSERT INTO {table} ({names}) VALUES ({marks})",
                [_value(c[2], c[1]) for c in chosen],
            )
        db.commit()
    finally:
        db.close()


def _counts(db_path: str) -> dict[str, int]:
    db = sqlite3.connect(db_path)
    try:
        return {
            table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in WIPED_BY_A_FULL_RESET
        }
    finally:
        db.close()


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "reset.db")
    await run_migrations(path)
    _seed(path)
    assert set(_counts(path).values()) == {1}, "every table must start with its row"
    return path


async def test_a_full_reset_empties_every_module_s_configuration(db_path):
    await reset_server_data(SERVER_ID, db_path, MagicMock(), full=True)

    assert _counts(db_path) == {table: 0 for table in WIPED_BY_A_FULL_RESET}


async def test_a_partial_reset_keeps_every_module_s_configuration(db_path):
    await reset_server_data(SERVER_ID, db_path, MagicMock(), full=False)

    assert _counts(db_path) == {table: 1 for table in WIPED_BY_A_FULL_RESET}
