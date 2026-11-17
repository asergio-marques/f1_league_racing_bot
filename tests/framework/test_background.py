"""Background work is started through one helper (#453).

A task started with `asyncio.ensure_future` and dropped can be collected mid-run, and its
failure reaches nobody. `start_background` holds each task until it ends and runs its work
through the job runner, so a failure is logged with its traceback and, where a report is
given, reported to the log channel (architecture.md, "Background work is started through one
helper").
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

#: Why each test fails until the helper is built.
NOT_YET = "#453: there is no background helper"


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_a_started_task_is_held_until_it_ends():
    """While the work runs the helper holds the task, so it cannot be collected; once it ends
    the helper lets it go."""
    from leaguebot.framework.utils import background

    release = asyncio.Event()

    async def work() -> None:
        await release.wait()

    task = background.start_background(work(), name="member warning")
    await asyncio.sleep(0)
    assert task in background._running

    release.set()
    await task
    await asyncio.sleep(0)
    assert task not in background._running


@pytest.mark.xfail(strict=True, reason=NOT_YET)
@pytest.mark.parametrize("reported", [True, False], ids=["with a report", "without a report"])
async def test_a_task_that_raises_is_logged_and_reported(reported, caplog):
    """The work raises: its traceback reaches the host's log, naming the task, and where a
    report is given one failure line goes through it; without one, the host's log alone."""
    from leaguebot.framework.utils.background import start_background
    from leaguebot.framework.utils.interaction_errors import job_failure_line

    error = KeyError("x")
    report = AsyncMock()

    async def work() -> None:
        raise error

    with caplog.at_level(logging.ERROR):
        task = start_background(
            work(), name="signup reason lapse", report=report if reported else None
        )
        await task

    records = [record for record in caplog.records if record.exc_info]
    assert [record.exc_info[1] for record in records] == [error]
    assert "signup reason lapse" in records[0].getMessage()
    if reported:
        report.assert_awaited_once_with(job_failure_line("signup reason lapse", error))
    else:
        report.assert_not_awaited()


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_a_cancelled_task_is_neither_logged_nor_reported(caplog):
    """A cancellation is not a failure: the task ends cancelled, nothing reaches the host's log
    as a fault and nothing is reported."""
    from leaguebot.framework.utils.background import start_background

    report = AsyncMock()

    async def work() -> None:
        await asyncio.Event().wait()

    with caplog.at_level(logging.WARNING):
        task = start_background(work(), name="factory reset clean-up", report=report)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert task.cancelled()
    assert [record for record in caplog.records if record.exc_info] == []
    report.assert_not_awaited()
