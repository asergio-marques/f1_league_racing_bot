"""Every completed signup is kept, under its season and the window it came through (#220).

A signup record was once keyed by server and Discord account, and a second signup overwrote the
first, so no season's signups survived the next. A record now belongs to the season and window
it was made in, and is never overwritten: a driver signing up twice holds two records, and each
season keeps its own.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.signup.models.signup_module import SignupRecord
from leaguebot.signup.services.signup_module_service import SignupModuleService
from tests.support.driver_transitions import transitioned

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

_NO_CHANNEL_COLUMN = "#286: SignupRecord still carries the signup_channel_id item 22 removes"


async def _save_record(svc, db_path, record):
    """Save *record* in a save of the test's own, as the submission's record does; gives its id."""
    async with get_connection(db_path) as db:
        record_id = await svc.save_record_on(db, record, now=NOW)
        await db.commit()
    return record_id


SERVER_ID = 22050
USER = "4242"


def _record(platform: str = "Steam") -> SignupRecord:
    return SignupRecord(
        id=-1,
        discord_user_id=USER,
        discord_username="racer",
        server_display_name="Racer",
        nationality="British",
        platform=platform,
        platform_id="racer_1",
        availability_slot_ids=["Mon_19_00"],
        driver_type="FULL_TIME",
        preferred_teams=[],
        preferred_teammate=None,
        lap_times={},
        notes=None,
    )


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "records.db")
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
        await db.commit()
    return path


async def _season(db_path, season_id: int, status: str = "SETUP", stage: str = "SIGNUPS"):
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number, stage) "
            "VALUES (?, '2026-09-17', ?, ?, ?)",
            (season_id, status, season_id, stage),
        )
        await db.commit()


async def _archive(db_path, season_id: int):
    async with get_connection(db_path) as db:
        await db.execute("UPDATE seasons SET status = 'COMPLETED' WHERE id = ?", (season_id,))
        await db.commit()


@pytest.mark.xfail(strict=True, reason=_NO_CHANNEL_COLUMN)
async def test_a_new_signup_is_kept_under_the_active_season_and_its_window(db_path):
    svc = SignupModuleService(db_path)
    await _season(db_path, 1)
    # The window is opened in a save of the caller's, as the `/signup open` job's record does it
    # (#439, slice 6b, C68).
    async with get_connection(db_path) as db:
        await svc.set_window_open_on(db, 111, ["3"], now=NOW)
        await db.commit()
    window_id = (await svc.get_windows(1))[0]["id"]

    await _save_record(svc, db_path, _record())

    stored = await svc.get_record(USER)
    assert stored.season_id == 1
    assert stored.window_id == window_id


@pytest.mark.xfail(strict=True, reason=_NO_CHANNEL_COLUMN)
async def test_signing_up_twice_in_a_season_keeps_both(db_path):
    svc = SignupModuleService(db_path)
    await _season(db_path, 1)
    await _save_record(svc, db_path, _record("Steam"))
    await _save_record(svc, db_path, _record("PSN"))

    records = await svc.get_records(1)

    assert [r.platform for r in records] == ["Steam", "PSN"]
    assert (await svc.get_record(USER)).platform == "PSN"


@pytest.mark.xfail(strict=True, reason=_NO_CHANNEL_COLUMN)
async def test_a_new_season_does_not_overwrite_the_last(db_path):
    svc = SignupModuleService(db_path)
    await _season(db_path, 1)
    await _save_record(svc, db_path, _record("Steam"))
    await _archive(db_path, 1)
    await _season(db_path, 2)

    await _save_record(svc, db_path, _record("Xbox"))

    assert [r.platform for r in await svc.get_records(1)] == ["Steam"]
    assert [r.platform for r in await svc.get_records(2)] == ["Xbox"]
    assert (await svc.get_record(USER, season_id=1)).platform == "Steam"
    assert (await svc.get_record(USER)).platform == "Xbox"


@pytest.mark.xfail(strict=True, reason=_NO_CHANNEL_COLUMN)
async def test_a_driver_leaving_keeps_their_signups_under_the_season(db_path):
    """Signups are keyed by the Discord account, and survive the driver whatever becomes of
    the profile — deleted at the season's end where they never raced."""
    from leaguebot.core.models.driver_profile import DriverState

    svc = SignupModuleService(db_path)
    await _season(db_path, 1)
    await _save_record(svc, db_path, _record())
    await transitioned(db_path, USER, DriverState.PENDING_SIGNUP_COMPLETION)

    await transitioned(db_path, USER, DriverState.NOT_SIGNED_UP)
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM driver_profiles")
        await db.commit()

    assert len(await svc.get_records(1)) == 1


@pytest.mark.xfail(
    strict=True, reason="#286: driver_signup_on cannot yet be asked for a season's signup"
)
async def test_a_driver_s_signup_for_a_season_is_the_one_kept_under_it(db_path):
    """Season 1 holds the driver's Steam signup and season 2, after it, their Xbox one. Asked for
    season 1, the one function that says which signup is the driver's gives the Steam signup; asked
    for no season, the latest season's; asked for a season they never signed up in, none."""
    from leaguebot.signup.services.signup_module_service import driver_signup_on

    svc = SignupModuleService(db_path)
    await _season(db_path, 1)
    await _save_record(svc, db_path, _record("Steam"))
    await _archive(db_path, 1)
    await _season(db_path, 2)
    await _save_record(svc, db_path, _record("Xbox"))

    async with get_connection(db_path) as db:
        assert (await driver_signup_on(db, [USER], season_id=1))["platform"] == "Steam"
        assert (await driver_signup_on(db, [USER]))["platform"] == "Xbox"
        assert await driver_signup_on(db, [USER], season_id=3) is None
