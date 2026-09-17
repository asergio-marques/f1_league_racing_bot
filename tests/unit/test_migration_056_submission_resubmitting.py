"""Migration 056: a submission channel records a resubmission in progress.

Issue #210. While a league manager re-enters a round's results, the round stays in penalty
review and keeps the results it already holds, so `in_penalty_review` and `results_posted` can
no longer say what is happening on their own. `resubmitting` is the third fact, and it has to
default to 0: every channel open when the migration runs is not resubmitting, and reading one
as though it were would let pastes into a locked review channel.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402


async def _columns(db_path: str) -> dict[str, tuple]:
    async with get_connection(db_path) as db:
        cursor = await db.execute("PRAGMA table_info(round_submission_channels)")
        return {row[1]: tuple(row) for row in await cursor.fetchall()}


async def test_the_channel_row_carries_the_resubmission_columns(tmp_path):
    db_path = os.path.join(str(tmp_path), "migration_056.db")
    await run_migrations(db_path)

    columns = await _columns(db_path)

    assert "resubmitting" in columns
    assert "resubmit_prompt_message_id" in columns


async def test_a_channel_is_not_resubmitting_unless_told_so(tmp_path):
    db_path = os.path.join(str(tmp_path), "migration_056_default.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 900, 100, 101)"
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (1, 1, 1, '2026-01-01', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (1, 1, 'Pro', 1, 555)"
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format) "
            "VALUES (1, 1, 1, '2026-02-01T18:00:00+00:00', 'NORMAL')"
        )
        await db.execute(
            "INSERT INTO round_submission_channels (round_id, channel_id, created_at) "
            "VALUES (1, 700, '2026-02-01T00:00:00+00:00')"
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT resubmitting, resubmit_prompt_message_id FROM round_submission_channels"
        )
        row = await cursor.fetchone()

    assert tuple(row) == (0, None)
