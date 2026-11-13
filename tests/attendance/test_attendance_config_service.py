"""`AttendanceService`'s configuration surface, and the amended-round recalculation.

Issue #208. `tests/attendance/test_attendance_config_commands.py` covers the *commands* a league
types; nothing covered the service beneath them. The eight settings' writes, the
`get_or_create_config` default row and the division channel upserts were all unexecuted, so
the SQL that actually persists a league's settings had no cover at all.

Two things are pinned here that a double would not catch, which is why every test runs
against a real migrated database:

The eight settings are changes on the queue (#285): that each writes its own column and no
other, with its audit entry and its log line in one save, is pinned against the real queue in
`test_attendance_setting_changes.py`.

**The division channel setters are upserts.** `ON CONFLICT DO UPDATE` must update the one
column named and leave the other as it was, and a second call must not insert a second row
for the division — a league re-pointing its RSVP channel would otherwise lose its attendance
channel, or end up with two rows and a non-deterministic read. Each is called by its setting's
save on the save's own connection (#283) and never commits, the save owning the transaction; each
gives back the channel it replaced as an integer, and writes nothing where the channel is already
the one held. The tests call each on a connection of their own and commit, as the save's
transaction does once it ends.

The recalculation tests cover FR-028–FR-030 (`_recalculate_forward` with `recompute="round"`),
the recalculation a points approval saves with its points through the attendance hook (#439).
Its ordering is the substance: the upgrade-only rule is deliberately *not* applied, pardons
must survive, and the forward propagation must reach every subsequent finalised round rather
than stopping at the next one. The sheet and the sanctions that follow are jobs of the
approval's own, not part of the recalculation.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.attendance.services import attendance_config_service as attendance_service
from leaguebot.attendance.services.attendance_config_service import AttendanceService

SERVER_ID = 8308
SEASON_ID = 1
DIVISION_ID = 1


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
    assert config.module_enabled is False
    assert await service.get_config() is not None


@pytest.mark.xfail(
    strict=True, reason="#285: reading a round's window still creates the configuration row"
)
async def test_reading_a_round_s_window_writes_nothing(tmp_path):
    """A round's timing windows are read while a season is approved or a round amended, and a
    read writes nothing: with attendance reported on and no configuration row stored, the read
    leaves the table as it found it rather than creating the row."""
    db_path = await _make_db(tmp_path)
    modules = MagicMock()
    modules.is_attendance_enabled = AsyncMock(return_value=True)

    await attendance_service.round_window(AttendanceService(db_path), modules, amending=False)

    assert await _row(db_path) == {}


async def test_get_or_create_is_idempotent(tmp_path):
    """Called on every config command. A second row for the same server would make which
    settings the league gets depend on row order."""
    db_path = await _make_db(tmp_path)
    service = AttendanceService(db_path)

    await service.get_or_create_config()
    async with get_connection(db_path) as db:
        await db.execute("UPDATE attendance_config SET no_rsvp_penalty = 42")
        await db.commit()
    await service.get_or_create_config()

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM attendance_config"
        )
        assert (await cursor.fetchone())["n"] == 1
    # The second call returned the existing row rather than resetting it.
    assert (await _row(db_path))["no_rsvp_penalty"] == 42


# ---------------------------------------------------------------------------
# Division channels — the upserts
# ---------------------------------------------------------------------------


#: The two division channel setters, each by its name in `division_channels`, with the column it
#: writes.
CHANNEL_SETTERS = [
    ("set_rsvp_channel", "rsvp_channel_id"),
    ("set_attendance_channel", "attendance_channel_id"),
]


def _channel_setter(name: str):
    """The setter *name* of `attendance/services/division_channels.py`, imported as the test
    runs so that this file collects before the build writes it."""
    from leaguebot.attendance.services import division_channels

    return getattr(division_channels, name)


async def _set_channel(db_path: str, name: str, *values) -> list:
    """Call the setter *name* with each of *values* in turn for Division 1, on one connection,
    then commit; the values each call gave back."""
    setter = _channel_setter(name)
    async with get_connection(db_path) as db:
        replaced = [await setter(db, DIVISION_ID, value) for value in values]
        await db.commit()
    return replaced


async def test_the_rsvp_channel_can_be_set_and_read_back(tmp_path):
    """Division 1 has no channels set. Its RSVP channel is set to 777001: the division's config
    reads it back as the text "777001", its attendance channel still unset, and the setter gives
    back no channel replaced."""
    db_path = await _make_db(tmp_path)
    service = AttendanceService(db_path)

    assert await _set_channel(db_path, "set_rsvp_channel", 777001) == [None]

    config = await service.get_division_config(DIVISION_ID)
    assert config is not None
    assert config.rsvp_channel_id == "777001"
    assert config.attendance_channel_id is None


async def test_repointing_the_rsvp_channel_updates_rather_than_duplicates(tmp_path):
    """Division 1's RSVP channel is set to 777001, then moved to 777002: the division keeps one
    config row, reading 777002, and the move gives back 777001, as an integer, as the channel it
    replaced."""
    db_path = await _make_db(tmp_path)
    service = AttendanceService(db_path)

    replaced = await _set_channel(db_path, "set_rsvp_channel", 777001, 777002)

    assert replaced == [None, 777001]
    assert isinstance(replaced[1], int)
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
    the caller did not pass — a league setting its attendance channel would lose check-in.
    Division 1's RSVP channel is set to 777001, then its attendance channel to 888001: both
    read back."""
    db_path = await _make_db(tmp_path)
    service = AttendanceService(db_path)

    await _set_channel(db_path, "set_rsvp_channel", 777001)
    await _set_channel(db_path, "set_attendance_channel", 888001)

    config = await service.get_division_config(DIVISION_ID)
    assert config is not None
    assert config.rsvp_channel_id == "777001"
    assert config.attendance_channel_id == "888001"


@pytest.mark.parametrize("name,column", CHANNEL_SETTERS, ids=[c for _n, c in CHANNEL_SETTERS])
async def test_a_division_channel_setter_leaves_the_commit_to_its_save(tmp_path, name, column):
    """Division 1 has no channels set. Its RSVP (attendance) channel is set to 777001 on a
    connection whose transaction is then rolled back, as a save's is when the audit entry beside
    it fails: the setter committed nothing, and Division 1 still has no such channel."""
    setter = _channel_setter(name)
    db_path = await _make_db(tmp_path)

    async with get_connection(db_path) as db:
        await setter(db, DIVISION_ID, 777001)
        await db.rollback()

    config = await AttendanceService(db_path).get_division_config(DIVISION_ID)
    assert config is None or getattr(config, column) is None


@pytest.mark.parametrize("name,column", CHANNEL_SETTERS, ids=[c for _n, c in CHANNEL_SETTERS])
async def test_a_division_channel_already_held_is_left_unwritten(tmp_path, name, column):
    """Division 1's RSVP (attendance) channel is already 777001, and the setter is asked for
    777001 again: it writes nothing, no row being changed on the connection, and gives back
    777001, as an integer, as the channel it would have replaced, so its save can tell nothing
    changed."""
    db_path = await _make_db(tmp_path)
    await _set_channel(db_path, name, 777001)
    setter = _channel_setter(name)

    async with get_connection(db_path) as db:
        before = db.total_changes
        replaced = await setter(db, DIVISION_ID, 777001)
        changed = db.total_changes - before
        await db.commit()

    assert changed == 0
    assert replaced == 777001
    assert isinstance(replaced, int)


async def test_a_division_with_no_config_reads_as_none(tmp_path):
    service = AttendanceService(await _make_db(tmp_path))
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
    """Stub the two recalculation steps, so the ordering and the arguments are what is
    tested."""
    with patch.object(
        attendance_service,
        "record_attendance_from_results_full_recompute",
        new=AsyncMock(return_value=None),
    ) as recompute, patch.object(
        attendance_service, "distribute_attendance_points", new=AsyncMock(return_value=None)
    ) as distribute:
        yield recompute, distribute


async def _recalculate(db_path: str, round_id: int = 1) -> None:
    await attendance_service._recalculate_forward(
        db_path, round_id, DIVISION_ID, recompute="round",
    )


async def test_an_amended_round_is_fully_recomputed_not_upgraded(tmp_path, pipeline):
    """FR-028. The upgrade-only rule does not apply to a deliberate correction, so this
    calls the *full recompute* — which may flip `attended` in either direction. Calling the
    ordinary recorder here would silently refuse to take an attendance away again."""
    recompute, _ = pipeline
    db_path = await _make_rounds_db(tmp_path, ["FINAL"])

    await _recalculate(db_path)

    # The positional arguments are the subject. The recompute is always handed the
    # recalculation's own connection, saved with the rest of it (#187, #439), and commits
    # nothing itself.
    recompute.assert_awaited_once()
    assert recompute.await_args.args == (db_path, 1, DIVISION_ID)
    assert recompute.await_args.kwargs["db"] is not None


async def test_points_are_propagated_through_every_subsequent_finalised_round(
    tmp_path, pipeline
):
    """FR-030. `total_points_after` is a running total, so amending round 1 changes the
    total carried by rounds 2 and 3 alike. Stopping at the next round would leave the
    later ones reporting a total nobody can reproduce."""
    _, distribute = pipeline
    db_path = await _make_rounds_db(tmp_path, ["FINAL", "FINAL", "AWAITING_APPEAL_VERDICTS"])

    await _recalculate(db_path, round_id=1)

    distributed = [call.args[1] for call in distribute.await_args_list]
    assert distributed == [1, 2, 3]


async def test_rounds_not_yet_finalised_are_not_propagated_into(tmp_path, pipeline):
    """A round still awaiting its results has no total to correct, and running the
    distribution over it would write one before the results exist."""
    _, distribute = pipeline
    db_path = await _make_rounds_db(tmp_path, ["FINAL", "NOT_RUN", "AWAITING_RESULTS"])

    await _recalculate(db_path, round_id=1)

    assert [call.args[1] for call in distribute.await_args_list] == [1]


async def test_earlier_rounds_are_left_alone(tmp_path, pipeline):
    """Amending round 2 cannot change round 1's running total — only the rounds after it."""
    _, distribute = pipeline
    db_path = await _make_rounds_db(tmp_path, ["FINAL", "FINAL", "FINAL"])

    await _recalculate(db_path, round_id=2)

    assert [call.args[1] for call in distribute.await_args_list] == [2, 3]
