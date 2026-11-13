"""The scheduler service's registration, its result-only scheduling and its signup timer.

Issue #208. `schedule_all_rounds`, `schedule_result_submission_jobs`, the signup close timer and
the season-end cancellation were uncovered. They are thin, and each has one property that matters
more than its size suggests. What every job is stored with, and that each kind's handler is reached
through the one runner, is pinned in `test_scheduler_one_off.py`.

**The service registers itself as the global only when it starts.** Constructing a second
service — a test, a reset — must not redirect jobs already firing at the running one.

**A results-only league still gets its submission jobs.** `schedule_round` arms weather and
results together; a league with results on and weather off never calls it, so
`schedule_result_submission_jobs` arms the submission alone, at the round's start time, under
the same id-suffix rule every round job uses — the round id is in it, so renumbering cannot
make one round's job overwrite another's.

**Re-arming the signup timer replaces it.** A manager who changes the close time must not end
up with two closes, and the job id is per server for that reason.

**Cancelling something that is not scheduled is not an error.** A job that has already fired,
or was never armed, is the ordinary case for every cancellation here; that is pinned, for each
of them, in `test_scheduler_cancels.py`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import leaguebot.core.services.scheduler_service as scheduler_module
from leaguebot.core.services.rounds.round_moments import POST_RACE_CLEANUP_DELAY
from leaguebot.core.services.scheduler_service import SchedulerService


@pytest.fixture(autouse=True)
def _restore_global():
    before = scheduler_module._GLOBAL_SERVICE
    yield
    scheduler_module._GLOBAL_SERVICE = before


def _service():
    service = SchedulerService.__new__(SchedulerService)
    service._db_path = "/tmp/not-read.db"
    service._jobstore_path = "/tmp/not-read-jobs.db"
    service._scheduler = MagicMock()
    service._scheduler.running = False
    return service


def _round(round_id=21, division_id=11, number=3):
    return SimpleNamespace(
        id=round_id,
        division_id=division_id,
        round_number=number,
        scheduled_at=datetime(2026, 3, 1, 18, tzinfo=timezone.utc),
    )


def _added(service) -> list:
    return service._scheduler.add_job.call_args_list


# ---------------------------------------------------------------------------
# Starting
# ---------------------------------------------------------------------------


def test_starting_makes_the_service_the_one_jobs_reach():
    service = _service()

    service.start()

    assert scheduler_module._GLOBAL_SERVICE is service
    service._scheduler.start.assert_called_once()


def test_constructing_a_service_does_not_redirect_running_jobs(tmp_path):
    """Only `start` sets the global — a second service built for a reset or a test must not
    take jobs from the one already running."""
    running = _service()
    running.start()

    second = SchedulerService(str(tmp_path / "bot.db"))
    try:
        assert scheduler_module._GLOBAL_SERVICE is running
    finally:
        # Released explicitly: Windows will not remove a SQLite file an engine still holds.
        second._scheduler._jobstores["default"].engine.dispose()


def test_paused_pauses_and_resumes_the_scheduler():
    """A running scheduler is paused for the block and resumed after it, and resumed too where
    the block raises, the error going on to the caller."""
    service = _service()
    service._scheduler.running = True

    with service.paused():
        service._scheduler.pause.assert_called_once_with()
        service._scheduler.resume.assert_not_called()
    service._scheduler.resume.assert_called_once_with()

    with pytest.raises(OSError, match="the disk is full"):
        with service.paused():
            raise OSError("the disk is full")
    assert service._scheduler.pause.call_count == 2
    assert service._scheduler.resume.call_count == 2


def test_a_stopped_scheduler_is_neither_paused_nor_resumed():
    """Resuming a scheduler nobody paused would start one the bot deliberately left down."""
    service = _service()

    with service.paused():
        pass

    service._scheduler.pause.assert_not_called()
    service._scheduler.resume.assert_not_called()


def test_starting_a_running_scheduler_does_not_start_it_again():
    service = _service()
    service._scheduler.running = True

    service.start()

    service._scheduler.start.assert_not_called()


def test_shutting_down_stops_a_running_scheduler():
    service = _service()
    service._scheduler.running = True

    service.shutdown(wait=False)

    service._scheduler.shutdown.assert_called_once_with(wait=False)


def test_shutting_down_a_stopped_scheduler_does_nothing():
    service = _service()

    service.shutdown()

    service._scheduler.shutdown.assert_not_called()


# ---------------------------------------------------------------------------
# Scheduling rounds
# ---------------------------------------------------------------------------


def test_schedule_all_rounds_passes_each_rounds_own_division(monkeypatch):
    service = _service()
    calls = []
    monkeypatch.setattr(service, "schedule_round", lambda rnd, **kw: calls.append((rnd.id, kw)))

    service.schedule_all_rounds(
        [_round(21, 11), _round(22, 12)],
        division_meta={11: (7, 1), 12: (7, 2)},
        phase_1_days=6,
    )

    assert [(rid, kw["division_tier"], kw["phase_1_days"]) for rid, kw in calls] == [
        (21, 1, 6),
        (22, 2, 6),
    ]


def test_a_results_only_league_gets_a_submission_job_per_round():
    service = _service()

    service.schedule_result_submission_jobs(
        [_round(21), _round(22, number=4)], division_meta={11: (7, 1)}
    )

    assert [c.kwargs["id"] for c in _added(service)] == [
        "results_s7_d1_r3_id21", "results_s7_d1_r4_id22",
    ]


def test_the_submission_job_fires_at_the_round_start():
    service = _service()

    service.schedule_result_submission_jobs([_round()], division_meta={11: (7, 1)})

    trigger = _added(service)[0].kwargs["trigger"]
    assert trigger.run_date == datetime(2026, 3, 1, 18, tzinfo=timezone.utc)


def test_the_submission_job_id_carries_the_round_id():
    """So renumbering cannot make one round's job overwrite another's."""
    service = _service()

    service.schedule_result_submission_jobs([_round(21, number=3)], division_meta={11: (7, 1)})

    job = _added(service)[0].kwargs
    assert job["id"] == "results_s7_d1_r3_id21"
    assert job["replace_existing"] is True


def test_a_submission_run_at_once_replaces_the_round_s_job_and_runs_however_late():
    """A round whose moment passed while its timed work stood removed (an amendment stopped on
    the change queue) has its results submission run at once: the round's own job, under its
    id, armed with no trigger, which APScheduler runs at once, and with no lateness limit."""
    service = _service()

    service.run_result_submission_now(_round(21, number=3), season_number=7, division_tier=1)

    [added] = _added(service)
    assert added.kwargs.get("trigger") is None
    assert added.kwargs["id"] == "results_s7_d1_r3_id21"
    assert added.kwargs["replace_existing"] is True
    assert added.kwargs["misfire_grace_time"] is None


def test_a_phase_left_out_is_not_armed_and_the_rest_of_the_round_is():
    """A phase an amendment draws at once, its horizon passed by less than the misfire grace, is
    left out of the round's timers, which would otherwise draw it a second time; the other
    phases, the clean-up and the results submission are armed as ever."""
    service = _service()

    service.schedule_round(
        _round(21, number=3), season_number=7, division_tier=1, skip_phases=frozenset({1, 2})
    )

    assert [c.kwargs["id"] for c in _added(service)] == [
        "weather_p3_s7_d1_r3_id21", "cleanup_s7_d1_r3_id21", "results_s7_d1_r3_id21",
    ]


# ---------------------------------------------------------------------------
# The check-in cleanup (#425)
# ---------------------------------------------------------------------------
#
# `schedule_attendance_round` skips any job whose moment is not after the `now` it is handed, and
# reads no clock of its own: each test hands a `now` the wall clock is nowhere near, so that a read
# of the wall clock would arm, or skip, what the test says it does not.

def _round_starting(start: datetime) -> SimpleNamespace:
    return SimpleNamespace(id=21, division_id=11, round_number=3, scheduled_at=start)


def _attendance_jobs(service, rnd, *, now: datetime) -> dict:
    service.schedule_attendance_round(
        rnd,
        season_number=7,
        division_tier=1,
        notice_days=5,
        last_notice_hours=24,
        deadline_hours=2,
        now=now,
    )
    return {c.kwargs["id"]: c for c in _added(service)}


def test_the_check_in_cleanup_is_armed_a_day_after_the_round():
    """A round on 1 March 2020 at 18:00, long past by the wall clock, armed with `now` a month
    before it: its check-in clean-up is armed a day after its start, as are its call, last
    notice and deadline."""
    service = _service()
    start = datetime(2020, 3, 1, 18, tzinfo=timezone.utc)

    jobs = _attendance_jobs(service, _round_starting(start), now=start - timedelta(days=30))

    cleanup = jobs["rsvp_cleanup_s7_d1_r3_id21"]
    assert cleanup.kwargs["trigger"].run_date == start + timedelta(hours=24)
    assert cleanup.kwargs["replace_existing"] is True
    assert sorted(jobs) == [
        "rsvp_cleanup_s7_d1_r3_id21", "rsvp_deadline_s7_d1_r3_id21",
        "rsvp_last_notice_s7_d1_r3_id21", "rsvp_notice_s7_d1_r3_id21",
    ]


def test_a_check_in_timer_is_armed_with_no_lateness_limit():
    """A round on 1 March 2026 at 18:00, armed with `now` a month before it: its call, last
    notice, deadline and clean-up are each armed with no lateness limit, so one due while the bot
    was down still fires when it is back, and its handler judges what late means."""
    service = _service()
    start = datetime(2026, 3, 1, 18, tzinfo=timezone.utc)

    jobs = _attendance_jobs(service, _round_starting(start), now=start - timedelta(days=30))

    limits = {job_id: job.kwargs.get("misfire_grace_time", "default")
              for job_id, job in jobs.items() if job_id.startswith("rsvp_")}
    assert limits == {
        "rsvp_cleanup_s7_d1_r3_id21": None, "rsvp_deadline_s7_d1_r3_id21": None,
        "rsvp_last_notice_s7_d1_r3_id21": None, "rsvp_notice_s7_d1_r3_id21": None,
    }


def test_a_check_in_cleanup_already_due_is_not_armed():
    """The same rule as the attendance module's other three jobs: a moment gone by is skipped. A
    round on 1 March 2099 at 18:00, still to come by the wall clock, armed with `now` two days
    after it: none of its four check-in jobs is armed."""
    service = _service()
    start = datetime(2099, 3, 1, 18, tzinfo=timezone.utc)

    jobs = _attendance_jobs(service, _round_starting(start), now=start + timedelta(days=2))

    assert not [job_id for job_id in jobs if job_id.startswith("rsvp_")]


def test_a_round_s_forecast_and_check_in_come_down_together():
    """One figure for both modules, so a round's channels clear at the same moment."""
    service = _service()
    start = datetime(2026, 3, 1, 18, tzinfo=timezone.utc)
    rnd = _round_starting(start)

    service.schedule_round(rnd, season_number=7, division_tier=1)
    jobs = _attendance_jobs(service, rnd, now=start - timedelta(days=30))

    forecast = jobs["cleanup_s7_d1_r3_id21"].kwargs["trigger"].run_date
    check_in = jobs["rsvp_cleanup_s7_d1_r3_id21"].kwargs["trigger"].run_date
    assert forecast == check_in == rnd.scheduled_at + POST_RACE_CLEANUP_DELAY


# ---------------------------------------------------------------------------
# The signup timer and the cancellations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cancel,args,job_id",
    [
        ("cancel_job", ("weather_p1_x",), "weather_p1_x"),
    ],
)
def test_a_cancellation_removes_its_job(cancel, args, job_id):
    service = _service()

    getattr(service, cancel)(*args)

    service._scheduler.remove_job.assert_called_once_with(job_id)


def test_cancelling_the_season_end_removes_every_season_end_job_and_nothing_else():
    """A store written by an older version keyed the job by server, so the id is matched on
    its prefix."""
    service = _service()
    service._scheduler.get_jobs = MagicMock(return_value=[
        SimpleNamespace(id="season_end_4242"),
        SimpleNamespace(id="season_end"),
        SimpleNamespace(id="signup_close"),
    ])

    service.cancel_season_end()

    removed = [c.args[0] for c in service._scheduler.remove_job.call_args_list]
    assert removed == ["season_end_4242", "season_end"]
