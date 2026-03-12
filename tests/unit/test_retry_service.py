"""Unit tests for retry_service — Feature 017.

Covers:
  - enqueue: inserts a row with correct fields
  - get_all_pending: returns rows ordered by enqueued_at
  - mark_delivered: deletes the row
  - mark_failed: increments retry_count and sets last_attempted_at
  - RETRY_WARN_THRESHOLD constant value
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations
from services.retry_service import (
    RETRY_WARN_THRESHOLD,
    enqueue,
    get_all_pending,
    mark_delivered,
    mark_failed,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

async def _make_db(tmp_path: str) -> str:
    """Run all migrations on a fresh SQLite file and return the path."""
    db_path = os.path.join(tmp_path, "test.db")
    await run_migrations(db_path)
    return db_path


async def _row_count(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM pending_messages")
        row = await cursor.fetchone()
        return row[0]


async def _fetch_all_rows(db_path: str) -> list:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id, server_id, channel_id, content, failure_reason, "
            "       enqueued_at, retry_count, last_attempted_at "
            "FROM pending_messages ORDER BY enqueued_at ASC"
        )
        return await cursor.fetchall()


# ---------------------------------------------------------------------------
# RETRY_WARN_THRESHOLD
# ---------------------------------------------------------------------------

class TestRetryWarnThreshold:
    def test_default_value(self):
        assert RETRY_WARN_THRESHOLD == 12


# ---------------------------------------------------------------------------
# enqueue
# ---------------------------------------------------------------------------

class TestEnqueue:

    @pytest.mark.asyncio
    async def test_inserts_row(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, server_id=1, channel_id=100, content="hello", failure_reason="503")
        assert await _row_count(db_path) == 1

    @pytest.mark.asyncio
    async def test_row_has_correct_fields(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        before = datetime.now(timezone.utc)
        await enqueue(db_path, server_id=42, channel_id=999, content="test msg", failure_reason="upstream error")
        rows = await _fetch_all_rows(db_path)
        assert len(rows) == 1
        r = rows[0]
        assert r["server_id"] == 42
        assert r["channel_id"] == 999
        assert r["content"] == "test msg"
        assert r["failure_reason"] == "upstream error"
        assert r["retry_count"] == 0
        assert r["last_attempted_at"] is None
        enqueued_at = datetime.fromisoformat(r["enqueued_at"])
        assert enqueued_at >= before

    @pytest.mark.asyncio
    async def test_multiple_enqueues_create_separate_rows(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 1, 100, "msg A", "err A")
        await enqueue(db_path, 1, 200, "msg B", "err B")
        assert await _row_count(db_path) == 2


# ---------------------------------------------------------------------------
# get_all_pending
# ---------------------------------------------------------------------------

class TestGetAllPending:

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_rows(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        result = await get_all_pending(db_path)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_all_rows(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 1, 100, "msg A", "err")
        await enqueue(db_path, 1, 200, "msg B", "err")
        result = await get_all_pending(db_path)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_ordered_by_enqueued_at_asc(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        # Insert two rows with manually set enqueued_at timestamps
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO pending_messages (server_id, channel_id, content, failure_reason, enqueued_at, retry_count) "
                "VALUES (1, 100, 'first', 'err', '2026-01-01T10:00:00+00:00', 0)"
            )
            await db.execute(
                "INSERT INTO pending_messages (server_id, channel_id, content, failure_reason, enqueued_at, retry_count) "
                "VALUES (1, 200, 'second', 'err', '2026-01-01T09:00:00+00:00', 0)"
            )
            await db.commit()
        result = await get_all_pending(db_path)
        assert result[0].content == "second"
        assert result[1].content == "first"

    @pytest.mark.asyncio
    async def test_pending_message_fields_populated(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, server_id=7, channel_id=77, content="hello", failure_reason="503")
        result = await get_all_pending(db_path)
        pm = result[0]
        assert pm.server_id == 7
        assert pm.channel_id == 77
        assert pm.content == "hello"
        assert pm.failure_reason == "503"
        assert pm.retry_count == 0
        assert pm.last_attempted_at is None
        assert isinstance(pm.enqueued_at, datetime)


# ---------------------------------------------------------------------------
# mark_delivered
# ---------------------------------------------------------------------------

class TestMarkDelivered:

    @pytest.mark.asyncio
    async def test_deletes_row(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 1, 100, "msg", "err")
        rows = await _fetch_all_rows(db_path)
        entry_id = rows[0]["id"]
        await mark_delivered(db_path, entry_id)
        assert await _row_count(db_path) == 0

    @pytest.mark.asyncio
    async def test_only_deletes_targeted_row(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 1, 100, "msg A", "err")
        await enqueue(db_path, 1, 200, "msg B", "err")
        rows = await _fetch_all_rows(db_path)
        first_id = rows[0]["id"]
        await mark_delivered(db_path, first_id)
        remaining = await get_all_pending(db_path)
        assert len(remaining) == 1
        assert remaining[0].content == "msg B"

    @pytest.mark.asyncio
    async def test_no_error_on_missing_id(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        # Should not raise
        await mark_delivered(db_path, 9999)


# ---------------------------------------------------------------------------
# mark_failed
# ---------------------------------------------------------------------------

class TestMarkFailed:

    @pytest.mark.asyncio
    async def test_increments_retry_count(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 1, 100, "msg", "err")
        rows = await _fetch_all_rows(db_path)
        entry_id = rows[0]["id"]

        await mark_failed(db_path, entry_id)

        rows2 = await _fetch_all_rows(db_path)
        assert rows2[0]["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_sets_last_attempted_at(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        before = datetime.now(timezone.utc)
        await enqueue(db_path, 1, 100, "msg", "err")
        rows = await _fetch_all_rows(db_path)
        entry_id = rows[0]["id"]

        await mark_failed(db_path, entry_id)

        rows2 = await _fetch_all_rows(db_path)
        last_attempted = datetime.fromisoformat(rows2[0]["last_attempted_at"])
        assert last_attempted >= before

    @pytest.mark.asyncio
    async def test_does_not_delete_row(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 1, 100, "msg", "err")
        rows = await _fetch_all_rows(db_path)
        entry_id = rows[0]["id"]
        await mark_failed(db_path, entry_id)
        assert await _row_count(db_path) == 1

    @pytest.mark.asyncio
    async def test_multiple_failures_accumulate(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 1, 100, "msg", "err")
        rows = await _fetch_all_rows(db_path)
        entry_id = rows[0]["id"]

        for _ in range(5):
            await mark_failed(db_path, entry_id)

        rows2 = await _fetch_all_rows(db_path)
        assert rows2[0]["retry_count"] == 5
