"""The scheduler's job identifiers, and the module-level callables APScheduler fires.

Issue #208. `scheduler_service.py` was at 50.7%. Every timed thing the bot does — forecasts,
check-in calls, deadlines, result submission — is a row in a job store keyed by one of these
identifiers, so the identifier scheme is load-bearing in a way nothing else in the bot is.

**The round id in the suffix prevents a silent catastrophe**, and the function's own docstring
records it. `renumber_rounds` rewrites the numbers of a whole division whenever an amendment
changes the order of its rounds, and nothing re-arms the jobs of the rounds that merely
shifted — so a round that became number 4 kept jobs saying `r5`, and the next amendment of the
round that became number 5 scheduled `..._r5` on top of them. Every `add_job` passes
`replace_existing=True`, so that destroyed the first round's entire schedule: its forecasts,
its result submission and its check-in, with nothing reporting it.

`test_two_rounds_that_swapped_numbers_keep_distinct_job_ids` is the regression test for exactly
that, and it is the reason this file exists. The season, tier and number stay in the identifier
only to make it readable in an admin view — they are decoration, and a number left stale by a
renumbering is cosmetic.

**The module-level job callables are the other half.** They have to be module-level and
picklable because `SQLAlchemyJobStore` stores them, which means they cannot close over the
service and must reach it through a global. Every one of them therefore guards against that
global being unset and against its callback being unregistered — a job firing during startup,
or for a module since switched off, must log and return rather than raise inside APScheduler's
executor where nothing would catch it.

`get_pending_advance_jobs` parses the identifier back apart, which is the other reason the
scheme matters: a change to the suffix format breaks the reader as well as the writer, and
these tests hold both ends against each other.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import services.scheduler_service as ss  # noqa: E402
from services.scheduler_service import (  # noqa: E402
    SchedulerService,
    _portrait_refresh_job,
    _round_job_suffix,
    _signup_close_timer_job,
)

SERVER_ID = 10408


def _round(round_id: int, round_number: int):
    return SimpleNamespace(id=round_id, round_number=round_number)


# ---------------------------------------------------------------------------
# The identifier scheme
# ---------------------------------------------------------------------------


def test_the_suffix_carries_season_tier_number_and_id():
    """All four, in that order. The reader below parses this back apart."""
    assert _round_job_suffix(_round(42, 5), season_number=3, division_tier=2) == (
        "_s3_d2_r5_id42"
    )


def test_two_rounds_that_swapped_numbers_keep_distinct_job_ids():
    """The regression this scheme exists for. After a renumbering, one round says `r5`
    from before and another says `r5` now — and with `replace_existing=True` on every
    `add_job`, identical ids would destroy the first round's whole schedule silently."""
    stale = _round(42, 5)  # was number 5, is now number 4, jobs never re-armed
    current = _round(43, 5)  # has just become number 5

    assert _round_job_suffix(stale, 3, 2) != _round_job_suffix(current, 3, 2)


def test_the_round_id_is_what_makes_it_unique():
    """Season, tier and number are decoration; two rounds agreeing on all three but
    differing in id must still differ."""
    first = _round_job_suffix(_round(42, 5), 3, 2)
    second = _round_job_suffix(_round(43, 5), 3, 2)

    assert "id42" in first
    assert "id43" in second


def test_a_stale_round_number_is_only_cosmetic():
    """A renumbering leaves the number wrong until the round is next scheduled. That is
    tolerated deliberately — the id is what the scheduler keys on."""
    before = _round_job_suffix(_round(42, 5), 3, 2)
    after = _round_job_suffix(_round(42, 4), 3, 2)

    assert before != after
    assert before.endswith("id42")
    assert after.endswith("id42")


# ---------------------------------------------------------------------------
# Reading the identifier back
# ---------------------------------------------------------------------------


def _job(job_id: str, round_id: int | None, *, minutes: int = 5, paused: bool = False):
    job = MagicMock()
    job.id = job_id
    job.kwargs = {"round_id": round_id} if round_id is not None else {}
    job.next_run_time = (
        None if paused else datetime.now(timezone.utc) + timedelta(minutes=minutes)
    )
    return job


def _service(jobs: list) -> SchedulerService:
    service = SchedulerService.__new__(SchedulerService)
    service._scheduler = MagicMock()
    service._scheduler.get_jobs = MagicMock(return_value=jobs)
    return service


def test_each_event_type_maps_to_its_phase_number():
    """The advance command fires events by phase number, so a prefix mapped wrongly would
    fire the wrong thing — a check-in deadline in place of a forecast."""
    suffix = _round_job_suffix(_round(42, 5), 3, 2)
    jobs = [
        _job(f"weather_p1{suffix}", 42, minutes=1),
        _job(f"weather_p2{suffix}", 42, minutes=2),
        _job(f"weather_p3{suffix}", 42, minutes=3),
        _job(f"rsvp_notice{suffix}", 42, minutes=4),
        _job(f"rsvp_last_notice{suffix}", 42, minutes=5),
        _job(f"rsvp_deadline{suffix}", 42, minutes=6),
    ]

    pending = _service(jobs).get_pending_advance_jobs({42})

    assert [p["phase_number"] for p in pending] == [1, 2, 3, 5, 6, 7]


def test_jobs_are_returned_in_the_order_they_will_fire():
    """Advance replays a season in order; out of sequence it would post a deadline before
    the call it closes."""
    suffix = _round_job_suffix(_round(42, 5), 3, 2)
    jobs = [
        _job(f"weather_p3{suffix}", 42, minutes=30),
        _job(f"weather_p1{suffix}", 42, minutes=10),
        _job(f"weather_p2{suffix}", 42, minutes=20),
    ]

    pending = _service(jobs).get_pending_advance_jobs({42})

    assert [p["phase_number"] for p in pending] == [1, 2, 3]


def test_a_paused_job_is_not_pending():
    """`next_run_time is None` means it will not fire, and advance must not claim it will."""
    suffix = _round_job_suffix(_round(42, 5), 3, 2)
    jobs = [_job(f"weather_p1{suffix}", 42, paused=True)]

    assert _service(jobs).get_pending_advance_jobs({42}) == []


def test_another_round_s_jobs_are_not_returned():
    suffix_mine = _round_job_suffix(_round(42, 5), 3, 2)
    suffix_theirs = _round_job_suffix(_round(43, 6), 3, 2)
    jobs = [
        _job(f"weather_p1{suffix_mine}", 42),
        _job(f"weather_p1{suffix_theirs}", 43),
    ]

    pending = _service(jobs).get_pending_advance_jobs({42})

    assert [p["round_id"] for p in pending] == [42]


def test_cleanup_and_season_end_jobs_are_excluded():
    """They are not events a season fires, so advance must not replay them."""
    suffix = _round_job_suffix(_round(42, 5), 3, 2)
    jobs = [
        _job(f"cleanup{suffix}", 42),
        _job(f"season_end{suffix}", 42),
        _job(f"weather_p1{suffix}", 42),
    ]

    pending = _service(jobs).get_pending_advance_jobs({42})

    assert [p["phase_number"] for p in pending] == [1]


def test_a_results_job_is_never_returned():
    """Result submission is found from database state instead, so that a past-dated job
    which already auto-fired can neither block the wizard nor open it twice. The docstring
    once listed it as phase 4 (issue #149)."""
    suffix = _round_job_suffix(_round(42, 5), 3, 2)
    jobs = [_job(f"results{suffix}", 42), _job(f"weather_p1{suffix}", 42)]

    pending = _service(jobs).get_pending_advance_jobs({42})

    assert [p["job_id"] for p in pending] == [f"weather_p1{suffix}"]


def test_a_job_with_no_round_is_ignored():
    """The portrait refresh and the signup close timer are server-scoped and carry no
    round."""
    jobs = [_job("portrait_refresh", None)]

    assert _service(jobs).get_pending_advance_jobs({42}) == []


def test_a_job_whose_id_does_not_parse_is_ignored():
    """Rather than raising. A job from an older version of the bot must not stop the
    advance command working."""
    jobs = [_job("something_odd", 42)]

    assert _service(jobs).get_pending_advance_jobs({42}) == []


def test_every_job_for_a_round_is_listed_without_filtering():
    """`get_job_ids_for_rounds` is the review summary's view and deliberately keeps the
    ones advance excludes — it answers "is a job queued", not "what will advance fire"."""
    suffix = _round_job_suffix(_round(42, 5), 3, 2)
    jobs = [
        _job(f"weather_p1{suffix}", 42),
        _job(f"cleanup{suffix}", 42),
        _job(f"results{suffix}", 42),
    ]

    ids = _service(jobs).get_job_ids_for_rounds({42})

    assert len(ids) == 3


def test_a_paused_job_is_not_listed_as_queued():
    """The summary distinguishes "queued" from "absent", and a paused job is neither
    going to fire nor worth reporting as pending."""
    suffix = _round_job_suffix(_round(42, 5), 3, 2)
    jobs = [_job(f"weather_p1{suffix}", 42, paused=True)]

    assert _service(jobs).get_job_ids_for_rounds({42}) == set()


# ---------------------------------------------------------------------------
# The module-level callables
# ---------------------------------------------------------------------------


@pytest.fixture
def no_global(monkeypatch):
    """The state during startup, before `start()` sets the global."""
    monkeypatch.setattr(ss, "_GLOBAL_SERVICE", None)


@pytest.fixture
def service_with(monkeypatch):
    """Install a global service, and hand back a setter for its callbacks."""

    def _install(**callbacks):
        service = SchedulerService.__new__(SchedulerService)
        service._signup_close_callback = callbacks.get("signup_close")
        service._portrait_refresh_callback = callbacks.get("portrait_refresh")
        monkeypatch.setattr(ss, "_GLOBAL_SERVICE", service)
        return service

    return _install


@pytest.mark.parametrize(
    "job,args", [(_signup_close_timer_job, ()), (_portrait_refresh_job, ())],
    ids=["signup-close", "portrait-refresh"],
)
async def test_a_job_firing_before_the_service_exists_is_survived(no_global, job, args, caplog):
    """A persisted job can fire during startup, before `start()` sets the global. Raising
    inside APScheduler's executor would surface nowhere."""
    with caplog.at_level("WARNING"):
        await job(*args)

    assert "_GLOBAL_SERVICE is None" in caplog.text


async def test_the_signup_close_timer_calls_its_callback(service_with):
    callback = AsyncMock(return_value=None)
    service_with(signup_close=callback)

    await _signup_close_timer_job()

    callback.assert_awaited_once_with()


async def test_the_portrait_refresh_calls_its_callback(service_with):
    callback = AsyncMock(return_value=None)
    service_with(portrait_refresh=callback)

    await _portrait_refresh_job()

    callback.assert_awaited_once_with()


@pytest.mark.parametrize(
    "job,args", [(_signup_close_timer_job, ()), (_portrait_refresh_job, ())],
    ids=["signup-close", "portrait-refresh"],
)
async def test_a_job_with_no_callback_registered_is_survived(service_with, job, args, caplog):
    """A job persisted for a module since switched off. It must log and return rather
    than raise — the module-output rule in the direction nobody thinks about."""
    service_with()

    with caplog.at_level("WARNING"):
        await job(*args)

    assert "no callback registered" in caplog.text
