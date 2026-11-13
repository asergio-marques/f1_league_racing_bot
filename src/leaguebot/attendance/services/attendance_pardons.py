"""A round's attendance pardons: what is recorded, and the putting back of what a recalculation held.

The only code that writes `attendance_pardons` (a pardon is a row of it): the report stage holds a
round's pardons, and a rewrite leaves the round carrying exactly the ones saved.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from leaguebot.core.db.database import get_connection


async def _rewrite_round_pardons_on(
    db, round_id: int, staged_pardons: list, now: datetime
) -> None:
    """Leave the round carrying exactly the pardons the report stage held, on *db*.

    ``INSERT OR IGNORE``, which the first pass uses, cannot express a pardon the manager
    removed, so the round's pardons go first and the approved set is written whole. A pardon
    kept keeps the time it was granted; one staged now takes *now*, the queue's clock. Commits
    nothing: it is a step of the amendment's save.
    """
    await db.execute(
        "DELETE FROM attendance_pardons WHERE attendance_id IN "
        "(SELECT id FROM driver_round_attendance WHERE round_id = ?)",
        (round_id,),
    )
    for pardon in staged_pardons:
        await db.execute(
            "INSERT OR IGNORE INTO attendance_pardons "
            "(attendance_id, pardon_type, justification, granted_by, granted_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (pardon.attendance_id, pardon.pardon_type, pardon.justification,
             pardon.grantor_id, getattr(pardon, "granted_at", None) or now.isoformat()),
        )


async def recorded_pardons(db_path: str, round_id: int) -> list[dict[str, Any]]:
    """The round's recorded pardons, oldest first: each with its attendance row
    (``attendance_id``), ``pardon_type``, ``justification``, ``granted_by``, ``granted_at``, the
    driver's profile (``driver_profile_id``) and their current Discord account
    (``discord_user_id``), which core names (`driver_names_for_profiles`).

    What the review's staged pardons are rebuilt from (`AttendanceAfterReview.recorded_pardons`).
    Reads only.
    """
    from leaguebot.core.services.drivers.driver_service import driver_names_for_profiles

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT p.attendance_id, p.pardon_type, p.justification, p.granted_by, p.granted_at, "
            "a.driver_profile_id AS driver_profile_id FROM attendance_pardons p "
            "JOIN driver_round_attendance a ON a.id = p.attendance_id "
            "WHERE a.round_id = ? ORDER BY p.id",
            (round_id,),
        )
        rows = [dict(row) for row in await cursor.fetchall()]
    names = await driver_names_for_profiles(db_path, [row["driver_profile_id"] for row in rows])
    return [
        {**row, "discord_user_id": names[row["driver_profile_id"]][0]}
        for row in rows
        if row["driver_profile_id"] in names
    ]


async def pardons_of_on(db, round_id: int) -> list[dict[str, Any]]:
    """The round's pardons whole, ``id`` and ``granted_at`` included, oldest first, on *db*.

    What an amendment's snapshot keeps (`AttendanceAfterReview.pardons_of_on`): the value is
    attendance's own, and `put_back_pardons_on` is the one that reads it back. Reads only.
    """
    cursor = await db.execute(
        "SELECT * FROM attendance_pardons WHERE attendance_id IN "
        "(SELECT id FROM driver_round_attendance WHERE round_id = ?) ORDER BY id",
        (round_id,),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def put_back_pardons_on(db, round_id: int, saved: list[dict[str, Any]]) -> None:
    """Leave the round carrying exactly the pardons *saved*, which `pardons_of_on` gave, ids and
    times granted as they were, on *db*. Commits nothing: it is a step of the revert's save."""
    await db.execute(
        "DELETE FROM attendance_pardons WHERE attendance_id IN "
        "(SELECT id FROM driver_round_attendance WHERE round_id = ?)",
        (round_id,),
    )
    for pardon in saved:
        columns = ", ".join(pardon)
        placeholders = ", ".join("?" for _ in pardon)
        await db.execute(
            f"INSERT INTO attendance_pardons ({columns}) VALUES ({placeholders})",  # noqa: S608
            list(pardon.values()),
        )
