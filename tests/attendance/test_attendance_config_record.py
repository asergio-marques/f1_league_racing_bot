"""Every `/attendance config` setter records its change with its value (#439, defect 12).

The attendance settings stay off the change queue until attendance's design pass (#285), but the
record of what changed is already theirs: `AttendanceService.set_setting` saves the value, its
`ATTENDANCE_CONFIG_SET` entry and the command's log line in one transaction, and the line is
sent once that is committed, so a stop between them loses none of the three. This pins it
against the real service and a real `OutputRouter` on a database built by the migrations,
rather than closing defect 12 unproved.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.attendance.cogs.attendance_cog import AttendanceCog
from leaguebot.attendance.services.attendance_service import AttendanceService
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services import audit_service
from tests.attendance.test_attendance_config_commands import (
    CONFIG_SETTER_IDS,
    CONFIG_SETTERS,
    _interaction,
    _invoke,
)
from tests.support.change_queue import league_double, seed_server

#: What `OutputRouter` puts beneath every line it sends to the log channel.
SEPARATOR = "\n" + "―" * 36


async def _league(db_path: str) -> AttendanceCog:
    """The cog over a real attendance service and a real router writing the league's log
    channel, the module on with its default configuration and no season confirmed."""
    await run_migrations(db_path)
    await seed_server(db_path)
    async with get_connection(db_path) as db:
        await db.execute("INSERT INTO attendance_config (id, module_enabled) VALUES (1, 1)")
        await db.commit()
    bot = league_double(db_path)
    bot.attendance_service = AttendanceService(db_path)
    bot.module_service = MagicMock()
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    bot.season_service = MagicMock()
    bot.season_service.get_confirmed_season = AsyncMock(return_value=None)
    return AttendanceCog(bot)


async def _saved(db_path: str, column: str) -> tuple:
    """The setting's value, the audit entries and the log lines waiting to be sent."""
    async with get_connection(db_path) as db:
        held = (await (await db.execute(f"SELECT {column} FROM attendance_config")).fetchone())[0]
        audits = [
            tuple(row) for row in await (await db.execute(
                "SELECT actor_id, change_type, old_value, new_value FROM audit_entries"
            )).fetchall()
        ]
        waiting = [
            row[0] for row in
            await (await db.execute("SELECT content FROM pending_messages")).fetchall()
        ]
    return held, audits, waiting


@pytest.mark.parametrize("command,value,column,expected", CONFIG_SETTERS, ids=CONFIG_SETTER_IDS)
async def test_each_attendance_settings_command_records_its_audit_entry(
    tmp_path, command, value, column, expected
):
    """One `ATTENDANCE_CONFIG_SET` entry naming the member, from the default held to the value
    set, and one Success line."""
    db_path = str(tmp_path / "attendance.db")
    cog = await _league(db_path)
    async with get_connection(db_path) as db:
        cursor = await db.execute(f"SELECT {column} FROM attendance_config")
        default = (await cursor.fetchone())[0]
    interaction = _interaction()

    await _invoke(command, cog, interaction, value)

    held, rows, waiting = await _saved(db_path, column)
    assert [(r[0], r[1], json.loads(r[2]), json.loads(r[3])) for r in rows] == [
        (42, "ATTENDANCE_CONFIG_SET", {column: default}, {column: expected})
    ]
    assert held == expected
    lines = cog.bot.log_channel.sent
    assert len(lines) == 1 and " | Success\n  " in lines[0]
    assert waiting == []


@pytest.mark.parametrize("fault,value", [
    ("audit entry", 7), ("log line", 7), ("log line", 5),
], ids=["audit-entry", "log-line", "log-line-value-held"])
async def test_a_fault_in_the_save_leaves_neither_value_entry_nor_line(
    tmp_path, monkeypatch, fault, value
):
    """The value, its audit entry and its log line are one save: a write of the entry or of the
    line that fails leaves none of the three, and nothing is sent to the log channel. A value
    already held (5, the default) writes its "Nothing changed" line alone, and a failure to
    write it raises as well and commits nothing."""
    db_path = str(tmp_path / "attendance.db")
    cog = await _league(db_path)
    broken = AsyncMock(side_effect=RuntimeError(f"{fault} refused"))
    if fault == "audit entry":
        monkeypatch.setattr(audit_service, "record_change_on", broken)
    else:
        monkeypatch.setattr(cog.bot.output_router, "queue_log_on", broken)

    with pytest.raises(RuntimeError, match=f"{fault} refused"):
        await _invoke(AttendanceCog.config_rsvp_notice, cog, _interaction(), value)

    broken.assert_awaited_once()
    assert await _saved(db_path, "rsvp_notice_days") == (5, [], [])
    assert cog.bot.log_channel.sent == []


async def test_the_line_is_sent_once_its_save_is_made_with_the_text_it_always_had(tmp_path):
    """The line reaches the log channel after the value, its entry and the line are committed,
    worded as the command always worded it: the member, the command, the outcome, and the value
    with the one it replaced beneath. Once sent it waits no more."""
    db_path = str(tmp_path / "attendance.db")
    cog = await _league(db_path)
    channel = cog.bot.log_channel
    send = channel.send
    at_send: list[tuple] = []

    async def _send(content: str = "", **kwargs):
        at_send.append(await _saved(db_path, "rsvp_notice_days"))
        return await send(content, **kwargs)

    channel.send = _send

    await _invoke(AttendanceCog.config_rsvp_notice, cog, _interaction(), 7)

    line = (
        "Manager (`<@42>`) | /attendance config rsvp-notice | Success\n"
        "  RSVP notice: 7 day(s) (was 5 day(s))" + SEPARATOR
    )
    held, audits, waiting = at_send[0]
    assert (held, len(audits), waiting) == (7, 1, [line])
    assert channel.sent == [line]
    assert (await _saved(db_path, "rsvp_notice_days"))[2] == []


async def test_a_value_already_held_still_records_that_nothing_changed(tmp_path):
    """A value the setting already holds writes no value and no entry, but its "Nothing
    changed" line is saved and sent as before."""
    db_path = str(tmp_path / "attendance.db")
    cog = await _league(db_path)

    await _invoke(AttendanceCog.config_rsvp_notice, cog, _interaction(), 5)

    assert await _saved(db_path, "rsvp_notice_days") == (5, [], [])
    assert cog.bot.log_channel.sent == [
        "Manager (`<@42>`) | /attendance config rsvp-notice | Nothing changed\n"
        "  RSVP notice: 5 day(s)" + SEPARATOR
    ]
