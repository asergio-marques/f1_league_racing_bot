"""Every job core's round jobs arm runs through the scheduler's one runner (#283, S7a; #544).

`RoundJobs` arms a round's forecasts, check-in, clean-ups and results submission through the
framework's scheduler, under the ids each job has always had. Each is stored as a one-off timer is:
its callable is the one runner, and its kind and the round id are its keywords, so that the
runner calls the kind's handler with the round.

What the runner does with a handler that raises is the scheduler's, and is pinned for each of its
generic calls in `tests/framework/test_scheduler_one_off.py`. These tests use the real
`SchedulerService`, on job-store files the test tears down, Windows included.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

#: Why each test here fails until core's round jobs exist.
NO_ROUND_JOBS = "#544: core has no RoundJobs yet; the round scheduling is still the scheduler's"


def _dispose(service: Any) -> None:
    """Stop the scheduler and close the job store's connections, so its file can be deleted."""
    try:
        service._scheduler.shutdown(wait=False)
    except Exception:
        pass
    try:
        service._scheduler._jobstores["default"].engine.dispose()
    except Exception:
        pass


@pytest.fixture
def services(tmp_path, monkeypatch):
    """Build a scheduler on one league database and one job store, as the bot does at each start;
    every one is stopped and its job store closed once the test has run."""
    from leaguebot.core.services import scheduler_service

    monkeypatch.setattr(scheduler_service, "_GLOBAL_SERVICE", None)
    made: list[Any] = []

    def build() -> Any:
        service = scheduler_service.SchedulerService(
            str(tmp_path / "bot.db"), str(tmp_path / "jobs.db")
        )
        made.append(service)
        return service

    yield build
    for service in made:
        _dispose(service)


def _now() -> datetime:
    return datetime.now(timezone.utc)


#: Pro's round 3 (id 21) of season 7, tier 1, a month away, so that no job of it is due.
_ROUND_ID = 21
_ROUND_SUFFIX = "_s7_d1_r3_id21"


def _a_round() -> Any:
    start = _now().replace(microsecond=0) + timedelta(days=30)
    return SimpleNamespace(id=_ROUND_ID, division_id=11, round_number=3, scheduled_at=start)


def _arm_round(round_jobs: Any, rnd: Any) -> None:
    round_jobs.schedule_round(rnd, season_number=7, division_tier=1)


def _arm_check_in(round_jobs: Any, rnd: Any) -> None:
    round_jobs.schedule_attendance_round(
        rnd, season_number=7, division_tier=1, notice_days=5, last_notice_hours=24,
        deadline_hours=2, now=_now(),
    )


def _arm_cleanups(round_jobs: Any, rnd: Any) -> None:
    round_jobs.schedule_round_cleanups(rnd, season_number=7, division_tier=1, now=_now())


def _arm_results_only(round_jobs: Any, rnd: Any) -> None:
    round_jobs.schedule_result_submission_jobs([rnd], division_meta={11: (7, 1)})


def _arm_results_now(round_jobs: Any, rnd: Any) -> None:
    round_jobs.run_result_submission_now(rnd, season_number=7, division_tier=1)


#: Every kind of round job, with the call of core's round jobs that arms it and the id it is
#: stored under. A round job's kind is its id's prefix and its key the round id.
EVERY_ROUND_JOB = [
    pytest.param("weather_p1", _arm_round, f"weather_p1{_ROUND_SUFFIX}",
                 id="weather_p1-schedule_round"),
    pytest.param("weather_p2", _arm_round, f"weather_p2{_ROUND_SUFFIX}",
                 id="weather_p2-schedule_round"),
    pytest.param("weather_p3", _arm_round, f"weather_p3{_ROUND_SUFFIX}",
                 id="weather_p3-schedule_round"),
    pytest.param("cleanup", _arm_round, f"cleanup{_ROUND_SUFFIX}", id="cleanup-schedule_round"),
    pytest.param("results", _arm_round, f"results{_ROUND_SUFFIX}", id="results-schedule_round"),
    pytest.param("results", _arm_results_only, f"results{_ROUND_SUFFIX}",
                 id="results-schedule_result_submission_jobs"),
    pytest.param("results", _arm_results_now, f"results{_ROUND_SUFFIX}",
                 id="results-run_result_submission_now"),
    pytest.param("rsvp_notice", _arm_check_in, f"rsvp_notice{_ROUND_SUFFIX}",
                 id="rsvp_notice-schedule_attendance_round"),
    pytest.param("rsvp_last_notice", _arm_check_in, f"rsvp_last_notice{_ROUND_SUFFIX}",
                 id="rsvp_last_notice-schedule_attendance_round"),
    pytest.param("rsvp_deadline", _arm_check_in, f"rsvp_deadline{_ROUND_SUFFIX}",
                 id="rsvp_deadline-schedule_attendance_round"),
    pytest.param("rsvp_cleanup", _arm_check_in, f"rsvp_cleanup{_ROUND_SUFFIX}",
                 id="rsvp_cleanup-schedule_attendance_round"),
    pytest.param("cleanup", _arm_cleanups, f"cleanup{_ROUND_SUFFIX}",
                 id="cleanup-schedule_round_cleanups"),
    pytest.param("rsvp_cleanup", _arm_cleanups, f"rsvp_cleanup{_ROUND_SUFFIX}",
                 id="rsvp_cleanup-schedule_round_cleanups"),
]


@pytest.mark.xfail(strict=True, reason=NO_ROUND_JOBS)
@pytest.mark.parametrize("kind, arm, job_id", EVERY_ROUND_JOB)
async def test_every_round_job_runs_through_one_runner(services, kind, arm, job_id):
    """Every kind of round job is signed up with a handler of its own, and core's round jobs arm
    the round on a paused scheduler: the job stands under the id it has always had, its callable
    is the one runner a one-off timer runs through, and its kind and the round id are its
    keywords. Fired with them, the runner calls that kind's handler alone, once, with the round
    id."""
    from leaguebot.core.services import scheduler_service
    from leaguebot.core.services.rounds.round_jobs import RoundJobs

    service = services()
    calls: dict[str, list[tuple[Any, ...]]] = {}

    def _handler_of(name: str) -> Any:
        async def _handler(*args: Any) -> None:
            calls.setdefault(name, []).append(args)
        return _handler

    for each in {param.values[0] for param in EVERY_ROUND_JOB}:
        service.register_timer(each, _handler_of(each))

    service.start(paused=True)
    arm(RoundJobs(service, service._db_path), _a_round())
    job = service._scheduler.get_job(job_id)
    assert job is not None, f"nothing is stored under {job_id}"

    assert job.func is scheduler_service._one_off_timer_job
    assert job.args == ()
    assert job.kwargs == {"kind": kind, "key": _ROUND_ID}

    await job.func(*job.args, **job.kwargs)

    assert calls == {kind: [(_ROUND_ID,)]}
