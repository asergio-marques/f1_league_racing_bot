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
from leaguebot.core.services.audit_service import record_change


async def test_record_change_writes_one_row_as_the_commands_did(tmp_path):
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


def test_record_change_is_always_handed_the_time():
    """Keyword-only and with no default, so no caller can leave it to read the clock."""
    now = inspect.signature(record_change).parameters["now"]
    assert now.kind is inspect.Parameter.KEYWORD_ONLY
    assert now.default is inspect.Parameter.empty


# ── Recording on a connection already open (#442) ─────────────────────────────


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


async def _audit_count(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM audit_entries")
        return (await cursor.fetchone())[0]


async def test_record_change_on_writes_on_the_connection_it_is_given(tmp_path):
    """A change and its record land together: the row is written on the caller's connection,
    inside the caller's transaction, and is not committed by the record — so it goes if the
    caller rolls back, and stays once the caller commits."""
    from leaguebot.core.services.audit_service import record_change_on

    db_path = str(tmp_path / "audit_on.db")
    await run_migrations(db_path)
    change = dict(
        actor_id=77,
        actor_name="Manager#0001",
        change_type="POINTS_CONFIG_SESSION_SET",
        old_value={"position": 1, "points": 20},
        new_value={"position": 1, "points": 25},
        now=NOW,
    )

    async with get_connection(db_path) as db:
        await record_change_on(db, **change)
        cursor = await db.execute("SELECT COUNT(*) FROM audit_entries")
        assert (await cursor.fetchone())[0] == 1, "the row is visible inside the transaction"
        await db.rollback()
    assert await _audit_count(db_path) == 0, "the record committed the caller's transaction"

    async with get_connection(db_path) as db:
        await record_change_on(db, **change)
        await db.commit()
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT actor_id, change_type, old_value, new_value, timestamp FROM audit_entries"
        )
        rows = [dict(r) for r in await cursor.fetchall()]
    assert rows == [
        {
            "actor_id": 77,
            "change_type": "POINTS_CONFIG_SESSION_SET",
            "old_value": json.dumps({"position": 1, "points": 20}),
            "new_value": json.dumps({"position": 1, "points": 25}),
            "timestamp": NOW.isoformat(),
        }
    ]


async def _writer_db(tmp_path) -> str:
    """A server with a points configuration, and a season in amendment mode."""
    from tests.support.saves import create_config, enable_amendment_mode

    db_path = str(tmp_path / "audit_writers.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 10, 20, 30)"
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 1)"
        )
        await db.execute(
            "INSERT INTO season_points_entries "
            "(season_id, config_name, session_type, position, points) "
            "VALUES (1, 'Standard', 'FEATURE_RACE', 1, 25)"
        )
        await db.commit()
    await create_config(db_path, "Standard")
    await enable_amendment_mode(db_path, 1)
    # A fault on the second audit row the writer adds, after its first has been written.
    already = await _audit_count(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "CREATE TRIGGER fail_the_second_record BEFORE INSERT ON audit_entries "
            f"WHEN (SELECT COUNT(*) FROM audit_entries) >= {already + 1} "
            "BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END"
        )
        await db.commit()
    return db_path


async def _written(db_path: str) -> dict[str, int]:
    async with get_connection(db_path) as db:
        counts = {}
        for table in (
            "audit_entries",
            "points_config_entries",
            "points_config_fl",
        ):
            cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")
            counts[table] = (await cursor.fetchone())[0]
        cursor = await db.execute(
            "SELECT COUNT(*) FROM season_modification_entries WHERE points != 25"
        )
        counts["staged changes"] = (await cursor.fetchone())[0]
    return counts


#: Why the save-as-one tests fail until the settings' writes on a connection exist.
UNBUILT_ON_FORMS = "#439: the points writers on a connection are not written yet"


@pytest.mark.xfail(strict=True, reason=UNBUILT_ON_FORMS)
@pytest.mark.parametrize("writer", ["bulk config", "xml import", "bulk amend"])
async def test_a_fault_after_the_audit_write_undoes_both_the_entries_and_the_points(
    tmp_path, writer
):
    """The change and its record are one transaction. A bulk paste of two positions into
    "Standard", an XML import of two positions and a fastest lap, or a staged amendment of two
    positions is written on one connection, as a settings change's save writes it, its audit
    entries written after it on that connection; the second audit entry faults. Nothing is
    left: no points, and no record of points that were never saved."""
    import sqlite3

    from leaguebot.core.services.audit_service import record_change_on
    from leaguebot.results.models.points_config import SessionType

    db_path = await _writer_db(tmp_path)
    before = await _written(db_path)

    with pytest.raises(sqlite3.Error):
        async with get_connection(db_path) as db:
            if writer == "bulk config":
                from leaguebot.results.services.points_config_service import config_points_on

                saved = await config_points_on(
                    db, "Standard", SessionType.FEATURE_RACE, [(1, 25), (2, 18)]
                )
            elif writer == "xml import":
                from leaguebot.results.services.points_config_service import (
                    xml_import_config_on,
                )

                saved = await xml_import_config_on(
                    db,
                    "Standard",
                    {SessionType.FEATURE_RACE: {1: 25, 2: 18}},
                    {SessionType.FEATURE_RACE: (2, 10)},
                )
            else:
                from leaguebot.results.services.amendment_mode_service import stage_points_on

                saved = await stage_points_on(
                    db, 1, "Standard", "FEATURE_RACE", [(1, 30), (2, 18)]
                )
            for audit in saved.audits:
                await record_change_on(
                    db,
                    actor_id=77,
                    actor_name="Manager#0001",
                    change_type=audit.change_type,
                    old_value=audit.old_value,
                    new_value=audit.new_value,
                    now=NOW,
                    division_id=audit.division_id,
                )
            await db.commit()

    assert await _written(db_path) == before
