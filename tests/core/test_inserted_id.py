"""`inserted_id` — the id of the row an INSERT has just written (#228).

aiosqlite types `Cursor.lastrowid` as optional, because a cursor in general need not have run
an INSERT. After one, SQLite always sets it; `inserted_id` says so once, and raises by name on
a cursor that wrote no row rather than letting None be stored as an id.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import aiosqlite
import pytest

from db.database import inserted_id


async def test_it_is_the_id_sqlite_gave_the_row(tmp_path):
    async with aiosqlite.connect(tmp_path / "scratch.db") as db:
        await db.execute("CREATE TABLE scratch (id INTEGER PRIMARY KEY, name TEXT)")
        await db.execute("INSERT INTO scratch (name) VALUES ('first')")
        cursor = await db.execute("INSERT INTO scratch (name) VALUES ('second')")

        assert inserted_id(cursor) == 2


def test_a_cursor_that_wrote_no_row_is_refused():
    cursor = MagicMock()
    cursor.lastrowid = None
    with pytest.raises(RuntimeError, match="wrote no row"):
        inserted_id(cursor)
