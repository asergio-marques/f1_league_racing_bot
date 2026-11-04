"""Every kind of delete, against a database holding a row in every table (architecture.md, "The
database").

A season, division or round deleted takes everything that refers to it, and nothing else. Core
deletes its own rows; each module deletes its own on the hook core calls for that kind of delete,
on the delete's connection, inside its save. A list of the tables a delete has to clear goes stale
the day a table is added (the hand list this test replaced already had), so the tables are read
from the schema `run_migrations` builds, and a table the filler cannot fill fails the test by name
rather than drop out of it.

**Two of everything.** Every table gets a row under each of two seasons: the first is the one
deleted from, in setup, the second a completed season beside it. A table no season reaches (a
driver, a points configuration) gets its two rows all the same, and keeps them; a table that holds
one row at most (the server's settings, a module's) holds the one, shared.

**What refers to a row.** A foreign key, and a column with no key whose name says what it holds
(`REFERENCE_BY_NAME`), as `season_review_prompts.season_id` does, but for the audit's
(`KEPT_REFERENCES`), which keeps its record of a row deleted. What refers to a deleted row is
deleted with it, and so is what refers to that, to the end of the chain; a key that sets its
column to NULL instead lets go of the row rather than going with it. The test works out from the
schema which rows those are, and asserts that the delete leaves every other row exactly as it was
and none of those.

**A placed driver is not filled in.** A division whose driver is placed in a seat is not deleted,
by decision (`test_a_division_with_a_driver_placed_in_it_is_not_deleted`), so the placement's seat
is left empty here and the division delete runs.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable
from typing import Any

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.season_service import SeasonService
from tests.support.delete_hooks import DIVISION_HOOKS, ROUND_HOOKS, SEASON_HOOKS, delete_hooks

#: A column with no foreign key that names a core row all the same, by what it is called.
REFERENCE_BY_NAME = {
    "season_id": "seasons",
    "division_id": "divisions",
    "round_id": "rounds",
    "driver_profile_id": "driver_profiles",
    "team_instance_id": "team_instances",
    "team_seat_id": "team_seats",
}

#: Values a column must hold for its row to stand, by table and world (0, the season deleted from;
#: 1, the season kept). The first season is in setup, as only a season in setup is deleted.
OVERRIDES: dict[str, dict[str, tuple[Any, Any]]] = {
    "seasons": {"status": ("SETUP", "COMPLETED"), "stage": ("PLACEMENTS", "COMPLETED")},
    # A placed driver stops a division's delete, by decision: the seat is left empty.
    "driver_season_assignments": {"team_seat_id": (None, None)},
}

#: A column naming a core row that is kept when the row is deleted: the audit keeps its record of
#: what was done to a division, the division deleted included.
KEPT_REFERENCES = {("audit_entries", "division_id")}

_IN_LIST = r"\b{column}\b[^,]*?\bIN\s*\(\s*('(?:[^']*)'|-?\d+)"


async def _tables(db) -> list[str]:
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    )
    return [row[0] for row in await cursor.fetchall()]


async def _links(db, table: str) -> list[tuple[str, str, str, str]]:
    """What each row of *table* refers to: (column, table, column referred to, on delete)."""
    cursor = await db.execute(f'PRAGMA foreign_key_list("{table}")')
    keyed = [(row[3], row[2], row[4] or "id", row[6]) for row in await cursor.fetchall()]
    keyed_columns = {column for column, *_ in keyed}
    cursor = await db.execute(f'PRAGMA table_info("{table}")')
    named = [
        (row[1], REFERENCE_BY_NAME[row[1]], "id", "NO ACTION")
        for row in await cursor.fetchall()
        if row[1] in REFERENCE_BY_NAME
        and row[1] not in keyed_columns
        and (table, row[1]) not in KEPT_REFERENCES
        and REFERENCE_BY_NAME[row[1]] != table
    ]
    return keyed + named


def _filled_value(table: str, column: str, kind: str, sql: str, world: int, n: int) -> Any:
    """A value for a column that must hold one: the first a CHECK allows, else one of its type,
    unlike any other world's."""
    allowed = re.search(_IN_LIST.format(column=re.escape(column)), sql, re.IGNORECASE)
    if allowed:
        value = allowed.group(1)
        return value.strip("'") if value.startswith("'") else int(value)
    if any(word in kind.upper() for word in ("INT", "BOOL", "REAL", "NUM")):
        return 100 * (world + 1) + n
    if column.endswith("_at") or column.endswith("_date"):
        return f"2026-0{world + 1}-0{n % 9 + 1}T00:00:00+00:00"
    return f"{table}.{column}.{world}"


def _order(tables: list[str], links: dict[str, list[tuple[str, str, str, str]]]) -> list[str]:
    """The tables, each after every table it refers to but itself (a cycle is broken at a column
    left empty)."""
    ordered: list[str] = []
    pending = list(tables)
    while pending:
        ready = [
            t for t in pending
            if all(parent == t or parent in ordered for _c, parent, _p, _d in links[t])
        ] or pending[:1]
        ordered.extend(ready)
        pending = [t for t in pending if t not in ready]
    return ordered


async def _fill(db_path: str) -> dict[str, list[dict[str, Any]]]:
    """A row in every table under each of two seasons; each world's rows, by table. A table that
    takes no row fails the test, naming it."""
    worlds: list[dict[str, dict[str, Any]]] = [{}, {}]
    async with get_connection(db_path) as db:
        tables = await _tables(db)
        links = {table: await _links(db, table) for table in tables}
        cursor = await db.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table'"
        )
        sql_of = {row[0]: row[1] for row in await cursor.fetchall()}
        unfilled: list[str] = []
        for table in _order(tables, links):
            cursor = await db.execute(f'PRAGMA table_info("{table}")')
            columns = list(await cursor.fetchall())
            refers = {column: (parent, to) for column, parent, to, _d in links[table]}
            for world, rows in enumerate(worlds):
                values: dict[str, Any] = {}
                for n, (_cid, column, kind, notnull, default, pk) in enumerate(columns):
                    if column in OVERRIDES.get(table, {}):
                        values[column] = OVERRIDES[table][column][world]
                    elif column in refers:
                        parent, to = refers[column]
                        if parent in rows:
                            values[column] = rows[parent][to]
                    elif pk and kind.upper() == "INTEGER":
                        continue
                    elif notnull and default is None:
                        values[column] = _filled_value(table, column, kind, sql_of[table], world, n)
                names = ", ".join(f'"{c}"' for c in values)
                marks = ", ".join("?" for _ in values)
                try:
                    cursor = await db.execute(
                        f'INSERT INTO "{table}" ({names}) VALUES ({marks})'
                        if values else f'INSERT INTO "{table}" DEFAULT VALUES',
                        tuple(values.values()),
                    )
                except Exception as error:  # noqa: BLE001 — named in the failure below
                    if world == 1 and table in worlds[0] and not links[table]:
                        # A table holding one row at most (a settings row): both share it.
                        rows[table] = worlds[0][table]
                        continue
                    unfilled.append(f"{table} ({type(error).__name__}: {error})")
                    continue
                cursor = await db.execute(
                    f'SELECT * FROM "{table}" WHERE rowid = ?', (cursor.lastrowid,)
                )
                rows[table] = dict(await cursor.fetchone())
        await db.commit()
    if unfilled:
        pytest.fail("tables the test could not fill: " + "; ".join(unfilled))
    return {table: [w[table] for w in worlds] for table in worlds[0]}


async def _snapshot(db_path: str) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list]]:
    """Every row of every table, and what each table's rows refer to."""
    async with get_connection(db_path) as db:
        tables = await _tables(db)
        rows = {}
        for table in tables:
            cursor = await db.execute(f'SELECT * FROM "{table}"')
            rows[table] = [dict(row) for row in await cursor.fetchall()]
        links = {table: await _links(db, table) for table in tables}
    return rows, links


def _under(
    rows: dict[str, list[dict[str, Any]]], links: dict[str, list], table: str, row_id: int
) -> set[tuple[str, int]]:
    """The rows that refer to *table*'s row *row_id*, to the end of the chain, with it: each as
    (table, its position in *rows*). A key that sets its column to NULL does not carry the chain."""
    gone = {(table, i) for i, row in enumerate(rows[table]) if row["id"] == row_id}
    while True:
        values = {
            (t, column, value)
            for t, i in gone for column, value in rows[t][i].items() if value is not None
        }
        more = {
            (t, i)
            for t, table_rows in rows.items()
            for i, row in enumerate(table_rows)
            if (t, i) not in gone
            and any(
                on_delete != "SET NULL" and (parent, to, row[column]) in values
                for column, parent, to, on_delete in links[t]
            )
        }
        if not more:
            return gone
        gone |= more


def _sorted(rows: Iterable[dict[str, Any]]) -> list[str]:
    return sorted(repr(sorted(row.items())) for row in rows)


async def _delete(db_path: str, deleted: str, row_id: int) -> None:
    if deleted == "season":
        async with get_connection(db_path) as db:
            await SeasonService.delete_season(db, row_id, **delete_hooks(*SEASON_HOOKS))
            await db.commit()
    elif deleted == "division":
        await SeasonService(db_path).delete_division(row_id, **delete_hooks(*DIVISION_HOOKS))
    else:
        await SeasonService(db_path).delete_round(row_id, **delete_hooks(*ROUND_HOOKS))


_TABLE_OF = {"season": "seasons", "division": "divisions", "round": "rounds"}


@pytest.mark.xfail(
    strict=True,
    reason="#283: the deletes take no delete hooks, and the builder makes none to hand them",
)
@pytest.mark.parametrize("deleted", ["season", "division", "round"])
async def test_every_delete_leaves_no_orphan(tmp_path, deleted):
    """A row in every table under two seasons; the first season's own row of *deleted* (the
    season itself, in setup; its one division; its one round) is deleted as `/season abort`,
    `/division delete` and `/round delete` delete it. Nothing referring to it is left, to the end
    of the chain, and every other row in the database is exactly as it was."""
    db_path = os.path.join(str(tmp_path), "every_table.db")
    await run_migrations(db_path)
    filled = await _fill(db_path)
    rows, links = await _snapshot(db_path)
    target = _TABLE_OF[deleted]
    gone = _under(rows, links, target, filled[target][0]["id"])
    assert len({t for t, _i in gone}) > 1  # the seed is real: something refers to it

    await _delete(db_path, deleted, filled[target][0]["id"])

    after, _ = await _snapshot(db_path)
    left = {
        table: _sorted(after[table])
        for table in rows
        if _sorted(after[table]) != _sorted(
            row for i, row in enumerate(rows[table]) if (table, i) not in gone
        )
    }
    assert left == {}, f"tables not as the delete should leave them: {sorted(left)}"
