"""Unit tests for migration 055 — availability answers become durable slot IDs.

The migration translates every stored slot *position* into the slot's durable
"Mon_19_00" identity and drops the slot_sequence_id column the positions came
from. See issue #126 and the migration's own header.

The pre-055 schema is built by hand here rather than by running the migration
chain: the point of the test is what 055 does to data already in the old shape.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

_MIGRATION = os.path.join(
    os.path.dirname(__file__), "..", "..", "src", "db", "migrations",
    "055_availability_slot_durable_ids.sql",
)

_PRE_055_SCHEMA = """
CREATE TABLE server_configs (
    server_id              INTEGER PRIMARY KEY,
    interaction_role_id    INTEGER NOT NULL DEFAULT 0,
    interaction_channel_id INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE signup_availability_slots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id        INTEGER NOT NULL
                         REFERENCES server_configs(server_id)
                         ON DELETE CASCADE,
    day_of_week      INTEGER NOT NULL,
    time_hhmm        TEXT    NOT NULL,
    slot_sequence_id INTEGER,
    UNIQUE(server_id, day_of_week, time_hhmm)
);

CREATE TABLE signup_records (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id             INTEGER NOT NULL,
    discord_user_id       TEXT    NOT NULL,
    availability_slot_ids TEXT
);

CREATE TABLE signup_wizard_records (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id          INTEGER NOT NULL,
    discord_user_id    TEXT    NOT NULL,
    draft_answers_json TEXT
);
"""


@pytest.fixture
async def migrated_db(tmp_path):
    """A pre-055 database seeded with two servers, then migrated by 055.

    Server 1: Mon 19:00 (#1), Wed 20:00 (#2), Fri 21:00 (#3).
    Server 2: Tue 18:00 (#1), Thu 22:30 (#2) — its rows carry ids 4 and 5, so a
    test cannot pass by confusing a position with a surrogate primary key.
    """
    import aiosqlite

    path = str(tmp_path / "migration_055.db")
    with open(_MIGRATION, encoding="utf-8") as fh:
        migration_sql = fh.read()

    async with aiosqlite.connect(path) as db:
        await db.executescript(_PRE_055_SCHEMA)
        await db.executescript(
            """
            INSERT INTO server_configs (server_id) VALUES (1), (2);

            INSERT INTO signup_availability_slots
                (server_id, day_of_week, time_hhmm, slot_sequence_id) VALUES
                (1, 1, '19:00', 1),
                (1, 3, '20:00', 2),
                (1, 5, '21:00', 3),
                (2, 2, '18:00', 1),
                (2, 4, '22:30', 2);

            INSERT INTO signup_records
                (server_id, discord_user_id, availability_slot_ids) VALUES
                (1, 'friday_only',   '[3]'),
                (1, 'mon_and_wed',   '[1, 2]'),
                (1, 'nothing',       '[]'),
                (1, 'never_asked',   NULL),
                (1, 'stale_answer',  '[9]'),
                (2, 'other_server',  '[2]');

            INSERT INTO signup_wizard_records
                (server_id, discord_user_id, draft_answers_json) VALUES
                (2, 'mid_wizard',    '{"platform":"PC","availability_slot_ids":[1,2]}'),
                (1, 'no_slots_yet',  '{"platform":"PC"}'),
                (1, 'no_draft',      NULL);
            """
        )
        await db.commit()
        await db.executescript(migration_sql)
        await db.commit()

    return path


async def _answers(path: str, discord_user_id: str):
    import aiosqlite

    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            "SELECT availability_slot_ids FROM signup_records WHERE discord_user_id = ?",
            (discord_user_id,),
        )
        row = await cursor.fetchone()
    return row[0]


class TestRecordTranslation:
    async def test_existing_positions_translated_to_durable_ids(self, migrated_db):
        assert json.loads(await _answers(migrated_db, "friday_only")) == ["Fri_21_00"]
        assert json.loads(await _answers(migrated_db, "mon_and_wed")) == [
            "Mon_19_00", "Wed_20_00",
        ]

    async def test_translation_is_scoped_to_the_server(self, migrated_db):
        """Server 2's position 2 is Thu 22:30, not server 1's Wed 20:00."""
        assert json.loads(await _answers(migrated_db, "other_server")) == ["Thu_22_30"]

    async def test_empty_answer_stays_empty(self, migrated_db):
        assert json.loads(await _answers(migrated_db, "nothing")) == []

    async def test_absent_answer_left_null(self, migrated_db):
        assert await _answers(migrated_db, "never_asked") is None

    async def test_unmatched_position_dropped(self, migrated_db):
        """A position matching no slot is already unrecoverable; it is not invented."""
        assert json.loads(await _answers(migrated_db, "stale_answer")) == []


class TestWizardDraftTranslation:
    async def test_in_flight_wizard_draft_translated(self, migrated_db):
        import aiosqlite

        async with aiosqlite.connect(migrated_db) as db:
            cursor = await db.execute(
                "SELECT draft_answers_json FROM signup_wizard_records "
                "WHERE discord_user_id = ?",
                ("mid_wizard",),
            )
            row = await cursor.fetchone()
        draft = json.loads(row[0])
        assert draft["availability_slot_ids"] == ["Tue_18_00", "Thu_22_30"]
        assert draft["platform"] == "PC", "untouched keys must survive"

    async def test_draft_without_availability_untouched(self, migrated_db):
        import aiosqlite

        async with aiosqlite.connect(migrated_db) as db:
            cursor = await db.execute(
                "SELECT discord_user_id, draft_answers_json FROM signup_wizard_records "
                "WHERE discord_user_id IN ('no_slots_yet', 'no_draft') "
                "ORDER BY discord_user_id"
            )
            rows = await cursor.fetchall()
        assert rows[0] == ("no_draft", None)
        assert json.loads(rows[1][1]) == {"platform": "PC"}


class TestColumnRemoved:
    async def test_slot_sequence_id_column_removed(self, migrated_db):
        import aiosqlite

        async with aiosqlite.connect(migrated_db) as db:
            cursor = await db.execute("PRAGMA table_info(signup_availability_slots)")
            columns = {row[1] for row in await cursor.fetchall()}
        assert "slot_sequence_id" not in columns
        assert columns == {"id", "server_id", "day_of_week", "time_hhmm"}

    async def test_slots_survive_the_rebuild_with_their_ids(self, migrated_db):
        import aiosqlite

        async with aiosqlite.connect(migrated_db) as db:
            cursor = await db.execute(
                "SELECT id, server_id, day_of_week, time_hhmm "
                "FROM signup_availability_slots ORDER BY id"
            )
            rows = await cursor.fetchall()
        assert rows == [
            (1, 1, 1, "19:00"),
            (2, 1, 3, "20:00"),
            (3, 1, 5, "21:00"),
            (4, 2, 2, "18:00"),
            (5, 2, 4, "22:30"),
        ]
