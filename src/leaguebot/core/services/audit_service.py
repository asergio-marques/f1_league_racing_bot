"""The record of what changed: one `audit_entries` row per change a member makes.

`audit_entries` is core's table, and a module's command records its changes in it all the
same — a module's channel command sets a channel only its own module reads, and the change is
still one a league asks about afterwards. So the row is written here, by core's service, rather
than by the module's cog: a cog holds no database code, and a module writes only the tables it
owns (`docs/design/architecture.md`, "The database").

**The row** has the same columns for every command, the old and new values as JSON, and the time
as a UTC ISO timestamp. A reader querying the audit for a channel's history must not have to know
which command, or which release, wrote the row.

**The time is handed in** (architecture.md, "The time is always passed in"), keyword-only and
with no default, so no caller can leave it to read the clock.

**Every row is written on the connection of the change it records**, by `record_change_on`: the
row joins the caller's transaction and is not committed here, so a fault before the caller commits
takes the change and its record back together. The points writers — a bulk paste, an XML import
and a bulk amendment — record each position they change so. A change run on the change queue
records through the queue's own save instead.
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
    old_value: dict[str, Any],
    new_value: dict[str, Any],
    now: datetime,
    division_id: int | None = None,
) -> None:
    """Write the row of a member's change, on *db*, inside the caller's transaction.

    Nothing is committed here: the caller commits the change and its record together, or
    rolls both back (see the module docstring).
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
            json.dumps(old_value),
            json.dumps(new_value),
            now.isoformat(),
        ),
    )


async def record_system_change_on(
    db: "aiosqlite.Connection", *, change_type: str, old_value: str, new_value: str, now: datetime
) -> None:
    """Write the row of a change nobody ran, on *db*, inside the caller's transaction.

    The actor is the system (id 0), and the old and new values are the plain words the audit has
    always carried for such a change ("open", "closed"), not JSON. Nothing is committed here.
    """
    await db.execute(
        "INSERT INTO audit_entries "
        "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
        "VALUES (?, ?, NULL, ?, ?, ?, ?)",
        (0, "system", change_type, old_value, new_value, now.isoformat()),
    )
