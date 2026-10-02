"""The change queue when Discord fails a step: retried with growing waits, reported, or tried once (#439).

`docs/design/architecture.md`, "A step that fails because of Discord is retried", designs it, and
the owner's "Try once" (2026-10-01) adds the step tried once: its failure is kept in its result for
the change's outcome to report, and it neither waits nor holds a place. The dummy change types and
the league are `test_change_queue.py`'s.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import discord
import pytest

from tests.core.test_change_queue import (  # noqa: F401 — `env` is the fixture
    NAMED,
    NOW,
    WHAT,
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
    change_rows,
    http_error,
    maybe_await,
    member_interaction,
    restart_queue,
    run_queue,
    step_rows,
    updated_reply,
)

STILL_AT_WORK = "is still at work"


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


async def _waiting_step(env) -> dict:
    [step] = [s for s in await step_rows(env.db_path) if s["done_at"] is None and s["next_try_at"]]
    return step


async def _retry_when_due(env) -> dict:
    """Move the clock to the waiting step's next try, run the queue, and give the step as it stands."""
    step = await _waiting_step(env)
    env.clock.now = datetime.fromisoformat(step["next_try_at"])
    await run_queue(env.bot)
    return await _waiting_step(env)


# ---------------------------------------------------------------------------
# Retries
# ---------------------------------------------------------------------------


async def test_a_discord_failure_is_retried_with_growing_waits_up_to_the_ceiling(env):
    holder = {"fail": _missing_access()}
    _queue(env, _type(steps=[_flaky("post", holder)]))

    await _ask(env)
    await run_queue(env.bot)

    step = await _waiting_step(env)
    assert await _states(env) == ["WAITING"]
    assert step["tries"] == 1
    assert step["failing_since"] == NOW.isoformat()
    assert "Missing Access" in step["last_failure"]
    waits = [datetime.fromisoformat(step["next_try_at"]) - NOW]
    for _ in range(7):
        tried_at = datetime.fromisoformat(step["next_try_at"])
        step = await _retry_when_due(env)
        waits.append(datetime.fromisoformat(step["next_try_at"]) - tried_at)

    assert [w.total_seconds() for w in waits] == [30, 60, 120, 240, 480, 900, 900, 900]
    assert step["tries"] == 8
    assert step["failing_since"] == NOW.isoformat()


async def test_a_change_waiting_on_a_retry_steps_aside_for_changes_elsewhere(env):
    holder = {"fail": _missing_access()}
    ran: list[str] = []
    _queue(
        env,
        _type("waiting", steps=[_flaky("post", holder)], places=("channel:1",)),
        _type("elsewhere", steps=[_act("b", ran)], places=("channel:2",)),
    )

    await _ask(env, "waiting")
    await _ask(env, "elsewhere")
    await run_queue(env.bot)

    assert ran == ["b"]
    assert await _states(env) == ["WAITING", "DONE"]


async def test_a_change_for_the_same_place_waits_behind_one_waiting_on_a_retry(env):
    holder = {"fail": _missing_access()}
    ran: list[str] = []
    _queue(
        env,
        _type("waiting", steps=[_flaky("post", holder, ran=ran)], places=("channel:1",)),
        _type("same", steps=[_act("b", ran)], places=("channel:1",)),
    )

    await _ask(env, "waiting")
    await _ask(env, "same")
    await run_queue(env.bot)

    assert ran == ["post"]
    assert await _states(env) == ["WAITING", "QUEUED"]

    holder["fail"] = None
    await _retry_when_due_or_done(env)

    assert ran == ["post", "post", "b"]
    assert await _states(env) == ["DONE", "DONE"]


async def _retry_when_due_or_done(env) -> None:
    step = await _waiting_step(env)
    env.clock.now = datetime.fromisoformat(step["next_try_at"])
    await run_queue(env.bot)


async def test_a_switch_off_is_not_held_behind_a_waiting_change(env):
    holder = {"fail": _missing_access()}
    ran: list[str] = []
    _queue(
        env,
        _type("waiting", steps=[_flaky("post", holder)], places=("channel:1",)),
        _type("switch_off", steps=[_act("off", ran)], places=("channel:1",),
              overrides_waiting=True),
    )

    await _ask(env, "waiting")
    await _ask(env, "switch_off")
    await run_queue(env.bot)

    assert ran == ["off"]
    assert await _states(env) == ["WAITING", "DONE"]


async def test_a_waiting_change_s_steps_no_longer_due_are_dropped_and_the_rest_go_ahead(env):
    holder = {"fail": _missing_access()}
    due = {"post": True}
    ran: list[str] = []

    async def _still_due(_ctx):
        return due["post"]

    _queue(env, _type(steps=[_flaky("post", holder, ran=ran, still_due=_still_due),
                             _act("after", ran)]))

    await _ask(env)
    await run_queue(env.bot)
    due["post"] = False
    await _retry_when_due_or_done(env)

    steps = await step_rows(env.db_path)
    assert steps[0]["result"] == {"dropped": True}
    assert ran == ["post", "after"]
    assert await _states(env) == ["DONE"]


# ---------------------------------------------------------------------------
# Reporting a step that keeps failing
# ---------------------------------------------------------------------------


def _still_at_work(lines: list[str]) -> list[str]:
    return [line for line in lines if STILL_AT_WORK in line]


async def test_a_step_still_failing_after_about_an_hour_is_reported_and_still_retried(env):
    holder = {"fail": _missing_access()}

    async def _describe(_ctx):
        return "posting the dummy"

    _queue(env, _type(steps=[_flaky("post", holder, describe=_describe)]))

    await _ask(env)
    await run_queue(env.bot)
    step = await _waiting_step(env)
    while env.clock.now - NOW < timedelta(hours=1):
        assert _still_at_work(await _lines(env)) == []
        step = await _retry_when_due(env)

    [line] = _still_at_work(await _lines(env))
    assert line.startswith(
        f"⚠️ {WHAT} for {NAMED} is still at work: posting the dummy has failed for over an hour ("
    )
    assert "Missing Access" in line
    assert "The bot keeps trying." in line
    assert step["reported_at"] is not None
    assert step["next_try_at"] is not None
    assert await _states(env) == ["WAITING"]


async def test_a_step_still_failing_is_reported_again_after_a_day_and_not_at_every_try(env):
    holder = {"fail": _missing_access()}
    _queue(env, _type(steps=[_flaky("post", holder)]))

    await _ask(env)
    await run_queue(env.bot)
    while not _still_at_work(await _lines(env)):
        step = await _retry_when_due(env)
    reported_at = datetime.fromisoformat(step["reported_at"])

    while env.clock.now - reported_at < timedelta(hours=23, minutes=30):
        step = await _retry_when_due(env)
        assert len(_still_at_work(await _lines(env))) == 1
    while env.clock.now - reported_at < timedelta(hours=24, minutes=30):
        step = await _retry_when_due(env)

    assert len(_still_at_work(await _lines(env))) == 2


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


# ---------------------------------------------------------------------------
# Trying again at once
# ---------------------------------------------------------------------------


async def test_a_change_waiting_on_a_retry_is_tried_at_once_when_the_bot_starts(env):
    holder = {"fail": _missing_access()}
    ran: list[str] = []
    _queue(env, _type(steps=[_flaky("post", holder, ran=ran)]))

    await _ask(env)
    await run_queue(env.bot)
    holder["fail"] = None

    await restart_queue(env.bot)
    try:
        await run_queue(env.bot)
    finally:
        await maybe_await(env.bot.change_queue.stop())

    assert env.clock.now == NOW
    assert ran == ["post", "post"]
    assert await _states(env) == ["DONE"]


async def test_wake_tries_a_waiting_step_at_once(env):
    """A command repairing what a waiting step lacks wakes the queue for that place."""
    holder = {"fail": _missing_access()}
    ran: list[str] = []
    _queue(env, _type(steps=[_flaky("post", holder, ran=ran)], places=("channel:1",)))

    await _ask(env)
    await run_queue(env.bot)
    holder["fail"] = None

    await maybe_await(env.bot.change_queue.wake("channel:1"))
    await run_queue(env.bot)

    assert env.clock.now == NOW
    assert ran == ["post", "post"]
    assert await _states(env) == ["DONE"]


# ---------------------------------------------------------------------------
# A step tried once
# ---------------------------------------------------------------------------


def _tried_once(name: str = "remove", *, ran: list | None = None, left=(5, 6)) -> Any:
    api = _api()

    async def _run(_ctx):
        if ran is not None:
            ran.append(name)
        raise api.StepFailedOnDiscord("Missing Access", result={"left": list(left)})

    return api.Step(name, api.StepKind.DELETE, _run, tried_once=True)


async def test_a_step_tried_once_that_fails_on_discord_is_done_with_its_failure_recorded(env):
    ran: list[str] = []
    _queue(env, _type(steps=[_tried_once(ran=ran)]))

    await _ask(env)
    await run_queue(env.bot)
    env.clock.advance(hours=2)
    await run_queue(env.bot)

    [step] = await step_rows(env.db_path)
    assert step["done_at"] is not None
    assert step["result"] == {"failed": "Missing Access", "left": [5, 6]}
    assert step["tries"] == 0
    assert step["next_try_at"] is None
    assert step["failing_since"] is None
    assert ran == ["remove"]
    assert await _states(env) == ["DONE"]


async def test_a_step_tried_once_and_failed_holds_up_no_later_change(env):
    ran: list[str] = []
    _queue(
        env,
        _type("removal", steps=[_tried_once(), _act("after", ran)], places=("channel:1",)),
        _type("same", steps=[_act("b", ran)], places=("channel:1",)),
    )

    await _ask(env, "removal")
    await _ask(env, "same")
    await run_queue(env.bot)

    assert ran == ["after", "b"]
    assert await _states(env) == ["DONE", "DONE"]


async def test_the_outcome_reads_what_a_step_tried_once_left_undone(env):
    def _outcome(ctx):
        left = [m for s in ctx.steps if s.result and "failed" in s.result for m in s.result["left"]]
        return f"⚠️ {len(left)} message(s) could not be removed: {left}"

    _queue(env, _type(steps=[_tried_once()], outcome=_outcome))
    interaction = member_interaction(env.bot)

    await _ask(env, interaction=interaction)
    await run_queue(env.bot)

    assert updated_reply(interaction) == "⚠️ 2 message(s) could not be removed: [5, 6]"
    assert [r["state"] for r in await change_rows(env.db_path)] == ["DONE"]
