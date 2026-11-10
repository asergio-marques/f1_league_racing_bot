"""Asking for a change with another carried out before it, in one save (`ChangeQueue.ask_after`)
(#439, C97).

An approval's take-down of its review is asked with the approval, ahead of it, and only for an
approval the queue accepts: a request it refuses leaves the review standing, so no take-down may
be left on the queue then, and none may be there should the bot stop between the two.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from leaguebot.core.db.database import run_migrations
from leaguebot.core.models import change
from leaguebot.core.models.change import ChangeOrigin
from leaguebot.core.services import change_queue
from leaguebot.core.services.change_queue import Ahead
from tests.support.change_queue import (
    acknowledgement,
    attach_queue,
    change_rows,
    league_double,
    member_interaction,
    run_queue,
    seed_server,
)

MAIN, AHEAD = "test.main", "test.ahead"


def _type(kind: str, *, verdict=None, ran: list[str] | None = None) -> change_queue.ChangeType:
    async def _check(_ctx):
        return verdict if verdict is not None else change.Verdict.go()

    async def _act(_ctx):
        if ran is not None:
            ran.append(kind)
        return change.StepResult()

    return change_queue.ChangeType(
        kind=kind,
        opening=(change.PlannedStep("act", {}),),
        steps={"act": change_queue.Step("act", change.StepKind.ACT, _act)},
        check=_check,
        key=lambda payload: f"{kind}|{payload.get('n')}",
        doing=lambda _payload: f"doing {kind}",
        outcome=lambda _ctx: "✅ Done.",
    )


async def _queue(tmp_path, *, main_verdict=None, ahead_verdict=None, ran=None):
    db_path = str(tmp_path / "queue.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    bot = league_double(db_path)
    attach_queue(
        bot, db_path, now=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        types=[_type(MAIN, verdict=main_verdict, ran=ran), _type(AHEAD, verdict=ahead_verdict, ran=ran)],
    )
    return bot, db_path


def _ahead(n: int = 1) -> Ahead:
    return Ahead(AHEAD, {"n": n}, "the thing ahead")


async def _ask_after(bot, interaction, n: int = 1):
    return await bot.change_queue.ask_after(
        _ahead(), MAIN, {"n": n}, interaction=interaction, what="the main thing"
    )


async def test_the_two_are_saved_together_the_one_ahead_first_and_run_in_that_order(tmp_path):
    """Both changes are saved by one ask, the one ahead with the lower id, the bot's; only the
    change asked for is acknowledged to the member. The queue then carries the one ahead out first."""
    ran: list[str] = []
    bot, db_path = await _queue(tmp_path, ran=ran)
    interaction = member_interaction(bot)

    change_id = await _ask_after(bot, interaction)

    rows = await change_rows(db_path)
    assert [row["kind"] for row in rows] == [AHEAD, MAIN]
    assert rows[1]["id"] == change_id and rows[0]["id"] < change_id
    assert rows[0]["origin"] == ChangeOrigin.BOT.value
    assert "doing test.main" in acknowledgement(interaction)
    assert interaction.response.send_message.await_count == 1

    await run_queue(bot)

    assert ran == [AHEAD, MAIN]


async def test_a_request_the_queue_refuses_at_once_leaves_nothing_ahead_of_it_queued(tmp_path):
    """The change asked for fails its check, so the member is refused and nothing is saved: the
    one that was to run ahead of it is not left on the queue."""
    bot, db_path = await _queue(tmp_path, main_verdict=change.Verdict.refuse("⛔ No."))
    interaction = member_interaction(bot)

    assert await _ask_after(bot, interaction) is None

    assert await change_rows(db_path) == []
    assert "No." in acknowledgement(interaction)


async def test_a_request_repeating_one_not_started_saves_neither(tmp_path):
    """The change asked for repeats the last on its lane before that has started: it is not asked
    for again, and the one ahead of it is not saved either."""
    bot, db_path = await _queue(tmp_path)
    first = await bot.change_queue.ask(MAIN, {"n": 1}, what="the main thing", origin=ChangeOrigin.BOT)
    assert first is not None

    assert await _ask_after(bot, member_interaction(bot)) is None

    assert [row["id"] for row in await change_rows(db_path)] == [first]


async def test_a_fault_in_the_save_leaves_neither_queued(tmp_path, monkeypatch):
    """The save raises after the change ahead is written and before the other is: the transaction
    is not committed, so the queue holds neither, and the fault reaches the caller."""
    bot, db_path = await _queue(tmp_path)
    queue = bot.change_queue
    real = queue._insert
    calls = {"n": 0}

    async def _second_fails(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("the disk is full")
        return await real(*args, **kwargs)

    monkeypatch.setattr(queue, "_insert", _second_fails)

    with pytest.raises(RuntimeError):
        await _ask_after(bot, member_interaction(bot))

    assert await change_rows(db_path) == []


async def test_the_one_ahead_is_left_out_where_it_is_no_longer_due(tmp_path):
    """The change ahead finds itself no longer due: the change asked for is asked alone."""
    bot, db_path = await _queue(tmp_path, ahead_verdict=change.Verdict.not_due("gone"))

    change_id = await _ask_after(bot, member_interaction(bot))

    assert [(row["id"], row["kind"]) for row in await change_rows(db_path)] == [(change_id, MAIN)]


async def test_the_one_ahead_is_left_out_where_it_repeats_one_not_started(tmp_path):
    """The change ahead repeats the last on its lane before that has started: it is not saved a
    second time, and the change asked for still is."""
    bot, db_path = await _queue(tmp_path)
    first = await bot.change_queue.ask(AHEAD, {"n": 1}, what="the thing ahead", origin=ChangeOrigin.BOT)

    change_id = await _ask_after(bot, member_interaction(bot))

    assert [(row["id"], row["kind"]) for row in await change_rows(db_path)] == [
        (first, AHEAD), (change_id, MAIN)
    ]
