"""The change queue's lanes: the league lane, and one lane per conversation's channel (#439, 6b).

Each change carries a lane: the league's (`"league"`, the queue as a league knows it) or a
conversation's, keyed by its channel (`"channel:<id>"`). Each lane runs in order and stops alone.
These tests use dummy change types on a database built by the migrations, with "now" pinned, as
`test_change_queue.py` does; every existing queue test runs on the league lane, the default.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.change_queue import (
    ADMIN_ROLE_ID,
    Clock,
    acknowledgement,
    attach_queue,
    change_rows,
    discard_job,
    http_error,
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


def _api() -> SimpleNamespace:
    from leaguebot.core.models import change
    from leaguebot.core.services import change_queue

    return SimpleNamespace(
        StepKind=change.StepKind,
        Verdict=change.Verdict,
        PlannedStep=change.PlannedStep,
        FollowOn=change.FollowOn,
        StepResult=change.StepResult,
        AuditRecord=change.AuditRecord,
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


async def test_a_change_takes_its_type_s_lane(env):
    """A conversation kind asked for channel 7 is saved on channel 7's lane; a kind that names no
    lane is saved on the league's."""
    _queue(env, _type("turn", steps=[_act("a")], lane=_in_channel),
           _type("dummy", steps=[_act("b")]))

    await _ask(env, "turn", {"channel": 7})
    await _ask(env, "dummy")

    assert await _lanes(env) == [("turn", TURNS_7), ("dummy", "league")]


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


async def test_a_repeat_is_refused_only_against_the_last_change_on_its_own_lane(env):
    """A kind keyed without its channel, asked for channel 7 then for channel 8: the same request
    by its key, but on another lane, so the second is not a repeat and is queued."""
    _queue(env, _type("turn", steps=[_act("a")], lane=_in_channel, key=lambda _p: "turn"))
    second = member_interaction(env.bot)

    await _ask(env, "turn", {"channel": 7})
    await _ask(env, "turn", {"channel": 8}, interaction=second)

    assert await _lanes(env) == [("turn", TURNS_7), ("turn", TURNS_8)]
    assert "already been asked for" not in acknowledgement(second)


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


# ---------------------------------------------------------------------------
# L6: one write lock around the queue's saves, and no Discord call under it
# ---------------------------------------------------------------------------


def _write_lock_held(env) -> bool | None:
    """Whether the queue's write lock is held now; None where the queue has none."""
    lock = getattr(env.bot.change_queue, "_writing", None)
    return None if lock is None else lock.locked()


def _held_save(gate: SimpleNamespace) -> Any:
    """A `SAVE` job held in the middle of its save until `gate.release` is set."""
    api = _api()

    async def _run(_db, _ctx):
        gate.entered.set()
        await gate.release.wait()
        return api.StepResult()

    return api.Step("save", api.StepKind.SAVE, _run)


async def test_two_lanes_saves_never_overlap(env):
    """A turn in channel 7 is in the middle of a save, which holds the queue's write lock. A league
    change asked meanwhile waits for that save to end before saving the request, and is saved,
    on the league lane, once it has."""
    gate = _gate(fail=False)
    _queue(env, _type("turn", steps=[_held_save(gate)], lane=_in_channel),
           _type("dummy", steps=[_act("d")]))
    await _ask(env, "turn", {"channel": 7})
    running = asyncio.create_task(run_queue(env.bot))
    asking = None
    try:
        await asyncio.wait_for(gate.entered.wait(), 5)
        assert _write_lock_held(env) is True
        asking = asyncio.create_task(_ask(env, "dummy"))
        for _ in range(20):
            await asyncio.sleep(0.01)
        assert not asking.done()
        assert [row["kind"] for row in await change_rows(env.db_path)] == ["turn"]
    finally:
        gate.release.set()
        await asyncio.wait_for(running, 5)
        if asking is not None:
            await asyncio.wait_for(asking, 5)

    assert await _lanes(env) == [("turn", TURNS_7), ("dummy", "league")]


async def test_no_discord_call_is_made_under_the_write_lock(env):
    """A member asks for a turn in channel 7 of a save and a post, and a second turn there stops at
    a post Discord refuses. The save holds the queue's write lock; the member's acknowledgement
    and its update, the post, the log lines and the stop notice are each made with it free."""
    api = _api()
    in_save: list[bool | None] = []
    in_discord: list[bool | None] = []

    async def _save(_db, _ctx):
        in_save.append(_write_lock_held(env))
        return api.StepResult()

    async def _post(_ctx):
        in_discord.append(_write_lock_held(env))
        return api.StepResult()

    def _watched(mock: Any) -> None:
        original = mock.side_effect

        async def _call(*args, **kwargs):
            in_discord.append(_write_lock_held(env))
            return None if original is None else await original(*args, **kwargs)

        mock.side_effect = _call

    _queue(env, _type("turn", steps=[api.Step("save", api.StepKind.SAVE, _save),
                                     api.Step("post", api.StepKind.ACT, _post)],
                      lane=_in_channel),
           _type("stuck", steps=[_failing("refused")], lane=_in_channel))
    interaction = member_interaction(env.bot)
    for mock in (interaction.response.send_message, interaction.edit_original_response,
                 interaction.followup.send, env.bot.log_channel.send):
        _watched(mock)

    await _ask(env, "turn", {"channel": 7}, interaction=interaction)
    await _ask(env, "stuck", {"channel": 7})
    await run_queue(env.bot)

    assert in_save == [True]
    assert interaction.response.send_message.await_count == 1
    assert interaction.edit_original_response.await_count >= 1
    assert (await stopped_job(env.db_path))["notice_message_id"] is not None
    assert len(in_discord) >= 4
    assert all(held is False for held in in_discord)


async def test_a_turn_s_save_waits_for_a_long_league_save_rather_than_failing(env, monkeypatch):
    """SQLite's wait for a lock is cut to a fifth of a second. A league change's save is held three
    times as long; a turn in channel 7 asked meanwhile waits for it rather than failing on a
    locked database, and is saved on its lane once the league's save has ended."""
    from leaguebot.core.db import database

    monkeypatch.setattr(database, "_BUSY_TIMEOUT_SECONDS", 0.2)
    gate = _gate(fail=False)
    _queue(env, _type("dummy", steps=[_held_save(gate)]),
           _type("turn", steps=[_act("t")], lane=_in_channel))
    await _ask(env, "dummy")
    running = asyncio.create_task(run_queue(env.bot))
    asking = None
    try:
        await asyncio.wait_for(gate.entered.wait(), 5)
        asking = asyncio.create_task(_ask(env, "turn", {"channel": 7}))
        await asyncio.sleep(0.6)
    finally:
        gate.release.set()
        await asyncio.wait_for(running, 5)
    assert asking is not None
    await asyncio.wait_for(asking, 5)

    assert await _lanes(env) == [("dummy", "league"), ("turn", TURNS_7)]


# ---------------------------------------------------------------------------
# The conversations the tests of L7–L9 end: the test's own tables
# ---------------------------------------------------------------------------


async def _conversations(env) -> None:
    """The test's own tables: `conversations`, one row per conversation's channel (7 and 8, both
    open, both channels standing); `answers`, what a conversation's turn saves; and
    `league_rows`, a table no conversation may write."""
    async with get_connection(env.db_path) as db:
        await db.execute(
            "CREATE TABLE conversations (channel INTEGER PRIMARY KEY, "
            "open INTEGER NOT NULL DEFAULT 1, channel_stands INTEGER NOT NULL DEFAULT 1)"
        )
        await db.execute("CREATE TABLE answers (channel INTEGER, v TEXT)")
        await db.execute("CREATE TABLE league_rows (v TEXT)")
        await db.executemany("INSERT INTO conversations (channel) VALUES (?)", [(7,), (8,)])
        await db.commit()


async def _end_now(env, channel: int, *, delete_channel: bool = False) -> None:
    """A league save landing now: the conversation in *channel* ended, and its channel deleted
    where *delete_channel* is set."""
    async with get_connection(env.db_path) as db:
        await db.execute(
            "UPDATE conversations SET open = 0, channel_stands = ? WHERE channel = ?",
            (0 if delete_channel else 1, channel),
        )
        await db.commit()


async def _is_open(env, channel: int) -> bool:
    async with get_connection(env.db_path) as db:
        cursor = await db.execute("SELECT open FROM conversations WHERE channel = ?", (channel,))
        row = await cursor.fetchone()
    return bool(row and row["open"])


async def _answers(env) -> list[str]:
    async with get_connection(env.db_path) as db:
        cursor = await db.execute("SELECT v FROM answers ORDER BY rowid")
        return [row["v"] for row in await cursor.fetchall()]


async def _league_rows(env) -> list[str]:
    async with get_connection(env.db_path) as db:
        cursor = await db.execute("SELECT v FROM league_rows ORDER BY rowid")
        return [row["v"] for row in await cursor.fetchall()]


def _saving(name: str, value: str, *, still_due=None) -> Any:
    """A `SAVE` job writing *value* into `answers` for its conversation's channel."""
    api = _api()

    async def _run(db, ctx):
        await db.execute("INSERT INTO answers (channel, v) VALUES (?, ?)",
                         (ctx.payload.get("channel"), value))
        return api.StepResult()

    return api.Step(name, api.StepKind.SAVE, _run, still_due=still_due)


# ---------------------------------------------------------------------------
# L7: a job is checked again inside its save
# ---------------------------------------------------------------------------


def _due_until_ended_after_the_first_check(env, asked: list[bool | None]) -> Any:
    """A job's `still_due`: due while channel 7's conversation is open. Each time it is asked it
    notes whether the write lock is held; the first time, once it has answered, a league save
    lands ending the conversation, as one may between a job's check and its save."""
    async def _due(ctx):
        asked.append(_write_lock_held(env))
        due = await _is_open(env, ctx.payload["channel"])
        if len(asked) == 1:
            await _end_now(env, ctx.payload["channel"])
        return due

    return _due


async def test_a_save_finds_its_job_no_longer_due_where_a_league_save_landed_after_its_check(env):
    """A change saves an answer for the conversation in channel 7, due only while that
    conversation is open. The job is found due, and then, before its save opens, a league save
    ends the conversation. Asked again inside the save, with the write lock held, the job is found
    no longer due: the answer is not written and the job is marked done as dropped. (The change is
    left on the league lane, the default: every save is checked again, on any lane.)"""
    await _conversations(env)
    asked: list[bool | None] = []
    _queue(env, _type("turn", steps=[
        _saving("answer", "Max", still_due=_due_until_ended_after_the_first_check(env, asked)),
    ]))

    await _ask(env, "turn", {"channel": 7})
    await run_queue(env.bot)

    assert await _answers(env) == []
    job = await _job(env, "answer")
    assert job["done_at"] is not None and job["result"] == {"dropped": True}
    assert len(asked) == 2 and asked[1] is True


async def test_a_record_is_checked_again_inside_its_save(env):
    """A change posts a message whose record saves it into `answers` for the conversation in
    channel 7, due only while that conversation is open. The job is found due and posts, and a league save then ends the
    conversation. Asked again inside the save that would run the record, with the write lock held,
    the job is found no longer due: the record writes nothing and the job is marked done as
    dropped."""
    api = _api()
    await _conversations(env)
    asked: list[bool | None] = []
    recorded: list[str] = []

    async def _post(_ctx):
        return api.StepResult(result={"id": 1})

    async def _record(db, ctx, _result):
        recorded.append("record")
        await db.execute("INSERT INTO answers (channel, v) VALUES (?, 'posted')",
                         (ctx.payload["channel"],))

    _queue(env, _type("turn", steps=[
        api.Step("post", api.StepKind.ACT, _post,
                 still_due=_due_until_ended_after_the_first_check(env, asked), record=_record),
    ]))

    await _ask(env, "turn", {"channel": 7})
    await run_queue(env.bot)

    assert recorded == []
    assert await _answers(env) == []
    job = await _job(env, "post")
    assert job["done_at"] is not None and job["result"].get("dropped") is True
    assert len(asked) == 2 and asked[1] is True


# ---------------------------------------------------------------------------
# L8: a conversation the league has ended
# ---------------------------------------------------------------------------


async def _still_running(db, payload: dict[str, Any]) -> bool:
    """A turn's `conversation_open`: its conversation is still running."""
    cursor = await db.execute("SELECT open FROM conversations WHERE channel = ?",
                              (payload["channel"],))
    row = await cursor.fetchone()
    return bool(row and row["open"])


async def _channel_stands(db, payload: dict[str, Any]) -> bool:
    """A closing change's `conversation_open`: only its channel need still stand."""
    cursor = await db.execute("SELECT channel_stands FROM conversations WHERE channel = ?",
                              (payload["channel"],))
    row = await cursor.fetchone()
    return bool(row and row["channel_stands"])


def _turn(kind: str, steps, *, reader=_still_running) -> Any:
    """A conversation's change type: on its channel's lane, its conversation read by *reader*."""
    return dataclasses.replace(_type(kind, steps=steps, lane=_in_channel),
                               conversation_open=reader)


def _ender(*, asks: tuple[str, ...] = ()) -> Any:
    """A league change type, "end", whose save ends the conversation in its payload's channel and
    asks each kind in *asks* for that channel as a follow-on."""
    api = _api()

    async def _run(db, ctx):
        await db.execute("UPDATE conversations SET open = 0 WHERE channel = ?",
                         (ctx.payload["channel"],))
        return api.StepResult(follow_ons=tuple(
            api.FollowOn(kind, {"channel": ctx.payload["channel"]}, f"Asking {kind}")
            for kind in asks
        ))

    return _type("end", steps=[api.Step("end", api.StepKind.SAVE, _run)])


def _notice_buttons(env, job: dict[str, Any]) -> set[str]:
    """The buttons on *job*'s stop notice in the log channel."""
    [notice] = [m for m in env.bot.log_channel.posted if m.id == job["notice_message_id"]]
    return {child.custom_id for child in getattr(notice.view, "children", None) or ()}


async def _open_changes(env) -> list[str]:
    return [row["kind"] for row in await change_rows(env.db_path)
            if row["state"] in ("QUEUED", "RUNNING")]


async def test_a_conversation_s_waiting_change_is_dropped_once_the_league_has_ended_it(env):
    """The league is asked to end the conversation in channel 7, and a turn there is asked behind
    it, saving an answer. Once the league's change has ended the conversation, the waiting turn is
    found no longer due when its turn comes: its answer is not written, a line says it is no
    longer due, and nothing is left open."""
    await _conversations(env)
    _queue(env, _ender(), _turn("turn", [_saving("answer", "Max")]))

    await _ask(env, "end", {"channel": 7})
    await _ask(env, "turn", {"channel": 7})
    await run_queue(env.bot)

    assert await _answers(env) == []
    assert (await _job(env, "answer"))["done_at"] is not None
    assert any("no longer due" in line for line in env.bot.log_channel.sent)
    assert await _open_changes(env) == []


async def test_a_turn_whose_check_fails_after_the_league_ended_its_conversation_is_dropped(env):
    """The league is asked to end the conversation in channel 7, and a member's turn there is
    asked behind it, a turn whose check refuses once the conversation is over. When the turn's
    turn comes it is dropped as no longer due, with a line saying so, and not refused: no refusal
    line, no answer written, nothing left open. Its acknowledgement is nevertheless updated with
    what the check said, not left saying it is under way."""
    await _conversations(env)
    api = _api()

    async def _check(_ctx):
        return api.Verdict.go() if await _is_open(env, 7) else api.Verdict.refuse("⚠️ Gone.")

    kind = dataclasses.replace(
        _type("turn", steps=[_saving("answer", "Max")], lane=_in_channel, check=_check),
        conversation_open=_still_running,
    )
    _queue(env, _ender(), kind)
    press = member_interaction(env.bot)

    await _ask(env, "end", {"channel": 7})
    await _ask(env, "turn", {"channel": 7}, interaction=press)
    await run_queue(env.bot)

    assert await _answers(env) == []
    assert [row["state"] for row in await change_rows(env.db_path) if row["kind"] == "turn"] == [
        "DROPPED"
    ]
    assert any("no longer due" in line for line in env.bot.log_channel.sent)
    assert not any("Gone" in line for line in env.bot.log_channel.sent)
    assert await _open_changes(env) == []
    assert press.edit_original_response.await_args.kwargs["content"] == "⚠️ Gone."


async def test_a_stopped_job_of_an_ended_conversation_stays_stopped_with_its_notice(env):
    """A turn in channel 7 stops at a post Discord refuses. The league then ends that conversation,
    and Discord would now take the post. The bot carries on past the job's scheduled tries, an
    hour and more. No scheduled try is made: the post is never made and the job's count of tries
    does not rise. The turn's job stays stopped: it is not cleared or dropped, and its stop notice
    keeps its Retry and Discard buttons, until an admin presses one of them."""
    await _conversations(env)
    holder = {"fail": True}
    ran: list[str] = []
    _queue(env, _ender(), _turn("turn", [_flaky("post", holder, ran)]))
    await _ask(env, "turn", {"channel": 7})
    await run_queue(env.bot)
    job = await stopped_job(env.db_path)
    assert job is not None and _notice_buttons(env, job)

    await _ask(env, "end", {"channel": 7})
    await run_queue(env.bot)
    tries = (await _job(env, "post"))["tries"]
    holder["fail"] = False
    for minutes in (1, 4, 5, 5, 15, 30, 61):
        env.clock.advance(minutes=minutes)
        await run_queue(env.bot)

    assert ran == []
    assert (await _job(env, "post"))["tries"] == tries
    still = await stopped_job(env.db_path)
    assert still is not None and still["id"] == job["id"] and still["done_at"] is None
    assert _notice_buttons(env, job)
    assert not await _is_open(env, 7)
    assert not any(f"Job #{job['id']}" in line and "no longer stops" in line
                   for line in env.bot.log_channel.sent)


async def test_retry_of_a_stopped_job_of_an_ended_conversation_finds_it_no_longer_due_and_clears_the_stop(env):
    """A turn in channel 7 stops at a post Discord refuses, and the league then ends that
    conversation. Discord would now take the post, but a league manager's Retry finds the job no
    longer due: the post is not made, a line says the job no longer stops the turns, the notice
    loses its buttons and nothing is left open."""
    await _conversations(env)
    holder = {"fail": True}
    ran: list[str] = []
    _queue(env, _ender(), _turn("turn", [_flaky("post", holder, ran)]))
    await _ask(env, "turn", {"channel": 7})
    await run_queue(env.bot)
    job = await stopped_job(env.db_path)
    assert job is not None
    await _ask(env, "end", {"channel": 7})
    await run_queue(env.bot)

    holder["fail"] = False
    await retry_job(env.bot)

    assert ran == []
    assert await stopped_job(env.db_path) is None
    assert (await _job(env, "post"))["done_at"] is not None
    assert any(f"Job #{job['id']}" in line and "no longer stops" in line
               for line in env.bot.log_channel.sent)
    assert _notice_buttons(env, job) == set()
    assert await _open_changes(env) == []


async def test_discard_of_it_drops_it_and_the_lane_s_waiting_changes_are_then_dropped(env):
    """In channel 7 a first turn stops at a post Discord refuses and a second turn, saving an
    answer, waits behind it. The league ends the conversation. A league admin's Discard drops the
    stopped job; the waiting turn is then found no longer due: its answer is not written and
    nothing is left open."""
    await _conversations(env)
    _queue(env, _ender(), _turn("first", [_failing()]),
           _turn("second", [_saving("answer", "Max")]))
    await _ask(env, "first", {"channel": 7})
    await _ask(env, "second", {"channel": 7})
    await run_queue(env.bot)
    await _ask(env, "end", {"channel": 7})
    await run_queue(env.bot)
    assert await _answers(env) == []

    await discard_job(env.bot)

    assert "discarded" in (await _job(env, "post"))["result"]
    assert await _answers(env) == []
    assert (await _job(env, "answer"))["done_at"] is not None
    assert await _open_changes(env) == []


async def test_a_closing_change_asked_back_needs_only_its_channel_to_stand(env):
    """A turn is asked in channel 7, then the league ends that conversation and, in the same save,
    asks back a closing change of channel 7's lane, which needs only the channel to stand. The
    waiting turn is found no longer due and writes nothing; the closing change runs, its notice
    posted, though the conversation has ended."""
    await _conversations(env)
    ran: list[str] = []
    _queue(env, _ender(asks=("closing",)), _turn("turn", [_saving("answer", "Max")]),
           _turn("closing", [_act("notice", ran)], reader=_channel_stands))

    await _ask(env, "end", {"channel": 7})
    await _ask(env, "turn", {"channel": 7})
    await run_queue(env.bot)

    assert await _answers(env) == []
    assert ran == ["notice"]
    assert await _lanes(env) == [("end", "league"), ("turn", TURNS_7), ("closing", TURNS_7)]
    assert await _open_changes(env) == []


async def test_a_job_failing_because_its_channel_was_deleted_under_it_stops_its_lane_and_retry_drops_it(env):
    """A turn in channel 7 is posting when the league ends the conversation and deletes its
    channel, so Discord refuses the post as the channel is unknown. The turn's lane stops at the
    post, and a league change asked then is still carried out. A league manager's Retry then finds
    the job no longer due: the post is not tried again and nothing is left open."""
    api = _api()
    await _conversations(env)
    tries: list[int] = []
    ran: list[str] = []

    async def _post(ctx):
        tries.append(1)
        if len(tries) == 1:
            await _end_now(env, ctx.payload["channel"], delete_channel=True)
            raise api.StepFailedOnDiscord("Unknown Channel") from http_error(
                discord.NotFound, status=404, text="Unknown Channel")
        return api.StepResult()

    _queue(env, _turn("turn", [api.Step("post", api.StepKind.ACT, _post)]),
           _type("dummy", steps=[_act("d", ran)]))
    await _ask(env, "turn", {"channel": 7})
    await run_queue(env.bot)
    job = await stopped_job(env.db_path)
    assert job is not None and job["name"] == "post"
    await _ask(env, "dummy")
    await run_queue(env.bot)
    assert ran == ["d"]

    await retry_job(env.bot)

    assert tries == [1]
    assert await stopped_job(env.db_path) is None
    assert await _open_changes(env) == []


# ---------------------------------------------------------------------------
# L9: a conversation lane's saves write only its conversation's own tables
# ---------------------------------------------------------------------------


def _writing(name: str, *tables: str, lines: tuple[str, ...] = ()) -> Any:
    """A `SAVE` job writing one row into each of *tables*, in order, with an audit record and
    *lines*."""
    api = _api()

    async def _run(db, ctx):
        for table in tables:
            column = "channel, v" if table == "answers" else "v"
            values = "?, ?" if table == "answers" else "?"
            params = (ctx.payload.get("channel"), name) if table == "answers" else (name,)
            await db.execute(f"INSERT INTO {table} ({column}) VALUES ({values})", params)
        return api.StepResult(
            audits=(api.AuditRecord("DUMMY_CHANGED", {"value": 1}, {"value": 2}),), lines=lines,
        )

    return api.Step(name, api.StepKind.SAVE, _run)


async def test_a_conversation_lane_s_save_may_not_write_another_table(env):
    """A turn in channel 7 declares that it writes `answers` alone. Its save writes an answer, then
    a row into `league_rows`, a table its conversation does not own. The write is refused: the
    turn's lane stops at the save, nothing it wrote is saved, and the stop itself, written in the
    queue's own tables, is recorded with its notice. A league change asked then is carried out."""
    await _conversations(env)
    ran: list[str] = []
    _queue(env,
           dataclasses.replace(_turn("turn", [_writing("save", "answers", "league_rows")]),
                               writes=("answers",)),
           _type("dummy", steps=[_act("d", ran)]))

    await _ask(env, "turn", {"channel": 7})
    await run_queue(env.bot)
    await _ask(env, "dummy")
    await run_queue(env.bot)

    job = await stopped_job(env.db_path)
    assert job is not None and job["name"] == "save"
    assert job["done_at"] is None and job["notice_message_id"] is not None
    assert await _answers(env) == [] and await _league_rows(env) == []
    assert ran == ["d"]


async def test_a_conversation_save_s_mark_and_lines_pass_and_a_league_save_is_not_restricted(env):
    """A turn in channel 7 declares that it writes `answers` alone; its save writes an answer, an
    audit record and a log line, and asks nothing else. A league change declaring nothing saves a
    row into both `answers` and `league_rows`. Both are carried out: each save lands with its mark,
    the audit records are kept and the turn's line reaches the log channel."""
    await _conversations(env)
    line = "📝 An answer was saved in channel 7."
    _queue(env,
           dataclasses.replace(_turn("turn", [_writing("turn_save", "answers", lines=(line,))]),
                               writes=("answers",)),
           _type("dummy", steps=[_writing("league_save", "answers", "league_rows")]))

    await _ask(env, "turn", {"channel": 7})
    await _ask(env, "dummy")
    await run_queue(env.bot)

    assert await stopped_job(env.db_path) is None
    assert await _answers(env) == ["turn_save", "league_save"]
    assert await _league_rows(env) == ["league_save"]
    assert all(row["done_at"] is not None for row in await step_rows(env.db_path))
    async with get_connection(env.db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) AS n FROM audit_entries")
        assert (await cursor.fetchone())["n"] == 2
    assert any(line in sent for sent in env.bot.log_channel.sent)
    assert await _states(env) == ["DONE", "DONE"]


# ---------------------------------------------------------------------------
# L10: the production task runs a worker per lane
# ---------------------------------------------------------------------------


async def _eventually(predicate, *, within: float = 5.0) -> None:
    """Wait, on the event loop, until *predicate()* (awaited) is true, failing after *within* s."""
    async def _poll() -> None:
        while not await predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), within)


async def _a_while() -> None:
    """Give every task on the event loop a fair while to move."""
    for _ in range(30):
        await asyncio.sleep(0.01)


def _holding() -> SimpleNamespace:
    """A gate whose `_held` job holds at its first try until released, then goes through."""
    gate = _gate(fail=False)
    gate.hold = True
    return gate


async def test_two_lanes_run_side_by_side_in_the_production_task(env):
    """With the bot running, a turn in channel 7 is in the middle of a job that takes its time. A
    league change asked meanwhile is carried out to the end while the turn's job is still under
    way; the turn then finishes once its job does."""
    gate = _holding()
    ran: list[str] = []
    queue = _queue(env, _type("turn", steps=[_held("t", gate)], lane=_in_channel),
                   _type("dummy", steps=[_act("d", ran)]))
    await _ask(env, "turn", {"channel": 7})
    await maybe_await(queue.start())
    try:
        await asyncio.wait_for(gate.entered.wait(), 5)
        await _ask(env, "dummy")

        async def _league_done() -> bool:
            return await _states(env) == ["RUNNING", "DONE"]

        await _eventually(_league_done)
        assert ran == ["d"] and gate.ran == []

        gate.release.set()

        async def _all_done() -> bool:
            return await _states(env) == ["DONE", "DONE"]

        await _eventually(_all_done)
    finally:
        gate.release.set()
        await maybe_await(queue.stop())

    assert gate.ran == ["t"]


async def test_at_most_three_conversation_lanes_run_beside_the_league(env):
    """With the bot running, turns are asked in channels 7, 8, 9 and 10, in that order, then a
    league change, each in the middle of a job that takes its time. The league change and the
    three oldest turns, in channels 7, 8 and 9, are under way at once; the turn in channel 10 waits
    untouched. Once the turn in channel 8 finishes, the turn in channel 10 starts."""
    from leaguebot.core.services import change_queue

    assert change_queue.CONVERSATION_LANES_AT_ONCE == 3
    gates = {channel: _holding() for channel in (7, 8, 9, 10)}
    league = _holding()
    queue = _queue(
        env,
        *(_type(f"turn{channel}", steps=[_held(f"t{channel}", gate)], lane=_in_channel)
          for channel, gate in gates.items()),
        _type("dummy", steps=[_held("d", league)]),
    )
    for channel in gates:
        await _ask(env, f"turn{channel}", {"channel": channel})
    await _ask(env, "dummy")
    await maybe_await(queue.start())
    try:
        await asyncio.wait_for(asyncio.gather(
            league.entered.wait(), *(gates[c].entered.wait() for c in (7, 8, 9))), 5)
        await _a_while()
        assert not gates[10].entered.is_set()

        gates[8].release.set()
        await asyncio.wait_for(gates[10].entered.wait(), 5)
        assert gates[8].ran == ["t8"]
    finally:
        for gate in (league, *gates.values()):
            gate.release.set()

        async def _all_done() -> bool:
            return await _states(env) == ["DONE"] * 5

        try:
            await _eventually(_all_done)
        finally:
            await maybe_await(queue.stop())


async def test_a_fault_outside_a_change_pauses_only_its_lane(env, monkeypatch):
    """With the bot running, choosing the next change of channel 7's lane meets a fault outside
    any change (the database locked), so that lane's worker pauses, for an hour here. Turns in
    channel 8 and a league change, asked after it, are carried out all the same; the turn in
    channel 7 waits, still asked and not started."""
    import sqlite3

    from leaguebot.core.services import change_queue

    monkeypatch.setattr(change_queue, "WORKER_PAUSE_AFTER_FAULT", 3600.0)
    ran: list[str] = []
    queue = _queue(env, _type("turn", steps=[_act("t", ran)], lane=_in_channel),
                   _type("dummy", steps=[_act("d", ran)]))
    faults: list[str] = []
    choose = queue._choose

    async def _locked_for_channel_7(*args, **kwargs):
        if TURNS_7 in (*args, *kwargs.values()) and not faults:
            faults.append(TURNS_7)
            raise sqlite3.OperationalError("database is locked")
        return await choose(*args, **kwargs)

    monkeypatch.setattr(queue, "_choose", _locked_for_channel_7)
    await _ask(env, "turn", {"channel": 7})
    await _ask(env, "turn", {"channel": 8})
    await _ask(env, "dummy")
    await maybe_await(queue.start())
    try:
        async def _others_done() -> bool:
            return await _states(env) == ["QUEUED", "DONE", "DONE"]

        await _eventually(_others_done)
        assert faults == [TURNS_7]
    finally:
        await maybe_await(queue.stop())

    assert sorted(ran) == ["d", "t"]


async def test_stop_ends_every_lane_task(env):
    """With the bot running, a turn in channel 7 and a league change are each in the middle of a
    job that takes its time, side by side. Stopping the queue, as the bot does when it shuts down,
    ends every task the queue started, and leaves both changes where they stood for the next start:
    still running, their jobs not done."""
    turn, league = _holding(), _holding()
    queue = _queue(env, _type("turn", steps=[_held("t", turn)], lane=_in_channel),
                   _type("dummy", steps=[_held("d", league)]))
    await _ask(env, "turn", {"channel": 7})
    await _ask(env, "dummy")
    before = asyncio.all_tasks()
    await maybe_await(queue.start())
    started: set[asyncio.Task[Any]] = set()
    try:
        await asyncio.wait_for(asyncio.gather(turn.entered.wait(), league.entered.wait()), 5)
        started = asyncio.all_tasks() - before - {asyncio.current_task()}
    finally:
        await maybe_await(queue.stop())
    await asyncio.sleep(0)

    assert len(started) >= 2
    assert all(task.done() for task in started)
    assert await _states(env) == ["RUNNING", "RUNNING"]
    assert all(row["done_at"] is None for row in await step_rows(env.db_path))


# ---------------------------------------------------------------------------
# L11: a stop notice per lane, naming its channel, and the league admins pinged
# ---------------------------------------------------------------------------

#: The league admins' role, as a message mentions it.
ADMINS = f"<@&{ADMIN_ROLE_ID}>"


def _notice_of(env, job: dict[str, Any]) -> Any:
    """The stop notice of *job*, as it landed in the log channel."""
    [notice] = [m for m in env.bot.log_channel.posted if m.id == job["notice_message_id"]]
    return notice


def _sends(env) -> list[Any]:
    """Every message the log channel was sent, in order, as the bot sent it."""
    return list(env.bot.log_channel.send.await_args_list)


def _notices(env) -> list[Any]:
    """Every stop notice the log channel was sent, in order: the messages carrying buttons."""
    return [call for call in _sends(env) if call.kwargs.get("view") is not None]


def _pings_the_admins(call: Any) -> bool:
    """Whether the message sent notifies the league admins: their role mentioned bare, not wrapped
    in code, and allowed to notify."""
    content = str(call.args[0] if call.args else call.kwargs.get("content", ""))
    roles = getattr(call.kwargs.get("allowed_mentions"), "roles", False)
    allowed = roles is True or (
        isinstance(roles, list) and ADMIN_ROLE_ID in {getattr(role, "id", role) for role in roles}
    )
    return allowed and ADMINS in content.replace(f"`{ADMINS}`", "")


def _stubborn(name: str, holder: dict[str, Any], key: str) -> Any:
    """An `ACT` job Discord refuses while `holder[key]` is set, and due while `holder["due"]`."""
    api = _api()

    async def _run(_ctx):
        if holder[key]:
            raise api.StepFailedOnDiscord("Missing Access")
        return api.StepResult()

    async def _due(_ctx):
        return holder["due"]

    return api.Step(name, api.StepKind.ACT, _run, still_due=_due)


async def test_a_conversation_s_stop_notice_names_its_channel(env):
    """A turn in channel 7 stops at a job Discord refuses. Its stop notice in the log channel says
    the turns in channel 7 are stopped at that job, never that the queue is, and carries Retry and
    Discard as every stop notice does."""
    _queue(env, _type("turn", steps=[_failing()], lane=_in_channel))
    await _ask(env, "turn", {"channel": 7})
    await run_queue(env.bot)

    job = await stopped_job(env.db_path)
    assert job is not None
    notice = _notice_of(env, job)
    assert "the turns in <#7>" in notice.content.lower()
    assert f"job #{job['id']}" in notice.content
    assert "the queue" not in notice.content.lower()
    assert {child.custom_id for child in notice.view.children} == {"queue:retry", "queue:discard"}


async def test_a_league_stop_notice_mentions_the_league_admin_role(env):
    """A turn in channel 7 stops first, its notice standing. A league change then stops at a job
    Discord refuses: its stop notice mentions the league admins' role, so that they are notified,
    though a conversation's notice already stands. Once it is discarded, a second league change
    stops, and its notice mentions the role too: a league stop always pings."""
    _queue(env, _type("turn", steps=[_failing()], lane=_in_channel),
           _type("dummy", steps=[_failing("league_post")]))
    await _ask(env, "turn", {"channel": 7})
    await run_queue(env.bot)
    await _ask(env, "dummy", {"n": 1})
    await run_queue(env.bot)
    await _press_on(env, await _job(env, "league_post"), "discard")
    await run_queue(env.bot)
    await _ask(env, "dummy", {"n": 2})
    await run_queue(env.bot)

    notices = _notices(env)
    assert len(notices) == 3
    assert [_pings_the_admins(call) for call in notices[1:]] == [True, True]


async def test_a_conversation_stop_notice_mentions_the_role_only_where_no_other_conversation_notice_stands(env):
    """A league change stops first, its notice standing. A turn in channel 7 then stops: its notice
    mentions the league admins' role, as no other conversation's stop notice stands (the league's
    does not count). A turn in channel 8 stops while channel 7's stop stands: its notice mentions
    nobody. Both are discarded, so no conversation's stop stands; a turn in channel 9 then stops,
    and its notice mentions the role again."""
    _queue(env, _type("turn", steps=[_failing()], lane=_in_channel),
           _type("dummy", steps=[_failing("league_post")]))
    await _ask(env, "dummy")
    await run_queue(env.bot)
    for channel in (7, 8):
        await _ask(env, "turn", {"channel": channel})
        await run_queue(env.bot)
    for job in [row for row in await step_rows(env.db_path) if row["name"] == "post"]:
        await _press_on(env, job, "discard")
    await run_queue(env.bot)
    await _ask(env, "turn", {"channel": 9})
    await run_queue(env.bot)

    assert [_pings_the_admins(call) for call in _notices(env)] == [True, True, False, True]


async def test_the_first_notice_to_land_pings_the_admins_once_and_restart_and_hour_lines_mention_nobody(env):
    """The league admins are pinged when a stop's notice first lands (decision 28, "League always";
    A28): where the first post of the notice is refused, the re-post is that first landing and pings
    them. A league change stops while the log channel refuses every post, so its stop notice does
    not land. The log channel takes posts again, and a minute on the bot posts the notice again:
    that re-posted notice mentions the league admins' role. The job still fails an hour on, and the
    hour line mentions nobody; the bot restarts, and the line saying the queue is still stopped
    mentions nobody. Of the messages that landed, exactly one pings the admins: the notice."""
    _queue(env, _type("dummy", steps=[_failing("league_post")]))
    env.bot.log_channel.fails = http_error(discord.Forbidden, status=403, text="Missing Access")
    await _ask(env, "dummy")
    await run_queue(env.bot)
    job = await stopped_job(env.db_path)
    assert job is not None and job["notice_message_id"] is None
    refused = len(_sends(env))

    env.bot.log_channel.fails = None
    env.clock.now = NOW + timedelta(minutes=1)
    await run_queue(env.bot)
    assert (await stopped_job(env.db_path))["notice_message_id"] is not None
    env.clock.now = NOW + timedelta(minutes=60)
    await run_queue(env.bot)
    await restart_queue(env.bot)
    await maybe_await(env.bot.change_queue.stop())

    sent = [str(call.args[0] if call.args else call.kwargs.get("content", ""))
            for call in _sends(env)]
    assert len([m for m in env.bot.log_channel.posted if m.view is not None]) == 1
    assert any("after an hour" in line for line in sent)
    assert any("after the restart" in line.lower() for line in sent)
    pings = [call for call in _sends(env)[refused:] if _pings_the_admins(call)]
    assert len(pings) == 1
    assert pings[0].kwargs.get("view") is not None


async def test_a_conversation_s_lines_say_the_turns_in_its_channel_and_the_league_s_say_the_queue(env):
    """A turn in channel 7 and a league change, each of two jobs, both stop at their first job.
    A member then asks a second turn in channel 7, and a second league change: the first is told
    the turns in channel 7 are stopped at that turn's job, the second that the queue is stopped at
    the league's. Retry fails on each; Retry then carries each first job through, and each second
    job stops; the bot restarts; and Retry finds each second job no longer due. Every line the log
    channel is given of the turn's jobs (its stops, the failed Retry, the job gone through, the
    line after the restart, the job cleared) says "the turns in" channel 7 and never "the queue";
    every one of the league's says "the queue", as today."""
    turn = {"first": True, "second": True, "due": True}
    league = {"first": True, "second": True, "due": True}
    _queue(env,
           _type("turn", steps=[_stubborn("t1", turn, "first"), _stubborn("t2", turn, "second")],
                 lane=_in_channel),
           _type("dummy", steps=[_stubborn("l1", league, "first"),
                                 _stubborn("l2", league, "second")]),
           _type("answer", steps=[_act("a")], lane=_in_channel),
           _type("other", steps=[_act("o")]))
    await _ask(env, "turn", {"channel": 7})
    await _ask(env, "dummy")
    await run_queue(env.bot)
    t1, l1 = await _job(env, "t1"), await _job(env, "l1")
    answer, other = member_interaction(env.bot), member_interaction(env.bot)
    await _ask(env, "answer", {"channel": 7}, interaction=answer)
    await _ask(env, "other", interaction=other)

    for job in (t1, l1):
        await _press_on(env, job, "retry")
    await run_queue(env.bot)
    turn["first"] = league["first"] = False
    for job in (t1, l1):
        await _press_on(env, job, "retry")
    await run_queue(env.bot)
    t2, l2 = await _job(env, "t2"), await _job(env, "l2")
    assert t2["failing_since"] is not None and l2["failing_since"] is not None
    await restart_queue(env.bot)
    try:
        turn["due"] = league["due"] = False
        for job in (t2, l2):
            await _press_on(env, job, "retry")
        await run_queue(env.bot)
    finally:
        await maybe_await(env.bot.change_queue.stop())

    told = acknowledgement(answer)
    assert "the turns in <#7>" in told.lower() and f"job #{t1['id']}" in told
    assert "the queue" not in told.lower()
    assert f"the queue is stopped at job #{l1['id']}" in acknowledgement(other).lower()

    lines = [line for line in env.bot.log_channel.sent if line[:1] in ("❌", "✅", "ℹ")]

    def _of(*jobs: dict[str, Any]) -> list[str]:
        return [line for line in lines
                if any(re.search(rf"job #{job['id']}\b", line, re.IGNORECASE) for job in jobs)]

    conversation, league_lines = _of(t1, t2), _of(l1, l2)
    assert len(conversation) >= 6 and len(league_lines) >= 6
    assert all("the turns in <#7>" in line.lower() and "the queue" not in line.lower()
               for line in conversation)
    assert all("the queue" in line.lower() and "the turns in" not in line.lower()
               for line in league_lines)
