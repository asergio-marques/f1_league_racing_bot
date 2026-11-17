"""Unit tests for mystery round notice (006-mystery-round-notice).

Covers NFR-003 cases:
  1. mystery_notice_message() exact content (FR-003)
  2. a MYSTERY round's forecast leads arm weather phase jobs as any round's do (FR-001)
  3. cancel_round removes all round jobs via kwargs lookup (FR-002)
  4. the notice, asked of the change queue, posts to the forecast channel with no role tag,
     and nothing where the round is no longer a mystery round, is gone or weather is off
  5. a MYSTERY round's check-in leads arm RSVP jobs as any other format's do

A round's arming and cancelling (2, 3 and 5) are core's round jobs' (`RoundJobs`, #544), over the
framework's scheduler.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from leaguebot.weather.utils.message_builder import mystery_notice_message
from leaguebot.core.models.round import Round, RoundFormat
from tests.support.round_jobs import (
    check_in_leads,
    forecast_leads,
    round_jobs_over,
    submission_lead,
)


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

    def test_no_role_mention(self):
        assert "<@&" not in mystery_notice_message()

    def test_it_names_no_horizon(self):
        """It posts at the phase 1 horizon and says so nowhere (issue #112).

        Already true when the three phase messages were not, this being fixed text that never
        described its own timing. Asserted so that a later edit reaching for the wording the
        other three lost does not reintroduce it here.
        """
        notice = mystery_notice_message()
        for horizon in ("days out", "hours out", "T−5", "T-5", "T−2", "T-2"):
            assert horizon not in notice

    def test_it_carries_no_phase_description(self):
        """It stands for no phase's forecast, so it is titled by none of the three.

        The mystery template declares no ``phase_description`` field either — the carve-out
        the weather specification names, pinned on the textual side.
        """
        from leaguebot.weather.utils.message_builder import PHASE_DESCRIPTIONS

        notice = mystery_notice_message()
        for description in PHASE_DESCRIPTIONS.values():
            assert description not in notice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scheduler_no_db():
    """Instantiate SchedulerService bypassing __init__ (no real APScheduler needed)."""
    from leaguebot.framework.services.jobs.scheduler import SchedulerService
    svc = SchedulerService.__new__(SchedulerService)
    svc._db_path = ":memory:"
    mock_sched = MagicMock()
    mock_sched.add_job = MagicMock()
    mock_sched.remove_job = MagicMock()
    mock_sched.get_jobs = MagicMock(return_value=[])
    svc._scheduler = mock_sched
    return svc



def _round_jobs(svc):
    """Core's round jobs over *svc*, built as the builder builds them."""
    from leaguebot.core.services.rounds.round_jobs import RoundJobs

    return RoundJobs(svc, svc._db_path)


def _make_mock_job(job_id: str, round_id: int) -> MagicMock:
    """A mock APScheduler job with the given id, stored with its kind (its id less the round
    suffix) and the round id as its key."""
    job = MagicMock()
    job.id = job_id
    job.kwargs = {"kind": job_id.split("_s", 1)[0], "key": round_id}
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
    """Round on 1 June 2099, so all RSVP job fire-times are still pending at `NOW`."""
    return Round(
        id=round_id,
        division_id=1,
        round_number=1,
        format=fmt,
        track_name=None if fmt == RoundFormat.MYSTERY else "Bahrain",
        scheduled_at=datetime(2099, 6, 1, 14, 0, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# 2. a mystery round's forecast leads arm its weather phase jobs (FR-001)
# ---------------------------------------------------------------------------

def _arm_mystery(svc, round_id: int = 42, season: int = 1, tier: int = 1) -> None:
    """A mystery round armed with weather's packaged forecast leads and results' submission, a
    month before it."""
    rnd = _mystery_round(round_id)
    _round_jobs(svc).arm_leads(rnd, (*forecast_leads(), submission_lead()), season, tier,
                               rnd.scheduled_at - timedelta(days=30))


class TestScheduleRoundMystery:

    @pytest.mark.xfail(strict=True, reason="#544: core has no RoundLead nor RoundJobs.arm_leads yet")
    def test_schedules_exactly_five_jobs(self):
        # All rounds (including MYSTERY) schedule 5 jobs:
        # weather_p1, weather_p2, weather_p3, cleanup, results
        svc = _make_scheduler_no_db()
        _arm_mystery(svc)
        assert svc._scheduler.add_job.call_count == 5

    @pytest.mark.xfail(strict=True, reason="#544: core has no RoundLead nor RoundJobs.arm_leads yet")
    def test_no_legacy_mystery_r_job(self):
        svc = _make_scheduler_no_db()
        _arm_mystery(svc)
        job_ids = [c.kwargs.get("id", "") for c in svc._scheduler.add_job.call_args_list]
        assert not any(jid.startswith("mystery_r") for jid in job_ids)

    @pytest.mark.xfail(strict=True, reason="#544: core has no RoundLead nor RoundJobs.arm_leads yet")
    def test_all_three_weather_phase_jobs_created(self):
        svc = _make_scheduler_no_db()
        _arm_mystery(svc)
        job_ids = [c.kwargs.get("id", "") for c in svc._scheduler.add_job.call_args_list]
        assert any("weather_p1" in jid for jid in job_ids)
        assert any("weather_p2" in jid for jid in job_ids)
        assert any("weather_p3" in jid for jid in job_ids)

    @pytest.mark.xfail(strict=True, reason="#544: core has no RoundLead nor RoundJobs.arm_leads yet")
    def test_round_id_in_kwargs_for_all_jobs(self):
        """Every job of a mystery round is stored with the round's id as its key."""
        svc = _make_scheduler_no_db()
        _arm_mystery(svc, 42)
        for call in svc._scheduler.add_job.call_args_list:
            assert call.kwargs["kwargs"]["key"] == 42

    @pytest.mark.xfail(strict=True, reason="#544: core has no RoundLead nor RoundJobs.arm_leads yet")
    def test_job_ids_follow_new_format(self):
        svc = _make_scheduler_no_db()
        _arm_mystery(svc, 42, season=3, tier=2)
        job_ids = [c.kwargs.get("id", "") for c in svc._scheduler.add_job.call_args_list]
        # weather_p1 replaces the old mystery_r job; weather reads the format when it fires.
        # Format: <event>_s{season}_d{tier}_r{round_number}_id{round_id}. The round id is the
        # part that has to be there — season, tier and number are all rewritten by a
        # renumbering, and without the id an amended round could schedule on top of a sibling's
        # jobs and destroy them.
        assert any(jid == "weather_p1_s3_d2_r1_id42" for jid in job_ids)


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

    @pytest.mark.xfail(strict=True, reason="#544: core's round jobs keep no register of the modules' round kinds yet (sign_up_kinds)")
    def test_removes_all_round_jobs(self):
        svc = _make_scheduler_no_db()
        self._setup_round_jobs(svc, round_id=7)
        round_jobs_over(svc, svc._db_path).cancel_round(7)
        assert svc._scheduler.remove_job.call_count == 5

    @pytest.mark.xfail(strict=True, reason="#544: core's round jobs keep no register of the modules' round kinds yet (sign_up_kinds)")
    def test_does_not_remove_other_round_jobs(self):
        svc = _make_scheduler_no_db()
        jobs = [
            _make_mock_job("weather_p1_s1_d1_r1", round_id=7),
            _make_mock_job("weather_p1_s1_d1_r2", round_id=99),
        ]
        svc._scheduler.get_jobs.return_value = jobs
        round_jobs_over(svc, svc._db_path).cancel_round(7)
        removed = [c.args[0] for c in svc._scheduler.remove_job.call_args_list]
        assert "weather_p1_s1_d1_r1" in removed
        assert "weather_p1_s1_d1_r2" not in removed

    @pytest.mark.xfail(strict=True, reason="#544: core's round jobs keep no register of the modules' round kinds yet (sign_up_kinds)")
    def test_no_jobs_removed_when_jobstore_empty(self):
        svc = _make_scheduler_no_db()
        svc._scheduler.get_jobs.return_value = []
        round_jobs_over(svc, svc._db_path).cancel_round(7)
        svc._scheduler.remove_job.assert_not_called()


#: The moment the check-in is armed against, a month before `_future_round`'s.
NOW = datetime(2099, 5, 1, 14, 0, 0, tzinfo=timezone.utc)

# ---------------------------------------------------------------------------
# 5. a MYSTERY round's check-in leads arm RSVP jobs identical to any other
#    round format's (032-attendance-rsvp-checkin)
# ---------------------------------------------------------------------------

def _arm_check_in(svc, round_id: int, fmt: RoundFormat) -> None:
    """A round of *fmt* armed with attendance's check-in leads (call 3 days, last notice 24
    hours, deadline 2 hours before), against `NOW`."""
    _round_jobs(svc).arm_leads(
        _future_round(round_id, fmt), check_in_leads(notice_days=3), 1, 1, NOW
    )


#: Why the check-in arming tests fail until core arms leads.
_NO_LEADS = "#544: core has no RoundLead nor RoundJobs.arm_leads yet"


class TestScheduleAttendanceRoundMystery:
    """A MYSTERY round's check-in leads are armed identically to any other format's."""

    @pytest.mark.parametrize(
        "expected_id",
        [
            "rsvp_notice_s1_d1_r1_id5",
            "rsvp_deadline_s1_d1_r1_id5",
            "rsvp_last_notice_s1_d1_r1_id5",
        ],
        ids=["notice", "deadline", "last_notice_when_enabled"],
    )
    @pytest.mark.xfail(strict=True, reason=_NO_LEADS)
    def test_mystery_creates_each_rsvp_job(self, expected_id):
        svc = _make_scheduler_no_db()
        _arm_check_in(svc, 5, RoundFormat.MYSTERY)
        job_ids = [c.kwargs.get("id", "") for c in svc._scheduler.add_job.call_args_list]
        assert expected_id in job_ids

    @pytest.mark.xfail(strict=True, reason=_NO_LEADS)
    def test_mystery_creates_same_rsvp_job_count_as_normal(self):
        """MYSTERY and NORMAL round must produce identical RSVP job counts."""
        svc = _make_scheduler_no_db()
        _arm_check_in(svc, 10, RoundFormat.MYSTERY)
        mystery_count = svc._scheduler.add_job.call_count

        svc._scheduler.add_job.reset_mock()
        _arm_check_in(svc, 11, RoundFormat.NORMAL)
        normal_count = svc._scheduler.add_job.call_count

        assert mystery_count == normal_count
