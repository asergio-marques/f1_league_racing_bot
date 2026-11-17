"""The one job runner: a timed job or background work that fails reaches the log channel (#453).

A job has no member to answer, so its failure was heard of only in the host's log. `run_job`
is the one place such work is run (architecture.md, "Errors and failures"): the traceback goes
to the host's log, one line to the log channel through the report it is handed, and the runner
never raises.
"""
from __future__ import annotations

import logging
import sqlite3
from unittest.mock import AsyncMock

import pytest

#: Why each test fails until the runner is built.
NOT_YET = "#453: there is no job runner"


def _raising(error: Exception):
    async def work() -> None:
        raise error

    return work


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_a_job_that_raises_is_logged_with_its_traceback_and_reported_once(caplog):
    """The work raises: one ERROR record carries the traceback and names the job, the report
    gets the job's failure line once, and the runner says the work failed without raising."""
    from leaguebot.framework.utils.interaction_errors import job_failure_line
    from leaguebot.framework.utils.job_runner import run_job

    error = RuntimeError("boom")
    report = AsyncMock()

    with caplog.at_level(logging.ERROR):
        succeeded = await run_job("rsvp_notice (key 12)", _raising(error), report=report)

    assert succeeded is False
    records = [record for record in caplog.records if record.exc_info]
    assert [record.exc_info[1] for record in records] == [error]
    assert records[0].levelno == logging.ERROR
    assert "rsvp_notice (key 12)" in records[0].getMessage()
    report.assert_awaited_once_with(job_failure_line("rsvp_notice (key 12)", error))


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_a_report_that_raises_is_logged_and_the_runner_still_does_not_raise(caplog):
    """The work raises and so does the report, the log channel being what failed: both faults
    reach the host's log, and the runner still returns rather than raising."""
    from leaguebot.framework.utils.job_runner import run_job

    error = RuntimeError("boom")
    refused = sqlite3.OperationalError("database is locked")

    with caplog.at_level(logging.WARNING):
        succeeded = await run_job(
            "log line retry", _raising(error), report=AsyncMock(side_effect=refused)
        )

    assert succeeded is False
    logged = {record.exc_info[1] for record in caplog.records if record.exc_info}
    assert logged == {error, refused}


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_a_job_that_succeeds_reports_nothing(caplog):
    """Work that finishes is not reported, and nothing about it reaches the host's log as a
    failure."""
    from leaguebot.framework.utils.job_runner import run_job

    work = AsyncMock()
    report = AsyncMock()

    with caplog.at_level(logging.WARNING):
        succeeded = await run_job("daily portrait refresh", work, report=report)

    assert succeeded is True
    work.assert_awaited_once_with()
    report.assert_not_awaited()
    assert [record for record in caplog.records if record.exc_info] == []
