"""Taking a change back before the worker has touched it (`ChangeQueue.withdraw`), and asking for
several that belong together with the worker held back (`ChangeQueue.holding`) (#439, C97).

An approval's take-down of its review is asked ahead of the approval, which the queue may then
refuse at once; a request refused at once leaves the review standing, so the take-down must still
be there to take back, and the worker must not have carried it out in between.
"""
from __future__ import annotations

import asyncio

from leaguebot.core.models import change
from leaguebot.core.services import change_queue
from leaguebot.core.models.change import ChangeOrigin
from tests.support.change_queue import attach_queue, change_rows, league_double, run_queue, seed_server

KIND = "test.withdraw"


def _type() -> change_queue.ChangeType:
    async def _check(_ctx):
        return change.Verdict.go()

    async def _nothing(_ctx):
        return change.StepResult()

    return change_queue.ChangeType(
        kind=KIND,
        opening=(change.PlannedStep("act", {}),),
        steps={"act": change_queue.Step("act", change.StepKind.ACT, _nothing)},
        check=_check,
        key=lambda payload: f"{KIND}|{payload.get('n')}",
        doing=lambda _payload: "doing the test thing",
        outcome=lambda _ctx: "✅ Done.",
    )


async def _queue(tmp_path):
    from datetime import datetime, timezone

    from leaguebot.core.db.database import run_migrations

    db_path = str(tmp_path / "queue.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    bot = league_double(db_path)
    attach_queue(bot, db_path, now=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc), types=[_type()])
    return bot, db_path


async def _ask(bot, n: int) -> int | None:
    return await bot.change_queue.ask(KIND, {"n": n}, what="the test thing", origin=ChangeOrigin.BOT)


async def test_a_change_asked_and_not_started_can_be_taken_back(tmp_path):
    """A change is asked and the worker has not touched it: it is taken back, its rows with it, so
    the worker finds nothing to run. Taken back again, there is nothing to take."""
    bot, db_path = await _queue(tmp_path)
    change_id = await _ask(bot, 1)
    assert change_id is not None

    assert await bot.change_queue.withdraw(change_id) is True
    assert await change_rows(db_path) == []
    await run_queue(bot)
    assert await change_rows(db_path) == []
    assert await bot.change_queue.withdraw(change_id) is False


async def test_a_change_the_worker_has_carried_out_is_not_taken_back(tmp_path):
    """The worker has run the change: it is DONE and stays, and withdrawing it says it was not
    taken back."""
    bot, db_path = await _queue(tmp_path)
    change_id = await _ask(bot, 1)
    await run_queue(bot)

    assert await bot.change_queue.withdraw(change_id) is False
    assert [row["id"] for row in await change_rows(db_path)] == [change_id]


async def test_asks_made_while_holding_pass_through_and_the_worker_waits(tmp_path):
    """While the caller holds the queue it can ask for several changes, in order, without waiting
    on its own hold; the worker, asked to run meanwhile, does nothing until the hold is let go, so
    a change asked and taken back inside it was never started."""
    bot, db_path = await _queue(tmp_path)
    queue = bot.change_queue

    async with queue.holding():
        first = await asyncio.wait_for(_ask(bot, 1), timeout=5)
        second = await asyncio.wait_for(_ask(bot, 2), timeout=5)
        worker = asyncio.create_task(run_queue(bot))
        await asyncio.sleep(0.2)
        assert not worker.done()
        assert [row["state"] for row in await change_rows(db_path)] == ["QUEUED", "QUEUED"]
        assert await queue.withdraw(first) is True
    await asyncio.wait_for(worker, timeout=5)

    assert [(row["id"], row["state"]) for row in await change_rows(db_path)] == [(second, "DONE")]
