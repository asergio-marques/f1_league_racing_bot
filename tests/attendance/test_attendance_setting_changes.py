"""The eight `/attendance config` settings as changes on the queue: the rules a setting is judged by.

Each of the eight is a `Setting` on the change queue (#285), as the attendance channels are
(`test_attendance_channel_commands.py`): its check judges a request at the ask and again as the
change starts, on the database as the changes ahead of it left it. Three rules compare a setting
with something else, and the cog once judged them alone, at the ask, from a configuration read
before the save: the timing invariant (the first notice before the last, the last before the
deadline), auto-sack and auto-reserve never both on, and the three timings fixed once a season's
placements are confirmed. A change ahead on the queue, or a season confirmed meanwhile, could
break them unseen.

These tests ask each change with no interaction, so no command judges it: the change is asked
while the rule holds, the rule is broken while it waits, and the queue is run. The change is
refused as it starts, in the words the command always used, and nothing is saved: the setting
stands as it was and no audit entry is written. They run the bot the entry point builds, with the
real queue and "now" pinned.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.utils.log_lines import refusal_line
from tests.support.change_queue import (
    Clock,
    change_rows,
    league_bot,
    league_queue,
    member,
    run_queue,
    seed_server,
)
from tests.support.season_league import set_module_flag
from tests.support.settings_league import audit_rows, unruled

ACTOR_ID = 77
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

#: The manager asking, as a log line names them, the mention wrapped so it notifies nobody.
NAMED = f"Manager (`<@{ACTOR_ID}>`)"

#: Each setting's command, by the kind of change it asks of the queue. Each change carries its
#: value as `{"value": ...}`, a threshold's 0 carried as None, as the command stores it.
KINDS = {
    "rsvp-notice": "attendance.setting.rsvp_notice",
    "rsvp-last-notice": "attendance.setting.rsvp_last_notice",
    "rsvp-deadline": "attendance.setting.rsvp_deadline",
    "no-rsvp-penalty": "attendance.setting.no_rsvp_penalty",
    "absent-penalty": "attendance.setting.absent_penalty",
    "no-show-penalty": "attendance.setting.no_show_penalty",
    "autosack": "attendance.setting.autosack",
    "autoreserve": "attendance.setting.autoreserve",
}

#: Why each test fails until the eight settings are changes on the queue whose check holds the rule.
NOT_IN_THE_SERVICE = (
    "#285: the eight settings are not yet changes on the queue, and the rule is judged by the "
    "cog alone, at the ask"
)


async def _league(tmp_path, **config: Any):
    """The league's bot: attendance on, its configuration the packaged defaults (notice 5 days,
    last notice 24 hours, deadline 2 hours, no threshold) bar *config*, no season confirmed, and
    the real queue with "now" pinned."""
    db_path = os.path.join(str(tmp_path), "attendance_settings.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    await set_module_flag(db_path, "attendance", True)
    await _write(db_path, **config)
    bot = await league_bot(db_path)
    league_queue(bot, db_path, now=Clock(NOW))
    return bot, db_path


async def _write(db_path: str, **config: Any) -> None:
    """Set attendance's settings as a change ahead on the queue would leave them."""
    async with get_connection(db_path) as db:
        for column, value in config.items():
            await db.execute(f"UPDATE attendance_config SET {column} = ?", (value,))  # noqa: S608
        await db.commit()


async def _confirm_a_season(db_path: str) -> None:
    """Season 1, its placements confirmed: ACTIVE, racing (stage ONGOING)."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status, stage) "
            "VALUES (1, 1, '2026-01-01', 'ACTIVE', 'ONGOING')"
        )
        await db.commit()


async def _ask(bot, command: str, value: int | None) -> int | None:
    """Manager 77 asks `/attendance config <command>` for *value* through the queue, with no
    interaction: no command judges it."""
    return await bot.change_queue.ask(
        KINDS[command], {"value": value},
        actor=member(ACTOR_ID, "Manager", "Manager#0001"),
        what=f"`/attendance config {command}`",
    )


async def _setting(db_path: str, column: str) -> Any:
    async with get_connection(db_path) as db:
        cursor = await db.execute(f"SELECT {column} FROM attendance_config")  # noqa: S608
        row = await cursor.fetchone()
    return row[0]


async def _assert_refused(bot, db_path: str, command: str, reply: str, column: str,
                          held: Any) -> None:
    """The change ended refused, the refusal recorded in the command's words, the setting still
    *held*, and no audit entry written."""
    assert [row["state"] for row in await change_rows(db_path)] == ["REFUSED"]
    first_lines = [unruled(str(line)).split("\n")[0] for line in bot.log_channel.sent]
    assert refusal_line(NAMED, f"`/attendance config {command}`", reply.split(" ", 1)[1]) in (
        first_lines
    )
    assert await _setting(db_path, column) == held
    assert await audit_rows(db_path) == []


@pytest.mark.xfail(strict=True, reason=NOT_IN_THE_SERVICE)
async def test_set_setting_refuses_a_timing_that_breaks_the_invariant(tmp_path):
    """`/attendance config rsvp-deadline 12` is asked while the last notice stands at 24 hours,
    so the deadline falls before it; before it runs, the last notice is moved to 6 hours. It is
    refused as it starts, saying the last notice must be greater than the deadline, and the
    deadline stays at 2 hours."""
    bot, db_path = await _league(tmp_path)

    assert await _ask(bot, "rsvp-deadline", 12) is not None
    await _write(db_path, rsvp_last_notice_hours=6)
    await run_queue(bot)

    await _assert_refused(
        bot, db_path, "rsvp-deadline",
        "❌ `rsvp_last_notice_hours` (6h) must be greater than `rsvp_deadline_hours` (12h).",
        "rsvp_deadline_hours", 2,
    )


@pytest.mark.xfail(strict=True, reason=NOT_IN_THE_SERVICE)
async def test_set_setting_refuses_an_autosack_and_autoreserve_that_overlap(tmp_path):
    """`/attendance config autosack 3` is asked while auto-reserve is off; before it runs,
    auto-reserve is set to 2. It is refused as it starts, telling the manager to disable
    auto-reserve first, and auto-sack stays off."""
    bot, db_path = await _league(tmp_path)

    assert await _ask(bot, "autosack", 3) is not None
    await _write(db_path, autoreserve_threshold=2)
    await run_queue(bot)

    await _assert_refused(
        bot, db_path, "autosack",
        "❌ Cannot set auto-sack while auto-reserve is active. "
        "Disable auto-reserve first (`/attendance config autoreserve 0`).",
        "autosack_threshold", None,
    )


@pytest.mark.xfail(strict=True, reason=NOT_IN_THE_SERVICE)
async def test_set_setting_refuses_a_timing_change_once_a_season_is_confirmed(tmp_path):
    """`/attendance config rsvp-notice 7` is asked while no season is confirmed; before it runs,
    Season 1's placements are confirmed. It is refused as it starts, saying attendance's
    configuration cannot be changed once a season's placements are confirmed, and the notice
    stays at 5 days."""
    bot, db_path = await _league(tmp_path)

    assert await _ask(bot, "rsvp-notice", 7) is not None
    await _confirm_a_season(db_path)
    await run_queue(bot)

    await _assert_refused(
        bot, db_path, "rsvp-notice",
        "❌ Attendance configuration cannot be changed once a season's placements are confirmed.",
        "rsvp_notice_days", 5,
    )
