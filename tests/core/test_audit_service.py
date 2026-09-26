"""Core's record of what changed, written for a module's command (#462).

`audit_entries` is core's table. A module's channel command sets a channel its own module reads,
and the change is recorded all the same — so the record is written by core's service rather
than by the module's cog, which would put database code in a cog and have a module write a
table it does not own.

**The row is the one the channel commands wrote themselves**: the same columns, the old and new
values as JSON, and the time as a UTC ISO timestamp. A reader querying the audit for a channel's
history must not have to know which command, or which release, wrote the row.

**The time is handed in** (`docs/design/architecture.md`, "The time is always passed in"), so
the test pins the moment it records rather than reading the wall clock.
"""
from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone

import pytest

from leaguebot.core.db.database import get_connection, run_migrations

_NO_AUDIT_SERVICE = pytest.mark.xfail(
    strict=True, reason="#462: core has no audit_service.record_change yet"
)


@_NO_AUDIT_SERVICE
async def test_record_change_writes_one_row_as_the_commands_did(tmp_path):
    from leaguebot.core.services.audit_service import record_change

    db_path = str(tmp_path / "audit.db")
    await run_migrations(db_path)
    now = datetime(2026, 9, 26, 18, 30, 5, tzinfo=timezone.utc)

    await record_change(
        db_path,
        actor_id=77,
        actor_name="Manager#0001",
        change_type="DIVISION_CHANNEL_SET",
        old_value={"channel_type": "weather", "channel_id": None},
        new_value={"channel_type": "weather", "channel_id": 770001},
        now=now,
        division_id=11,
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT actor_id, actor_name, division_id, change_type, old_value, new_value, "
            "timestamp FROM audit_entries"
        )
        rows = [dict(row) for row in await cursor.fetchall()]
    assert rows == [
        {
            "actor_id": 77,
            "actor_name": "Manager#0001",
            "division_id": 11,
            "change_type": "DIVISION_CHANNEL_SET",
            "old_value": json.dumps({"channel_type": "weather", "channel_id": None}),
            "new_value": json.dumps({"channel_type": "weather", "channel_id": 770001}),
            "timestamp": "2026-09-26T18:30:05+00:00",
        }
    ]


@_NO_AUDIT_SERVICE
def test_record_change_is_always_handed_the_time():
    """Keyword-only and with no default, so no caller can leave it to read the clock."""
    from leaguebot.core.services.audit_service import record_change

    now = inspect.signature(record_change).parameters["now"]
    assert now.kind is inspect.Parameter.KEYWORD_ONLY
    assert now.default is inspect.Parameter.empty
