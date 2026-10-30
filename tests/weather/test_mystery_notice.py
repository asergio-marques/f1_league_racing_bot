"""Unit tests for mystery round notice (006-mystery-round-notice).

Covers NFR-003 cases:
  1. mystery_notice_message() exact content (FR-003)
  2. schedule_round for MYSTERY schedules weather phase jobs (FR-001)
  3. cancel_round removes all round jobs via kwargs lookup (FR-002)
  4. the notice, asked of the change queue, posts to the forecast channel with no role tag,
     and nothing where the round is no longer a mystery round, is gone or weather is off
  5. schedule_attendance_round treats MYSTERY identically to other formats (RSVP jobs)
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from leaguebot.weather.utils.message_builder import mystery_notice_message
from leaguebot.core.models.round import Round, RoundFormat


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
    from leaguebot.core.services.scheduler_service import SchedulerService
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
        # weather_p1 replaces the old mystery_r job; it checks format at runtime.
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

    @pytest.mark.parametrize(
        "expected_id",
        [
            "rsvp_notice_s1_d1_r1_id5",
            "rsvp_deadline_s1_d1_r1_id5",
            "rsvp_last_notice_s1_d1_r1_id5",
        ],
        ids=["notice", "deadline", "last_notice_when_enabled"],
    )
    def test_mystery_creates_each_rsvp_job(self, expected_id):
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
        assert expected_id in job_ids

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
        """When last_notice_hours=0, MYSTERY round gets notice, deadline and cleanup (3 jobs)."""
        svc = _make_scheduler_no_db()
        svc.schedule_attendance_round(
            _future_round(5, RoundFormat.MYSTERY),
            season_number=1,
            division_tier=1,
            notice_days=3,
            last_notice_hours=0,
            deadline_hours=2,
        )
        assert svc._scheduler.add_job.call_count == 3
        job_ids = [c.kwargs.get("id", "") for c in svc._scheduler.add_job.call_args_list]
        assert "rsvp_last_notice_s1_d1_r1" not in job_ids


# ---------------------------------------------------------------------------
# 4. The notice on the change queue (FR-005, FR-008; #439, slice 6)
# ---------------------------------------------------------------------------

async def _seed_mystery_round(db_path: str, round_id: int = 1) -> None:
    from leaguebot.core.db.database import get_connection
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 100, 200, 300)"
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status) "
            "VALUES (1, '2026-01-01', 'ACTIVE')"
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


async def _notice_league(tmp_path, *, weather: bool = True):
    """`ongoing_league`'s season 3, Pro's round 3 (id 213) a mystery round, weather as asked."""
    from tests.support.season_league import PRO, ongoing_league, round_id

    league = await ongoing_league(tmp_path, weather=weather)
    await league.write("UPDATE rounds SET format = 'MYSTERY' WHERE id = ?", round_id(PRO, 3))
    return league


async def _ask_notice(league, round_id: int) -> None:
    """Ask for the round's phase 1, as its timer does: a mystery round's is its notice."""
    from leaguebot.weather.services.forecast_changes import ask_forecast

    await ask_forecast(league.bot.change_queue, league.db_path, round_id, 1)


def _forecast_texts(league) -> list[str]:
    from tests.support.season_league import DIVISIONS, PRO

    return league.texts(DIVISIONS[PRO][3].forecast)


async def _phase1_done(league, round_id: int) -> int:
    [row] = await league.rows("SELECT phase1_done FROM rounds WHERE id = ?", round_id)
    return row["phase1_done"]


class TestRunMysteryNotice:
    """The notice asked of the change queue for Pro's mystery round 3, and the queue run."""

    async def test_posted_message_has_no_role_tag(self, tmp_path):
        from tests.support.change_queue import run_queue
        from tests.support.season_league import PRO, round_id

        league = await _notice_league(tmp_path)

        await _ask_notice(league, round_id(PRO, 3))
        await run_queue(league.bot)

        [posted_msg] = _forecast_texts(league)
        assert "<@&" not in posted_msg
        assert "🏁" in posted_msg
        assert "Mystery" in posted_msg

    async def test_skips_if_format_amended_to_non_mystery(self, tmp_path):
        """Guard: the round amended away from MYSTERY while its notice waited on the queue: the
        change is dropped at its check, nothing posted and Phase 1 left undone."""
        from tests.support.change_queue import run_queue, stopped_job
        from tests.support.season_league import PRO, round_id

        league = await _notice_league(tmp_path)
        await _ask_notice(league, round_id(PRO, 3))
        await league.write("UPDATE rounds SET format = 'NORMAL' WHERE id = ?", round_id(PRO, 3))

        await run_queue(league.bot)

        assert _forecast_texts(league) == []
        assert await _phase1_done(league, round_id(PRO, 3)) == 0
        assert await stopped_job(league.db_path) is None

    async def test_skips_if_round_not_found(self, tmp_path):
        """A round that does not exist (999): nothing is asked of the queue."""
        from tests.support.change_queue import change_rows

        league = await _notice_league(tmp_path)

        await _ask_notice(league, 999)

        assert [row for row in await change_rows(league.db_path)
                if row["kind"].startswith("weather.")] == []

    async def test_no_notice_is_posted_while_the_weather_module_is_off(self, tmp_path):
        """The notice is a weather posting, and a disabled module produces nothing whatever the
        path arrives at it — so its change is dropped at its check, as the phases' are (#113),
        and the round's Phase 1 is left undone (#426)."""
        from tests.support.change_queue import run_queue, stopped_job
        from tests.support.season_league import PRO, round_id

        league = await _notice_league(tmp_path, weather=False)

        await _ask_notice(league, round_id(PRO, 3))
        await run_queue(league.bot)

        assert _forecast_texts(league) == []
        assert await _phase1_done(league, round_id(PRO, 3)) == 0
        assert await stopped_job(league.db_path) is None


# ---------------------------------------------------------------------------
# 5. The notice's record, on the save its job hands it (#439)
# ---------------------------------------------------------------------------


async def test_the_notice_s_record_writes_only_on_the_connection_it_is_handed(tmp_path):
    """The notice marks the round's Phase 1 done on the save handed, committing nothing of its
    own: its flag stands or falls with the job, and the notice is posted by a job after it, so
    a notice Discord refuses no longer leaves the flag set and nothing posted (#439)."""
    from leaguebot.core.db.database import get_connection, run_migrations
    from leaguebot.weather.services.mystery_notice_service import record_mystery_notice_on

    db_path = str(tmp_path / "notice_record.db")
    await run_migrations(db_path)
    await _seed_mystery_round(db_path, round_id=5)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE server_configs SET weather_module_enabled = 1")
        await db.commit()

    async with get_connection(db_path) as db:
        drawn = await record_mystery_notice_on(db, 5)
        cursor = await db.execute("SELECT phase1_done FROM rounds WHERE id = 5")
        assert (await cursor.fetchone())["phase1_done"] == 1
        await db.rollback()

    # Nothing is drawn for a notice, and it writes no line (weather spec :128).
    assert drawn.payload is None
    assert drawn.line is None
    assert not drawn.blocked
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT phase1_done FROM rounds WHERE id = 5")
        assert (await cursor.fetchone())["phase1_done"] == 0
