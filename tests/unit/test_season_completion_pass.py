"""Completing a season returns its drivers to Not Signed Up and ends test mode (issue #220).

Before #220 completing a season changed no driver's state: every placed driver stayed Assigned
to an archived season, could never sign up again, and held test mode shut on the server for
good. Completion now runs the end-of-season pass after writing the season's history: the driver
pass, the signup window closed, test mode switched off.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.season_end_service import execute_season_end  # noqa: E402
from services.season_service import SeasonService  # noqa: E402
from services.signup_module_service import SignupModuleService  # noqa: E402
from services.test_mode_service import count_live_real_drivers  # noqa: E402

SERVER_ID = 22150


class _Scheduler:
    def cancel_season_end(self, server_id):
        pass


class _Router:
    def __init__(self):
        self.logged: list[str] = []

    async def post_log(self, server_id, content):
        self.logged.append(content)


def _bot(db_path):
    return SimpleNamespace(
        db_path=db_path,
        season_service=SeasonService(db_path),
        signup_module_service=SignupModuleService(db_path),
        scheduler_service=_Scheduler(),
        output_router=_Router(),
        get_guild=lambda guild_id: None,
    )


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "completion.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, test_mode_active) VALUES (?, 1, 2, 3, 1)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, start_date, status, season_number, stage) "
            "VALUES (1, ?, '2026-01-01', 'ACTIVE', 4, 'PENDING_COMPLETION')",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier, status) "
            "VALUES (1, 1, 'Pro', 1, 1, 'FINISHED')"
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, max_seats, is_reserve) "
            "VALUES (10, 1, 'Alpha', 4, 0)"
        )
        for pid, uid, former, test in ((1, "1001", 1, 0), (2, "1002", 0, 0),
                                        (3, "9000000000000000003", 0, 1)):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state, "
                "former_driver, is_test_driver) VALUES (?, ?, 'ASSIGNED', ?, ?)",
                (pid, uid, former, test),
            )
            cursor = await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                "VALUES (10, ?, ?)",
                (pid, pid),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments "
                "(driver_profile_id, season_id, division_id, team_seat_id, committed) "
                "VALUES (?, 1, 1, ?, 1)",
                (pid, cursor.lastrowid),
            )
        await db.commit()
    return path


async def _complete(db_path):
    bot = _bot(db_path)
    with patch("services.forecast_cleanup_service.flush_pending_deletions", new=AsyncMock()):
        await execute_season_end(SERVER_ID, 1, bot)
    return bot


async def test_completion_returns_drivers_to_not_signed_up(db_path):
    await _complete(db_path)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT id, current_state FROM driver_profiles ORDER BY id")
        rows = [tuple(r) for r in await cursor.fetchall()]
    # The former driver is kept at Not Signed Up; the one who never raced is deleted; the
    # test driver goes with test mode.
    assert rows == [(1, "NOT_SIGNED_UP")]


async def test_completion_leaves_no_live_driver_to_hold_test_mode_shut(db_path):
    """The defect behind `/test-mode toggle` refusing between seasons."""
    await _complete(db_path)

    assert await count_live_real_drivers(db_path) == 0


async def test_completion_switches_test_mode_off(db_path):
    await _complete(db_path)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT test_mode_active FROM server_configs WHERE server_id = ?", (SERVER_ID,)
        )
        assert (await cursor.fetchone())[0] == 0


async def test_the_season_is_archived_with_history_for_its_former_driver(db_path):
    await _complete(db_path)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT status, stage FROM seasons WHERE id = 1")
        assert tuple(await cursor.fetchone()) == ("COMPLETED", "COMPLETED")
        cursor = await db.execute(
            "SELECT discord_user_id, driver_profile_id FROM driver_history_entries ORDER BY id"
        )
        rows = [tuple(r) for r in await cursor.fetchall()]
    # The former driver keeps their entry; the test driver's is kept by identifier.
    assert rows == [("1001", 1), ("9000000000000000003", None)]


async def test_an_open_signup_window_is_closed(db_path):
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO signup_module_config (id, signups_open) VALUES (?, 1)",
            (1,),
        )
        await db.commit()

    with patch("cogs.module_cog.execute_forced_close", new=AsyncMock()) as closed:
        await _complete(db_path)

    closed.assert_awaited_once()


async def test_test_mode_that_cannot_be_switched_off_does_not_stop_completion(db_path):
    """Each step of the end-of-season pass stands apart from the next."""
    with patch(
        "services.test_mode_service.switch_test_mode_off",
        new=AsyncMock(side_effect=RuntimeError("disk full")),
    ):
        await _complete(db_path)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT status FROM seasons WHERE id = 1")
        assert (await cursor.fetchone())[0] == "COMPLETED"


async def test_a_window_that_cannot_be_closed_does_not_keep_test_mode_on(db_path):
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO signup_module_config (id, signups_open) VALUES (?, 1)",
            (1,),
        )
        await db.commit()

    with patch(
        "cogs.module_cog.execute_forced_close", new=AsyncMock(side_effect=RuntimeError("gone"))
    ):
        await _complete(db_path)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT test_mode_active FROM server_configs WHERE server_id = ?", (SERVER_ID,)
        )
        assert (await cursor.fetchone())[0] == 0


async def test_the_window_is_closed_before_the_driver_pass(db_path):
    """Closed first, so that nobody begins a signup the driver pass has already gone by."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO signup_module_config (id, signups_open) VALUES (?, 1)",
            (1,),
        )
        await db.commit()
    order: list[str] = []

    async def closing(*args, **kwargs):
        order.append("window")

    async def passing(*args, **kwargs):
        order.append("driver pass")
        return {"reset": 0, "deleted": 0}

    with patch("cogs.module_cog.execute_forced_close", new=closing), \
            patch("services.season_lifecycle_service.run_driver_pass", new=passing):
        await _complete(db_path)

    assert order == ["window", "driver pass"]


async def test_completing_deletes_the_saved_test_mode_backup(db_path):
    """Decided 2026-09-17: a season run to its end leaves a state nothing could restore."""
    from pathlib import Path

    from services import backup_service

    jobstore = Path(db_path).with_name("scheduler.db")
    jobstore.write_bytes(b"")
    backup_service.backup_path(db_path).write_bytes(b"saved")
    backup_service.backup_path(jobstore).write_bytes(b"saved jobs")
    backup_service.set_lock(db_path, who="Maintainer")

    await _complete(db_path)

    assert not backup_service.backup_path(db_path).exists()
    assert not backup_service.backup_path(jobstore).exists()
    assert not backup_service.lock_path(db_path).exists()
