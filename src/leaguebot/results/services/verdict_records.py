"""The one way to find a round's verdict records, by the driver and session each belongs to.

``penalty_records`` and ``appeal_records`` store neither the driver a verdict was given to nor
its session — only a reference to that driver's row in ``race_session_results`` or
``qualifying_session_results``, whichever the session writes to. The driver is read off that
row, and the session through ``session_results``. Every reader of a round's verdicts needs the
same join, and it was written out seven times over before this module (#345): the capture of
superseded announcements, the snapshot and the revert, the rewrite, the hydration of a review,
and the republish. A copy missed by a change to how a verdict maps to its driver split one of
those from the others, so the join lives here and each caller names only its columns and scope.

Callers pass their own connection, because several read or delete inside a transaction of
their own.
"""
from __future__ import annotations

from typing import Iterable

#: The two tables a verdict is recorded in.
VERDICT_TABLES: tuple[str, str] = ("penalty_records", "appeal_records")

#: Each verdict points at one driver's result row, through the column naming its table.
_RESULT_LINKS: tuple[tuple[str, str], ...] = (
    ("race_result_id", "race_session_results"),
    ("qual_result_id", "qualifying_session_results"),
)


def _scope(
    round_id: int | None,
    session_types: Iterable | None,
    session_result_id: int | None,
) -> tuple[str, list]:
    """The WHERE clause over ``sr`` for one session, or a round narrowed to some session types."""
    if session_result_id is not None:
        return "sr.id = ?", [session_result_id]
    if round_id is None:
        raise ValueError("a verdict query needs a round or a session")
    clause, params = "sr.round_id = ?", [round_id]
    if session_types is not None:
        values = [getattr(st, "value", st) for st in session_types]
        clause += f" AND sr.session_type IN ({', '.join('?' for _ in values)})"
        params += values
    return clause, params


async def select_verdicts(
    db,
    table: str,
    columns: str,
    *,
    round_id: int | None = None,
    session_types: Iterable | None = None,
    session_result_id: int | None = None,
    where: str = "",
) -> list[dict]:
    """The verdict records of *table* in scope, each joined to its driver's row and its session.

    *columns* may name ``v`` (the verdict), ``r`` (the driver's result row) and ``sr`` (the
    session). The scope is one session, by *session_result_id*, or a round, narrowed to
    *session_types* where given. *where* adds a condition of the caller's own, beginning with
    ``AND``.

    Oldest first within each result table; a caller wanting one order across both sorts on the
    verdict id it selects. *table* and *columns* are the caller's literals, never input.
    """
    if table not in VERDICT_TABLES:
        raise ValueError(f"not a verdict table: {table}")
    clause, params = _scope(round_id, session_types, session_result_id)
    rows: list[dict] = []
    for fk_col, result_table in _RESULT_LINKS:
        cursor = await db.execute(
            f"""
            SELECT {columns}
            FROM {table} v
            JOIN {result_table} r ON r.id = v.{fk_col}
            JOIN session_results sr ON sr.id = r.session_result_id
            WHERE {clause}{where}
            ORDER BY v.id
            """,  # noqa: S608 — every name is a constant above or the caller's literal
            params,
        )
        rows.extend(dict(row) for row in await cursor.fetchall())
    return rows


async def delete_verdicts(
    db,
    *,
    round_id: int | None = None,
    session_types: Iterable | None = None,
    session_result_id: int | None = None,
) -> None:
    """Delete the verdict records of both tables in scope, as :func:`select_verdicts` scopes them.

    Nothing is committed: the caller's transaction decides that.
    """
    clause, params = _scope(round_id, session_types, session_result_id)
    for table in VERDICT_TABLES:
        for fk_col, result_table in _RESULT_LINKS:
            await db.execute(
                f"""
                DELETE FROM {table}
                WHERE {fk_col} IN (
                    SELECT r.id FROM {result_table} r
                    JOIN session_results sr ON sr.id = r.session_result_id
                    WHERE {clause}
                )
                """,  # noqa: S608 — every name is a constant above
                params,
            )
