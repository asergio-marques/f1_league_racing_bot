"""Core's round jobs (`RoundJobs`): a round's scheduling, its results alone and the season end.

`RoundJobs` arms and cancels a round's jobs through the framework's scheduler (#544), under the
same ids, names and moments as the scheduler armed them before it.

Its starting, pausing and shutting down are in `tests/framework/test_scheduler_lifecycle.py`.

Issue #208. `schedule_all_rounds`, `schedule_result_submission_jobs`, the signup close timer and
the season-end cancellation were uncovered. They are thin, and each has one property that matters
more than its size suggests. What every job is stored with, and that each kind's handler is reached
through the one runner, is pinned in `test_scheduler_one_off.py`.

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


#: Why each test here fails until core's round jobs exist.
NO_ROUND_JOBS = "#544: core has no RoundJobs yet; the round scheduling is still the scheduler's"


def _round_jobs(service):
    """Core's round jobs over *service*, built as the builder builds them."""
    from leaguebot.core.services.rounds.round_jobs import RoundJobs

    return RoundJobs(service, service._db_path)


def _added(service) -> list:
    return service._scheduler.add_job.call_args_list


# ---------------------------------------------------------------------------
# Scheduling rounds
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_ROUND_JOBS)
def test_schedule_all_rounds_passes_each_rounds_own_division(monkeypatch):
    round_jobs = _round_jobs(_service())
    calls = []
    monkeypatch.setattr(round_jobs, "schedule_round", lambda rnd, **kw: calls.append((rnd.id, kw)))

    round_jobs.schedule_all_rounds(
        [_round(21, 11), _round(22, 12)],
        division_meta={11: (7, 1), 12: (7, 2)},
        phase_1_days=6,
    )

    assert [(rid, kw["division_tier"], kw["phase_1_days"]) for rid, kw in calls] == [
        (21, 1, 6),
        (22, 2, 6),
    ]


@pytest.mark.xfail(strict=True, reason=NO_ROUND_JOBS)
def test_a_results_only_league_gets_a_submission_job_per_round():
    service = _service()

    _round_jobs(service).schedule_result_submission_jobs(
        [_round(21), _round(22, number=4)], division_meta={11: (7, 1)}
    )

    assert [c.kwargs["id"] for c in _added(service)] == [
        "results_s7_d1_r3_id21", "results_s7_d1_r4_id22",
    ]


@pytest.mark.xfail(strict=True, reason=NO_ROUND_JOBS)
def test_the_submission_job_fires_at_the_round_start():
    service = _service()

    _round_jobs(service).schedule_result_submission_jobs([_round()], division_meta={11: (7, 1)})

    trigger = _added(service)[0].kwargs["trigger"]
    assert trigger.run_date == datetime(2026, 3, 1, 18, tzinfo=timezone.utc)


@pytest.mark.xfail(strict=True, reason=NO_ROUND_JOBS)
def test_the_submission_job_id_carries_the_round_id():
    """So renumbering cannot make one round's job overwrite another's."""
    service = _service()

    _round_jobs(service).schedule_result_submission_jobs(
        [_round(21, number=3)], division_meta={11: (7, 1)}
    )

    job = _added(service)[0].kwargs
    assert job["id"] == "results_s7_d1_r3_id21"
    assert job["replace_existing"] is True


@pytest.mark.xfail(strict=True, reason=NO_ROUND_JOBS)
def test_a_submission_run_at_once_replaces_the_round_s_job_and_runs_however_late():
    """A round whose moment passed while its timed work stood removed (an amendment stopped on
    the change queue) has its results submission run at once: the round's own job, under its
    id, armed with no trigger, which APScheduler runs at once, and with no lateness limit."""
    service = _service()

    _round_jobs(service).run_result_submission_now(
        _round(21, number=3), season_number=7, division_tier=1
    )

    [added] = _added(service)
    assert added.kwargs.get("trigger") is None
    assert added.kwargs["id"] == "results_s7_d1_r3_id21"
    assert added.kwargs["replace_existing"] is True
    assert added.kwargs["misfire_grace_time"] is None


@pytest.mark.xfail(strict=True, reason=NO_ROUND_JOBS)
def test_a_phase_left_out_is_not_armed_and_the_rest_of_the_round_is():
    """A phase an amendment draws at once, its horizon passed by less than the misfire grace, is
    left out of the round's timers, which would otherwise draw it a second time; the other
    phases, the clean-up and the results submission are armed as ever, the third phase two
    hours before the round's start, the clean-up a day after it and the submission at it."""
    service = _service()

    _round_jobs(service).schedule_round(
        _round(21, number=3), season_number=7, division_tier=1, skip_phases=frozenset({1, 2})
    )

    assert [c.kwargs["id"] for c in _added(service)] == [
        "weather_p3_s7_d1_r3_id21", "cleanup_s7_d1_r3_id21", "results_s7_d1_r3_id21",
    ]
    start = _round().scheduled_at
    assert [c.kwargs["trigger"].run_date for c in _added(service)] == [
        start - timedelta(hours=2), start + POST_RACE_CLEANUP_DELAY, start,
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
    _round_jobs(service).schedule_attendance_round(
        rnd,
        season_number=7,
        division_tier=1,
        notice_days=5,
        last_notice_hours=24,
        deadline_hours=2,
        now=now,
    )
    return {c.kwargs["id"]: c for c in _added(service)}


@pytest.mark.xfail(strict=True, reason=NO_ROUND_JOBS)
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


@pytest.mark.xfail(strict=True, reason=NO_ROUND_JOBS)
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


@pytest.mark.xfail(strict=True, reason=NO_ROUND_JOBS)
def test_a_check_in_cleanup_already_due_is_not_armed():
    """The same rule as the attendance module's other three jobs: a moment gone by is skipped. A
    round on 1 March 2099 at 18:00, still to come by the wall clock, armed with `now` two days
    after it: none of its four check-in jobs is armed."""
    service = _service()
    start = datetime(2099, 3, 1, 18, tzinfo=timezone.utc)

    jobs = _attendance_jobs(service, _round_starting(start), now=start + timedelta(days=2))

    assert not [job_id for job_id in jobs if job_id.startswith("rsvp_")]


@pytest.mark.xfail(strict=True, reason=NO_ROUND_JOBS)
def test_a_round_s_forecast_and_check_in_come_down_together():
    """One figure for both modules, so a round's channels clear at the same moment."""
    service = _service()
    start = datetime(2026, 3, 1, 18, tzinfo=timezone.utc)
    rnd = _round_starting(start)

    _round_jobs(service).schedule_round(rnd, season_number=7, division_tier=1)
    jobs = _attendance_jobs(service, rnd, now=start - timedelta(days=30))

    forecast = jobs["cleanup_s7_d1_r3_id21"].kwargs["trigger"].run_date
    check_in = jobs["rsvp_cleanup_s7_d1_r3_id21"].kwargs["trigger"].run_date
    assert forecast == check_in == rnd.scheduled_at + POST_RACE_CLEANUP_DELAY


# ---------------------------------------------------------------------------
# The season end
# ---------------------------------------------------------------------------



@pytest.mark.xfail(strict=True, reason=NO_ROUND_JOBS)
def test_cancelling_the_season_end_removes_every_season_end_job_and_nothing_else():
    """A store written by an older version keyed the job by server, so the id is matched on
    its prefix."""
    service = _service()
    service._scheduler.get_jobs = MagicMock(return_value=[
        SimpleNamespace(id="season_end_4242", kwargs={"kind": "season_end", "key": 4242},
                        next_run_time=None),
        SimpleNamespace(id="season_end", kwargs={"kind": "season_end"}, next_run_time=None),
        SimpleNamespace(id="signup_close", kwargs={"kind": "signup_close"}, next_run_time=None),
    ])

    _round_jobs(service).cancel_season_end()

    removed = [c.args[0] for c in service._scheduler.remove_job.call_args_list]
    assert removed == ["season_end_4242", "season_end"]
