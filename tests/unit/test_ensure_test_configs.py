"""The points configurations test mode seeds are ordinary server configurations.

`/test-mode toggle` seeds "Standard" and "Half Points" so a test season can be scored
without the manager building a ladder by hand. They are written the way `/results config`
writes one — into `points_config_store`, `points_config_entries` and `points_config_fl` —
and attached to the season through `season_points_links`, so `/season approve` copies them
into the season's own store through the ordinary snapshot.

They used to be written straight into `season_points_entries` with only an empty name row
in the server-level store. Everything *scored* correctly, because scoring reads the season
store, but `/results config view` reads the server-level store while a season is in SETUP,
so a manager checking the configuration was told "Standard — no entries configured" over a
config that was fully populated. Nothing about a seeded config should differ from a
hand-built one once it exists.
"""
from __future__ import annotations

import os
import sys

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from services import points_config_service, season_points_service  # noqa: E402
from services.test_roster_service import ensure_test_configs  # noqa: E402

SERVER_ID = 9310


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "configs.db")
    await run_migrations(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.commit()
    return path


@pytest.fixture
async def season_id(db_path):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, '2026-03-01', 'SETUP', 1)",
            (SERVER_ID,),
        )
        await db.commit()
        return cursor.lastrowid


async def test_both_configs_are_created(db_path, season_id):
    created = await ensure_test_configs(SERVER_ID, season_id, db_path)

    assert sorted(created) == ["Half Points", "Standard"]


@pytest.mark.parametrize("config_name", ["Standard", "Half Points"])
async def test_a_seeded_config_is_readable_as_a_server_config(
    db_path, season_id, config_name
):
    """The bug: this read back empty, so the view reported no entries configured."""
    await ensure_test_configs(SERVER_ID, season_id, db_path)

    entries, fl = await points_config_service.get_config_entries(
        db_path, SERVER_ID, config_name
    )

    assert entries, f"{config_name} read back with no entries at all"
    assert fl, f"{config_name} read back with no fastest-lap bonus"


async def test_the_standard_ladder_is_the_one_seeded(db_path, season_id):
    await ensure_test_configs(SERVER_ID, season_id, db_path)

    entries, fl = await points_config_service.get_config_entries(
        db_path, SERVER_ID, "Standard"
    )
    feature = sorted(
        (e.position, e.points)
        for e in entries
        if e.session_type is SessionType.FEATURE_RACE
    )

    assert feature[:5] == [(1, 30), (2, 27), (3, 24), (4, 21), (5, 18)]
    fl_feature = [f for f in fl if f.session_type is SessionType.FEATURE_RACE]
    assert len(fl_feature) == 1
    assert (fl_feature[0].fl_points, fl_feature[0].fl_position_limit) == (2, 15)


async def test_every_session_type_of_a_race_weekend_is_covered(db_path, season_id):
    await ensure_test_configs(SERVER_ID, season_id, db_path)

    entries, _ = await points_config_service.get_config_entries(
        db_path, SERVER_ID, "Standard"
    )

    assert {e.session_type for e in entries} == {
        SessionType.SPRINT_QUALIFYING,
        SessionType.SPRINT_RACE,
        SessionType.FEATURE_QUALIFYING,
        SessionType.FEATURE_RACE,
    }


async def test_the_configs_are_attached_to_the_season(db_path, season_id):
    await ensure_test_configs(SERVER_ID, season_id, db_path)

    names = await season_points_service.get_attached_config_names(db_path, season_id)

    assert sorted(names) == ["Half Points", "Standard"]


async def test_seeding_twice_creates_nothing_and_changes_nothing(db_path, season_id):
    """`/test-mode toggle` can be run again; it must not duplicate or disturb a config."""
    await ensure_test_configs(SERVER_ID, season_id, db_path)
    before, before_fl = await points_config_service.get_config_entries(
        db_path, SERVER_ID, "Standard"
    )

    created_again = await ensure_test_configs(SERVER_ID, season_id, db_path)
    after, after_fl = await points_config_service.get_config_entries(
        db_path, SERVER_ID, "Standard"
    )

    assert created_again == []
    assert len(after) == len(before)
    assert len(after_fl) == len(before_fl)


async def test_approve_carries_the_seeded_config_into_the_season_store(
    db_path, season_id
):
    """The ordinary snapshot, not a private path — this is what scoring then reads."""
    await ensure_test_configs(SERVER_ID, season_id, db_path)

    await season_points_service.snapshot_configs_to_season(
        db_path, season_id, SERVER_ID
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT position, points FROM season_points_entries "
            "WHERE season_id = ? AND config_name = 'Standard' "
            "  AND session_type = 'FEATURE_RACE' ORDER BY position LIMIT 5",
            (season_id,),
        )
        top = [(r["position"], r["points"]) for r in await cursor.fetchall()]
        cursor = await db.execute(
            "SELECT fl_points, fl_position_limit FROM season_points_fl "
            "WHERE season_id = ? AND config_name = 'Standard'",
            (season_id,),
        )
        fl = [(r["fl_points"], r["fl_position_limit"]) for r in await cursor.fetchall()]

    assert top == [(1, 30), (2, 27), (3, 24), (4, 21), (5, 18)]
    assert fl == [(2, 15)]


async def test_the_seeded_config_passes_the_approval_gate(db_path, season_id):
    """`/season approve` refuses a non-monotonic ladder; a seeded one must not trip it."""
    await ensure_test_configs(SERVER_ID, season_id, db_path)
    await season_points_service.snapshot_configs_to_season(
        db_path, season_id, SERVER_ID
    )

    assert await season_points_service.validate_monotonic_ordering(db_path, season_id) == []


async def test_half_points_really_is_the_lesser_ladder(db_path, season_id):
    """The two seeded configs must differ, or there is no point seeding both."""
    await ensure_test_configs(SERVER_ID, season_id, db_path)

    standard, _ = await points_config_service.get_config_entries(
        db_path, SERVER_ID, "Standard"
    )
    half, _ = await points_config_service.get_config_entries(
        db_path, SERVER_ID, "Half Points"
    )

    def winner(entries):
        return next(
            e.points
            for e in entries
            if e.session_type is SessionType.FEATURE_RACE and e.position == 1
        )

    assert winner(half) < winner(standard)


async def test_leaving_test_mode_keeps_the_seeded_configs(db_path, season_id):
    """Decided 2026-09-04: a mock driver is scaffolding, a points ladder is not.

    Disabling test mode deletes every fake driver on the server without confirmation.
    It deliberately does **not** take the seeded configurations with them: they are
    ordinary configurations of the server from the moment they are created, editable
    and removable like any other, and deleting them would be discarding a league
    manager's own configuration. `/results config remove` is the way to be rid of them.
    """
    from services.test_roster_service import clear_all_test_drivers

    await ensure_test_configs(SERVER_ID, season_id, db_path)

    # The whole of what disabling test mode does to stored data.
    await clear_all_test_drivers(SERVER_ID, db_path)

    entries, fl = await points_config_service.get_config_entries(
        db_path, SERVER_ID, "Standard"
    )
    assert entries, "leaving test mode discarded a points configuration"
    assert fl
    assert sorted(
        await season_points_service.get_attached_config_names(db_path, season_id)
    ) == ["Half Points", "Standard"], "the season lost its attachment"
