"""Every message a posting occupies is recorded, not just its first (#345, #189).

A results table, a standings table or a batch of verdicts past Discord's 2000-character limit is
split across consecutive messages. Only the **anchor** was ever stored, so deleting such a posting
had to guess at the rest: the deletion walked forward from the anchor taking every message the
bot authored, stopping at someone else's.

That heuristic was wrong as soon as two of the bot's own postings sat next to each other — which is
exactly what the amendment replay produces, because it posts the replacement **before** destroying
the original (Constitution XIV.8). Deleting the old anchor walked straight into the new posting and
deleted it. The columns here are what retired the walk (rule 11 of #345); `_delete_posting` now
removes what was recorded and nothing else.

The columns pinned here are the fix's foundation: the ids are captured at send time, so deletion
removes what posting actually created rather than what adjacency suggests. They are nullable
because a row written before they existed has no list to offer, and those rows keep the walk.

`announcement_message_id` on the two verdict tables is #189's own requirement: without it the bot
holds nothing by which to find a verdict it announced, so an amendment leaves a decision standing
that contradicts the classification it was applied to.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402

#: table -> the columns this change added to it.
_ADDED: dict[str, tuple[str, ...]] = {
    "session_results": ("results_message_ids",),
    "driver_standings_snapshots": (
        "standings_message_ids",
        "constructor_standings_message_ids",
    ),
    "penalty_records": ("announcement_message_id", "announcement_message_ids"),
    "appeal_records": ("announcement_message_id", "announcement_message_ids"),
}


async def _columns(db_path: str, table: str) -> dict[str, dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(f"PRAGMA table_info({table})")
        return {row["name"]: dict(row) for row in await cursor.fetchall()}


async def test_every_posting_table_records_its_chunk_ids(tmp_path):
    """The columns exist, on all four tables, after a plain migration."""
    db_path = os.path.join(str(tmp_path), "schema.db")
    await run_migrations(db_path)

    for table, added in _ADDED.items():
        columns = await _columns(db_path, table)
        for column in added:
            assert column in columns, f"{table}.{column} is missing"


async def test_the_new_columns_are_nullable(tmp_path):
    """A row written before this change has no list to offer, and must still be writable.

    Those rows fall back to the adjacency walk. Requiring a value would make the change a
    migration of existing data rather than an addition to it.
    """
    db_path = os.path.join(str(tmp_path), "nullable.db")
    await run_migrations(db_path)

    for table, added in _ADDED.items():
        columns = await _columns(db_path, table)
        for column in added:
            assert columns[column]["notnull"] == 0, f"{table}.{column} is NOT NULL"


async def test_a_chunk_list_defaults_to_null_rather_than_an_empty_list(tmp_path):
    """NULL and "no chunks recorded" must stay distinguishable.

    An empty JSON array would read as "this posting occupies no messages", which is a claim, and
    a delete honouring it would remove nothing and report success. NULL says only that nothing
    was recorded, which is what sends the caller to the fallback.
    """
    db_path = os.path.join(str(tmp_path), "default.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (1, 1, '2026-01-01', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (1, 1, 'Pro', 1, 555)"
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, status) "
            "VALUES (1, 1, 1, '2026-02-01T18:00:00+00:00', 'NORMAL', 'FINAL')"
        )
        await db.execute(
            "INSERT INTO session_results (id, round_id, division_id, session_type, status) "
            "VALUES (1, 1, 1, 'FEATURE_RACE', 'ACTIVE')"
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT results_message_ids FROM session_results WHERE id = 1"
        )
        assert (await cursor.fetchone())["results_message_ids"] is None
