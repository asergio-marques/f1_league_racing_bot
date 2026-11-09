"""A season keeps its signup windows and the signup configuration it ran under (issue #220).

A signup is kept permanently under its season and window, so what it answered is kept beside
it: each window's tracks and close time, and the season's time slots and questions as its
configuration was confirmed with. Both go with the season when it is deleted.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.signup.services.signup_module_service import SignupModuleService

SERVER_ID = 22040

#: The window is opened and closed only inside a save the caller holds: the `/signup open` job's
#: record and the close's (#439, slice 6b, C66, C68); the committing forms go (C72).


async def _open(svc, db_path, button_message_id, selected_tracks):
    """Open the window in a save of the test's own, as the job that posts the information does."""
    async with get_connection(db_path) as db:
        await svc.set_window_open_on(db, button_message_id, selected_tracks)
        await db.commit()


async def _close(svc, db_path):
    """Close the window in a save of the test's own, as the close's job does."""
    async with get_connection(db_path) as db:
        await svc.set_window_closed_on(db)
        await db.commit()


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

    await _open(svc, db_path, 111, ["3", "12"])

    windows = await svc.get_windows(5)
    assert len(windows) == 1
    assert windows[0]["selected_tracks"] == ["3", "12"]
    assert windows[0]["closed_at"] is None


async def test_the_close_time_is_kept_with_the_window(db_path):
    svc = SignupModuleService(db_path)
    await _open(svc, db_path, 111, [])

    await svc.set_close_at("2026-10-01T20:00:00+00:00")
    await _close(svc, db_path)

    window = (await svc.get_windows(5))[0]
    assert window["close_at"] == "2026-10-01T20:00:00+00:00"
    assert window["closed_at"] is not None


async def test_a_second_window_is_a_second_record(db_path):
    svc = SignupModuleService(db_path)
    await _open(svc, db_path, 111, ["1"])
    await _close(svc, db_path)

    await _open(svc, db_path, 222, ["2"])

    windows = await svc.get_windows(5)
    assert [w["selected_tracks"] for w in windows] == [["1"], ["2"]]
    assert windows[0]["closed_at"] is not None
    assert windows[1]["closed_at"] is None


_SUBSCRIBER = "#283: signup's settings are not yet kept by a subscriber to the configuration's save"
#: The queue's clock as the season's configuration is confirmed.
_CONFIRMED_AT = datetime(2026, 3, 1, 12, 30, tzinfo=timezone.utc)


async def _confirmed(db_path, season_id: int, now: datetime = _CONFIRMED_AT) -> None:
    """Season *season_id*'s configuration confirmed: signup's subscriber to the "configuration
    confirmed" hook called on the save's connection, which commits nothing of its own."""
    from leaguebot.signup.services.signup_module_service import configuration_confirmed_on

    async with get_connection(db_path) as db:
        await configuration_confirmed_on(db, season_id, now)
        assert db.in_transaction
        await db.commit()


@pytest.mark.xfail(strict=True, reason=_SUBSCRIBER)
async def test_the_season_config_snapshot_holds_settings_and_slots(db_path):
    """Signup asks for a short qualification time and no nationality, and offers one slot, Monday
    19:00. Season 5's configuration is confirmed: signup's subscriber, handed the save's
    connection, season 5 and the queue's clock, keeps those settings and that slot for season 5,
    stamped with the queue's clock, on the save's connection, committing nothing itself."""
    svc = SignupModuleService(db_path)
    await svc.add_slot(1, "19:00")
    settings = await svc.get_settings()
    settings.time_type = "SHORT_QUALIFICATION"
    settings.nationality_required = False
    await svc.save_settings(settings)

    await _confirmed(db_path, 5)

    config = await svc.get_season_config(5)
    assert config["time_type"] == "SHORT_QUALIFICATION"
    assert config["nationality_required"] is False
    assert config["slots"] == [{"slot_id": "Mon_19_00", "day_of_week": 1, "time_hhmm": "19:00"}]
    captured = datetime.fromisoformat(config["captured_at"]).replace(tzinfo=None)
    assert captured == _CONFIRMED_AT.replace(tzinfo=None)


@pytest.mark.xfail(strict=True, reason=_SUBSCRIBER)
async def test_the_snapshot_is_replaced_not_duplicated(db_path):
    """Season 5's configuration is confirmed with no slot offered; a Wednesday 20:00 slot is then
    added and it is confirmed again, a day on. Season 5 keeps one set of settings, the later:
    one slot, stamped with the later clock."""
    svc = SignupModuleService(db_path)
    await _confirmed(db_path, 5)
    await svc.add_slot(3, "20:00")
    later = datetime(2026, 3, 2, 12, 30, tzinfo=timezone.utc)

    await _confirmed(db_path, 5, later)

    config = await svc.get_season_config(5)
    assert len(config["slots"]) == 1
    assert datetime.fromisoformat(config["captured_at"]).replace(tzinfo=None) == (
        later.replace(tzinfo=None)
    )
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM season_signup_config WHERE season_id = 5")
        assert (await cursor.fetchone())[0] == 1
