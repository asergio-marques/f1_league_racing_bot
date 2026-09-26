"""The record of what changed: one `audit_entries` row per change a member makes.

`audit_entries` is core's table, and a module's command records its changes in it all the
same — a module's channel command sets a channel only its own module reads, and the change is
still one a league asks about afterwards. So the row is written here, by core's service, rather
than by the module's cog: a cog holds no database code, and a module writes only the tables it
owns (`docs/design/architecture.md`, "The database").

**The row is the one the channel commands wrote themselves** (#462): the same columns, the old
and new values as JSON, and the time as a UTC ISO timestamp. A reader querying the audit for a
channel's history must not have to know which command, or which release, wrote the row.

**The time is handed in** (architecture.md, "The time is always passed in"), keyword-only and
with no default, so no caller can leave it to read the clock.

**It saves in a transaction of its own**, after the change it records has been saved by the
service that makes it. Where a change and its record must land together, the command writes
both in one transaction of its own instead: `/division lineup-channel` and
`/division calendar-channel` do, and are not moved onto this, which would split their one save
into two.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from leaguebot.core.db.database import get_connection


async def record_change(
    db_path: str,
    *,
    actor_id: int,
    actor_name: str,
    change_type: str,
    old_value: dict[str, Any],
    new_value: dict[str, Any],
    now: datetime,
    division_id: int | None = None,
) -> None:
    """Record that *actor_id* made a change of *change_type*, from *old_value* to *new_value*.

    *actor_name* is the member as Discord renders them (``str(member)``), kept beside the id so
    the record still reads once they have left the server. *division_id* is the division the
    change belongs to, None for a change to the server as a whole. *now* is when it was made,
    and must be aware.
    """
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO audit_entries "
            "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                actor_id,
                actor_name,
                division_id,
                change_type,
                json.dumps(old_value),
                json.dumps(new_value),
                now.isoformat(),
            ),
        )
        await db.commit()
