"""`AttendanceService`'s configuration surface, and the amended-round recalculation.

Issue #208. `tests/attendance/test_attendance_config_commands.py` covers the *commands* a league
types; nothing covered the service beneath them. The eight settings' writes, the
`get_or_create_config` default row and the division channel upserts were all unexecuted, so
the SQL that actually persists a league's settings had no cover at all.

Two things are pinned here that a double would not catch, which is why every test runs
against a real migrated database:

**Each setting writes its own column and no other.** One method, `set_setting`, writes all
eight by the column it is given (#482), which is exactly the shape a column-name error
survives in — a write landing in the neighbouring column would still pass any test that only
read back the value it just wrote. `test_each_setter_touches_only_its_own_column` reads the
whole row back after each write.

**A change and its audit entry are one save** (#482; core specification, "The record of what
changed"). `set_setting` reads the value held, writes nothing where it already holds the value
given, and otherwise writes the column and its `ATTENDANCE_CONFIG_SET` entry together, so a
failure between them leaves neither.

**The division channel setters are upserts.** `ON CONFLICT DO UPDATE` must update the one
column named and leave the other as it was, and a second call must not insert a second row
for the division — a league re-pointing its RSVP channel would otherwise lose its attendance
channel, or end up with two rows and a non-deterministic read.

The recalculation tests cover FR-028–FR-031 (`recalculate_attendance_for_round`), the path an
amendment takes. Its ordering is the substance: the upgrade-only rule is deliberately *not*
applied, pardons must survive, and the forward propagation must reach every subsequent
finalised round rather than stopping at the next one.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.attendance.services import attendance_service
from leaguebot.attendance.services.attendance_service import (
    AttendanceService,
    recalculate_attendance_for_round,
)

SERVER_ID = 8308
SEASON_ID = 1
DIVISION_ID = 1

#: The moment a change is made, pinned.
NOW = datetime(2026, 3, 14, 18, 30, tzinfo=timezone.utc)

#: Every server-scoped setting, as the column `set_setting` writes, with a value to write.
#: Driving the tests from one table is what makes "and no other column" cheap to assert for
#: all of them.
SETTERS: list[tuple[str, int]] = [
    ("rsvp_notice_days", 9),
    ("rsvp_last_notice_hours", 7),
    ("rsvp_deadline_hours", 5),
    ("no_rsvp_penalty", 11),
    ("absent_penalty", 13),
    ("no_show_penalty", 17),
    ("autosack_threshold", 19),
    ("autoreserve_threshold", 23),
]


async def _set(service: AttendanceService, column: str, value):
    """`set_setting` as a league manager's command calls it."""
    return await service.set_setting(
        column, value, actor_id=42, actor_name="Manager", now=NOW
    )


async def _audit_entries(db_path: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT * FROM audit_entries ORDER BY id")
        return [dict(row) for row in await cursor.fetchall()]


async def _make_db(tmp_path) -> str:
    """A migrated DB with one server, season and division, and no attendance config."""
    db_path = os.path.join(str(tmp_path), "attendance_config.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 100, 200, 300)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 1', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.commit()
    return db_path


async def _row(db_path: str) -> dict:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT * FROM attendance_config"
        )
        row = await cursor.fetchone()
    return dict(row) if row else {}


# ---------------------------------------------------------------------------
# get_config / get_or_create_config
# ---------------------------------------------------------------------------


async def test_a_server_that_never_configured_attendance_has_no_config(tmp_path):
    service = AttendanceService(await _make_db(tmp_path))
    assert await service.get_config() is None


async def test_get_or_create_writes_the_packaged_defaults(tmp_path):
    """The first `/attendance` command a league runs lands here. It must leave a usable row
    rather than a row of nulls, because every setter after it is an UPDATE and would write
    into nothing."""
    service = AttendanceService(await _make_db(tmp_path))

    config = await service.get_or_create_config()

    assert config is not None
    assert await service.get_config() is not None


async def test_get_or_create_is_idempotent(tmp_path):
    """Called on every config command. A second row for the same server would make which
    settings the league gets depend on row order."""
    db_path = await _make_db(tmp_path)
    service = AttendanceService(db_path)

    await service.get_or_create_config()
    await _set(service, "no_rsvp_penalty", 42)
    await service.get_or_create_config()

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM attendance_config"
        )
        assert (await cursor.fetchone())["n"] == 1
    # The second call returned the existing row rather than resetting it.
    assert (await _row(db_path))["no_rsvp_penalty"] == 42


# ---------------------------------------------------------------------------
# The setters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("column,value", SETTERS, ids=[c for c, _v in SETTERS])
async def test_each_setter_persists_its_value(tmp_path, column, value):
    db_path = await _make_db(tmp_path)
    service = AttendanceService(db_path)
    await service.get_or_create_config()

    await _set(service, column, value)

    assert (await _row(db_path))[column] == value


@pytest.mark.parametrize("column,value", SETTERS, ids=[c for c, _v in SETTERS])
async def test_each_setter_touches_only_its_own_column(tmp_path, column, value):
    """One method writing eight columns by name: a write landing in the wrong column is
    invisible to a test that reads back only what it wrote."""
    db_path = await _make_db(tmp_path)
    service = AttendanceService(db_path)
    await service.get_or_create_config()
    before = await _row(db_path)

    await _set(service, column, value)
    after = await _row(db_path)

    changed = {k for k in after if before.get(k) != after.get(k)}
    assert changed == {column}


@pytest.mark.parametrize("column", ["autosack_threshold", "autoreserve_threshold"])
async def test_a_threshold_can_be_cleared_back_to_null(tmp_path, column):
    """Switching a sanction off again is a real thing a league does, and the two thresholds
    are the only settings that can be unset. The audit entry gives the value cleared as
    null."""
    db_path = await _make_db(tmp_path)
    service = AttendanceService(db_path)
    await service.get_or_create_config()
    await _set(service, column, 25)

    result = await _set(service, column, None)

    assert (await _row(db_path))[column] is None
    assert result.changed is True
    assert result.old == 25
    entry = (await _audit_entries(db_path))[-1]
    assert json.loads(entry["old_value"]) == {column: 25}
    assert json.loads(entry["new_value"]) == {column: None}


async def test_a_change_is_written_with_its_audit_entry(tmp_path):
    """A3 in the service: the value, and one ATTENDANCE_CONFIG_SET entry saying who changed
    which setting from what to what, and when, for the server as a whole."""
    db_path = await _make_db(tmp_path)
    service = AttendanceService(db_path)
    held = (await service.get_or_create_config()).no_show_penalty

    result = await _set(service, "no_show_penalty", held + 3)

    assert result.changed is True
    assert result.old == held
    assert (await _row(db_path))["no_show_penalty"] == held + 3
    entries = await _audit_entries(db_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["change_type"] == "ATTENDANCE_CONFIG_SET"
    assert entry["actor_id"] == 42
    assert entry["actor_name"] == "Manager"
    assert entry["division_id"] is None
    assert json.loads(entry["old_value"]) == {"no_show_penalty": held}
    assert json.loads(entry["new_value"]) == {"no_show_penalty": held + 3}
    assert entry["timestamp"] == NOW.isoformat()


@pytest.mark.parametrize(
    "column,start,value",
    [("no_rsvp_penalty", 4, 4), ("autosack_threshold", None, None)],
    ids=["a-value", "a-disabled-threshold"],
)
async def test_a_value_already_held_writes_nothing_and_no_audit_entry(
    tmp_path, column, start, value
):
    """A4 in the service: the setting already holds the value, so nothing is written, no
    entry is recorded, and the caller is told the setting was unchanged."""
    db_path = await _make_db(tmp_path)
    service = AttendanceService(db_path)
    await service.get_or_create_config()
    async with get_connection(db_path) as db:
        await db.execute(f"UPDATE attendance_config SET {column} = ?", (start,))
        await db.commit()
    before = await _row(db_path)

    result = await _set(service, column, value)

    assert result.changed is False
    assert result.old == start
    assert await _row(db_path) == before
    assert await _audit_entries(db_path) == []


@pytest.mark.parametrize(
    "column", ["module_enabled", "id", "no_show_penalty = 0; --"], ids=["module", "id", "sql"]
)
async def test_a_column_that_is_not_a_setting_is_refused(tmp_path, column):
    """Only the eight settings' columns reach the SQL: any other name is refused before
    anything is read or written."""
    db_path = await _make_db(tmp_path)
    service = AttendanceService(db_path)
    await service.get_or_create_config()
    before = await _row(db_path)

    with pytest.raises(ValueError):
        await _set(service, column, 1)

    assert await _row(db_path) == before
    assert await _audit_entries(db_path) == []


async def test_a_change_whose_audit_entry_fails_leaves_the_setting_as_it_was(tmp_path):
    """One save: where the audit entry cannot be written, the value is not written either."""
    db_path = await _make_db(tmp_path)
    service = AttendanceService(db_path)
    held = (await service.get_or_create_config()).absent_penalty
    async with get_connection(db_path) as db:
        await db.execute(
            "CREATE TRIGGER refuse_audit BEFORE INSERT ON audit_entries "
            "BEGIN SELECT RAISE(ABORT, 'audit refused'); END"
        )
        await db.commit()

    with pytest.raises(sqlite3.DatabaseError):
        await _set(service, "absent_penalty", held + 1)

    assert (await _row(db_path))["absent_penalty"] == held


# ---------------------------------------------------------------------------
# Division channels — the upserts
# ---------------------------------------------------------------------------


async def test_the_rsvp_channel_can_be_set_and_read_back(tmp_path):
    db_path = await _make_db(tmp_path)
    service = AttendanceService(db_path)

    await service.set_rsvp_channel(DIVISION_ID, 777001)

    config = await service.get_division_config(DIVISION_ID)
    assert config is not None
    assert str(config.rsvp_channel_id) == "777001"


async def test_repointing_the_rsvp_channel_updates_rather_than_duplicates(tmp_path):
    db_path = await _make_db(tmp_path)
    service = AttendanceService(db_path)

    await service.set_rsvp_channel(DIVISION_ID, 777001)
    await service.set_rsvp_channel(DIVISION_ID, 777002)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM attendance_division_config WHERE division_id = ?",
            (DIVISION_ID,),
        )
        assert (await cursor.fetchone())["n"] == 1
    config = await service.get_division_config(DIVISION_ID)
    assert str(config.rsvp_channel_id) == "777002"


async def test_setting_one_division_channel_leaves_the_other_alone(tmp_path):
    """The two upserts share a row. `DO UPDATE SET` naming both columns would blank whichever
    the caller did not pass — a league setting its attendance channel would lose check-in."""
    db_path = await _make_db(tmp_path)
    service = AttendanceService(db_path)

    await service.set_rsvp_channel(DIVISION_ID, 777001)
    await service.set_attendance_channel(DIVISION_ID, 888001)

    config = await service.get_division_config(DIVISION_ID)
    assert str(config.rsvp_channel_id) == "777001"
    assert str(config.attendance_channel_id) == "888001"


async def test_a_division_with_no_config_reads_as_none(tmp_path):
    service = AttendanceService(await _make_db(tmp_path))
    assert await service.get_division_config(DIVISION_ID) is None


async def test_deleting_a_server_s_division_configs_clears_them(tmp_path):
    """Run when a season ends. A stale row would point the next season's division at the
    previous one's channel."""
    db_path = await _make_db(tmp_path)
    service = AttendanceService(db_path)
    await service.set_rsvp_channel(DIVISION_ID, 777001)

    await service.delete_division_configs()

    assert await service.get_division_config(DIVISION_ID) is None


# ---------------------------------------------------------------------------
# FR-028–FR-031 — recalculating an amended round
# ---------------------------------------------------------------------------


async def _make_rounds_db(tmp_path, statuses: list[str]) -> str:
    """A division whose rounds carry *statuses*, numbered from 1 in order."""
    db_path = await _make_db(tmp_path)
    scheduled = datetime.now(timezone.utc) - timedelta(days=30)
    async with get_connection(db_path) as db:
        for index, status in enumerate(statuses, start=1):
            await db.execute(
                "INSERT INTO rounds "
                "(id, division_id, round_number, format, track_name, scheduled_at, status) "
                "VALUES (?, ?, ?, 'NORMAL', 'Silverstone Circuit', ?, ?)",
                (
                    index,
                    DIVISION_ID,
                    index,
                    (scheduled + timedelta(days=index)).isoformat(),
                    status,
                ),
            )
        await db.commit()
    return db_path


@pytest.fixture
def pipeline():
    """Stub the four pipeline steps, so the ordering and the arguments are what is tested."""
    with patch.object(
        attendance_service,
        "record_attendance_from_results_full_recompute",
        new=AsyncMock(return_value=None),
    ) as recompute, patch.object(
        attendance_service, "distribute_attendance_points", new=AsyncMock(return_value=None)
    ) as distribute, patch.object(
        attendance_service, "post_attendance_sheet", new=AsyncMock(return_value=None)
    ) as sheet, patch.object(
        attendance_service, "enforce_attendance_sanctions", new=AsyncMock(return_value=None)
    ) as sanctions:
        yield recompute, distribute, sheet, sanctions


async def _recalculate(db_path: str, round_id: int = 1) -> None:
    await recalculate_attendance_for_round(
        bot=MagicMock(),
        guild=MagicMock(),
        db_path=db_path,
        round_id=round_id,
        division_id=DIVISION_ID,
        season_id=SEASON_ID,
    )


async def test_an_amended_round_is_fully_recomputed_not_upgraded(tmp_path, pipeline):
    """FR-028. The upgrade-only rule does not apply to a deliberate correction, so this
    calls the *full recompute* — which may flip `attended` in either direction. Calling the
    ordinary recorder here would silently refuse to take an attendance away again."""
    recompute, _, _, _ = pipeline
    db_path = await _make_rounds_db(tmp_path, ["FINAL"])

    await _recalculate(db_path)

    # The positional arguments are the subject; the connection it is handed is not.
    # Every step of the recalculation now shares one transaction so the propagation
    # lands whole or not at all (#187), and pinning the connection object here would
    # tie this test to that mechanism rather than to the rule it is about.
    recompute.assert_awaited_once()
    assert recompute.await_args.args == (db_path, 1, DIVISION_ID)


async def test_the_sheet_and_the_sanctions_are_both_re_run(tmp_path, pipeline):
    """FR-031. An amendment that changes who attended changes who is over a threshold."""
    _, _, sheet, sanctions = pipeline
    db_path = await _make_rounds_db(tmp_path, ["FINAL"])

    await _recalculate(db_path)

    sheet.assert_awaited_once()
    sanctions.assert_awaited_once()


async def test_points_are_propagated_through_every_subsequent_finalised_round(
    tmp_path, pipeline
):
    """FR-030. `total_points_after` is a running total, so amending round 1 changes the
    total carried by rounds 2 and 3 alike. Stopping at the next round would leave the
    later ones reporting a total nobody can reproduce."""
    _, distribute, _, _ = pipeline
    db_path = await _make_rounds_db(tmp_path, ["FINAL", "FINAL", "AWAITING_APPEAL_VERDICTS"])

    await _recalculate(db_path, round_id=1)

    distributed = [call.args[1] for call in distribute.await_args_list]
    assert distributed == [1, 2, 3]


async def test_rounds_not_yet_finalised_are_not_propagated_into(tmp_path, pipeline):
    """A round still awaiting its results has no total to correct, and running the
    distribution over it would write one before the results exist."""
    _, distribute, _, _ = pipeline
    db_path = await _make_rounds_db(tmp_path, ["FINAL", "NOT_RUN", "AWAITING_RESULTS"])

    await _recalculate(db_path, round_id=1)

    assert [call.args[1] for call in distribute.await_args_list] == [1]


async def test_earlier_rounds_are_left_alone(tmp_path, pipeline):
    """Amending round 2 cannot change round 1's running total — only the rounds after it."""
    _, distribute, _, _ = pipeline
    db_path = await _make_rounds_db(tmp_path, ["FINAL", "FINAL", "FINAL"])

    await _recalculate(db_path, round_id=2)

    assert [call.args[1] for call in distribute.await_args_list] == [2, 3]
