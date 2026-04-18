"""Unit tests for mystery round notice (006-mystery-round-notice).

Covers NFR-003 cases:
  1. mystery_notice_message() exact content (FR-003)
  2. schedule_round for MYSTERY schedules weather phase jobs (FR-001)
  3. cancel_round removes all round jobs via kwargs lookup (FR-002)
  4. run_mystery_notice posts to forecast channel only, not log channel (FR-005, FR-008)
  5. schedule_attendance_round treats MYSTERY identically to other formats (RSVP jobs)
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
# Helpers
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
    mock_sched.get_jobs = MagicMock(return_value=[])
    svc._scheduler = mock_sched
    return svc


def _make_mock_job(job_id: str, round_id: int) -> MagicMock:
    """Create a mock APScheduler job with the given id and round_id kwarg."""
    job = MagicMock()
    job.id = job_id
    job.kwargs = {"round_id": round_id}
    job.next_run_time = datetime(2099, 1, 1, tzinfo=timezone.utc)
    return job


def _mystery_round(round_id: int = 42) -> Round:
    return Round(
        id=round_id,
        division_id=1,
        round_number=1,
        format=RoundFormat.MYSTERY,
        track_name=None,
        scheduled_at=datetime(2026, 6, 1, 14, 0, 0, tzinfo=timezone.utc),
    )


def _future_round(round_id: int, fmt: RoundFormat) -> Round:
    """Round scheduled far in the future so all RSVP job fire-times are still pending."""
    return Round(
        id=round_id,
        division_id=1,
        round_number=1,
        format=fmt,
        track_name=None if fmt == RoundFormat.MYSTERY else "Bahrain",
        scheduled_at=datetime(2099, 6, 1, 14, 0, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# 2. schedule_round schedules weather phase jobs for MYSTERY (FR-001)
# ---------------------------------------------------------------------------

class TestScheduleRoundMystery:

    def test_schedules_exactly_five_jobs(self):
        # All rounds (including MYSTERY) schedule 5 jobs:
        # weather_p1, weather_p2, weather_p3, cleanup, results
        svc = _make_scheduler_no_db()
        svc.schedule_round(_mystery_round(), season_number=1, division_tier=1)
        assert svc._scheduler.add_job.call_count == 5

    def test_weather_p1_job_is_scheduled(self):
        # weather_p1 replaces the old mystery_r job; it checks format at runtime
        svc = _make_scheduler_no_db()
        svc.schedule_round(_mystery_round(round_id=42), season_number=3, division_tier=2)
        job_ids = [c.kwargs.get("id", "") for c in svc._scheduler.add_job.call_args_list]
        assert any("weather_p1" in jid for jid in job_ids)

    def test_no_legacy_mystery_r_job(self):
        svc = _make_scheduler_no_db()
        svc.schedule_round(_mystery_round(), season_number=1, division_tier=1)
        job_ids = [c.kwargs.get("id", "") for c in svc._scheduler.add_job.call_args_list]
        assert not any(jid.startswith("mystery_r") for jid in job_ids)

    def test_all_three_weather_phase_jobs_created(self):
        svc = _make_scheduler_no_db()
        svc.schedule_round(_mystery_round(), season_number=1, division_tier=1)
        job_ids = [c.kwargs.get("id", "") for c in svc._scheduler.add_job.call_args_list]
        assert any("weather_p1" in jid for jid in job_ids)
        assert any("weather_p2" in jid for jid in job_ids)
        assert any("weather_p3" in jid for jid in job_ids)

    def test_round_id_in_kwargs_for_all_jobs(self):
        svc = _make_scheduler_no_db()
        svc.schedule_round(_mystery_round(round_id=42), season_number=1, division_tier=1)
        for call in svc._scheduler.add_job.call_args_list:
            assert call.kwargs["kwargs"]["round_id"] == 42

    def test_job_ids_follow_new_format(self):
        svc = _make_scheduler_no_db()
        svc.schedule_round(_mystery_round(round_id=42), season_number=3, division_tier=2)
        job_ids = [c.kwargs.get("id", "") for c in svc._scheduler.add_job.call_args_list]
        # New format: <event>_s{season}_d{tier}_r{round_number}
        assert any(jid == "weather_p1_s3_d2_r1" for jid in job_ids)


# ---------------------------------------------------------------------------
# 3. cancel_round removes all round jobs via kwargs lookup (FR-002)
# ---------------------------------------------------------------------------

class TestCancelRoundIncludesMystery:

    def _setup_round_jobs(
        self, svc, round_id: int, season: int = 1, tier: int = 1, rnum: int = 1
    ) -> list[MagicMock]:
        """Populate get_jobs() with a typical set of round jobs."""
        suffix = f"_s{season}_d{tier}_r{rnum}"
        jobs = [
            _make_mock_job(f"weather_p1{suffix}", round_id),
            _make_mock_job(f"weather_p2{suffix}", round_id),
            _make_mock_job(f"weather_p3{suffix}", round_id),
            _make_mock_job(f"cleanup{suffix}", round_id),
            _make_mock_job(f"results{suffix}", round_id),
        ]
        svc._scheduler.get_jobs.return_value = jobs
        return jobs

    def test_removes_all_round_jobs(self):
        svc = _make_scheduler_no_db()
        self._setup_round_jobs(svc, round_id=7)
        svc.cancel_round(7)
        assert svc._scheduler.remove_job.call_count == 5

    def test_does_not_remove_other_round_jobs(self):
        svc = _make_scheduler_no_db()
        jobs = [
            _make_mock_job("weather_p1_s1_d1_r1", round_id=7),
            _make_mock_job("weather_p1_s1_d1_r2", round_id=99),
        ]
        svc._scheduler.get_jobs.return_value = jobs
        svc.cancel_round(7)
        removed = [c.args[0] for c in svc._scheduler.remove_job.call_args_list]
        assert "weather_p1_s1_d1_r1" in removed
        assert "weather_p1_s1_d1_r2" not in removed

    def test_no_jobs_removed_when_jobstore_empty(self):
        svc = _make_scheduler_no_db()
        svc._scheduler.get_jobs.return_value = []
        svc.cancel_round(7)
        svc._scheduler.remove_job.assert_not_called()


# ---------------------------------------------------------------------------
# 5. schedule_attendance_round: MYSTERY round must produce RSVP jobs identical
#    to any other round format (032-attendance-rsvp-checkin)
# ---------------------------------------------------------------------------

class TestScheduleAttendanceRoundMystery:
    """schedule_attendance_round must treat MYSTERY identically to all other formats."""

    def test_mystery_creates_rsvp_notice_job(self):
        svc = _make_scheduler_no_db()
        svc.schedule_attendance_round(
            _future_round(5, RoundFormat.MYSTERY),
            season_number=1,
            division_tier=1,
            notice_days=3,
            last_notice_hours=24,
            deadline_hours=2,
        )
        job_ids = [c.kwargs.get("id", "") for c in svc._scheduler.add_job.call_args_list]
        assert "rsvp_notice_s1_d1_r1" in job_ids

    def test_mystery_creates_rsvp_deadline_job(self):
        svc = _make_scheduler_no_db()
        svc.schedule_attendance_round(
            _future_round(5, RoundFormat.MYSTERY),
            season_number=1,
            division_tier=1,
            notice_days=3,
            last_notice_hours=24,
            deadline_hours=2,
        )
        job_ids = [c.kwargs.get("id", "") for c in svc._scheduler.add_job.call_args_list]
        assert "rsvp_deadline_s1_d1_r1" in job_ids

    def test_mystery_creates_last_notice_job_when_enabled(self):
        svc = _make_scheduler_no_db()
        svc.schedule_attendance_round(
            _future_round(5, RoundFormat.MYSTERY),
            season_number=1,
            division_tier=1,
            notice_days=3,
            last_notice_hours=24,
            deadline_hours=2,
        )
        job_ids = [c.kwargs.get("id", "") for c in svc._scheduler.add_job.call_args_list]
        assert "rsvp_last_notice_s1_d1_r1" in job_ids

    def test_mystery_creates_same_rsvp_job_count_as_normal(self):
        """MYSTERY and NORMAL round must produce identical RSVP job counts."""
        svc = _make_scheduler_no_db()
        svc.schedule_attendance_round(
            _future_round(10, RoundFormat.MYSTERY),
            season_number=1,
            division_tier=1,
            notice_days=3,
            last_notice_hours=24,
            deadline_hours=2,
        )
        mystery_count = svc._scheduler.add_job.call_count

        svc._scheduler.add_job.reset_mock()
        svc.schedule_attendance_round(
            _future_round(11, RoundFormat.NORMAL),
            season_number=1,
            division_tier=1,
            notice_days=3,
            last_notice_hours=24,
            deadline_hours=2,
        )
        normal_count = svc._scheduler.add_job.call_count

        assert mystery_count == normal_count

    def test_mystery_no_last_notice_when_disabled(self):
        """When last_notice_hours=0, MYSTERY round gets only notice + deadline (2 jobs)."""
        svc = _make_scheduler_no_db()
        svc.schedule_attendance_round(
            _future_round(5, RoundFormat.MYSTERY),
            season_number=1,
            division_tier=1,
            notice_days=3,
            last_notice_hours=0,
            deadline_hours=2,
        )
        assert svc._scheduler.add_job.call_count == 2
        job_ids = [c.kwargs.get("id", "") for c in svc._scheduler.add_job.call_args_list]
        assert "rsvp_last_notice_s1_d1_r1" not in job_ids


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

            mock_msg = MagicMock()
            mock_msg.id = 111
            mock_bot = MagicMock()
            mock_bot.db_path = db_path
            mock_bot.output_router.post_forecast = AsyncMock(return_value=mock_msg)
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

            mock_msg = MagicMock()
            mock_msg.id = 222
            mock_bot = MagicMock()
            mock_bot.db_path = db_path
            mock_bot.output_router.post_forecast = AsyncMock(return_value=mock_msg)
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
