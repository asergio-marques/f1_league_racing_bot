"""Unit tests for the SeasonService helpers the season's end leans on.

The season's end itself is a change on the queue (#439), tested in
`test_season_complete_change.py`, `test_season_cancel_change.py` and
`test_season_abort_change.py`.

The six tests that covered `check_and_schedule_season_end` were deleted with issue #154 along
with the function itself: an automatic season end, armed to fire seven days after the last
round, that nothing in `src/` ever called. Three of them exercised start-up recovery for a
function that was a documented no-op, so the suite reported coverage for a path that could not
run. A season is completed explicitly, by `/season complete`, and in no other way.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.season_service import SeasonService


async def _seed_server(db_path: str, server_id: int = 1) -> tuple[int, list[int]]:
    """Seed a fully active season with two rounds. Returns (season_id, round_ids)."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 100, 200, 300)",
            (server_id,),
        )
        await db.execute(
            "INSERT INTO seasons (start_date, status) "
            "VALUES ('2026-01-01', 'ACTIVE')"
        )
        cur = await db.execute("SELECT last_insert_rowid()")
        (season_id,) = await cur.fetchone()

        await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id) "
            "VALUES (?, 'Div A', 11, 21)",
            (season_id,),
        )
        cur = await db.execute("SELECT last_insert_rowid()")
        (div_id,) = await cur.fetchone()

        round_ids = []
        for i, scheduled_at in enumerate(
            ["2026-04-01T12:00:00", "2026-05-01T12:00:00"], start=1
        ):
            await db.execute(
                "INSERT INTO rounds "
                "(division_id, round_number, track_name, scheduled_at, format, "
                " phase1_done, phase2_done, phase3_done) "
                "VALUES (?, ?, 'Bahrain', ?, 'NORMAL', 0, 0, 0)",
                (div_id, i, scheduled_at),
            )
            cur = await db.execute("SELECT last_insert_rowid()")
            (rid,) = await cur.fetchone()
            round_ids.append(rid)

        await db.commit()

    return season_id, round_ids


async def _mark_all_phases_done(db_path: str, round_ids: list[int]) -> None:
    async with get_connection(db_path) as db:
        for rid in round_ids:
            await db.execute(
                "UPDATE rounds SET phase1_done = 1, phase2_done = 1, phase3_done = 1 WHERE id = ?",
                (rid,),
            )
        await db.commit()


# ---------------------------------------------------------------------------
# SeasonService helper tests
# ---------------------------------------------------------------------------

async def test_has_existing_season_true() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed_server(db_path, server_id=1)
        svc = SeasonService(db_path)
        assert await svc.has_existing_season() is True
    finally:
        os.unlink(db_path)


async def test_has_existing_season_false() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        svc = SeasonService(db_path)
        assert await svc.has_existing_season() is False
    finally:
        os.unlink(db_path)


async def test_all_phases_complete_false_when_pending() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed_server(db_path, server_id=1)
        svc = SeasonService(db_path)
        assert await svc.all_phases_complete() is False
    finally:
        os.unlink(db_path)


async def test_all_phases_complete_true_when_done() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        _, round_ids = await _seed_server(db_path, server_id=1)
        await _mark_all_phases_done(db_path, round_ids)
        svc = SeasonService(db_path)
        assert await svc.all_phases_complete() is True
    finally:
        os.unlink(db_path)


async def test_get_last_scheduled_at_returns_latest() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed_server(db_path, server_id=1)
        svc = SeasonService(db_path)
        last_at = await svc.get_last_scheduled_at()
        assert last_at is not None
        # The seeded rounds have scheduled_at '2026-04-01' and '2026-05-01'
        assert last_at.year == 2026
        assert last_at.month == 5
    finally:
        os.unlink(db_path)


async def test_get_last_scheduled_at_returns_none_for_unknown_server() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        svc = SeasonService(db_path)
        assert await svc.get_last_scheduled_at() is None
    finally:
        os.unlink(db_path)
