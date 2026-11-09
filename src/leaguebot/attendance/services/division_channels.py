"""The channels a division's check-in call and attendance sheet are posted in.

`/attendance channel rsvp` and `/attendance channel attendance` set them, each in its own column of
`attendance_division_config`, which the attendance module owns
(`attendance/services/channel_settings.py` holds the settings that call these). Two separate
upserts naming one column each, so that setting one never blanks its neighbour.

**Each writes on the connection it is handed and never commits**: the setting's save owns the
transaction, so the channel and its audit entry stand or fall together. Each gives back the channel
it replaced, and **writes nothing where the channel is already the one held**, so that a save can
tell that nothing changed. Both columns are TEXT; each stores the id as text and gives its previous
value back as an integer.
"""

from __future__ import annotations

import aiosqlite


async def set_rsvp_channel(
    db: aiosqlite.Connection, division_id: int, channel_id: int | None
) -> int | None:
    """Upsert attendance_division_config.rsvp_channel_id. Returns the previous value."""
    cursor = await db.execute(
        "SELECT rsvp_channel_id FROM attendance_division_config WHERE division_id = ?",
        (division_id,),
    )
    row = await cursor.fetchone()
    old_id = None if row is None or row[0] is None else int(row[0])
    if old_id != channel_id:
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, rsvp_channel_id) "
            "VALUES (?, ?) "
            "ON CONFLICT(division_id) DO UPDATE SET rsvp_channel_id = excluded.rsvp_channel_id",
            (division_id, None if channel_id is None else str(channel_id)),
        )
    return old_id


async def set_attendance_channel(
    db: aiosqlite.Connection, division_id: int, channel_id: int | None
) -> int | None:
    """Upsert attendance_division_config.attendance_channel_id. Returns the previous value."""
    cursor = await db.execute(
        "SELECT attendance_channel_id FROM attendance_division_config WHERE division_id = ?",
        (division_id,),
    )
    row = await cursor.fetchone()
    old_id = None if row is None or row[0] is None else int(row[0])
    if old_id != channel_id:
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, attendance_channel_id) "
            "VALUES (?, ?) "
            "ON CONFLICT(division_id) DO UPDATE SET "
            "attendance_channel_id = excluded.attendance_channel_id",
            (division_id, None if channel_id is None else str(channel_id)),
        )
    return old_id
