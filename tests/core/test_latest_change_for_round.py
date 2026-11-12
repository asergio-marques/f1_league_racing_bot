"""The queue's record of the latest change of a kind for a round (#284, S7).

`latest_change_for_round` gives a module the id and the first job's number of the change it has
just asked of the queue, which `ever_asked` and `in_hand` do not: they give payloads only.
"""
from __future__ import annotations

import json

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.queue.queue_records import AskedChange, latest_change_for_round

KIND = "results.review.open"


async def _change(db, kind: str, round_id: int, *, jobs: int) -> tuple[int, list[int]]:
    cursor = await db.execute(
        "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, what) "
        "VALUES (?, ?, ?, 'BOT', 'DONE', 'a change')",
        (kind, f"{kind}:{round_id}", json.dumps({"round_id": round_id})),
    )
    change_id = int(cursor.lastrowid)
    steps = []
    for position in range(jobs):
        step = await db.execute(
            "INSERT INTO queued_change_steps (change_id, position, name) VALUES (?, ?, 'step')",
            (change_id, position),
        )
        steps.append(int(step.lastrowid))
    return change_id, steps


async def test_the_latest_change_of_the_kind_for_the_round_is_named_with_its_first_job(tmp_path):
    path = str(tmp_path / "t.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await _change(db, KIND, 4, jobs=2)
        await _change(db, "results.other", 4, jobs=1)
        await _change(db, KIND, 9, jobs=1)
        later, steps = await _change(db, KIND, 4, jobs=3)
        await db.commit()

    assert await latest_change_for_round(path, KIND, 4) == AskedChange(later, steps[0])


async def test_a_change_with_no_jobs_has_no_first_job(tmp_path):
    path = str(tmp_path / "t.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        change_id, _ = await _change(db, KIND, 4, jobs=0)
        await db.commit()

    assert await latest_change_for_round(path, KIND, 4) == AskedChange(change_id, None)


async def test_no_change_of_the_kind_for_the_round_is_none(tmp_path):
    path = str(tmp_path / "t.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await _change(db, KIND, 9, jobs=1)
        await _change(db, "results.other", 4, jobs=1)
        await db.commit()

    assert await latest_change_for_round(path, KIND, 4) is None
