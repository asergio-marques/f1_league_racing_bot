"""Unit tests for reset_service.reset_server_data."""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations
from services.reset_service import reset_server_data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeScheduler:
    """Minimal scheduler stub that records cancel_round calls."""

    def __init__(self) -> None:
        self.cancelled: list[int] = []

    def cancel_round(self, round_id: int) -> None:
        self.cancelled.append(round_id)


async def _seed_full(db_path: str, *, server_id: int = 1) -> tuple[int, int, int]:
    """Seed one server with one season, two divisions, and two rounds each.

    Returns (season_id, [division_ids], [round_ids]) as flat counts.
    """
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 100, 200, 300)",
            (server_id,),
        )
        await db.execute(
            "INSERT INTO seasons (server_id, start_date, status) "
            "VALUES (?, '2026-01-01', 'ACTIVE')",
            (server_id,),
        )
        cur = await db.execute("SELECT last_insert_rowid()")
        (season_id,) = await cur.fetchone()

        division_ids = []
        for i in range(2):
            await db.execute(
                "INSERT INTO divisions "
                "(season_id, name, mention_role_id, forecast_channel_id) "
                "VALUES (?, ?, 11, 21)",
                (season_id, f"Div{i}"),
            )
            cur = await db.execute("SELECT last_insert_rowid()")
            (div_id,) = await cur.fetchone()
            division_ids.append(div_id)

        round_ids = []
        for div_id in division_ids:
            for _ in range(2):
                await db.execute(
                    "INSERT INTO rounds "
                    "(division_id, round_number, track_name, scheduled_at, format) "
                    "VALUES (?, 1, 'Bahrain', '2026-05-01T12:00:00', 'NORMAL')",
                    (div_id,),
                )
                cur = await db.execute("SELECT last_insert_rowid()")
                (rid,) = await cur.fetchone()
                round_ids.append(rid)

        await db.commit()

    return season_id, len(division_ids), len(round_ids)


async def _row_count(db_path: str, table: str, server_id: int | None = None) -> int:
    """Return the number of rows in *table*, optionally filtered by server_id."""
    async with get_connection(db_path) as db:
        if server_id is not None and table in ("server_configs", "seasons", "audit_entries"):
            cur = await db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE server_id = ?",
                (server_id,),
            )
        else:
            cur = await db.execute(f"SELECT COUNT(*) FROM {table}")
        (n,) = await cur.fetchone()
    return n


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_partial_reset_deletes_seasons_preserves_config() -> None:
    """Partial reset removes season data but keeps server_configs row."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed_full(db_path, server_id=1)

        sched = _FakeScheduler()
        result = await reset_server_data(1, db_path, sched, full=False)

        assert result["seasons_deleted"] == 1
        assert result["divisions_deleted"] == 2
        assert result["rounds_deleted"] == 4

        # Seasons, divisions, rounds must be gone
        assert await _row_count(db_path, "seasons", server_id=1) == 0
        assert await _row_count(db_path, "divisions") == 0
        assert await _row_count(db_path, "rounds") == 0

        # server_configs row must still be present
        assert await _row_count(db_path, "server_configs", server_id=1) == 1
    finally:
        os.unlink(db_path)


async def test_full_reset_deletes_server_config() -> None:
    """Full reset removes server_configs in addition to season data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed_full(db_path, server_id=1)

        sched = _FakeScheduler()
        result = await reset_server_data(1, db_path, sched, full=True)

        assert result["seasons_deleted"] == 1

        # server_configs row must be gone
        assert await _row_count(db_path, "server_configs", server_id=1) == 0
    finally:
        os.unlink(db_path)


async def test_empty_server_returns_zero_counts() -> None:
    """A server with no seasons returns all-zero counts without raising."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)

        # Seed server_config only — no seasons
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO server_configs "
                "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
                "VALUES (1, 100, 200, 300)"
            )
            await db.commit()

        sched = _FakeScheduler()
        result = await reset_server_data(1, db_path, sched, full=False)

        assert result == {"seasons_deleted": 0, "divisions_deleted": 0, "rounds_deleted": 0}
        assert sched.cancelled == []
    finally:
        os.unlink(db_path)


async def test_cancel_round_called_once_per_round() -> None:
    """scheduler.cancel_round is invoked exactly once for every round in scope."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        _, _, expected_round_count = await _seed_full(db_path, server_id=1)

        sched = _FakeScheduler()
        result = await reset_server_data(1, db_path, sched, full=False)

        assert result["rounds_deleted"] == expected_round_count
        assert len(sched.cancelled) == expected_round_count
        # Each round ID appears exactly once
        assert len(set(sched.cancelled)) == expected_round_count
    finally:
        os.unlink(db_path)


async def test_partial_reset_does_not_affect_other_server() -> None:
    """Resetting server 1 must not touch server 2's data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed_full(db_path, server_id=1)
        await _seed_full(db_path, server_id=2)

        sched = _FakeScheduler()
        await reset_server_data(1, db_path, sched, full=False)

        # Server 2 season must remain
        assert await _row_count(db_path, "seasons", server_id=2) == 1
        # Server 2 config must remain
        assert await _row_count(db_path, "server_configs", server_id=2) == 1
    finally:
        os.unlink(db_path)


async def test_transaction_rollback_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the service raises mid-transaction, no rows must be deleted."""
    import services.reset_service as rs

    original_fn = rs._ph  # save original helper

    call_count = 0

    def _patched_ph(values: list) -> str:
        nonlocal call_count
        call_count += 1
        # On the second call (deleting phase_results), blow up
        if call_count == 2:
            raise RuntimeError("simulated DB failure")
        return original_fn(values)

    monkeypatch.setattr(rs, "_ph", _patched_ph)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed_full(db_path, server_id=1)

        sched = _FakeScheduler()
        with pytest.raises(RuntimeError, match="simulated DB failure"):
            await reset_server_data(1, db_path, sched, full=False)

        # All season data must still be present (rolled back)
        assert await _row_count(db_path, "seasons", server_id=1) == 1
        assert await _row_count(db_path, "server_configs", server_id=1) == 1
    finally:
        monkeypatch.undo()
        os.unlink(db_path)


async def test_reset_deletes_forecast_messages() -> None:
    """Reset must delete forecast_messages rows before rounds to avoid FK violations.

    Bug 5 regression guard: if forecast_messages is not cleared before rounds are
    deleted, SQLite raises 'FOREIGN KEY constraint failed' and the reset aborts.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        season_id, _, _ = await _seed_full(db_path, server_id=1)

        # Retrieve a seeded round and division to use as FK targets
        async with get_connection(db_path) as db:
            cur = await db.execute(
                "SELECT r.id, r.division_id FROM rounds r "
                "JOIN divisions d ON d.id = r.division_id "
                "JOIN seasons s ON s.id = d.season_id "
                "WHERE s.server_id = 1 LIMIT 1"
            )
            round_id, division_id = await cur.fetchone()

            # Seed a forecast_messages row
            await db.execute(
                "INSERT INTO forecast_messages "
                "(round_id, division_id, phase_number, message_id, posted_at) "
                "VALUES (?, ?, 1, 999888777, '2026-05-01T12:00:00')",
                (round_id, division_id),
            )
            await db.commit()

        # Confirm it exists before reset
        assert await _row_count(db_path, "forecast_messages") == 1

        sched = _FakeScheduler()
        # Must not raise (previously raised FK constraint failed)
        await reset_server_data(1, db_path, sched, full=False)

        # forecast_messages must now be empty
        assert await _row_count(db_path, "forecast_messages") == 0
        # Season data deleted
        assert await _row_count(db_path, "seasons", server_id=1) == 0
        # Config preserved (partial reset)
        assert await _row_count(db_path, "server_configs", server_id=1) == 1
    finally:
        os.unlink(db_path)
