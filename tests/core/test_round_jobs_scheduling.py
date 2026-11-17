"""Core's round jobs (`RoundJobs`): a round's scheduling, its results alone and the season end.

`RoundJobs` arms and cancels a round's jobs through the framework's scheduler (#544), under the
same ids, names and moments as the scheduler armed them before it.

Its starting, pausing and shutting down are in `tests/framework/test_scheduler_lifecycle.py`.

Issue #208. `schedule_all_rounds`, the signup close timer and the season-end cancellation were
uncovered. They are thin, and each has one property that matters more than its size suggests. What
every job is stored with, and that each kind's handler is reached through the one runner, is pinned
in `test_round_jobs_runner.py`.

**Every lead is armed under the round's id.** Each module hands core the leads it wants set for a
round, results its submission among them, and `arm_leads` arms each under the same id-suffix rule
every round job uses — the round id is in it, so renumbering cannot make one round's job overwrite
another's.

**Re-arming the signup timer replaces it.** A manager who changes the close time must not end
up with two closes, and the job id is per server for that reason.

**Cancelling something that is not scheduled is not an error.** A job that has already fired,
or was never armed, is the ordinary case for every cancellation here; that is pinned, for each
of them, in `test_round_jobs_cancels.py`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import leaguebot.framework.services.jobs.scheduler as scheduler_module
from leaguebot.core.services.rounds.round_moments import POST_RACE_CLEANUP_DELAY
from leaguebot.framework.services.jobs.scheduler import SchedulerService


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



def _round_jobs(service):
    """Core's round jobs over *service*, built as the builder builds them."""
    from leaguebot.core.services.rounds.round_jobs import RoundJobs

    return RoundJobs(service, service._db_path)


def _added(service) -> list:
    return service._scheduler.add_job.call_args_list


# ---------------------------------------------------------------------------
# Scheduling rounds
# ---------------------------------------------------------------------------


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


@pytest.mark.xfail(strict=True, reason="#544: core runs no lead at once yet (run_lead_now)")
def test_a_submission_run_at_once_replaces_the_round_s_job_and_runs_however_late():
    """A round whose moment passed while its timed work stood removed (an amendment stopped on
    the change queue) has its results submission, the lead marked to run at once, run at once:
    the round's own job, under its id, armed with no trigger, which APScheduler runs at once,
    and with no lateness limit."""
    service = _service()
    lead = _lead("results", timedelta(0), "Result submission", run_at_once=True,
                 left_to_advance=True)

    _round_jobs(service).run_lead_now(_round(21, number=3), lead, 7, 1)

    [added] = _added(service)
    assert added.kwargs.get("trigger") is None
    assert added.kwargs["id"] == "results_s7_d1_r3_id21"
    assert added.kwargs["replace_existing"] is True
    assert added.kwargs["misfire_grace_time"] is None


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
    """A round on 1 March 2026 at 18:00 is armed in full, its forecast and its check-in (call 5
    days before, last notice 24 hours, deadline 2 hours), with `now` a month before it: every job
    stands at its moment, the three phases at 5 days, 2 days and 2 hours before the start, the
    submission at it, and both clean-ups at one figure after it, so a round's channels clear at
    the same moment."""
    service = _service()
    start = datetime(2026, 3, 1, 18, tzinfo=timezone.utc)
    rnd = _round_starting(start)

    _round_jobs(service).schedule_round(rnd, season_number=7, division_tier=1)
    jobs = _attendance_jobs(service, rnd, now=start - timedelta(days=30))

    moments = {job_id: job.kwargs["trigger"].run_date for job_id, job in jobs.items()}
    assert moments == {
        "weather_p1_s7_d1_r3_id21": start - timedelta(days=5),
        "weather_p2_s7_d1_r3_id21": start - timedelta(days=2),
        "weather_p3_s7_d1_r3_id21": start - timedelta(hours=2),
        "cleanup_s7_d1_r3_id21": start + POST_RACE_CLEANUP_DELAY,
        "results_s7_d1_r3_id21": start,
        "rsvp_notice_s7_d1_r3_id21": start - timedelta(days=5),
        "rsvp_last_notice_s7_d1_r3_id21": start - timedelta(hours=24),
        "rsvp_deadline_s7_d1_r3_id21": start - timedelta(hours=2),
        "rsvp_cleanup_s7_d1_r3_id21": start + POST_RACE_CLEANUP_DELAY,
    }


# ---------------------------------------------------------------------------
# Arming a round's leads (#544)
# ---------------------------------------------------------------------------
#
# Each module answers with the leads it wants set for a round (`RoundLead`), and core arms them
# under its own naming (`RoundJobs.arm_leads`): the id, moment, name and key every round job has
# always carried.

def _lead(kind, offset, label, **marks):
    from leaguebot.core.models.round import RoundLead

    return RoundLead(kind=kind, offset=offset, label=label, **marks)


@pytest.mark.xfail(strict=True, reason="#544: core has no RoundLead nor RoundJobs.arm_leads yet")
def test_each_lead_is_armed_under_the_round_s_id_moment_and_name():
    """A round (id 21, number 3, season 7, tier 1) on 1 March 2026 at 18:00 is handed four leads
    with `now` a month before it: weather's first phase 5 days before, the forecast clean-up a
    day after, the result submission at the start and the check-in's last notice 24 hours before.
    Each is armed under ``<kind>_s7_d1_r3_id21``, at its moment, named ``<label> s7 d1 r3``,
    handed its kind and the round id as its key, replacing any job of its id, with no lateness
    limit."""
    service = _service()
    start = datetime(2026, 3, 1, 18, tzinfo=timezone.utc)
    leads = (
        _lead("weather_p1", -timedelta(days=5), "Weather P1"),
        _lead("cleanup", POST_RACE_CLEANUP_DELAY, "Forecast cleanup", clean_up=True),
        _lead("results", timedelta(0), "Result submission", run_at_once=True,
              left_to_advance=True),
        _lead("rsvp_last_notice", -timedelta(hours=24), "RSVP last-notice", ahead_only=True),
    )

    _round_jobs(service).arm_leads(
        _round_starting(start), leads, 7, 1, start - timedelta(days=30)
    )

    armed = [(c.kwargs["id"], c.kwargs["trigger"].run_date, c.kwargs["name"], c.kwargs["kwargs"],
              c.kwargs["replace_existing"], c.kwargs["misfire_grace_time"])
             for c in _added(service)]
    assert armed == [
        ("weather_p1_s7_d1_r3_id21", start - timedelta(days=5), "Weather P1 s7 d1 r3",
         {"kind": "weather_p1", "key": 21}, True, None),
        ("cleanup_s7_d1_r3_id21", start + POST_RACE_CLEANUP_DELAY, "Forecast cleanup s7 d1 r3",
         {"kind": "cleanup", "key": 21}, True, None),
        ("results_s7_d1_r3_id21", start, "Result submission s7 d1 r3",
         {"kind": "results", "key": 21}, True, None),
        ("rsvp_last_notice_s7_d1_r3_id21", start - timedelta(hours=24),
         "RSVP last-notice s7 d1 r3", {"kind": "rsvp_last_notice", "key": 21}, True, None),
    ]


@pytest.mark.xfail(strict=True, reason="#544: core has no RoundLead nor RoundJobs.arm_leads yet")
def test_a_lead_armed_only_while_ahead_is_left_out_once_its_moment_has_passed():
    """A round on 1 March 2099 at 18:00, still to come by the wall clock, is handed its leads
    with `now` two days after its start. The check-in's call and clean-up, armed only while
    still ahead, are left out; the third forecast phase and the forecast clean-up, armed whatever
    the hour, are armed at their past moments; and the result submission, marked to run at once,
    is only armed at its moment, `arm_leads` running nothing itself."""
    service = _service()
    start = datetime(2099, 3, 1, 18, tzinfo=timezone.utc)
    leads = (
        _lead("rsvp_notice", -timedelta(days=5), "RSVP notice", ahead_only=True),
        _lead("weather_p3", -timedelta(hours=2), "Weather P3"),
        _lead("cleanup", POST_RACE_CLEANUP_DELAY, "Forecast cleanup", clean_up=True),
        _lead("rsvp_cleanup", POST_RACE_CLEANUP_DELAY, "RSVP cleanup", ahead_only=True,
              clean_up=True),
        _lead("results", timedelta(0), "Result submission", run_at_once=True,
              left_to_advance=True),
    )

    _round_jobs(service).arm_leads(
        _round_starting(start), leads, 7, 1, start + timedelta(days=2)
    )

    armed = {c.kwargs["id"]: c.kwargs["trigger"].run_date for c in _added(service)}
    assert armed == {
        "weather_p3_s7_d1_r3_id21": start - timedelta(hours=2),
        "cleanup_s7_d1_r3_id21": start + POST_RACE_CLEANUP_DELAY,
        "results_s7_d1_r3_id21": start,
    }


# ---------------------------------------------------------------------------
# The season end
# ---------------------------------------------------------------------------



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
