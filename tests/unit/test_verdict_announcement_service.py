"""Tests for verdict_announcement_service — translate_penalty and post helpers."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from services.verdict_announcement_service import (
    translate_penalty,
    post_penalty_announcements,
    post_appeal_announcements,
)


# ---------------------------------------------------------------------------
# translate_penalty
# ---------------------------------------------------------------------------

class TestTranslatePenalty:
    def test_positive_with_sign_and_s(self):
        assert translate_penalty("+5s") == "5 seconds added"

    def test_positive_no_sign_with_s(self):
        assert translate_penalty("5s") == "5 seconds added"

    def test_positive_no_sign_no_s(self):
        assert translate_penalty("5") == "5 seconds added"

    def test_negative_with_s(self):
        assert translate_penalty("-3s") == "3 seconds removed"

    def test_negative_no_s(self):
        assert translate_penalty("-10") == "10 seconds removed"

    def test_dsq_uppercase(self):
        assert translate_penalty("DSQ") == "Disqualified"

    def test_dsq_lowercase(self):
        assert translate_penalty("dsq") == "Disqualified"

    def test_dsq_mixed_case(self):
        assert translate_penalty("Dsq") == "Disqualified"

    def test_dsq_with_whitespace(self):
        assert translate_penalty("  DSQ  ") == "Disqualified"

    def test_large_penalty(self):
        assert translate_penalty("+30s") == "30 seconds added"

    def test_single_second_removed(self):
        assert translate_penalty("-1s") == "1 seconds removed"


# ---------------------------------------------------------------------------
# post_penalty_announcements
# ---------------------------------------------------------------------------

def _make_state(db_path: str, round_id: int = 1) -> MagicMock:
    state = MagicMock()
    state.db_path = db_path
    state.round_id = round_id
    state.division_id = 1
    return state


@pytest.mark.asyncio
async def test_post_penalty_announcements_empty_list_noop():
    """Empty applied_penalties list should not call any DB or Discord APIs."""
    import asyncio
    bot = MagicMock()
    state = _make_state("irrelevant.db")
    # Should complete without raising even though db_path is fake
    await post_penalty_announcements(bot, state, [])
    bot.get_channel.assert_not_called()


@pytest.mark.asyncio
async def test_post_penalty_announcements_skips_when_no_channel_configured(tmp_path):
    """If penalty_channel_id is NULL in DB, skip silently and don't call bot.get_channel."""
    from db.database import run_migrations, get_connection

    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1001, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (1001, '2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) VALUES (?, 'Division A', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, result_status, scheduled_at) "
            "VALUES (?, 1, 'STANDARD', 'PROVISIONAL', '2026-01-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        # division_results_config with no penalty_channel_id
        await db.execute(
            "INSERT INTO division_results_config (division_id) VALUES (?)",
            (division_id,),
        )
        await db.commit()

    bot = MagicMock()
    state = _make_state(db_path, round_id=round_id)

    fake_record = {"driver_session_result_id": 99, "penalty_type": "TIME",
                   "time_seconds": 5, "description": "test", "justification": "j"}
    await post_penalty_announcements(bot, state, [fake_record])
    # Should skip without calling bot.get_channel
    bot.get_channel.assert_not_called()


@pytest.mark.asyncio
async def test_post_penalty_announcements_skips_when_channel_inaccessible(tmp_path):
    """If bot.get_channel returns None (inaccessible), skip silently."""
    from db.database import run_migrations, get_connection

    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1001, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (1001, '2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) VALUES (?, 'Division A', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, result_status, scheduled_at) "
            "VALUES (?, 1, 'STANDARD', 'PROVISIONAL', '2026-01-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO division_results_config (division_id, penalty_channel_id) VALUES (?, 55555)",
            (division_id,),
        )
        await db.commit()

    bot = MagicMock()
    bot.get_channel.return_value = None  # channel not in cache / inaccessible

    state = _make_state(db_path, round_id=round_id)
    fake_record = {"driver_session_result_id": 99, "penalty_type": "TIME",
                   "time_seconds": 5, "description": "test", "justification": "j"}

    # Should not raise
    await post_penalty_announcements(bot, state, [fake_record])
    bot.get_channel.assert_called_once_with(55555)


@pytest.mark.asyncio
async def test_post_appeal_announcements_empty_list_noop():
    """Empty applied_corrections list should not call any DB or Discord APIs."""
    bot = MagicMock()
    state = _make_state("irrelevant.db")
    await post_appeal_announcements(bot, state, [])
    bot.get_channel.assert_not_called()
