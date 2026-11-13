"""The queue's bookkeeping: what is in hand, what was ever asked, and the numbering of its jobs.

Module-level functions over the queue's own tables, `queued_changes` and `queued_change_steps`,
which read none of `ChangeQueue`'s state: a change type's check, a restart's recovery, a restore
and a factory reset call them without a queue. `empty_queue_in` and `carry_job_numbering` write,
on a staged file nothing else holds open, so each commits and is named in the write check's
allowance (`SAVES_ALLOWED`) as the queue's own record. The decision that governs the reads is
below, pinned by a test in `tests/core/test_change_queue.py`.

**What is still in hand is read, not remembered** (`unfinished`). A module-level read gives the
payloads of the changes of the kinds asked that are QUEUED or RUNNING, a stopped one included
(its state stays as it was) and the one named in *excluding* left out, which a check handed its own
`change_id` uses to leave itself out. A check uses it to refuse a press while the same approval is
already in hand, and a restart's recovery and a sweep use it to leave alone what the queue is still
carrying out. It gives what the repeat rule of `change_queue` does not: that refuses a repeat only while the
first has not started, where a specification may ask for the refusal while the first is still being
applied, whatever its position.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from leaguebot.core.db.database import get_connection


def empty_queue_in(path: str | os.PathLike[str]) -> None:
    """Delete every change, and its steps, from the league database at *path*.

    Synchronous, for the restore: it runs at start-up before the swap, on the staged file, when no
    event loop is running and nothing holds a connection. Where the tables are not there (a file
    from before the queue) it does nothing.
    """
    db = sqlite3.connect(str(path))
    try:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('queued_changes', 'queued_change_steps')"
            )
        }
        if "queued_change_steps" in tables:
            db.execute("DELETE FROM queued_change_steps")
        if "queued_changes" in tables:
            db.execute("DELETE FROM queued_changes")
        db.commit()
    finally:
        db.close()


async def unfinished(
    db_path: str, kinds: Collection[str], *, excluding: int | None = None,
    before: int | None = None,
) -> list[dict[str, Any]]:
    """The payloads of the changes of *kinds* that are queued or running, a stopped one included,
    oldest first, leaving out the change whose id is *excluding*, and, with *before*, those asked
    at or after the change whose id that is: a check that holds only what was asked ahead of it.

    A change done, refused, dropped or discarded is not in hand and is not given. Read on a
    connection of its own, so a check may call it while the queue is between jobs.
    """
    kinds = list(kinds)
    if not kinds:
        return []
    marks = ", ".join("?" for _ in kinds)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT payload FROM queued_changes WHERE kind IN ({marks}) "  # noqa: S608 — marks only
            "AND state IN ('QUEUED', 'RUNNING') AND (? IS NULL OR id != ?) "
            "AND (? IS NULL OR id < ?) ORDER BY id",
            (*kinds, excluding, excluding, before, before),
        )
        return [json.loads(row["payload"]) for row in await cursor.fetchall()]


async def ever_asked(db_path: str, kinds: Collection[str]) -> list[dict[str, Any]]:
    """The payloads of every change of *kinds* the queue has recorded, in whatever state, oldest
    first.

    Where :func:`unfinished` gives only what is in hand, this gives what was asked for at all: a
    change done, discarded, dropped or refused at its start is still a record that it was asked
    for. A change refused when it was asked is not saved, so is not given. Read on a connection of
    its own.
    """
    kinds = list(kinds)
    if not kinds:
        return []
    marks = ", ".join("?" for _ in kinds)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT payload FROM queued_changes WHERE kind IN ({marks}) ORDER BY id",  # noqa: S608 — marks only
            tuple(kinds),
        )
        return [json.loads(row["payload"]) for row in await cursor.fetchall()]


async def in_hand(
    db_path: str, kinds: Collection[str], *, excluding: int | None = None
) -> list[tuple[dict[str, Any], int | None]]:
    """Each change of *kinds* that is queued or running, a stopped one included, as its payload
    and the number of the job it waits on, oldest first, leaving out the change whose id is
    *excluding*.

    *excluding* is how a check reads the queue for its own kind without finding itself, as
    :func:`unfinished` does.

    The job is the change's first job not yet done, which is the stopped one where the queue is
    stopped at it, or `None` where every job is done and only the change's own close remains. It
    is the number a manager sees on the notice, and so the one a refusal names. Read as
    :func:`unfinished` is.
    """
    kinds = list(kinds)
    if not kinds:
        return []
    marks = ", ".join("?" for _ in kinds)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT c.payload, (SELECT s.id FROM queued_change_steps s WHERE s.change_id = c.id "
            "AND s.done_at IS NULL ORDER BY s.position LIMIT 1) AS job "
            f"FROM queued_changes c WHERE c.kind IN ({marks}) "  # noqa: S608 — marks only
            "AND c.state IN ('QUEUED', 'RUNNING') AND (? IS NULL OR c.id != ?) ORDER BY c.id",
            (*kinds, excluding, excluding),
        )
        return [(json.loads(row["payload"]), row["job"]) for row in await cursor.fetchall()]


async def done_step_results_before(
    db_path: str, kind: str, step_name: str, before: int,
    matching: Mapping[str, Any] | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Each change of *kind* asked before the change whose id is *before* whose step *step_name* is
    done, as its payload and that step's saved result (an empty dict where it saved none), newest
    first. With *matching*, only the changes whose payload holds each of its values (by key, at
    the top level), filtered as the queue reads rather than after.

    For a change type whose step must know what an earlier change's step left, the queue being
    durable and the earlier change done by now: a redraw that learns from the nearest earlier
    answer to its call whether that one left the drawing to a later one, and so asks for the
    answers to its own call alone, never every answer ever queued. Read on a connection of its own,
    as :func:`unfinished` is.
    """
    wanted = dict(matching or {})
    for key in wanted:
        if not key.isidentifier():
            raise ValueError(f"cannot match a payload on {key!r}")
    where = "".join(f" AND json_extract(c.payload, '$.{key}') = ?" for key in wanted)  # noqa: S608
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT c.payload, s.result FROM queued_change_steps s "
            "JOIN queued_changes c ON c.id = s.change_id "
            "WHERE c.kind = ? AND c.id < ? AND s.name = ? AND s.done_at IS NOT NULL"
            f"{where} ORDER BY c.id DESC",  # noqa: S608 — identifiers only
            (kind, before, step_name, *wanted.values()),
        )
        return [
            (json.loads(row["payload"]), json.loads(row["result"] or "null") or {})
            for row in await cursor.fetchall()
        ]


@dataclass(frozen=True)
class AskedChange:
    """A change the queue was asked for: its id and the number of its first job, which is None
    for a change with no jobs recorded."""

    change_id: int
    first_job: int | None


async def latest_change_for_round(db_path: str, kind: str, round_id: int) -> AskedChange | None:
    """The latest change of *kind* whose payload names *round_id*, whatever its state, or None
    where none was ever asked.

    For a module naming a change it has just asked of its own (results' penalty review, which says
    in the channel which job is opening it): :func:`ever_asked` and :func:`in_hand` give payloads
    only, and this gives the change's id and the number of its first job, the job a manager sees on
    the notice. Read on a connection of its own, as they are.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT c.id, (SELECT MIN(s.id) FROM queued_change_steps s WHERE s.change_id = c.id) "
            "AS first_job FROM queued_changes c WHERE c.kind = ? "
            "AND json_extract(c.payload, '$.round_id') = ? ORDER BY c.id DESC LIMIT 1",
            (kind, round_id),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return AskedChange(
        change_id=int(row["id"]), first_job=None if row["first_job"] is None else int(row["first_job"])
    )


def highest_job_number(path: str | os.PathLike[str]) -> int:
    """The highest job number the league database at *path* has ever issued, or 0.

    Read from the database's own count of the numbers it has issued, which a deleted job does not
    lower, and from the jobs it holds. Synchronous, for a restore and a factory reset, which run
    where no connection is open. Where the table is not there, 0.
    """
    db = sqlite3.connect(str(path))
    try:
        counted = 0
        try:
            row = db.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = 'queued_change_steps'"
            ).fetchone()
            counted = int(row[0]) if row else 0
            held = db.execute("SELECT COALESCE(MAX(id), 0) FROM queued_change_steps").fetchone()
            return max(counted, int(held[0]))
        except sqlite3.OperationalError:
            return counted
    finally:
        db.close()


def carry_job_numbering(path: str | os.PathLike[str], highest: int) -> None:
    """Make the next job the league database at *path* numbers come after *highest*.

    A database put in place of another (a restored state, a freshly migrated one at a factory
    reset) would otherwise begin the numbering from its own count, and a number the log channel
    already names would name a second job. Never lowers a count the database already has.
    Synchronous, as `highest_job_number`.
    """
    if highest <= 0:
        return
    db = sqlite3.connect(str(path))
    try:
        row = db.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'queued_change_steps'"
        ).fetchone()
        if row is None:
            db.execute(
                "INSERT INTO sqlite_sequence (name, seq) VALUES ('queued_change_steps', ?)",
                (highest,),
            )
        elif int(row[0]) < highest:
            db.execute(
                "UPDATE sqlite_sequence SET seq = ? WHERE name = 'queued_change_steps'",
                (highest,),
            )
        db.commit()
    finally:
        db.close()
