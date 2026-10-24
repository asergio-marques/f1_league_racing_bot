"""A message typed into a conversation's channel is a turn on that channel's lane (#439, 6b).

A listener that receives a message it would act on marks it at once with ⏳ and asks a change that
answers it, the change declaring `turn_channel`; the change's last job clears the mark. A message
typed while a turn in its channel is still waiting is turned away at once, off the queue: a note
says the earlier message is still waiting and this one was not taken, and the turned-away message
is deleted, the note staying. A refusal of either is logged on the host and lost, never retried.

A channel waits where its own lane holds an open change, or where a league change a turn handed
over, declaring the channel as its `turn_channel`, is still in hand; a league stop alone never
makes a channel wait. These tests use dummy change types on a database built by the migrations,
with "now" pinned. `ask_turn` (`tests/support/change_queue.py`) does what a listener does; the
turn's payload carries the message's `channel_id` and `message_id`, which `clear_mark_step` reads.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import run_migrations
from tests.support.change_queue import (
    INTERACTION_CHANNEL_ID,
    LOG_CHANNEL_ID,
    MARK,
    Clock,
    ask_turn,
    attach_queue,
    change_rows,
    discard_job,
    http_error,
    league_double,
    queued_log_lines,
    restart_queue,
    retry_job,
    run_queue,
    seed_server,
    step_rows,
    stopped_job,
    text_channel,
    tier_member,
    typed_message,
    typed_message_deleted,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
#: The conversation channels the member types in, and the lane of the first.
CHANNEL = 707
OTHER_CHANNEL = 708
TURNS = f"channel:{CHANNEL}"
DRIVER = tier_member(None, member_id=4343, display_name="Driver", name="Driver#0001")


def _channels(bot: Any, *channels: Any) -> None:
    """Let *bot* find *channels* beside its log and interaction channels; any other is gone."""
    known = {LOG_CHANNEL_ID: bot.log_channel, INTERACTION_CHANNEL_ID: bot.interaction_channel}
    known.update({channel.id: channel for channel in channels})

    async def _fetch(channel_id: int) -> Any:
        if channel_id in known:
            return known[channel_id]
        raise http_error(discord.NotFound, status=404, text="Unknown Channel")

    bot.get_channel = MagicMock(side_effect=known.get)
    bot.fetch_channel = AsyncMock(side_effect=_fetch)
    bot.get_partial_messageable = MagicMock(side_effect=lambda channel_id, **_: known[channel_id])


@pytest.fixture
async def env(tmp_path):
    db_path = str(tmp_path / "queue.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    bot = league_double(db_path)
    channel, other = text_channel(CHANNEL), text_channel(OTHER_CHANNEL)
    _channels(bot, channel, other)
    return SimpleNamespace(db_path=db_path, bot=bot, clock=Clock(NOW), channel=channel,
                           other=other)


def _api() -> SimpleNamespace:
    from leaguebot.core.models import change
    from leaguebot.core.services import change_queue, typed_turns

    return SimpleNamespace(
        StepKind=change.StepKind, Verdict=change.Verdict, PlannedStep=change.PlannedStep,
        FollowOn=change.FollowOn, StepResult=change.StepResult,
        StepFailedOnDiscord=change.StepFailedOnDiscord, ChangeType=change_queue.ChangeType,
        Step=change_queue.Step, typed_turns=typed_turns,
    )


def _answer(name: str = "answer", *, ran: list | None = None,
            holder: dict[str, Any] | None = None) -> Any:
    """The turn's answer: an `ACT` job noting it ran, refused by Discord while `holder["fail"]`
    is set."""
    api = _api()

    async def _run(_ctx):
        if holder is not None and holder.get("fail"):
            raise api.StepFailedOnDiscord("Missing Access")
        if ran is not None:
            ran.append(name)
        return api.StepResult()

    return api.Step(name, api.StepKind.ACT, _run)


def _change_type(kind: str, steps: list[Any], *, turn: bool, conversation: bool) -> Any:
    """A change of *kind* running *steps*; answering in the payload's channel where *turn*, and
    on that channel's lane where *conversation*, else on the league lane."""
    api = _api()

    async def _go(_ctx):
        return api.Verdict.go()

    extra: dict[str, Any] = {}
    if turn:
        extra["turn_channel"] = lambda payload: payload["channel_id"]
    if conversation:
        extra["lane"] = lambda payload: f"channel:{payload['channel_id']}"
    return api.ChangeType(
        kind=kind,
        opening=tuple(api.PlannedStep(step.name, {}) for step in steps),
        steps={step.name: step for step in steps},
        check=_go,
        key=lambda payload: f"{kind}|{sorted(payload.items())}",
        doing=lambda _payload: "Answering a typed message",
        outcome=lambda _ctx: "",
        **extra,
    )


def _turn(*, ran: list | None = None, holder: dict[str, Any] | None = None) -> Any:
    """The `turn` kind: the answer, then the mark cleared."""
    clear = _api().typed_turns.clear_mark_step()
    return _change_type("turn", [_answer(ran=ran, holder=holder), clear], turn=True,
                        conversation=True)


def _league_work(kind: str = "league_work", *, payload_channel: bool = False) -> Any:
    """A league change Discord always refuses, so the league lane stops at it."""
    return _change_type(kind, [_answer(holder={"fail": True})], turn=payload_channel,
                        conversation=False)


def _typed(env, content: str = "Max Verstappen", *, channel: Any = None) -> Any:
    return typed_message(channel or env.channel, DRIVER, content, created_at=NOW)


async def _waiting(env, channel_id: int = CHANNEL) -> bool:
    from leaguebot.core.services.typed_turns import turn_waiting

    return await turn_waiting(env.db_path, env.types, channel_id)


def _queue(env, *types: Any) -> None:
    env.types = types
    attach_queue(env.bot, env.db_path, now=env.clock, types=types)


def _forbidden() -> discord.HTTPException:
    return http_error(discord.Forbidden, status=403, text="Missing Access")


def _note_mentions(channel: Any) -> discord.AllowedMentions:
    return channel.send.await_args_list[-1].kwargs["allowed_mentions"]


async def test_a_typed_turn_is_marked_at_once_and_the_mark_cleared_when_its_turn_is_done(env):
    """A driver types an answer into their channel: it is marked ⏳ before anything runs, its
    turn is saved on the channel's lane, and once the queue runs it the answer is given and the
    mark cleared, by the turn's last job."""
    ran: list[str] = []
    _queue(env, _turn(ran=ran))
    message = _typed(env)

    assert await ask_turn(env.bot, "turn", message) is not None
    assert MARK in message.marks
    assert ran == []
    [row] = await change_rows(env.db_path)
    assert row["lane"] == TURNS

    await run_queue(env.bot)

    assert ran == ["answer"]
    assert MARK not in message.marks
    names = [step["name"] for step in await step_rows(env.db_path)]
    assert names[0] == "answer" and names[-1] == _api().typed_turns.clear_mark_step().name
    assert [row["state"] for row in await change_rows(env.db_path)] == ["DONE"]


async def test_a_message_typed_while_a_turn_waits_in_its_channel_is_turned_away_at_once_with_a_note(
    env,
):
    """A driver's first answer waits its turn; they type a second before it is answered. The
    second is not marked and asks nothing: at once, a note in the channel names the driver, says
    the earlier message is still waiting its turn and this one was not taken, and mentions the
    driver alone."""
    _queue(env, _turn())
    first, second = _typed(env, "first"), _typed(env, "second")

    await ask_turn(env.bot, "turn", first)
    assert await ask_turn(env.bot, "turn", second) is None

    assert len(await change_rows(env.db_path)) == 1
    assert MARK in first.marks and MARK not in second.marks
    [note] = env.channel.sent
    assert DRIVER.mention in note
    assert "still waiting its turn" in note and "not taken" in note
    mentions = _note_mentions(env.channel)
    assert not mentions.everyone and not mentions.roles


async def test_a_message_in_another_channel_is_not_turned_away(env):
    """A turn waits in one driver's channel; a message typed in another driver's channel is
    marked and asked for as its own turn, and no note is posted anywhere."""
    _queue(env, _turn())
    await ask_turn(env.bot, "turn", _typed(env))
    elsewhere = _typed(env, channel=env.other)

    assert await ask_turn(env.bot, "turn", elsewhere) is not None

    assert MARK in elsewhere.marks
    assert len(await change_rows(env.db_path)) == 2
    assert env.channel.sent == [] and env.other.sent == []


async def test_a_turn_still_waits_while_the_queue_is_stopped_at_it_and_later_messages_are_turned_away(
    env,
):
    """A turn's answer is refused by Discord, so the channel's own lane stops at it: the channel
    still waits, and a message typed meanwhile is turned away. Once a league manager presses Retry
    and the answer goes through, the channel takes messages again."""
    holder = {"fail": True}
    ran: list[str] = []
    _queue(env, _turn(ran=ran, holder=holder))
    await ask_turn(env.bot, "turn", _typed(env, "first"))
    await run_queue(env.bot)

    assert (await stopped_job(env.db_path, TURNS))["name"] == "answer"
    assert await _waiting(env)
    assert await ask_turn(env.bot, "turn", _typed(env, "second")) is None
    assert len(env.channel.sent) == 1

    holder["fail"] = False
    await retry_job(env.bot, lane=TURNS)

    assert ran == ["answer"]
    assert not await _waiting(env)
    third = _typed(env, "third")
    assert await ask_turn(env.bot, "turn", third) is not None
    assert MARK in third.marks


async def test_turn_waiting_survives_a_restart(env):
    """A turn is stopped at its answer and the bot restarts: the channel still waits, read from
    the queue's records rather than memory, and a message typed after the restart is turned away."""
    _queue(env, _turn(holder={"fail": True}))
    await ask_turn(env.bot, "turn", _typed(env, "first"))
    await run_queue(env.bot)

    await restart_queue(env.bot)

    assert await _waiting(env)
    assert await ask_turn(env.bot, "turn", _typed(env, "second")) is None


async def test_a_mark_that_cannot_be_cleared_stops_the_queue(env):
    """The answer is given, but Discord refuses to take the ⏳ off the message: the turn's lane
    stops at the clearing job, the mark still on. A minute later the job is tried again on the
    schedule and, Discord taking it now, the mark comes off and the turn is done."""
    _queue(env, _turn())
    message = _typed(env)
    await ask_turn(env.bot, "turn", message)
    message.fails_marks = _forbidden()

    await run_queue(env.bot)

    clear = _api().typed_turns.clear_mark_step().name
    assert (await stopped_job(env.db_path, TURNS))["name"] == clear
    assert MARK in message.marks

    message.fails_marks = None
    env.clock.advance(minutes=1)
    await run_queue(env.bot)

    assert MARK not in message.marks
    assert await stopped_job(env.db_path, TURNS) is None
    assert [row["state"] for row in await change_rows(env.db_path)] == ["DONE"]


async def test_a_turned_away_message_is_deleted_at_once_and_the_note_stays(env):
    """A second message typed while the first waits is deleted at once, before the queue runs;
    the note saying so stays in the channel, and stays after the first turn is answered."""
    _queue(env, _turn())
    await ask_turn(env.bot, "turn", _typed(env, "first"))
    second = _typed(env, "second")

    await ask_turn(env.bot, "turn", second)

    assert second.delete.await_count == 1
    assert second.gone
    [note] = env.channel.posted[-1:]
    assert note.content == env.channel.sent[-1]

    await run_queue(env.bot)

    assert note in env.channel.posted
    assert note.delete.await_count == 0


@pytest.mark.parametrize("refused", [
    "note",
    "deletion",
])
async def test_a_refused_turn_away_note_or_deletion_is_lost_not_retried(env, caplog, refused):
    """Discord refuses the turn-away's note, or the deletion of the turned-away message: the
    refusal is logged on the host and nothing else — no error reaches the listener, no lane
    stops, nothing is queued, and once the clock moves on and the queue runs, it is not tried
    again."""
    _queue(env, _turn())
    await ask_turn(env.bot, "turn", _typed(env, "first"))
    second = _typed(env, "second")
    if refused == "note":
        env.channel.fails = _forbidden()
    else:
        second.fails_delete = _forbidden()

    with caplog.at_level(logging.WARNING):
        assert await ask_turn(env.bot, "turn", second) is None

    assert any(record.levelno >= logging.WARNING and record.name.startswith("leaguebot")
               for record in caplog.records)
    assert len(await change_rows(env.db_path)) == 1
    assert await queued_log_lines(env.db_path) == []
    tried = env.channel.send.await_count, second.delete.await_count

    env.clock.advance(minutes=5)
    await run_queue(env.bot)

    assert await stopped_job(env.db_path) is None
    assert (env.channel.send.await_count, second.delete.await_count) == tried


async def test_a_mark_on_a_message_since_deleted_completes_its_clear(env):
    """The driver deletes their answer before its turn comes: the answer is still given, the
    clearing job finds the message gone and is done, and no lane stops."""
    ran: list[str] = []
    _queue(env, _turn(ran=ran))
    message = _typed(env)
    await ask_turn(env.bot, "turn", message)
    typed_message_deleted(message)

    await run_queue(env.bot)

    assert ran == ["answer"]
    assert await stopped_job(env.db_path) is None
    assert all(step["done_at"] is not None and step["failing_since"] is None
               for step in await step_rows(env.db_path))
    assert [row["state"] for row in await change_rows(env.db_path)] == ["DONE"]


async def test_a_turn_discarded_lets_the_channel_take_messages_again(env):
    """A turn stopped at its answer is discarded by a league admin: the channel no longer waits,
    and the next message typed there is marked and asked for."""
    _queue(env, _turn(holder={"fail": True}))
    await ask_turn(env.bot, "turn", _typed(env, "first"))
    await run_queue(env.bot)

    await discard_job(env.bot, lane=TURNS)

    assert not await _waiting(env)
    again = _typed(env, "again")
    assert await ask_turn(env.bot, "turn", again) is not None
    assert MARK in again.marks


async def test_a_change_type_without_a_turn_channel_never_makes_a_channel_wait(env):
    """A league change naming the channel in its payload, but declaring no `turn_channel`, is
    stopped on the league lane: the channel does not wait, and a message typed there is marked and
    asked for."""
    _queue(env, _turn(), _league_work())
    await env.bot.change_queue.ask("league_work", {"channel_id": CHANNEL}, what="`/league`",
                                   actor=DRIVER)
    await run_queue(env.bot)
    assert await stopped_job(env.db_path, "league") is not None

    assert not await _waiting(env)
    message = _typed(env)
    assert await ask_turn(env.bot, "turn", message) is not None
    assert MARK in message.marks


async def test_a_league_stop_does_not_make_a_channel_wait(env):
    """The league lane is stopped at a change of its own. A driver types into their channel: the
    answer is marked, given and the mark cleared, the league lane still stopped."""
    ran: list[str] = []
    _queue(env, _turn(ran=ran), _league_work())
    await env.bot.change_queue.ask("league_work", {}, what="`/league`", actor=DRIVER)
    await run_queue(env.bot)

    assert not await _waiting(env)
    message = _typed(env)
    await ask_turn(env.bot, "turn", message)
    await run_queue(env.bot)

    assert ran == ["answer"]
    assert MARK not in message.marks
    assert await stopped_job(env.db_path, "league") is not None


async def test_a_league_change_a_turn_handed_over_still_makes_its_channel_wait(env):
    """A driver's turn hands its work to the league, asking a league change that answers in their
    channel; the turn is done, but the league change is stopped on the league lane. The channel
    still waits, and the driver's next message is turned away with the note."""
    api = _api()

    async def _hand(_ctx):
        return api.StepResult(follow_ons=(
            api.FollowOn("handed", {"channel_id": CHANNEL}, "Handing the answer over"),
        ))

    handing = _change_type("handing", [api.Step("hand", api.StepKind.ACT, _hand)], turn=True,
                           conversation=True)
    _queue(env, handing, _league_work("handed", payload_channel=True))
    await ask_turn(env.bot, "handing", _typed(env, "first"))
    await run_queue(env.bot)

    assert [(row["kind"], row["state"]) for row in await change_rows(env.db_path)][0] == (
        "handing", "DONE")
    assert (await stopped_job(env.db_path, "league")) is not None
    assert await _waiting(env)
    assert await ask_turn(env.bot, "handing", _typed(env, "second")) is None
    assert len(env.channel.sent) == 1
