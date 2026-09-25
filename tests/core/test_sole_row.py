"""`sole_row` — the one row a query certain to return one has returned (#228).

aiosqlite types every `fetchone` as optional. An aggregate always returns a row, and so does a
lookup by a key just taken from the database; `sole_row` says so once, and raises by name where
that certainty was wrong rather than letting a None be indexed.
"""
from __future__ import annotations

import aiosqlite
import pytest

from leaguebot.core.db.database import sole_row


async def test_an_aggregate_returns_its_row(tmp_path):
    async with aiosqlite.connect(tmp_path / "scratch.db") as db:
        await db.execute("CREATE TABLE scratch (id INTEGER PRIMARY KEY)")
        cursor = await db.execute("SELECT COUNT(*) FROM scratch")

        assert (await sole_row(cursor))[0] == 0


async def test_a_query_that_found_nothing_is_refused(tmp_path):
    async with aiosqlite.connect(tmp_path / "scratch.db") as db:
        await db.execute("CREATE TABLE scratch (id INTEGER PRIMARY KEY)")
        cursor = await db.execute("SELECT id FROM scratch WHERE id = 1")

        with pytest.raises(RuntimeError, match="returned none"):
            await sole_row(cursor)
