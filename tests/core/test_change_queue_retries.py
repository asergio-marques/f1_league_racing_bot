"""The change queue when Discord fails a job: the queue stops, and the job is tried again (#439).

The owner's rule (2026-10-02): the queue is a list of jobs carried out in order, and any job that
fails stops it until the job is cleared, by the bot's own tries 1, 5, 10, 15, 30 and 60 minutes
after its first failure, by Retry, or by an admin's Discard. `docs/design/architecture.md`, "How a
change is carried out", designs it. A message already gone still completes a removal or an edit.
Retry and Discard themselves are in `test_change_queue_controls.py`; the dummy change types and the
league are `test_change_queue.py`'s.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import discord
import pytest

from tests.core.test_change_queue import (  # noqa: F401 — `env` is the fixture
    NOW,
    _act,
    _api,
    _ask,
    _lines,
    _queue,
    _states,
    _type,
    env,
)
from tests.support.change_queue import (
    http_error,
    run_queue,
    step_rows,
    stopped_job,
)

#: What is not yet true of each test marked with it.
STOPS = "#439: a job Discord fails does not yet stop the queue on the 1-60 minute schedule"

HOUR_LINE = "still fails after an hour. The bot has stopped trying on its own"


def _flaky(name: str, holder: dict, *, kind=None, ran: list | None = None, **step) -> Any:
    """A step that is not a `SAVE`, failing with `holder["fail"]` while it is set and succeeding
    once it is cleared."""
    api = _api()

    async def _run(*args):
        if ran is not None:
            ran.append(name)
        if holder.get("fail") is not None:
            raise holder["fail"]
        return api.StepResult()

    return api.Step(name, kind or api.StepKind.ACT, _run, **step)


def _missing_access() -> Any:
    return _api().StepFailedOnDiscord("Missing Access")


async def _stopped(env) -> dict:
    step = await stopped_job(env.db_path)
    assert step is not None, "the queue is not stopped"
    return step


async def _try_when_due(env) -> None:
    """Move the clock to the stopped job's next try, and run the queue."""
    step = await _stopped(env)
    env.clock.now = datetime.fromisoformat(step["next_try_at"])
    await run_queue(env.bot)


# ---------------------------------------------------------------------------
# The tries
# ---------------------------------------------------------------------------


async def test_a_discord_failure_stops_the_queue_and_holds_a_later_change_through_every_try(env):
    """A job Discord fails stops the queue: it is tried again 1, 5, 10, 15, 30 and 60 minutes
    after its first failure, and a change asked after it waits the whole time."""
    holder = {"fail": _missing_access()}
    ran: list[str] = []
    _queue(env, _type(steps=[_flaky("post", holder)]), _type("later", steps=[_act("b", ran)]))

    await _ask(env)
    await _ask(env, "later")
    await run_queue(env.bot)

    step = await _stopped(env)
    assert step["tries"] == 1
    assert step["failing_since"] == NOW.isoformat()
    assert "Missing Access" in step["last_failure"]
    marks = [datetime.fromisoformat(step["next_try_at"]) - NOW]
    for _ in range(5):
        await _try_when_due(env)
        step = await _stopped(env)
        marks.append(datetime.fromisoformat(step["next_try_at"]) - NOW)

    assert [mark.total_seconds() / 60 for mark in marks] == [1, 5, 10, 15, 30, 60]
    assert step["tries"] == 6
    assert step["failing_since"] == NOW.isoformat()
    assert ran == []
    assert await _states(env) == ["RUNNING", "QUEUED"]


async def test_a_job_still_failing_at_the_sixty_minute_try_is_reported_once_and_tried_no_more(env):
    """The 60-minute try that fails writes one ❌ line naming the job, and the bot tries it no
    more on its own: from then on only Retry, or Discard, moves the queue."""
    holder = {"fail": _missing_access()}
    ran: list[str] = []

    async def _describe(_ctx):
        return "posting the dummy"

    _queue(env, _type(steps=[_flaky("post", holder, ran=ran, describe=_describe)]))

    await _ask(env)
    await run_queue(env.bot)
    for _ in range(5):
        assert [line for line in await _lines(env) if HOUR_LINE in line] == []
        await _try_when_due(env)
    step = await _stopped(env)
    assert datetime.fromisoformat(step["next_try_at"]) == NOW + timedelta(hours=1)
    await _try_when_due(env)

    step = await _stopped(env)
    [line] = [line for line in await _lines(env) if HOUR_LINE in line]
    assert line.startswith(f"❌ Job #{step['id']} (posting the dummy) still fails after an hour.")
    assert "press Retry on its notice, or Discard" in line
    assert step["next_try_at"] is None
    assert len(ran) == 7

    env.clock.advance(days=1)
    await run_queue(env.bot)

    assert len(ran) == 7
    assert len([line for line in await _lines(env) if HOUR_LINE in line]) == 1
    assert await _states(env) == ["RUNNING"]


async def test_a_stopped_job_no_longer_due_at_its_next_try_is_dropped_and_the_rest_go_ahead(env):
    """A stopped job found no longer due at its next try is dropped, and the queue runs on: the
    change's later jobs, then the change asked after it."""
    holder = {"fail": _missing_access()}
    due = {"post": True}
    ran: list[str] = []

    async def _still_due(_ctx):
        return due["post"]

    _queue(
        env,
        _type(steps=[_flaky("post", holder, ran=ran, still_due=_still_due), _act("after", ran)]),
        _type("later", steps=[_act("b", ran)]),
    )

    await _ask(env)
    await _ask(env, "later")
    await run_queue(env.bot)
    assert ran == ["post"]

    due["post"] = False
    await _try_when_due(env)

    steps = await step_rows(env.db_path)
    assert steps[0]["result"] == {"dropped": True}
    assert ran == ["post", "after", "b"]
    assert await _states(env) == ["DONE", "DONE"]


# ---------------------------------------------------------------------------
# What is already gone
# ---------------------------------------------------------------------------


async def test_a_message_already_gone_completes_a_delete_without_a_line(env):
    api = _api()
    holder = {"fail": http_error(discord.NotFound, status=404, text="Unknown Message")}
    _queue(env, _type(steps=[_flaky("remove", holder, kind=api.StepKind.DELETE)]))

    await _ask(env)
    await run_queue(env.bot)

    [step] = await step_rows(env.db_path)
    assert step["result"] == {"gone": True}
    assert step["tries"] == 0
    assert await _lines(env) == []
    assert await _states(env) == ["DONE"]


async def test_a_message_already_gone_completes_an_edit_with_a_line(env):
    api = _api()
    gone = "ℹ️ The dummy message was gone, so it was not edited."
    holder = {"fail": http_error(discord.NotFound, status=404, text="Unknown Message")}
    _queue(env, _type(steps=[_flaky("edit", holder, kind=api.StepKind.EDIT, gone_line=gone)]))

    await _ask(env)
    await run_queue(env.bot)

    [step] = await step_rows(env.db_path)
    assert step["done_at"] is not None
    assert step["tries"] == 0
    assert any(gone in line for line in await _lines(env))
    assert await _states(env) == ["DONE"]
