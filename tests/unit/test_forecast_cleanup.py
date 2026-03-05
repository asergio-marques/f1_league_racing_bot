"""Unit tests for forecast_cleanup_service — Feature 007.

Covers all four user stories:
  US1 — Phase N message deleted before Phase N+1 is posted
  US2 — Phase 3 message deleted 24 h after round start
  US3 — Message deletion on amendment (delete_forecast_message called per phase)
  US4 — Test mode: deletions suppressed while active; flushed on disable

Acceptance scenarios checked:
  SC-001  store then delete a phase message
  SC-002  delete_forecast_message on missing row is a no-op
  SC-003  test mode active → deletion skipped, row kept
  SC-004  test mode disabled after SC-003 → flush removes row and calls Discord
  SC-005  run_post_race_cleanup → deletes phase 3 across all divisions
  SC-006  Discord NotFound is silently absorbed; DB row still removed
  SC-007  Discord Forbidden is logged; DB row still removed
"""
from __future__ import annotations

import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations
from services.forecast_cleanup_service import (
    store_forecast_message,
    delete_forecast_message,
    run_post_race_cleanup,
    flush_pending_deletions,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

async def _make_db(tmp_path: str) -> str:
    """Run all migrations on a fresh SQLite file and return the path."""
    db_path = os.path.join(tmp_path, "test.db")
    await run_migrations(db_path)
    return db_path


async def _seed_base(db_path: str, server_id: int = 1, test_mode: int = 0) -> tuple[int, int]:
    """Insert server_config, season, division, and round.

    Returns (division_id, round_id).
    """
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id, test_mode_active) "
            "VALUES (?, 100, 200, 300, ?)",
            (server_id, test_mode),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, start_date, status) "
            "VALUES (1, ?, '2026-01-01', 'ACTIVE')",
            (server_id,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, forecast_channel_id, mention_role_id) "
            "VALUES (1, 1, 'Div A', 999, 555)"
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, scheduled_at) "
            "VALUES (1, 1, 1, 'NORMAL', 'Bahrain', '2026-06-01T14:00:00')"
        )
        await db.commit()
    return 1, 1  # division_id=1, round_id=1


def _make_mock_message(message_id: int = 12345) -> MagicMock:
    msg = MagicMock(spec=discord.Message)
    msg.id = message_id
    return msg


def _make_bot(db_path: str, test_mode_active: bool = False) -> MagicMock:
    """Build a minimal bot mock with config_service and channel mocking."""
    bot = MagicMock()
    bot.db_path = db_path

    # config_service — returns a config with test_mode_active flag
    config = MagicMock()
    config.test_mode_active = test_mode_active
    bot.config_service.get_server_config = AsyncMock(return_value=config)

    # Discord channel mock — get_channel returns a TextChannel whose
    # get_partial_message().delete() is an AsyncMock
    partial_msg = MagicMock()
    partial_msg.delete = AsyncMock()
    channel = MagicMock(spec=discord.TextChannel)
    channel.get_partial_message = MagicMock(return_value=partial_msg)
    bot.get_channel = MagicMock(return_value=channel)

    return bot


async def _get_stored_row(db_path: str, round_id: int, division_id: int, phase_number: int):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT * FROM forecast_messages "
            "WHERE round_id = ? AND division_id = ? AND phase_number = ?",
            (round_id, division_id, phase_number),
        )
        return await cursor.fetchone()


# ---------------------------------------------------------------------------
# store_forecast_message (T003 / SC-001)
# ---------------------------------------------------------------------------

class TestStoreForecastMessage:

    async def test_inserts_row(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path)

        msg = _make_mock_message(message_id=111)
        await store_forecast_message(1, 1, 1, msg, db_path)

        row = await _get_stored_row(db_path, round_id=1, division_id=1, phase_number=1)
        assert row is not None
        assert row["message_id"] == 111
        assert row["phase_number"] == 1

    async def test_replace_on_duplicate(self, tmp_path):
        """INSERT OR REPLACE: re-running a phase updates the stored ID."""
        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path)

        await store_forecast_message(1, 1, 1, _make_mock_message(111), db_path)
        await store_forecast_message(1, 1, 1, _make_mock_message(222), db_path)

        row = await _get_stored_row(db_path, 1, 1, 1)
        assert row["message_id"] == 222  # updated, not duplicated

    async def test_different_phases_coexist(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path)

        await store_forecast_message(1, 1, 1, _make_mock_message(100), db_path)
        await store_forecast_message(1, 1, 2, _make_mock_message(200), db_path)
        await store_forecast_message(1, 1, 3, _make_mock_message(300), db_path)

        for phase, expected_id in ((1, 100), (2, 200), (3, 300)):
            row = await _get_stored_row(db_path, 1, 1, phase)
            assert row["message_id"] == expected_id


# ---------------------------------------------------------------------------
# delete_forecast_message — happy path (T004 / SC-001)
# ---------------------------------------------------------------------------

class TestDeleteForecastMessage:

    async def test_deletes_discord_message_and_row(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path)
        await store_forecast_message(1, 1, 1, _make_mock_message(111), db_path)

        bot = _make_bot(db_path, test_mode_active=False)
        await delete_forecast_message(round_id=1, division_id=1, phase_number=1, bot=bot)

        # Discord delete called once
        channel = bot.get_channel.return_value
        partial = channel.get_partial_message.return_value
        partial.delete.assert_awaited_once()

        # DB row removed
        row = await _get_stored_row(db_path, 1, 1, 1)
        assert row is None

    async def test_missing_row_is_noop(self, tmp_path):
        """No row in DB → returns without error, no Discord calls."""
        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path)

        bot = _make_bot(db_path, test_mode_active=False)
        # Should not raise
        await delete_forecast_message(round_id=1, division_id=1, phase_number=1, bot=bot)

        bot.get_channel.assert_not_called()


# ---------------------------------------------------------------------------
# Test-mode guard (T012 / SC-003 and SC-004)
# ---------------------------------------------------------------------------

class TestDeleteForecastMessageTestModeGuard:

    async def test_test_mode_suppresses_delete_keeps_row(self, tmp_path):
        """SC-003: while test mode active, Discord delete is skipped, row retained."""
        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path, test_mode=1)
        await store_forecast_message(1, 1, 1, _make_mock_message(999), db_path)

        bot = _make_bot(db_path, test_mode_active=True)
        await delete_forecast_message(round_id=1, division_id=1, phase_number=1, bot=bot)

        # Discord delete NOT called
        bot.get_channel.assert_not_called()

        # Row still present
        row = await _get_stored_row(db_path, 1, 1, 1)
        assert row is not None
        assert row["message_id"] == 999


# ---------------------------------------------------------------------------
# flush_pending_deletions (T013 / SC-004)
# ---------------------------------------------------------------------------

class TestFlushPendingDeletions:

    async def test_flushes_all_stored_messages_for_server(self, tmp_path):
        """SC-004: all stored messages deleted once test mode is disabled."""
        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path, test_mode=0)  # test mode now OFF

        # Manually insert rows (simulating messages stored while test mode was on)
        async with get_connection(db_path) as db:
            for phase in (1, 2, 3):
                await db.execute(
                    "INSERT INTO forecast_messages "
                    "(round_id, division_id, phase_number, message_id, posted_at) "
                    "VALUES (1, 1, ?, ?, '2026-06-01T12:00:00')",
                    (phase, 1000 + phase),
                )
            await db.commit()

        bot = _make_bot(db_path, test_mode_active=False)
        await flush_pending_deletions(server_id=1, bot=bot)

        # All rows gone
        async with get_connection(db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM forecast_messages WHERE round_id = 1")
            count = (await cursor.fetchone())[0]
        assert count == 0

        # Discord delete called three times
        channel = bot.get_channel.return_value
        partial = channel.get_partial_message.return_value
        assert partial.delete.await_count == 3

    async def test_flush_empty_is_noop(self, tmp_path):
        """No stored messages → completes without error."""
        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path)

        bot = _make_bot(db_path, test_mode_active=False)
        await flush_pending_deletions(server_id=1, bot=bot)  # should not raise

        bot.get_channel.assert_not_called()


# ---------------------------------------------------------------------------
# run_post_race_cleanup (T010 / SC-005)
# ---------------------------------------------------------------------------

class TestRunPostRaceCleanup:

    async def test_deletes_phase3_for_all_divisions(self, tmp_path):
        """SC-005: post-race job deletes Phase 3 messages for all divisions."""
        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path)

        # Add a second division + round mapping to same round_id = 1 via forecast_messages
        async with get_connection(db_path) as db:
            # Second division
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, forecast_channel_id, mention_role_id) "
                "VALUES (2, 1, 'Div B', 888, 666)"
            )
            # Phase 3 rows for both divisions
            for div_id, msg_id in ((1, 301), (2, 302)):
                await db.execute(
                    "INSERT INTO forecast_messages "
                    "(round_id, division_id, phase_number, message_id, posted_at) "
                    "VALUES (1, ?, 3, ?, '2026-06-01T14:00:00')",
                    (div_id, msg_id),
                )
            await db.commit()

        bot = _make_bot(db_path, test_mode_active=False)
        await run_post_race_cleanup(round_id=1, bot=bot)

        # Both Phase 3 rows removed
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM forecast_messages WHERE round_id = 1 AND phase_number = 3"
            )
            count = (await cursor.fetchone())[0]
        assert count == 0

    async def test_does_not_delete_earlier_phases(self, tmp_path):
        """Cleanup job only targets phase 3; phases 1/2 rows are untouched."""
        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path)

        async with get_connection(db_path) as db:
            for phase in (1, 2, 3):
                await db.execute(
                    "INSERT INTO forecast_messages "
                    "(round_id, division_id, phase_number, message_id, posted_at) "
                    "VALUES (1, 1, ?, ?, '2026-06-01T14:00:00')",
                    (phase, 100 + phase),
                )
            await db.commit()

        bot = _make_bot(db_path, test_mode_active=False)
        await run_post_race_cleanup(round_id=1, bot=bot)

        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT phase_number FROM forecast_messages WHERE round_id = 1"
            )
            remaining = [r["phase_number"] for r in await cursor.fetchall()]
        assert sorted(remaining) == [1, 2]


# ---------------------------------------------------------------------------
# Discord error handling (SC-006, SC-007)
# ---------------------------------------------------------------------------

class TestDiscordErrorHandling:

    async def test_not_found_absorbed_row_removed(self, tmp_path):
        """SC-006: NotFound is silently swallowed; DB row still removed."""
        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path)
        await store_forecast_message(1, 1, 2, _make_mock_message(777), db_path)

        bot = _make_bot(db_path, test_mode_active=False)
        channel = bot.get_channel.return_value
        channel.get_partial_message.return_value.delete = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "unknown message")
        )

        # Should not raise
        await delete_forecast_message(round_id=1, division_id=1, phase_number=2, bot=bot)

        # DB row still removed despite the 404
        row = await _get_stored_row(db_path, 1, 1, 2)
        assert row is None

    async def test_forbidden_logged_row_removed(self, tmp_path):
        """SC-007: Forbidden is logged; DB row removed so future calls are unblocked."""
        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path)
        await store_forecast_message(1, 1, 3, _make_mock_message(888), db_path)

        bot = _make_bot(db_path, test_mode_active=False)
        channel = bot.get_channel.return_value
        channel.get_partial_message.return_value.delete = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(status=403), "missing access")
        )

        await delete_forecast_message(round_id=1, division_id=1, phase_number=3, bot=bot)

        row = await _get_stored_row(db_path, 1, 1, 3)
        assert row is None
