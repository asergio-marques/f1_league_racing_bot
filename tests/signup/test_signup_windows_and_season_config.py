"""A season keeps its signup windows and the signup configuration it ran under (issue #220).

A signup is kept permanently under its season and window, so what it answered is kept beside
it: each window's tracks and close time, and the season's time slots and questions as its
configuration was confirmed with. Both go with the season when it is deleted.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
from leaguebot.signup.services.signup_module_service import SignupModuleService  # noqa: E402

SERVER_ID = 22040


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "windows.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO signup_module_config (id, signups_open) VALUES (?, 0)",
            (1,),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number, stage) "
            "VALUES (5, '2026-09-17', 'SETUP', 1, 'WAITING')"
        )
        await db.commit()
    return path


async def test_opening_records_a_window_of_the_season(db_path):
    svc = SignupModuleService(db_path)

    await svc.set_window_open(111, ["3", "12"])

    windows = await svc.get_windows(5)
    assert len(windows) == 1
    assert windows[0]["selected_tracks"] == ["3", "12"]
    assert windows[0]["closed_at"] is None


async def test_the_close_time_is_kept_with_the_window(db_path):
    svc = SignupModuleService(db_path)
    await svc.set_window_open(111, [])

    await svc.set_close_at("2026-10-01T20:00:00+00:00")
    await svc.set_window_closed()

    window = (await svc.get_windows(5))[0]
    assert window["close_at"] == "2026-10-01T20:00:00+00:00"
    assert window["closed_at"] is not None


async def test_a_second_window_is_a_second_record(db_path):
    svc = SignupModuleService(db_path)
    await svc.set_window_open(111, ["1"])
    await svc.set_window_closed()

    await svc.set_window_open(222, ["2"])

    windows = await svc.get_windows(5)
    assert [w["selected_tracks"] for w in windows] == [["1"], ["2"]]
    assert windows[0]["closed_at"] is not None
    assert windows[1]["closed_at"] is None


async def test_the_season_config_snapshot_holds_settings_and_slots(db_path):
    svc = SignupModuleService(db_path)
    await svc.add_slot(1, "19:00")
    settings = await svc.get_settings()
    settings.time_type = "SHORT_QUALIFICATION"
    settings.nationality_required = False
    await svc.save_settings(settings)

    await svc.snapshot_season_config(5)

    config = await svc.get_season_config(5)
    assert config["time_type"] == "SHORT_QUALIFICATION"
    assert config["nationality_required"] is False
    assert config["slots"] == [{"slot_id": "Mon_19_00", "day_of_week": 1, "time_hhmm": "19:00"}]


async def test_the_snapshot_is_replaced_not_duplicated(db_path):
    svc = SignupModuleService(db_path)
    await svc.snapshot_season_config(5)
    await svc.add_slot(3, "20:00")

    await svc.snapshot_season_config(5)

    assert len((await svc.get_season_config(5))["slots"]) == 1


async def test_windows_and_config_go_with_their_season(db_path):
    svc = SignupModuleService(db_path)
    await svc.set_window_open(111, [])
    await svc.snapshot_season_config(5)

    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM seasons WHERE id = 5")
        await db.commit()

    assert await svc.get_windows(5) == []
    assert await svc.get_season_config(5) is None
