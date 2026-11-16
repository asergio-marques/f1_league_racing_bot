"""The record of what changed: one `audit_entries` row per change a member makes.

`audit_entries` is the framework's table, and a module's command records its changes in it all
the same — a module's channel command sets a channel only its own module reads, and the change is
still one a league asks about afterwards. So the row is written here, by the framework's service,
rather than by the module's cog: a cog holds no database code, and a module writes only the tables it
owns (`docs/design/architecture.md`, "The database").

**The row** has the same columns for every command, the old and new values as JSON with their keys
sorted, and the time as a UTC ISO timestamp. Both writers encode so, a change nobody ran included,
so a reader querying the audit for a channel's history must not have to know which command wrote
the row. A value is handed as it stands, whatever JSON can encode (a dict, a word, an id, a
list, None); one it cannot, such as a moment, is handed as the text it is to be read as.

**The time is handed in** (architecture.md, "The time is always passed in"), keyword-only and
with no default, so no caller can leave it to read the clock.

**Every row this service writes is written on the connection of the change it records**, by
`record_change_on`: the row joins the caller's transaction and is not committed here, so a fault
before the caller commits takes the change and its record back together. The change queue's save
writes each change's entries through it, and so do the points writers — a bulk paste, an XML
import and a bulk amendment — for each position they change.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite


async def record_change_on(
    db: aiosqlite.Connection,
    *,
    actor_id: int,
    actor_name: str,
    change_type: str,
    old_value: Any,
    new_value: Any,
    now: datetime,
    division_id: int | None = None,
) -> None:
    """Write the row of a member's change, on *db*, inside the caller's transaction.

    *old_value* and *new_value* are any value JSON can encode, written as JSON with sorted keys.
    *actor_name* is the member as Discord renders them (``str(member)``), kept beside the id so
    the record still reads once they have left the server. *division_id* is the division the
    change belongs to, None for a change to the server as a whole. *now* is when it was made, and
    must be aware. Nothing is committed here: the caller commits the change and its record
    together, or rolls both back (see the module docstring).
    """
    await db.execute(
        "INSERT INTO audit_entries "
        "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            actor_id,
            actor_name,
            division_id,
            change_type,
            json.dumps(old_value, sort_keys=True),
            json.dumps(new_value, sort_keys=True),
            now.isoformat(),
        ),
    )


async def record_system_change_on(
    db: aiosqlite.Connection, *, change_type: str, old_value: Any, new_value: Any, now: datetime
) -> None:
    """Write the row of a change nobody ran, on *db*, inside the caller's transaction.

    The actor is the system (id 0), and the old and new values are any value JSON can encode,
    written as JSON with sorted keys, as `record_change_on` writes them. Nothing is committed here.
    """
    await db.execute(
        "INSERT INTO audit_entries "
        "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
        "VALUES (?, ?, NULL, ?, ?, ?, ?)",
        (
            0,
            "system",
            change_type,
            json.dumps(old_value, sort_keys=True),
            json.dumps(new_value, sort_keys=True),
            now.isoformat(),
        ),
    )
