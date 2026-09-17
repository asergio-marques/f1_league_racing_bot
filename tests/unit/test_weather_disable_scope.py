"""Switching the weather module off cancels the weather module's jobs and nothing else —
issue #117.

Every round carries eight scheduled jobs and all eight are keyed by the same ``round_id``
kwarg: three forecast phases and a post-race cleanup (weather), a result submission
(results), and three RSVP jobs (attendance). ``cancel_all_weather_for_server`` swept every
round of the server's ACTIVE and SETUP seasons through a whole-round cancel, so switching
weather off took the other four down with them, for every round still to come — while the
league was told that only weather jobs had been cancelled.

The result-submission job is the worse of the two losses. ``run_result_submission_job`` is
the round's one clock-driven status transition and runs whatever the modules, so without it
a round never leaves NOT_RUN and its season can never be completed. The three RSVP jobs are
the more permanent: only the confirmation of placements creates them, and it cannot be run again on an
active season.

These tests hold the cancel to the weather prefixes, hold the round sweep that must not
narrow with it, and pin the unfiltered default the round- and season-level cancels rely on.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402

SERVER_ID = 4242

# The eight job kinds a round carries, split by the module that owns them.
WEATHER_PREFIXES = ("weather_p1", "weather_p2", "weather_p3", "cleanup")
RESULTS_PREFIXES = ("results",)
ATTENDANCE_PREFIXES = ("rsvp_notice", "rsvp_last_notice", "rsvp_deadline")
ALL_PREFIXES = WEATHER_PREFIXES + RESULTS_PREFIXES + ATTENDANCE_PREFIXES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scheduler(db_path: str):
    """A SchedulerService over *db_path* with a mock APScheduler in place of a real one."""
    from services.scheduler_service import SchedulerService

    svc = SchedulerService.__new__(SchedulerService)
    svc._db_path = db_path
    mock_sched = MagicMock()
    mock_sched.remove_job = MagicMock()
    mock_sched.get_jobs = MagicMock(return_value=[])
    svc._scheduler = mock_sched
    return svc


def _make_mock_job(job_id: str, round_id: int) -> MagicMock:
    job = MagicMock()
    job.id = job_id
    job.kwargs = {"round_id": round_id}
    job.next_run_time = datetime(2099, 1, 1, tzinfo=timezone.utc)
    return job


def _round_jobs(round_id: int, *, season: int, tier: int, rnum: int) -> list[MagicMock]:
    """All eight jobs a round carries, as the scheduler would hold them."""
    suffix = f"_s{season}_d{tier}_r{rnum}"
    return [_make_mock_job(f"{prefix}{suffix}", round_id) for prefix in ALL_PREFIXES]


async def _seed(db_path: str, *, live_status: str = "ACTIVE") -> dict[str, int]:
    """One server with a live season of two rounds and an archived season of one.

    A server holds at most one live season — migration 049 enforces it with a partial
    unique index over SETUP and ACTIVE — so *live_status* chooses which of the two states
    the season in scope is in rather than seeding one of each.

    Returns the round IDs by name.
    """
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id, "
            " weather_module_enabled) VALUES (?, 100, 200, 300, 1)",
            (SERVER_ID,),
        )
        for season_id, season_number, status in (
            (1, 1, live_status),
            (3, 3, "COMPLETED"),
        ):
            await db.execute(
                "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
                "VALUES (?, ?, ?, '2026-01-01', ?)",
                (season_id, SERVER_ID, season_number, status),
            )
            await db.execute(
                "INSERT INTO divisions "
                "(id, season_id, name, tier, forecast_channel_id, mention_role_id) "
                "VALUES (?, ?, ?, 1, 999, 555)",
                (season_id, season_id, f"Div {season_id}"),
            )
        # The live season holds two rounds, the archived one a single round.
        for round_id, division_id, round_number in ((11, 1, 1), (12, 1, 2), (31, 3, 1)):
            await db.execute(
                "INSERT INTO rounds "
                "(id, division_id, round_number, format, track_name, scheduled_at) "
                "VALUES (?, ?, ?, 'NORMAL', 'Bahrain International Circuit', "
                "'2099-06-01T18:00:00+00:00')",
                (round_id, division_id, round_number),
            )
        await db.commit()
    return {"live_r1": 11, "live_r2": 12, "completed_r1": 31}


async def _disable_weather(tmp_path, jobs_for, *, live_status: str = "ACTIVE") -> list[str]:
    """Seed, load the scheduler with *jobs_for* (round ids → jobs), run the cancel, and
    return the job IDs it removed."""
    db_path = os.path.join(str(tmp_path), "test.db")
    ids = await _seed(db_path, live_status=live_status)
    svc = _make_scheduler(db_path)
    svc._scheduler.get_jobs.return_value = jobs_for(ids)
    await svc.cancel_all_weather_for_server(SERVER_ID)
    return [c.args[0] for c in svc._scheduler.remove_job.call_args_list]


def _all_seeded_jobs(ids: dict[str, int]) -> list[MagicMock]:
    return (
        _round_jobs(ids["live_r1"], season=1, tier=1, rnum=1)
        + _round_jobs(ids["live_r2"], season=1, tier=1, rnum=2)
        + _round_jobs(ids["completed_r1"], season=3, tier=1, rnum=1)
    )


# ---------------------------------------------------------------------------
# What the disable must still do
# ---------------------------------------------------------------------------

async def test_disable_cancels_the_four_weather_jobs(tmp_path):
    """All three forecast phases and the post-race cleanup go, for each round in scope."""
    removed = await _disable_weather(tmp_path, _all_seeded_jobs)

    for rnum in (1, 2):
        for prefix in WEATHER_PREFIXES:
            assert f"{prefix}_s1_d1_r{rnum}" in removed


async def test_disable_covers_every_round_of_the_season(tmp_path):
    """The round sweep is unchanged by the job-kind filter: every round of the live
    season is reached, not just the next one."""
    removed = await _disable_weather(tmp_path, _all_seeded_jobs)

    assert "weather_p1_s1_d1_r1" in removed
    assert "weather_p1_s1_d1_r2" in removed


@pytest.mark.parametrize("live_status", ["ACTIVE", "SETUP"])
async def test_disable_reaches_a_season_in_either_live_state(tmp_path, live_status):
    """A season still in setup has its forecasts cancelled just as a running one does."""
    removed = await _disable_weather(tmp_path, _all_seeded_jobs, live_status=live_status)

    assert "weather_p1_s1_d1_r1" in removed
    assert "cleanup_s1_d1_r2" in removed


async def test_disable_ignores_rounds_of_completed_seasons(tmp_path):
    """A completed season holds no work to lose and is left alone."""
    removed = await _disable_weather(tmp_path, _all_seeded_jobs)

    assert not any(jid.endswith("_s3_d1_r1") for jid in removed)


# ---------------------------------------------------------------------------
# What it must not touch — issue #117
# ---------------------------------------------------------------------------

async def test_disable_leaves_the_result_submission_job(tmp_path):
    """Results collection — and with it the round's own arrival transition — survives
    switching weather off."""
    removed = await _disable_weather(tmp_path, _all_seeded_jobs)

    assert "results_s1_d1_r1" not in removed
    assert "results_s1_d1_r2" not in removed


async def test_disable_leaves_the_three_rsvp_jobs(tmp_path):
    """The check-in call, its reminder and its deadline survive — nothing short of
    the confirmation of placements would put them back."""
    removed = await _disable_weather(tmp_path, _all_seeded_jobs)

    for prefix in ATTENDANCE_PREFIXES:
        assert f"{prefix}_s1_d1_r1" not in removed
        assert f"{prefix}_s1_d1_r2" not in removed


async def test_disable_removes_exactly_the_weather_jobs_in_scope(tmp_path):
    """Stated as a whole: four kinds × the two rounds in scope, and nothing else."""
    removed = await _disable_weather(tmp_path, _all_seeded_jobs)

    expected = {
        f"{prefix}{suffix}"
        for prefix in WEATHER_PREFIXES
        for suffix in ("_s1_d1_r1", "_s1_d1_r2")
    }
    assert set(removed) == expected
    assert len(removed) == len(expected)


async def test_disable_leaves_a_job_whose_id_carries_no_round_suffix(tmp_path):
    """A job keyed to the round but not named by the round-scoped convention cannot have
    its owner read, so the filter leaves it rather than guessing."""
    def _jobs(ids):
        return _all_seeded_jobs(ids) + [
            _make_mock_job("legacy_phase_job", ids["live_r1"])
        ]

    removed = await _disable_weather(tmp_path, _jobs)

    assert "legacy_phase_job" not in removed


# ---------------------------------------------------------------------------
# The unfiltered default, which the round- and season-level cancels rely on
# ---------------------------------------------------------------------------

async def test_cancel_round_without_a_filter_still_removes_every_job(tmp_path):
    """`/season cancel`, `/round cancel`, reset and amend all cancel a *round* and want
    all eight of its jobs gone.  The new keyword must not have changed that."""
    db_path = os.path.join(str(tmp_path), "test.db")
    await _seed(db_path)
    svc = _make_scheduler(db_path)
    svc._scheduler.get_jobs.return_value = _round_jobs(11, season=1, tier=1, rnum=1)

    svc.cancel_round(11)

    removed = [c.args[0] for c in svc._scheduler.remove_job.call_args_list]
    assert set(removed) == {f"{prefix}_s1_d1_r1" for prefix in ALL_PREFIXES}


async def test_cancel_round_with_a_filter_spares_other_rounds(tmp_path):
    """The filter narrows by job kind, never by round: a job of the same kind on another
    round is still left alone."""
    db_path = os.path.join(str(tmp_path), "test.db")
    await _seed(db_path)
    svc = _make_scheduler(db_path)
    from services.scheduler_service import _WEATHER_JOB_PREFIXES

    svc._scheduler.get_jobs.return_value = (
        _round_jobs(11, season=1, tier=1, rnum=1)
        + _round_jobs(12, season=1, tier=1, rnum=2)
    )

    svc.cancel_round(11, only=_WEATHER_JOB_PREFIXES)

    removed = [c.args[0] for c in svc._scheduler.remove_job.call_args_list]
    assert set(removed) == {f"{prefix}_s1_d1_r1" for prefix in WEATHER_PREFIXES}
