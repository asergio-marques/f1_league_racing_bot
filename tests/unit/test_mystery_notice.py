"""Unit tests for mystery round notice (006-mystery-round-notice).

Covers NFR-003 cases:
  1. mystery_notice_message() exact content (FR-003)
  2. schedule_round for MYSTERY schedules exactly one mystery_r job (FR-001)
  3. cancel_round removes mystery_r job id (FR-002)
  4. run_mystery_notice posts to forecast channel only, not log channel (FR-005, FR-008)
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.message_builder import mystery_notice_message
from models.round import Round, RoundFormat


# ---------------------------------------------------------------------------
# 1. mystery_notice_message content (FR-003, FR-004)
# ---------------------------------------------------------------------------

class TestMysteryNoticeMessage:

    def test_exact_content(self):
        expected = (
            "🏁 **Weather Forecast**\n"
            "**Track**: Mystery\n"
            "Conditions are unknown to all — weather will be determined by the game at race time."
        )
        assert mystery_notice_message() == expected

    def test_contains_forecast_header(self):
        assert "🏁 **Weather Forecast**" in mystery_notice_message()

    def test_contains_mystery_track_line(self):
        assert "**Track**: Mystery" in mystery_notice_message()

    def test_contains_unknown_conditions_text(self):
        assert "Conditions are unknown to all" in mystery_notice_message()

    def test_no_role_mention(self):
        assert "<@&" not in mystery_notice_message()


# ---------------------------------------------------------------------------
# 2. schedule_round schedules exactly one mystery_r job for MYSTERY (FR-001)
# ---------------------------------------------------------------------------

def _make_scheduler_no_db():
    """Instantiate SchedulerService bypassing __init__ (no real APScheduler needed)."""
    from services.scheduler_service import SchedulerService
    svc = SchedulerService.__new__(SchedulerService)
    svc._phase_callbacks = {}
    svc._mystery_notice_callback = None
    svc._db_path = ":memory:"
    mock_sched = MagicMock()
    mock_sched.add_job = MagicMock()
    mock_sched.remove_job = MagicMock()
    svc._scheduler = mock_sched
    return svc


def _mystery_round(round_id: int = 42) -> Round:
    return Round(
        id=round_id,
        division_id=1,
        round_number=1,
        format=RoundFormat.MYSTERY,
        track_name=None,
        scheduled_at=datetime(2026, 6, 1, 14, 0, 0, tzinfo=timezone.utc),
    )


class TestScheduleRoundMystery:

    def test_schedules_exactly_one_job(self):
        svc = _make_scheduler_no_db()
        svc.schedule_round(_mystery_round())
        assert svc._scheduler.add_job.call_count == 1

    def test_job_id_is_mystery_r(self):
        svc = _make_scheduler_no_db()
        svc.schedule_round(_mystery_round(round_id=42))
        job_id = svc._scheduler.add_job.call_args.kwargs.get("id")
        assert job_id == "mystery_r42"

    def test_no_phase_jobs_created(self):
        svc = _make_scheduler_no_db()
        svc.schedule_round(_mystery_round())
        job_id = svc._scheduler.add_job.call_args.kwargs.get("id", "")
        assert job_id.startswith("mystery_r")
        assert "phase" not in job_id


# ---------------------------------------------------------------------------
# 3. cancel_round removes mystery_r job id (FR-002)
# ---------------------------------------------------------------------------

class TestCancelRoundIncludesMystery:

    def test_removes_mystery_job(self):
        svc = _make_scheduler_no_db()
        svc.cancel_round(7)
        removed = [c.args[0] for c in svc._scheduler.remove_job.call_args_list]
        assert "mystery_r7" in removed

    def test_still_removes_all_phase_jobs(self):
        svc = _make_scheduler_no_db()
        svc.cancel_round(7)
        removed = [c.args[0] for c in svc._scheduler.remove_job.call_args_list]
        assert "phase1_r7" in removed
        assert "phase2_r7" in removed
        assert "phase3_r7" in removed

    def test_removes_four_job_ids_total(self):
        svc = _make_scheduler_no_db()
        svc.cancel_round(7)
        # phase1, phase2, phase3, mystery, cleanup = 5 job IDs
        assert svc._scheduler.remove_job.call_count == 5


# ---------------------------------------------------------------------------
# 4. run_mystery_notice: forecast yes, log no (FR-005, FR-008)
# ---------------------------------------------------------------------------

async def _seed_mystery_round(db_path: str, round_id: int = 1) -> None:
    from db.database import get_connection
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 100, 200, 300)"
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, start_date, status) "
            "VALUES (1, 1, '2026-01-01', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO divisions "
            "(id, season_id, name, mention_role_id, forecast_channel_id) "
            "VALUES (1, 1, 'Pro', 11, 999)"
        )
        await db.execute(
            "INSERT INTO rounds "
            "(id, division_id, round_number, format, track_name, scheduled_at) "
            "VALUES (?, 1, 1, 'MYSTERY', NULL, '2026-06-01T14:00:00')",
            (round_id,),
        )
        await db.commit()


class TestRunMysteryNotice:

    async def test_posts_to_forecast_not_log(self):
        from db.database import run_migrations
        from services.mystery_notice_service import run_mystery_notice

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            await run_migrations(db_path)
            await _seed_mystery_round(db_path, round_id=1)

            mock_bot = MagicMock()
            mock_bot.db_path = db_path
            mock_bot.output_router.post_forecast = AsyncMock()
            mock_bot.output_router.post_log = AsyncMock()

            await run_mystery_notice(1, mock_bot)

            assert mock_bot.output_router.post_forecast.call_count == 1
            assert mock_bot.output_router.post_log.call_count == 0
        finally:
            os.unlink(db_path)

    async def test_posted_message_has_no_role_tag(self):
        from db.database import run_migrations
        from services.mystery_notice_service import run_mystery_notice

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            await run_migrations(db_path)
            await _seed_mystery_round(db_path, round_id=2)

            mock_bot = MagicMock()
            mock_bot.db_path = db_path
            mock_bot.output_router.post_forecast = AsyncMock()
            mock_bot.output_router.post_log = AsyncMock()

            await run_mystery_notice(2, mock_bot)

            posted_msg = mock_bot.output_router.post_forecast.call_args.args[1]
            assert "<@&" not in posted_msg
            assert "🏁" in posted_msg
            assert "Mystery" in posted_msg
        finally:
            os.unlink(db_path)

    async def test_skips_if_format_amended_to_non_mystery(self):
        """Guard: if round was amended away from MYSTERY before job fires, do nothing."""
        from db.database import run_migrations, get_connection
        from services.mystery_notice_service import run_mystery_notice

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            await run_migrations(db_path)
            await _seed_mystery_round(db_path, round_id=3)

            async with get_connection(db_path) as db:
                await db.execute("UPDATE rounds SET format = 'NORMAL' WHERE id = 3")
                await db.commit()

            mock_bot = MagicMock()
            mock_bot.db_path = db_path
            mock_bot.output_router.post_forecast = AsyncMock()
            mock_bot.output_router.post_log = AsyncMock()

            await run_mystery_notice(3, mock_bot)

            assert mock_bot.output_router.post_forecast.call_count == 0
        finally:
            os.unlink(db_path)

    async def test_skips_if_round_not_found(self):
        from db.database import run_migrations
        from services.mystery_notice_service import run_mystery_notice

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            await run_migrations(db_path)

            mock_bot = MagicMock()
            mock_bot.db_path = db_path
            mock_bot.output_router.post_forecast = AsyncMock()
            mock_bot.output_router.post_log = AsyncMock()

            # round_id 999 does not exist
            await run_mystery_notice(999, mock_bot)

            assert mock_bot.output_router.post_forecast.call_count == 0
        finally:
            os.unlink(db_path)
