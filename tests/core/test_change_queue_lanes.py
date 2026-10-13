"""The change queue's lanes: the league lane, and one lane per conversation's channel (#439, 6b).

Each change carries a lane: the league's (`"league"`, the queue as a league knows it) or a
conversation's, keyed by its channel (`"channel:<id>"`). Each lane runs in order and stops alone.
These tests use dummy change types on a database built by the migrations, with "now" pinned, as
`test_change_queue.py` does; every existing queue test runs on the league lane, the default.

Everything of the queue is imported inside a test, so this file collects while lanes are unbuilt.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.change_queue import (
    Clock,
    acknowledgement,
    attach_queue,
    change_rows,
    discard_job,
    league_double,
    maybe_await,
    member_interaction,
    restart_queue,
    retry_job,
    run_queue,
    seed_server,
    step_rows,
    stopped_job,
    tier_member,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
WHAT = "`/dummy`"
DOING = "Doing the dummy thing"
#: The lane a conversation in channel 7 runs on, and in channel 8.
TURNS_7 = "channel:7"
TURNS_8 = "channel:8"

NO_LANE_COLUMN = "#439: a queued change has no lane yet"
NO_TYPE_LANE = "#439: a change type does not say its lane yet"
NO_FOLLOW_ON_LANE = "#439: a follow-on does not take its own kind's lane yet"
NO_LANE_REPEAT = "#439: changes carry no lane, so a repeat is compared across the whole queue"
NO_LANE_ACK = "#439: changes carry no lane, so an acknowledgement names a stop anywhere"
NO_LANE_CHOICE = (
    "#439: the queue chooses one change for the whole bot, so a stop on one lane holds every lane"
)
NO_LANE_TRYING = "#439: the job being tried is the whole queue's, not each lane's"
NO_WRITE_LOCK = "#439: the queue's saves take no write lock of their own"


def _api() -> SimpleNamespace:
    from leaguebot.core.models import change
    from leaguebot.core.services import change_queue

    return SimpleNamespace(
        StepKind=change.StepKind,
        Verdict=change.Verdict,
        PlannedStep=change.PlannedStep,
        FollowOn=change.FollowOn,
        StepResult=change.StepResult,
        StepFailedOnDiscord=change.StepFailedOnDiscord,
        ChangeType=change_queue.ChangeType,
        Step=change_queue.Step,
    )


def _type(kind: str = "dummy", *, steps, lane=None, check=None, key=None,
          repeatable: bool = False) -> Any:
    """A change type running *steps* in order; on *lane* where given (a callable of the
    payload), else on the league lane by default. Its key is the kind and payload, or *key*."""
    api = _api()

    async def _go(_ctx):
        return api.Verdict.go()

    lanes: dict[str, Any] = {} if lane is None else {"lane": lane}
    return api.ChangeType(
        kind=kind,
        opening=tuple(api.PlannedStep(s.name, {}) for s in steps),
        steps={s.name: s for s in steps},
        check=check or _go,
        key=key or (lambda payload: f"{kind}|{json.dumps(payload, sort_keys=True)}"),
        doing=lambda _payload: DOING,
        outcome=lambda _ctx: "✅ Done.",
        repeatable=repeatable,
        **lanes,
    )


def _act(name: str, ran: list | None = None) -> Any:
    api = _api()

    async def _run(_ctx):
        if ran is not None:
            ran.append(name)
        return api.StepResult()

    return api.Step(name, api.StepKind.ACT, _run)


def _failing(name: str = "post") -> Any:
    """An `ACT` job Discord always refuses, so the change's lane stops at it."""
    api = _api()

    async def _run(_ctx):
        raise api.StepFailedOnDiscord("Missing Access")

    return api.Step(name, api.StepKind.ACT, _run)


def _in_channel(payload: dict[str, Any]) -> str:
    """A conversation kind's lane: its channel's."""
    return f"channel:{payload['channel']}"


def _flaky(name: str, holder: dict[str, Any], ran: list | None = None) -> Any:
    """An `ACT` job Discord refuses while `holder["fail"]` is set, and that goes through after."""
    api = _api()

    async def _run(_ctx):
        if holder.get("fail"):
            raise api.StepFailedOnDiscord("Missing Access")
        if ran is not None:
            ran.append(name)
        return api.StepResult()

    return api.Step(name, api.StepKind.ACT, _run)


async def _job(env, name: str) -> dict[str, Any]:
    """The one job named *name*."""
    [job] = [row for row in await step_rows(env.db_path) if row["name"] == name]
    return job


async def _states(env) -> list[str]:
    return [row["state"] for row in await change_rows(env.db_path)]


def _gate(*, fail: bool) -> SimpleNamespace:
    """What a `_held` job does: `hold` it at its next try until `release` is set, then fail where
    `fail` is set. `entered` is set once the held try has begun."""
    return SimpleNamespace(hold=False, fail=fail, entered=asyncio.Event(),
                           release=asyncio.Event(), ran=[])


def _held(name: str, gate: SimpleNamespace) -> Any:
    """An `ACT` job a test can hold in the middle of a try, through *gate*."""
    api = _api()

    async def _run(_ctx):
        if gate.hold:
            gate.hold = False
            gate.entered.set()
            await gate.release.wait()
        if gate.fail:
            raise api.StepFailedOnDiscord("Missing Access")
        gate.ran.append(name)
        return api.StepResult()

    return api.Step(name, api.StepKind.ACT, _run)


async def _press_on(env, job: dict[str, Any], action: str) -> Any:
    """Press *action*, Retry by a league manager or Discard by a league admin, on *job*'s stop
    notice, without running the queue. Gives the presser's interaction."""
    user = tier_member("manager" if action == "retry" else "admin")
    interaction = member_interaction(env.bot, user=user)
    interaction.message.id = job["notice_message_id"]
    await maybe_await(getattr(env.bot.change_queue, action)(job["notice_message_id"], interaction))
    return interaction


def _told(interaction: Any) -> list[str]:
    """What the presser was told."""
    calls = (interaction.response.send_message.await_args_list
             + interaction.followup.send.await_args_list)
    return [str(call.args[0] if call.args else call.kwargs.get("content", "")) for call in calls]


def _refused_as_tried(interaction: Any) -> bool:
    return any(text.startswith("⛔") and "is being tried now" in text
               for text in _told(interaction))


@pytest.fixture
async def env(tmp_path):
    db_path = str(tmp_path / "queue.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    return SimpleNamespace(db_path=db_path, bot=league_double(db_path), clock=Clock(NOW))


def _queue(env, *types) -> Any:
    return attach_queue(env.bot, env.db_path, now=env.clock, types=types)


async def _ask(env, kind: str = "dummy", payload=None, *, interaction=None) -> Any:
    """Ask for a change as a member, through *interaction* or a fresh one."""
    return await env.bot.change_queue.ask(
        kind, payload or {}, interaction=interaction or member_interaction(env.bot),
        what=WHAT, refusal_what=WHAT,
    )


async def _lanes(env) -> list[tuple[str, str]]:
    return [(row["kind"], row["lane"]) for row in await change_rows(env.db_path)]


# ---------------------------------------------------------------------------
# L1: a change's lane is saved with it
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_LANE_COLUMN)
async def test_a_change_saved_without_a_lane_is_the_league_s(env):
    """A change written without naming a lane, as every change was before lanes, is the league's."""
    async with get_connection(env.db_path) as db:
        await db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, origin, what) "
            "VALUES ('dummy', 'dummy', 'MEMBER', ?)", (WHAT,),
        )
        await db.commit()

    assert [row["lane"] for row in await change_rows(env.db_path)] == ["league"]


# ---------------------------------------------------------------------------
# L2: each change type says its lane, and a follow-on takes its own kind's
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_TYPE_LANE)
async def test_a_change_takes_its_type_s_lane(env):
    """A conversation kind asked for channel 7 is saved on channel 7's lane; a kind that names no
    lane is saved on the league's."""
    _queue(env, _type("turn", steps=[_act("a")], lane=_in_channel),
           _type("dummy", steps=[_act("b")]))

    await _ask(env, "turn", {"channel": 7})
    await _ask(env, "dummy")

    assert await _lanes(env) == [("turn", TURNS_7), ("dummy", "league")]


@pytest.mark.xfail(strict=True, reason=NO_FOLLOW_ON_LANE)
async def test_a_follow_on_takes_its_own_kind_s_lane(env):
    """A league change asking a conversation kind as a follow-on in its save puts it on the
    conversation's lane; a conversation's change asking a league kind puts that on the league's."""
    api = _api()

    def _asking(kind: str, payload: dict[str, Any]) -> Any:
        async def _run(_db, _ctx):
            return api.StepResult(follow_ons=(api.FollowOn(kind, payload, f"Asking {kind}"),))

        return api.Step("ask", api.StepKind.SAVE, _run)

    _queue(
        env,
        _type("league_work", steps=[_asking("prompt", {"channel": 7})]),
        _type("prompt", steps=[_act("p")], lane=_in_channel),
        _type("turn", steps=[_asking("league_more", {})], lane=_in_channel),
        _type("league_more", steps=[_act("m")]),
    )

    await _ask(env, "league_work")
    await _ask(env, "turn", {"channel": 8})
    await run_queue(env.bot)

    assert await _lanes(env) == [
        ("league_work", "league"), ("turn", TURNS_8), ("prompt", TURNS_7),
        ("league_more", "league"),
    ]


@pytest.mark.xfail(strict=True, reason=NO_FOLLOW_ON_LANE)
async def test_a_refusal_s_and_a_discard_s_follow_ons_take_their_own_lanes(env):
    """A league change refused as it starts, whose refusal hook asks a conversation kind, and one
    stopped at its check and discarded, whose discard hook asks one: each follow-on is saved on its
    own kind's lane, channel 7's and channel 8's."""
    api = _api()
    holder = {"refuse": False, "raise": False}

    async def _check(_ctx):
        if holder["raise"]:
            raise RuntimeError("the check broke")
        return api.Verdict.refuse("⚠️ Not now.") if holder["refuse"] else api.Verdict.go()

    def _follow(channel: int):
        return lambda _ctx: (api.FollowOn("tell", {"channel": channel}, "Telling"),)

    refused = dataclasses.replace(
        _type("refused", steps=[_act("a")], check=_check), on_refused=_follow(7),
    )
    discarded = dataclasses.replace(
        _type("discarded", steps=[_act("b")], check=_check), on_discarded=_follow(8),
    )
    _queue(env, refused, discarded, _type("tell", steps=[_act("t")], lane=_in_channel))

    await _ask(env, "refused")
    holder["refuse"] = True
    await run_queue(env.bot)
    holder["refuse"] = False
    await _ask(env, "discarded")
    holder["raise"] = True
    await run_queue(env.bot)
    assert await stopped_job(env.db_path) is not None
    holder["raise"] = False
    await discard_job(env.bot)

    assert await _lanes(env) == [
        ("refused", "league"), ("tell", TURNS_7), ("discarded", "league"), ("tell", TURNS_8),
    ]


# ---------------------------------------------------------------------------
# L3: the repeat rule, and the stop an acknowledgement names, are the lane's own
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_LANE_REPEAT)
async def test_a_repeat_is_refused_only_against_the_last_change_on_its_own_lane(env):
    """A kind keyed without its channel, asked for channel 7 then for channel 8: the same request
    by its key, but on another lane, so the second is not a repeat and is queued."""
    _queue(env, _type("turn", steps=[_act("a")], lane=_in_channel, key=lambda _p: "turn"))
    second = member_interaction(env.bot)

    await _ask(env, "turn", {"channel": 7})
    await _ask(env, "turn", {"channel": 8}, interaction=second)

    assert await _lanes(env) == [("turn", TURNS_7), ("turn", TURNS_8)]
    assert "already been asked for" not in acknowledgement(second)


@pytest.mark.xfail(strict=True, reason=NO_LANE_REPEAT)
async def test_a_turn_on_another_lane_between_two_presses_does_not_make_the_second_new(env):
    """A league change asked twice, a conversation's turn in channel 7 asked between the two: the
    second press still repeats the last change on the league lane and is refused, saying so."""
    _queue(env, _type("dummy", steps=[_act("a")]),
           _type("turn", steps=[_act("t")], lane=_in_channel))
    second = member_interaction(env.bot)

    await _ask(env, "dummy")
    await _ask(env, "turn", {"channel": 7})
    await _ask(env, "dummy", interaction=second)

    assert await _lanes(env) == [("dummy", "league"), ("turn", TURNS_7)]
    assert acknowledgement(second) == (
        f"⚠️ {DOING} has already been asked for and has not started yet, "
        "so it was not asked for again."
    )


@pytest.mark.xfail(strict=True, reason=NO_LANE_ACK)
async def test_an_acknowledgement_names_the_stop_on_its_own_lane_only(env):
    """Channel 7's lane stopped at a job Discord refuses. A league change asked then is
    acknowledged with no word of that stop, and its job numbers name none; a second turn in
    channel 7 is acknowledged as waiting behind the stopped job, and its job numbers name it."""
    _queue(env, _type("turn", steps=[_failing()], lane=_in_channel, repeatable=True),
           _type("dummy", steps=[_act("a")]))
    await _ask(env, "turn", {"channel": 7})
    await run_queue(env.bot)
    job = await stopped_job(env.db_path)
    assert job is not None
    league, turn = member_interaction(env.bot), member_interaction(env.bot)

    league_id = await _ask(env, "dummy", interaction=league)
    turn_id = await _ask(env, "turn", {"channel": 7}, interaction=turn)

    assert "stopped at job" not in acknowledgement(league)
    assert f"stopped at job #{job['id']}" in acknowledgement(turn)
    assert (await env.bot.change_queue.job_numbers(league_id))[1] is None
    assert (await env.bot.change_queue.job_numbers(turn_id))[1] == job["id"]


# ---------------------------------------------------------------------------
# L4: each lane's next change is chosen apart; the tests' runner stays serial
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_LANE_CHOICE)
async def test_a_stop_on_one_lane_leaves_another_running(env):
    """A turn in channel 7 stops at a job Discord refuses. A league change and a turn in channel 8,
    asked after it, are carried out all the same; the turn in channel 7 stays stopped."""
    ran: list[str] = []
    _queue(env, _type("stuck", steps=[_failing()], lane=_in_channel),
           _type("turn", steps=[_act("t", ran)], lane=_in_channel),
           _type("dummy", steps=[_act("d", ran)]))

    await _ask(env, "stuck", {"channel": 7})
    await _ask(env, "dummy")
    await _ask(env, "turn", {"channel": 8})
    await run_queue(env.bot)

    assert ran == ["d", "t"]
    assert await _states(env) == ["RUNNING", "DONE", "DONE"]
    assert (await stopped_job(env.db_path))["name"] == "post"


@pytest.mark.xfail(strict=True, reason=NO_LANE_CHOICE)
async def test_changes_run_in_the_order_asked_within_a_lane(env):
    """In channel 7 a first turn stops at its second job, and a second turn is asked behind it,
    with a league change asked between the two. The league change runs; the second turn waits and
    does not overtake the first. Once the stopped job goes through on Retry, the first turn
    finishes, and only then does the second run."""
    ran: list[str] = []
    holder = {"fail": True}
    _queue(env,
           _type("first", steps=[_act("a1", ran), _flaky("a2", holder, ran)], lane=_in_channel),
           _type("second", steps=[_act("b", ran)], lane=_in_channel),
           _type("dummy", steps=[_act("d", ran)]))

    await _ask(env, "first", {"channel": 7})
    await _ask(env, "dummy")
    await _ask(env, "second", {"channel": 7})
    await run_queue(env.bot)

    assert ran == ["a1", "d"]
    assert await _states(env) == ["RUNNING", "DONE", "QUEUED"]

    holder["fail"] = False
    await retry_job(env.bot)

    assert ran == ["a1", "d", "a2", "b"]
    assert await _states(env) == ["DONE", "DONE", "DONE"]


@pytest.mark.xfail(strict=True, reason=NO_LANE_CHOICE)
async def test_each_lane_resumes_its_running_change_after_a_restart(env):
    """A turn in channel 7 is stopped at its second job, and a league change of two jobs is cut
    off by a restart after its first. After the restart the league change carries on from its
    second job while the turn stays stopped; Retry then carries the turn on from its stopped job.
    No job done before the restart runs again."""
    ran: list[str] = []
    holder = {"fail": True}
    _queue(env,
           _type("turn", steps=[_act("a1", ran), _flaky("a2", holder, ran)], lane=_in_channel),
           _type("dummy", steps=[_act("l1", ran), _act("l2", ran)]))

    await _ask(env, "turn", {"channel": 7})
    await run_queue(env.bot)
    await _ask(env, "dummy")
    await run_queue(env.bot, steps=1)
    assert ran == ["a1", "l1"]
    assert await _states(env) == ["RUNNING", "RUNNING"]

    await restart_queue(env.bot)
    try:
        await run_queue(env.bot)
        assert ran == ["a1", "l1", "l2"]
        assert (await stopped_job(env.db_path))["name"] == "a2"

        holder["fail"] = False
        await retry_job(env.bot)
    finally:
        await maybe_await(env.bot.change_queue.stop())

    assert ran == ["a1", "l1", "l2", "a2"]
    assert await _states(env) == ["DONE", "DONE"]


@pytest.mark.xfail(strict=True, reason=NO_LANE_CHOICE)
async def test_the_serial_runner_takes_the_lowest_runnable_change_first(env):
    """A turn in channel 9 is stopped. Then, in this order: a turn of two jobs in channel 7, a
    league change, a turn in channel 8 and a second turn in channel 7. The tests' runner carries
    out one change at a time, the runnable change asked first first: the first turn in channel 7
    whole, then the league change, the turn in channel 8 and the second turn in channel 7."""
    ran: list[str] = []
    _queue(env, _type("stuck", steps=[_failing()], lane=_in_channel),
           _type("first", steps=[_act("a1", ran), _act("a2", ran)], lane=_in_channel),
           _type("dummy", steps=[_act("d", ran)]),
           _type("turn", steps=[_act("c", ran)], lane=_in_channel),
           _type("second", steps=[_act("b", ran)], lane=_in_channel))
    await _ask(env, "stuck", {"channel": 9})
    await run_queue(env.bot)

    await _ask(env, "first", {"channel": 7})
    await _ask(env, "dummy")
    await _ask(env, "turn", {"channel": 8})
    await _ask(env, "second", {"channel": 7})
    await run_queue(env.bot)

    assert ran == ["a1", "a2", "d", "c", "b"]
    assert await _states(env) == ["RUNNING", "DONE", "DONE", "DONE", "DONE"]


@pytest.mark.xfail(strict=True, reason=NO_LANE_CHOICE)
async def test_a_lane_s_retry_falls_due_on_its_own_schedule(env):
    """A turn in channel 7 stops at noon, and a league change stops thirty seconds later. Each job
    is tried again on its own schedule, counted from its own first failure: at 12:01 only the
    turn's, at 12:01:30 only the league's; the turn's next try then falls at 12:05 and the
    league's at 12:05:30."""
    _queue(env, _type("turn", steps=[_flaky("post7", {"fail": True})], lane=_in_channel),
           _type("dummy", steps=[_flaky("postL", {"fail": True})]))

    await _ask(env, "turn", {"channel": 7})
    await run_queue(env.bot)
    env.clock.advance(seconds=30)
    await _ask(env, "dummy")
    await run_queue(env.bot)
    assert [(await _job(env, n))["tries"] for n in ("post7", "postL")] == [1, 1]

    env.clock.advance(seconds=30)
    await run_queue(env.bot)
    assert [(await _job(env, n))["tries"] for n in ("post7", "postL")] == [2, 1]

    env.clock.advance(seconds=30)
    await run_queue(env.bot)
    turn, league = await _job(env, "post7"), await _job(env, "postL")
    assert [turn["tries"], league["tries"]] == [2, 2]
    assert datetime.fromisoformat(turn["next_try_at"]) == NOW + timedelta(minutes=5)
    assert datetime.fromisoformat(league["next_try_at"]) == NOW + timedelta(minutes=5, seconds=30)


# ---------------------------------------------------------------------------
# L5: Retry and Discard are refused only while their own lane tries the job
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_LANE_TRYING)
async def test_retry_and_discard_are_refused_only_while_their_own_lane_tries_the_job(env):
    """A turn in channel 7 and a league change each stop at a job Discord refuses. While the turn's
    job is being tried again, Retry and Discard on its notice are each refused as being tried now.
    While the league's job is being tried, Discard on the league's notice is refused the same way,
    but Retry and then Discard on the turn's notice go through: the turn's job is dropped."""
    turn_gate, league_gate = _gate(fail=True), _gate(fail=True)
    _queue(env, _type("turn", steps=[_held("post7", turn_gate)], lane=_in_channel),
           _type("dummy", steps=[_held("postL", league_gate)]))
    await _ask(env, "turn", {"channel": 7})
    await _ask(env, "dummy")
    await run_queue(env.bot)
    turn_job, league_job = await _job(env, "post7"), await _job(env, "postL")
    assert turn_job["failing_since"] is not None and league_job["failing_since"] is not None

    turn_gate.hold = True
    env.clock.advance(minutes=1)
    trying = asyncio.create_task(run_queue(env.bot))
    try:
        await asyncio.wait_for(turn_gate.entered.wait(), 5)
        retried = await _press_on(env, turn_job, "retry")
        discarded = await _press_on(env, turn_job, "discard")
    finally:
        turn_gate.release.set()
        await asyncio.wait_for(trying, 5)
    assert _refused_as_tried(retried) and _refused_as_tried(discarded)

    league_gate.hold = True
    env.clock.advance(minutes=4)
    trying = asyncio.create_task(run_queue(env.bot))
    try:
        await asyncio.wait_for(league_gate.entered.wait(), 5)
        league_discarded = await _press_on(env, league_job, "discard")
        retried = await _press_on(env, await _job(env, "post7"), "retry")
        discarded = await _press_on(env, await _job(env, "post7"), "discard")
    finally:
        league_gate.release.set()
        await asyncio.wait_for(trying, 5)

    assert _refused_as_tried(league_discarded)
    assert not _refused_as_tried(retried) and not _refused_as_tried(discarded)
    assert any(text.startswith("🔁") for text in _told(retried))
    assert "discarded" in (await _job(env, "post7"))["result"]
    assert await _states(env) == ["DONE", "RUNNING"]


@pytest.mark.xfail(strict=True, reason=NO_LANE_TRYING)
async def test_a_press_on_one_lane_s_notice_goes_through_while_another_lane_runs(env):
    """A turn in channel 7 is stopped at a job Discord refuses. While a league change is in the
    middle of a long job, Retry on the turn's notice is taken at once and the presser told it is
    being tried again; once the league's job ends, the turn's job is tried and goes through."""
    turn_gate, league_gate = _gate(fail=True), _gate(fail=False)
    _queue(env, _type("turn", steps=[_held("post7", turn_gate)], lane=_in_channel),
           _type("dummy", steps=[_held("long", league_gate)]))
    await _ask(env, "turn", {"channel": 7})
    await run_queue(env.bot)
    turn_job = await stopped_job(env.db_path)
    assert turn_job is not None

    await _ask(env, "dummy")
    league_gate.hold = True
    trying = asyncio.create_task(run_queue(env.bot))
    try:
        await asyncio.wait_for(league_gate.entered.wait(), 5)
        turn_gate.fail = False
        retried = await _press_on(env, turn_job, "retry")
        assert turn_gate.ran == []
    finally:
        league_gate.release.set()
        await asyncio.wait_for(trying, 5)
    await run_queue(env.bot)

    assert not _refused_as_tried(retried)
    assert any(text.startswith("🔁") for text in _told(retried))
    assert (league_gate.ran, turn_gate.ran) == (["long"], ["post7"])
    assert await _states(env) == ["DONE", "DONE"]
