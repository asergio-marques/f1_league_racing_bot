"""Core's round jobs (`RoundJobs`): a round's scheduling, its results alone and the season end.

`RoundJobs` arms and cancels a round's jobs through the framework's scheduler (#544), under the
same ids, names and moments as the scheduler armed them before it.

Its starting, pausing and shutting down are in `tests/framework/test_scheduler_lifecycle.py`.

Issue #208. The arming of every round, the signup close timer and the season-end cancellation
were uncovered. They are thin, and each has one property that matters more than its size suggests. What
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


def _round_starting(start: datetime) -> SimpleNamespace:
    return SimpleNamespace(id=21, division_id=11, round_number=3, scheduled_at=start)


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
