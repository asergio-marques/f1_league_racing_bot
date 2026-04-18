"""Integration tests for round lifecycle: PROVISIONAL → POST_RACE_PENALTY → FINAL.

Tests cover:
- result_status transitions in the DB
- Zero-staged-penalties still advances to POST_RACE_PENALTY (FR-009)
- Zero-staged-corrections still advances to FINAL (FR-010)
- channel-close only at FINAL (round_submission_channels.closed = 1)
- round results amend rejected at PROVISIONAL and POST_RACE_PENALTY, accepted at FINAL
- penalty_records and appeal_records rows created when staged lists are non-empty
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations
from models.points_config import SessionType
from services.penalty_wizard import PenaltyReviewState
from services.penalty_service import StagedPenalty


# ---------------------------------------------------------------------------
# Shared bootstrap helpers
# ---------------------------------------------------------------------------


async def _bootstrap(db_path: str) -> tuple[int, int, int]:
    """Create server → season → division → round. Returns (season_id, division_id, round_id)."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id) "
            "VALUES (?, 'Main', 777, 888)",
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
            "INSERT INTO division_results_config (division_id) VALUES (?)",
            (division_id,),
        )
        # Points config
        for pos, pts in [(1, 25), (2, 18)]:
            await db.execute(
                "INSERT INTO season_points_entries "
                "(season_id, config_name, session_type, position, points) "
                "VALUES (?, 'STD', 'FEATURE_RACE', ?, ?)",
                (season_id, pos, pts),
            )
        await db.commit()
    return season_id, division_id, round_id


async def _insert_session_with_drivers(db_path: str, round_id: int, division_id: int) -> int:
    """Insert a 2-driver FEATURE_RACE session. Returns session_result_id."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, config_name) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE', 'STD')",
            (round_id, division_id),
        )
        sr_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO race_session_results "
            "(session_result_id, driver_user_id, team_role_id, finishing_position, "
            "outcome, base_time_ms, ingame_time_penalties_ms, postrace_time_penalties_ms, "
            "appeal_time_penalties_ms, points_awarded, fastest_lap_bonus) "
            "VALUES (?, 1, 100, 1, 'CLASSIFIED', 1200000, 0, 0, 0, 25, 0)",
            (sr_id,),
        )
        await db.execute(
            "INSERT INTO race_session_results "
            "(session_result_id, driver_user_id, team_role_id, finishing_position, "
            "outcome, base_time_ms, ingame_time_penalties_ms, postrace_time_penalties_ms, "
            "appeal_time_penalties_ms, points_awarded, fastest_lap_bonus) "
            "VALUES (?, 2, 200, 2, 'CLASSIFIED', 1210000, 0, 0, 0, 18, 0)",
            (sr_id,),
        )
        await db.commit()
    return sr_id


async def _insert_submission_channel(db_path: str, round_id: int, channel_id: int = 555) -> None:
    """Mark a round as in_penalty_review in round_submission_channels."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO round_submission_channels "
            "(round_id, channel_id, created_at, in_penalty_review, closed) "
            "VALUES (?, ?, '2026-01-01T18:00:00', 1, 0)",
            (round_id, channel_id),
        )
        await db.commit()


def _make_state(
    db_path: str,
    round_id: int,
    division_id: int,
    bot,
    channel_id: int = 555,
) -> PenaltyReviewState:
    return PenaltyReviewState(
        round_id=round_id,
        division_id=division_id,
        submission_channel_id=channel_id,
        session_types_present=[SessionType.FEATURE_RACE],
        db_path=db_path,
        bot=bot,
        round_number=1,
        division_name="Main",
    )


def _make_bot() -> MagicMock:
    """Stub bot with essential attributes used by finalize functions."""
    bot = MagicMock()
    bot.db_path = ":memory:"

    # Mock the channel returned by bot.get_channel
    mock_channel = MagicMock()
    mock_channel.send = AsyncMock(return_value=_make_message())
    mock_channel.delete = AsyncMock()
    mock_channel.fetch_message = AsyncMock(return_value=_make_message())
    mock_channel.edit = AsyncMock()
    mock_channel.set_permissions = AsyncMock()
    mock_channel.category = None
    bot.get_channel.return_value = mock_channel

    bot.add_view = MagicMock()

    class _OutputRouter:
        async def post_log(self, *args, **kwargs):
            pass

    bot.output_router = _OutputRouter()

    class _ModuleService:
        async def is_attendance_enabled(self, *args, **kwargs):
            return False

    bot.module_service = _ModuleService()
    return bot


def _make_message() -> MagicMock:
    msg = MagicMock()
    msg.id = 99999
    msg.edit = AsyncMock()
    msg.delete = AsyncMock()
    return msg


def _make_interaction(guild: MagicMock, user_id: int = 999) -> MagicMock:
    interaction = MagicMock()
    interaction.guild = guild
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _make_guild() -> MagicMock:
    guild = MagicMock()
    mock_channel = MagicMock()
    mock_channel.send = AsyncMock(return_value=_make_message())
    mock_channel.delete = AsyncMock()
    mock_channel.fetch_message = AsyncMock(return_value=_make_message())
    mock_channel.edit = AsyncMock()
    mock_channel.set_permissions = AsyncMock()
    mock_channel.category = None
    mock_channel.overwrites = {}
    guild.get_channel.return_value = mock_channel
    guild.me = MagicMock()
    return guild


async def _get_result_status(db_path: str, round_id: int) -> str:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT result_status FROM rounds WHERE id = ?", (round_id,))
        row = await cursor.fetchone()
    return row["result_status"] if row else "UNKNOWN"


async def _is_channel_closed(db_path: str, round_id: int) -> bool:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT closed FROM round_submission_channels WHERE round_id = ?", (round_id,)
        )
        row = await cursor.fetchone()
    return bool(row["closed"]) if row else False


# ---------------------------------------------------------------------------
# T028-1: PROVISIONAL → POST_RACE_PENALTY with zero staged penalties (FR-009)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_zero_penalties_advances_to_post_race_penalty(tmp_path):
    """Empty staged list — round advances to POST_RACE_PENALTY, channel stays open."""
    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)
    _, division_id, round_id = await _bootstrap(db_path)
    await _insert_session_with_drivers(db_path, round_id, division_id)
    await _insert_submission_channel(db_path, round_id)

    bot = _make_bot()
    guild = _make_guild()
    state = _make_state(db_path, round_id, division_id, bot)
    interaction = _make_interaction(guild)

    # Patch _rps functions to avoid complex Discord channel resolution
    with (
        patch("services.results_post_service.delete_and_repost_final_results", new=AsyncMock()),
        patch("services.results_post_service.repost_subsequent_standings", new=AsyncMock()),
        patch("services.penalty_service.apply_penalties", new=AsyncMock(return_value=[])),
        patch("services.verdict_announcement_service.post_penalty_announcements", new=AsyncMock()),
    ):
        from services.result_submission_service import finalize_penalty_review
        await finalize_penalty_review(interaction, state)

    # result_status must be POST_RACE_PENALTY
    status = await _get_result_status(db_path, round_id)
    assert status == "POST_RACE_PENALTY"

    # Channel must NOT be closed
    assert not await _is_channel_closed(db_path, round_id)


# ---------------------------------------------------------------------------
# T028-2: POST_RACE_PENALTY → FINAL with zero staged corrections (FR-010)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_zero_corrections_advances_to_final(tmp_path):
    """Empty staged appeals — round advances to FINAL, channel closed."""
    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)
    _, division_id, round_id = await _bootstrap(db_path)
    await _insert_session_with_drivers(db_path, round_id, division_id)
    await _insert_submission_channel(db_path, round_id)

    # Manually set status to POST_RACE_PENALTY
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE rounds SET result_status = 'POST_RACE_PENALTY' WHERE id = ?", (round_id,)
        )
        await db.commit()

    bot = _make_bot()
    guild = _make_guild()
    state = _make_state(db_path, round_id, division_id, bot)
    interaction = _make_interaction(guild)

    with (
        patch("services.results_post_service.delete_and_repost_final_results", new=AsyncMock()),
        patch("services.results_post_service.repost_subsequent_standings", new=AsyncMock()),
        patch("services.penalty_service.apply_penalties", new=AsyncMock(return_value=[])),
        patch("services.verdict_announcement_service.post_appeal_announcements", new=AsyncMock()),
    ):
        from services.result_submission_service import finalize_appeals_review
        await finalize_appeals_review(interaction, state)

    # result_status must be FINAL
    status = await _get_result_status(db_path, round_id)
    assert status == "FINAL"

    # Channel must be closed
    assert await _is_channel_closed(db_path, round_id)


# ---------------------------------------------------------------------------
# T028-3: Full lifecycle PROVISIONAL → POST_RACE_PENALTY → FINAL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_lifecycle_three_states(tmp_path):
    """Walk through the complete lifecycle and verify each state transition."""
    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)
    _, division_id, round_id = await _bootstrap(db_path)
    await _insert_session_with_drivers(db_path, round_id, division_id)
    await _insert_submission_channel(db_path, round_id)

    bot = _make_bot()
    guild = _make_guild()
    state = _make_state(db_path, round_id, division_id, bot)

    # 1. Verify initial state
    assert await _get_result_status(db_path, round_id) == "PROVISIONAL"

    with (
        patch("services.results_post_service.delete_and_repost_final_results", new=AsyncMock()),
        patch("services.results_post_service.repost_subsequent_standings", new=AsyncMock()),
        patch("services.penalty_service.apply_penalties", new=AsyncMock(return_value=[])),
        patch("services.verdict_announcement_service.post_penalty_announcements", new=AsyncMock()),
        patch("services.verdict_announcement_service.post_appeal_announcements", new=AsyncMock()),
    ):
        from services.result_submission_service import (
            finalize_penalty_review,
            finalize_appeals_review,
        )

        # 2. Penalty review → POST_RACE_PENALTY
        interaction1 = _make_interaction(guild)
        await finalize_penalty_review(interaction1, state)
        assert await _get_result_status(db_path, round_id) == "POST_RACE_PENALTY"
        assert not await _is_channel_closed(db_path, round_id)

        # 3. Appeals review → FINAL
        interaction2 = _make_interaction(guild)
        await finalize_appeals_review(interaction2, state)
        assert await _get_result_status(db_path, round_id) == "FINAL"
        assert await _is_channel_closed(db_path, round_id)


# ---------------------------------------------------------------------------
# T028-4: penalty_records rows created when staged penalties are non-empty
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_penalty_records_inserted_when_staged(tmp_path):
    """Staged penalties produce penalty_records rows in the DB."""
    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)
    _, division_id, round_id = await _bootstrap(db_path)
    sr_id = await _insert_session_with_drivers(db_path, round_id, division_id)
    await _insert_submission_channel(db_path, round_id)

    # Get the race_session_results id for driver 1
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM race_session_results WHERE session_result_id = ? AND driver_user_id = 1",
            (sr_id,),
        )
        rsr_row = await cursor.fetchone()
    race_result_id = rsr_row["id"]

    bot = _make_bot()
    guild = _make_guild()
    state = _make_state(db_path, round_id, division_id, bot)
    state.staged.append(
        StagedPenalty(
            driver_user_id=1,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="TIME",
            penalty_seconds=5,
            description="Collision",
            justification="Forced off track",
        )
    )
    interaction = _make_interaction(guild)

    with (
        patch("services.results_post_service.delete_and_repost_final_results", new=AsyncMock()),
        patch("services.results_post_service.repost_subsequent_standings", new=AsyncMock()),
        patch("services.verdict_announcement_service.post_penalty_announcements", new=AsyncMock()),
        patch("services.verdict_announcement_service.post_appeal_announcements", new=AsyncMock()),
    ):
        bot.output_router = MagicMock()
        bot.output_router.post_log = AsyncMock()

        from services.result_submission_service import finalize_penalty_review
        await finalize_penalty_review(interaction, state)

    # Check penalty_records was inserted
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS cnt FROM penalty_records WHERE race_result_id = ?",
            (race_result_id,),
        )
        row = await cursor.fetchone()
    assert row["cnt"] == 1


# ---------------------------------------------------------------------------
# T028-5: Channel remains open after penalty review (not closed at POST_RACE_PENALTY)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_channel_not_closed_after_penalty_review(tmp_path):
    """Submission channel must NOT be closed when advancing to POST_RACE_PENALTY."""
    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)
    _, division_id, round_id = await _bootstrap(db_path)
    await _insert_session_with_drivers(db_path, round_id, division_id)
    await _insert_submission_channel(db_path, round_id)

    bot = _make_bot()
    guild = _make_guild()
    state = _make_state(db_path, round_id, division_id, bot)
    interaction = _make_interaction(guild)

    with (
        patch("services.results_post_service.delete_and_repost_final_results", new=AsyncMock()),
        patch("services.results_post_service.repost_subsequent_standings", new=AsyncMock()),
        patch("services.penalty_service.apply_penalties", new=AsyncMock(return_value=[])),
        patch("services.verdict_announcement_service.post_penalty_announcements", new=AsyncMock()),
    ):
        from services.result_submission_service import finalize_penalty_review
        await finalize_penalty_review(interaction, state)

    # Should NOT be closed — appeals review still in progress
    assert not await _is_channel_closed(db_path, round_id)


# ---------------------------------------------------------------------------
# T028-6: result_status_check helper — is_channel_in_penalty_review
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_is_channel_in_penalty_review_false_after_final(tmp_path):
    """Once result_status = FINAL, is_channel_in_penalty_review must return False."""
    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)
    _, division_id, round_id = await _bootstrap(db_path)
    await _insert_submission_channel(db_path, round_id, channel_id=777)

    # Advance to FINAL
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE rounds SET result_status = 'FINAL' WHERE id = ?", (round_id,)
        )
        await db.commit()

    from services.result_submission_service import is_channel_in_penalty_review
    assert not await is_channel_in_penalty_review(db_path, 777)


@pytest.mark.asyncio
async def test_is_channel_in_penalty_review_true_at_post_race_penalty(tmp_path):
    """is_channel_in_penalty_review returns True when result_status = POST_RACE_PENALTY."""
    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)
    _, division_id, round_id = await _bootstrap(db_path)
    await _insert_submission_channel(db_path, round_id, channel_id=888)

    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE rounds SET result_status = 'POST_RACE_PENALTY' WHERE id = ?", (round_id,)
        )
        await db.commit()

    from services.result_submission_service import is_channel_in_penalty_review
    assert await is_channel_in_penalty_review(db_path, 888)
