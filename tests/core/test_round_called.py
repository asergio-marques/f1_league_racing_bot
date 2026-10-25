"""How a round is named where a job may not be discarded (#439, slice 8).

The refusal of a Discard says what would follow from dropping the job, naming the round as a
league reads it: "round 3 in **Pro**". `season_service.round_called` reads that name at the press;
a round deleted meanwhile is named as no longer there rather than failing the refusal.
"""
from __future__ import annotations

import os

import pytest

from leaguebot.core.db.database import get_connection, run_migrations

SEASON_ID = 7
DIVISION_ID = 11
ROUND_ID = 21


async def _make_db(tmp_path) -> str:
    """Season 3 (id 7) with division Pro, whose round 3 is round id 21."""
    db_path = os.path.join(str(tmp_path), "round_called.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status, stage) "
            "VALUES (?, 3, '2026-11-01', 'ACTIVE', 'ONGOING')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id, status) "
            "VALUES (?, ?, 'Pro', 1, 801, 'ACTIVE')",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format) "
            "VALUES (?, ?, 3, '2026-12-01T18:00:00+00:00', 'NORMAL')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.commit()
    return db_path


async def test_a_round_is_called_by_its_number_and_its_division(tmp_path):
    """Round 3 of Pro is named "round 3 in **Pro**"."""
    from leaguebot.core.services.season_service import round_called

    db_path = await _make_db(tmp_path)

    assert await round_called(db_path, ROUND_ID) == "round 3 in **Pro**"


async def test_a_round_no_longer_there_is_called_so(tmp_path):
    """A round id the database no longer holds is named "a round no longer there"."""
    from leaguebot.core.services.season_service import round_called

    db_path = await _make_db(tmp_path)

    assert await round_called(db_path, ROUND_ID + 1) == "a round no longer there"
