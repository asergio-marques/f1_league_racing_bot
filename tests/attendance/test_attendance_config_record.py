"""Every `/attendance config` setter records its audit entry with its value (#439, defect 12).

The attendance settings stay off the change queue until attendance's design pass (#285), but the
record of what changed is already theirs: `AttendanceService.set_setting` saves the value and its
`ATTENDANCE_CONFIG_SET` entry in one transaction. This pins it against the real service on a
database built by the migrations, rather than closing defect 12 unproved.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.attendance.cogs.attendance_cog import AttendanceCog
from leaguebot.attendance.services.attendance_service import AttendanceService
from leaguebot.core.db.database import get_connection, run_migrations
from tests.attendance.test_attendance_config_commands import (
    CONFIG_SETTER_IDS,
    CONFIG_SETTERS,
    _interaction,
    _invoke,
)


async def _league(db_path: str) -> AttendanceCog:
    """The cog over a real attendance service, the module on with its default configuration and
    no season confirmed."""
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute("INSERT INTO attendance_config (id, module_enabled) VALUES (1, 1)")
        await db.commit()
    bot = MagicMock()
    bot.db_path = db_path
    bot.attendance_service = AttendanceService(db_path)
    bot.module_service = MagicMock()
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    bot.season_service = MagicMock()
    bot.season_service.get_confirmed_season = AsyncMock(return_value=None)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)
    return AttendanceCog(bot)


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

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT actor_id, change_type, old_value, new_value FROM audit_entries"
        )
        rows = [tuple(row) for row in await cursor.fetchall()]
        cursor = await db.execute(f"SELECT {column} FROM attendance_config")
        held = (await cursor.fetchone())[0]
    assert [(r[0], r[1], json.loads(r[2]), json.loads(r[3])) for r in rows] == [
        (42, "ATTENDANCE_CONFIG_SET", {column: default}, {column: expected})
    ]
    assert held == expected
    lines = [str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list]
    assert len(lines) == 1 and " | Success\n  " in lines[0]
