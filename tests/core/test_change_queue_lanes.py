"""The change queue's lanes: the league lane, and one lane per conversation's channel (#439, 6b).

Each change carries a lane: the league's (`"league"`, the queue as a league knows it) or a
conversation's, keyed by its channel (`"channel:<id>"`). Each lane runs in order and stops alone.
These tests use dummy change types on a database built by the migrations, with "now" pinned, as
`test_change_queue.py` does; every existing queue test runs on the league lane, the default.

Everything of the queue is imported inside a test, so this file collects while lanes are unbuilt.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
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
    member_interaction,
    run_queue,
    seed_server,
    stopped_job,
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
