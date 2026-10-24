"""The running job's number, which the change queue's worker sets around each step (#439, 6b).

A context variable holds the number of the job whose `run` or `record` is running, so a function
that posts can tell whether it is being called from inside a job. The worker's task and every
coroutine it awaits share it, a task a step starts copies it, and nothing outside a step sees it.
These tests use dummy change types on a database built by the migrations, with "now" pinned.

A function that posts carries `posts_in_a_job`, which refuses to run outside a job: it raises
`PostedOutsideAJob`, naming the function, a fault in the bot and never Discord's.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from leaguebot.core.db.database import run_migrations
from tests.support.change_queue import (
    Clock,
    attach_queue,
    league_double,
    member_interaction,
    run_queue,
    seed_server,
    step_rows,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)



@pytest.fixture
async def env(tmp_path):
    db_path = str(tmp_path / "queue.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    return SimpleNamespace(db_path=db_path, bot=league_double(db_path), clock=Clock(NOW))


def _type(*steps: Any, check=None) -> Any:
    """A member's change, `dummy`, running *steps* in order."""
    from leaguebot.core.models.change import PlannedStep, Verdict
    from leaguebot.core.services.change_queue import ChangeType

    async def _go(_ctx):
        return Verdict.go()

    return ChangeType(
        kind="dummy",
        opening=tuple(PlannedStep(step.name, {}) for step in steps),
        steps={step.name: step for step in steps},
        check=check or _go,
        key=lambda payload: f"dummy|{json.dumps(payload, sort_keys=True)}",
        doing=lambda _payload: "Doing the dummy thing",
        outcome=lambda _ctx: "✅ Done.",
    )


def _peeking(name: str, seen: dict[str, Any]) -> Any:
    """An `ACT` job noting the running job's number it sees, under its own name."""
    from leaguebot.core.models.change import StepKind, StepResult
    from leaguebot.core.services.change_queue import Step
    from leaguebot.core.utils.in_a_job import current_job

    async def _run(_ctx):
        seen[name] = current_job()
        return StepResult()

    return Step(name, StepKind.ACT, _run)


async def _ask_and_run(env, *steps: Any, check=None) -> None:
    attach_queue(env.bot, env.db_path, now=env.clock, types=(_type(*steps, check=check),))
    await env.bot.change_queue.ask(
        "dummy", {}, interaction=member_interaction(env.bot), what="`/dummy`",
        refusal_what="`/dummy`",
    )
    await run_queue(env.bot)


async def _job_ids(env) -> dict[str, int]:
    return {row["name"]: row["id"] for row in await step_rows(env.db_path)}


async def test_a_step_runs_with_its_job_number_in_context(env):
    """A change of two jobs runs: each job's `run` sees its own job's number, not the other's."""
    seen: dict[str, Any] = {}

    await _ask_and_run(env, _peeking("first", seen), _peeking("second", seen))

    ids = await _job_ids(env)
    assert seen == {"first": ids["first"], "second": ids["second"]}
    assert ids["first"] != ids["second"]


async def test_a_record_runs_with_its_job_number_in_context(env):
    """A job that posts and records what it posted: its `record`, saved with its mark, sees the
    job's number too."""
    from leaguebot.core.models.change import StepKind, StepResult
    from leaguebot.core.services.change_queue import Step
    from leaguebot.core.utils.in_a_job import current_job

    seen: dict[str, Any] = {}

    async def _run(_ctx):
        return StepResult()

    async def _record(_db, _ctx, _result):
        seen["record"] = current_job()

    await _ask_and_run(env, Step("post", StepKind.ACT, _run, record=_record))

    assert seen == {"record": (await _job_ids(env))["post"]}


async def test_nothing_outside_a_step_sees_a_job_number(env):
    """Before a change is asked, and once the queue has run it, the test's own code — outside
    every job — sees no job's number."""
    from leaguebot.core.utils.in_a_job import current_job

    seen: dict[str, Any] = {}
    assert current_job() is None

    await _ask_and_run(env, _peeking("post", seen))

    assert seen["post"] is not None
    assert current_job() is None


async def test_a_task_a_step_starts_inherits_its_job_number(env):
    """A job starts a task of its own and waits for it: the task sees the job's number."""
    from leaguebot.core.models.change import StepKind, StepResult
    from leaguebot.core.services.change_queue import Step
    from leaguebot.core.utils.in_a_job import current_job

    seen: dict[str, Any] = {}

    async def _in_the_task() -> None:
        seen["task"] = current_job()

    async def _run(_ctx):
        await asyncio.create_task(_in_the_task())
        return StepResult()

    await _ask_and_run(env, Step("post", StepKind.ACT, _run))

    assert seen == {"task": (await _job_ids(env))["post"]}


async def test_a_step_s_check_and_still_due_run_outside_a_job(env):
    """The change's check, asked when it is asked for and again when it runs, and a job's
    `still_due`, asked before the job runs, are not the job: they see no job's number, while
    the job itself does."""
    from leaguebot.core.models.change import StepKind, StepResult, Verdict
    from leaguebot.core.services.change_queue import Step
    from leaguebot.core.utils.in_a_job import current_job

    seen: dict[str, list[Any]] = {"check": [], "still_due": [], "run": []}

    async def _check(_ctx):
        seen["check"].append(current_job())
        return Verdict.go()

    async def _still_due(_ctx):
        seen["still_due"].append(current_job())
        return True

    async def _run(_ctx):
        seen["run"].append(current_job())
        return StepResult()

    await _ask_and_run(env, Step("post", StepKind.ACT, _run, still_due=_still_due), check=_check)

    assert seen["check"] and set(seen["check"]) == {None}
    assert seen["still_due"] and set(seen["still_due"]) == {None}
    assert seen["run"] == [(await _job_ids(env))["post"]]


async def _a_post() -> str:
    """Stands for a function that posts a message: it says it ran."""
    return "posted"


async def test_a_post_outside_a_job_raises_naming_the_function():
    """A function that posts, marked as posting in a job, is called from the test's own code,
    outside every job: it does not run, and the error raised names it."""
    from leaguebot.core.utils.in_a_job import PostedOutsideAJob, posts_in_a_job

    ran: list[str] = []

    @posts_in_a_job
    async def post_the_sheet() -> None:
        ran.append("posted")

    with pytest.raises(PostedOutsideAJob, match="post_the_sheet"):
        await post_the_sheet()
    assert ran == []


async def test_a_post_inside_a_job_runs():
    """The same function called while job 7 runs: it runs and gives back what it returned, its
    name and docstring kept."""
    from leaguebot.core.utils.in_a_job import posts_in_a_job, running_job

    guarded = posts_in_a_job(_a_post)

    with running_job(7):
        assert await guarded() == "posted"
    assert guarded.__name__ == "_a_post"
    assert guarded.__doc__ == _a_post.__doc__


async def test_the_guard_raises_no_discord_failure():
    """A post outside a job is a fault in the bot's own code, not a refusal by Discord: the error
    is no `StepFailedOnDiscord`, so a step that posts outside its job is never taken for a
    message Discord refused and tried again on the schedule as one."""
    from leaguebot.core.models.change import StepFailedOnDiscord
    from leaguebot.core.utils.in_a_job import PostedOutsideAJob, posts_in_a_job

    assert not issubclass(PostedOutsideAJob, StepFailedOnDiscord)
    with pytest.raises(PostedOutsideAJob) as raised:
        await posts_in_a_job(_a_post)()
    assert not isinstance(raised.value, StepFailedOnDiscord)
