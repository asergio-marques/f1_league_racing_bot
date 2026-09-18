"""The scheduler service's registration, its result-only scheduling and its signup timer.

Issue #208. The callback registrations, `schedule_all_rounds`, `schedule_result_submission_jobs`,
the signup close timer and the season-end cancellation were uncovered. They are thin, and each
has one property that matters more than its size suggests.

**Every callback registers under the attribute its job function reads.** The module-level job
functions reach the service through a global and read one attribute each; a registration that
wrote a different name would leave the job skipping silently for ever, logging only that no
callback was registered. The pairing is tested for all of them together, because that is the
shape the mistake would take.

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
or was never armed, is the ordinary case for every cancellation here.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import services.scheduler_service as scheduler_module  # noqa: E402
from services.scheduler_service import SchedulerService  # noqa: E402



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
    service._phase_callbacks = {}
    for attr in (
        "_signup_close_callback",
        "_portrait_refresh_callback",
        "_mystery_notice_callback",
        "_forecast_cleanup_callback",
        "_result_submission_callback",
        "_rsvp_notice_callback",
        "_rsvp_last_notice_callback",
        "_rsvp_deadline_callback",
    ):
        setattr(service, attr, None)
    return service


def _round(round_id=21, division_id=11, number=3, *, naive=False):
    at = datetime(2026, 3, 1, 18, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=round_id,
        division_id=division_id,
        round_number=number,
        scheduled_at=at.replace(tzinfo=None) if naive else at,
    )


def _added(service) -> list:
    return service._scheduler.add_job.call_args_list


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "register,attribute",
    [
        ("register_mystery_notice_callback", "_mystery_notice_callback"),
        ("register_forecast_cleanup_callback", "_forecast_cleanup_callback"),
        ("register_result_submission_callback", "_result_submission_callback"),
        ("register_rsvp_notice_callback", "_rsvp_notice_callback"),
        ("register_rsvp_last_notice_callback", "_rsvp_last_notice_callback"),
        ("register_rsvp_deadline_callback", "_rsvp_deadline_callback"),
        ("register_signup_close_callback", "_signup_close_callback"),
        ("register_portrait_refresh_callback", "_portrait_refresh_callback"),
    ],
)
def test_each_callback_registers_where_its_job_reads_it(register, attribute):
    """A registration writing a different name leaves the job skipping silently for ever."""
    service = _service()
    callback = MagicMock()

    getattr(service, register)(callback)

    assert getattr(service, attribute) is callback


def test_the_three_phase_callbacks_are_keyed_by_phase():
    service = _service()
    p1, p2, p3 = MagicMock(), MagicMock(), MagicMock()

    service.register_callbacks(p1, p2, p3)

    assert service._phase_callbacks == {1: p1, 2: p2, 3: p3}


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

    assert len(_added(service)) == 2
    assert all(
        c.args[0] is scheduler_module._result_submission_job_wrapper for c in _added(service)
    )


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
    assert job["kwargs"] == {"round_id": 21}


def test_a_naive_round_time_is_read_as_utc():
    service = _service()

    service.schedule_result_submission_jobs([_round(naive=True)], division_meta={11: (7, 1)})

    assert _added(service)[0].kwargs["trigger"].run_date.utcoffset() == timedelta(0)


# ---------------------------------------------------------------------------
# The signup timer and the cancellations
# ---------------------------------------------------------------------------


def test_the_signup_timer_is_armed_for_the_league():
    service = _service()

    service.schedule_signup_close_timer("2026-03-01T20:00:00")

    job = _added(service)[0]
    assert job.args[0] is scheduler_module._signup_close_timer_job
    assert job.kwargs["id"] == "signup_close"
    assert "kwargs" not in job.kwargs
    assert job.kwargs["trigger"].run_date == datetime(2026, 3, 1, 20, tzinfo=timezone.utc)


def test_re_arming_the_signup_timer_replaces_it():
    """A manager who changes the close time must not end up with two closes."""
    service = _service()

    service.schedule_signup_close_timer("2026-03-01T20:00:00")
    service.schedule_signup_close_timer("2026-03-02T20:00:00")

    ids = {c.kwargs["id"] for c in _added(service)}
    assert ids == {"signup_close"}
    assert all(c.kwargs["replace_existing"] for c in _added(service))


@pytest.mark.parametrize(
    "cancel,args,job_id",
    [
        ("cancel_signup_close_timer", (), "signup_close"),
        ("cancel_job", ("weather_p1_x",), "weather_p1_x"),
    ],
)
def test_a_cancellation_removes_its_job(cancel, args, job_id):
    service = _service()

    getattr(service, cancel)(*args)

    service._scheduler.remove_job.assert_called_once_with(job_id)


@pytest.mark.parametrize(
    "cancel,args",
    [
        ("cancel_signup_close_timer", ()),
        ("cancel_job", ("gone",)),
    ],
)
def test_cancelling_what_is_not_scheduled_is_not_an_error(cancel, args):
    """A job that has already fired, or was never armed, is the ordinary case."""
    service = _service()
    service._scheduler.remove_job = MagicMock(side_effect=Exception("No job by the id"))

    getattr(service, cancel)(*args)  # must not raise


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


def test_a_season_end_job_that_will_not_go_is_stepped_over():
    service = _service()
    service._scheduler.get_jobs = MagicMock(return_value=[SimpleNamespace(id="season_end_1")])
    service._scheduler.remove_job = MagicMock(side_effect=Exception("No job by the id"))

    service.cancel_season_end()  # must not raise
