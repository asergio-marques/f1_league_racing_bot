"""A mock roster survives season setup rewriting its snapshot.

`save_pending_snapshot` deletes and re-creates the whole SETUP season — divisions,
teams and seats all take new row IDs — and it runs on every season-setup command, not
on the first. Until this was fixed it also deleted every mock driver seated in that
season, so a test-mode roster built during setup vanished the moment the manager ran
`/round add`, or restarted the bot and ran another setup command.

The rule these pin: a seated mock driver is a league manager's work and outlives the
snapshot, reseated by division name, team name and seat number the same way channel
configuration is carried across the rebuild.
"""
from __future__ import annotations

import os
import sys
from datetime import date

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.season_service import SeasonService  # noqa: E402
from services.test_roster_service import add_test_driver, list_test_drivers  # noqa: E402

SERVER_ID = 8420
DIVISION = "Division 1"
_TEAMS = (("Redline", 2, 0), ("Reserve", 0, 1))


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "snapshot.db")
    await run_migrations(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.commit()
    return path


async def _seed_teams(db, division_id):
    """The two teams every division here gets: a two-seat one and the reserve."""
    for name, seats, reserve in _TEAMS:
        cursor = await db.execute(
            "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
            "VALUES (?, ?, ?, ?)",
            (division_id, name, seats, reserve),
        )
        team_id = cursor.lastrowid
        for seat_number in range(1, seats + 1):
            await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, ?)",
                (team_id, seat_number),
            )


async def _seed_setup_season(db_path):
    """One SETUP season with one division, a two-seat team and a reserve team."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, '2026-03-01', 'SETUP', 1)",
            (SERVER_ID,),
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
            "VALUES (?, ?, 1, 'ACTIVE', 1)",
            (season_id, DIVISION),
        )
        await _seed_teams(db, cursor.lastrowid)
        await db.commit()
    return season_id


async def _snapshot(svc, db_path, season_id, divisions=None, forecast_channel_id=None):
    """Re-snapshot the way a setup command does, re-seeding teams as the cog then does."""
    divisions = divisions or [DIVISION]
    new_season_id, _ = await svc.save_pending_snapshot(
        SERVER_ID,
        date(2026, 3, 1),
        season_id,
        [
            {
                "name": name,
                "role_id": 1,
                "channel_id": forecast_channel_id,
                "tier": i + 1,
                "rounds": [],
            }
            for i, name in enumerate(divisions)
        ],
    )
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM divisions WHERE season_id = ? ORDER BY id", (new_season_id,)
        )
        for row in await cursor.fetchall():
            await _seed_teams(db, row[0])
        await db.commit()
    await svc.restore_driver_seats(new_season_id)
    return new_season_id


async def _seat_map(db_path):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT dp.test_display_name AS name, ts.seat_number
            FROM team_seats ts
            JOIN driver_profiles dp ON dp.id = ts.driver_profile_id
            ORDER BY dp.test_display_name
            """
        )
        return {r["name"]: r["seat_number"] for r in await cursor.fetchall()}


async def test_seated_mock_driver_survives_a_snapshot(db_path):
    """The bug: a setup command after seating a mock driver used to delete them."""
    season_id = await _seed_setup_season(db_path)
    added = await add_test_driver(SERVER_ID, "Mock Alpha", "Redline", DIVISION, db_path)
    assert not isinstance(added, str), added

    await _snapshot(SeasonService(db_path), db_path, season_id)

    survivors = await list_test_drivers(SERVER_ID, DIVISION, db_path)
    assert [d["display_name"] for d in survivors] == ["Mock Alpha"]
    assert survivors[0]["team_name"] == "Redline"


async def test_mock_driver_keeps_its_seat_number(db_path):
    """Reseating is by seat number, not by "the next free seat"."""
    season_id = await _seed_setup_season(db_path)
    await add_test_driver(SERVER_ID, "Mock One", "Redline", DIVISION, db_path)
    second = await add_test_driver(SERVER_ID, "Mock Two", "Redline", DIVISION, db_path)
    assert not isinstance(second, str), second

    before = await _seat_map(db_path)
    await _snapshot(SeasonService(db_path), db_path, season_id)

    assert await _seat_map(db_path) == before


async def test_season_assignment_is_restored_under_the_new_season(db_path):
    """The assignment must point at the new season and division IDs, not the dead ones."""
    season_id = await _seed_setup_season(db_path)
    added = await add_test_driver(SERVER_ID, "Mock Alpha", "Redline", DIVISION, db_path)
    profile_id = added["profile_id"]

    new_season_id = await _snapshot(SeasonService(db_path), db_path, season_id)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT season_id, division_id FROM driver_season_assignments "
            "WHERE driver_profile_id = ?",
            (profile_id,),
        )
        rows = await cursor.fetchall()
        cursor = await db.execute(
            "SELECT id FROM divisions WHERE season_id = ?", (new_season_id,)
        )
        new_div_id = (await cursor.fetchone())[0]

    assert len(rows) == 1, "exactly one assignment, not a duplicate or an orphan"
    assert rows[0]["season_id"] == new_season_id
    assert rows[0]["division_id"] == new_div_id


async def test_the_restored_assignment_carries_its_team_seat_id(db_path):
    """`team_seat_id` must name the seat the driver actually sits in.

    Everything that reads a seated driver joins `team_seats` through this column —
    `/season review`'s lineup block and the whole attendance module among them — so an
    assignment restored without it is invisible to every one of them. The driver is
    seated, `/test-mode roster list` shows them, and the review reports the division
    empty. That is what a NULL here looks like from the outside.
    """
    season_id = await _seed_setup_season(db_path)
    added = await add_test_driver(SERVER_ID, "Mock Alpha", "Redline", DIVISION, db_path)
    profile_id = added["profile_id"]

    await _snapshot(SeasonService(db_path), db_path, season_id)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT ts.id AS seat_id, ts.seat_number, ti.name AS team_name
            FROM driver_season_assignments dsa
            JOIN team_seats ts     ON ts.id = dsa.team_seat_id
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            WHERE dsa.driver_profile_id = ?
            """,
            (profile_id,),
        )
        joined = await cursor.fetchall()
        cursor = await db.execute(
            "SELECT id FROM team_seats WHERE driver_profile_id = ?", (profile_id,)
        )
        occupied_seat = (await cursor.fetchone())[0]

    assert len(joined) == 1, "the join every reader makes must find exactly this driver"
    assert joined[0]["seat_id"] == occupied_seat, "names a different seat than it occupies"
    assert joined[0]["team_name"] == "Redline"


async def test_repeated_snapshots_do_not_accumulate_assignments(db_path):
    """Several setup commands in a row leave one assignment, not one per command."""
    season_id = await _seed_setup_season(db_path)
    added = await add_test_driver(SERVER_ID, "Mock Alpha", "Redline", DIVISION, db_path)
    profile_id = added["profile_id"]

    svc = SeasonService(db_path)
    for _ in range(3):
        season_id = await _snapshot(svc, db_path, season_id)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM driver_season_assignments WHERE driver_profile_id = ?",
            (profile_id,),
        )
        assignments = (await cursor.fetchone())[0]
        cursor = await db.execute(
            "SELECT COUNT(*) FROM team_seats WHERE driver_profile_id = ?", (profile_id,)
        )
        seats = (await cursor.fetchone())[0]

    assert assignments == 1
    assert seats == 1
    assert len(await list_test_drivers(SERVER_ID, DIVISION, db_path)) == 1


async def test_driver_in_a_removed_division_is_left_unseated_not_deleted(db_path):
    """A division dropped from the setup takes the seat with it, but not the profile.

    The driver stops being seated because there is nowhere to seat them; deleting the
    profile instead would be the very data loss this change exists to stop.
    """
    season_id = await _seed_setup_season(db_path)
    added = await add_test_driver(SERVER_ID, "Mock Alpha", "Redline", DIVISION, db_path)
    profile_id = added["profile_id"]

    # Re-snapshot with the division renamed — the one they sat in no longer exists.
    await _snapshot(SeasonService(db_path), db_path, season_id, divisions=["Division 2"])

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM driver_profiles WHERE id = ?", (profile_id,)
        )
        profile = await cursor.fetchone()
        cursor = await db.execute(
            "SELECT COUNT(*) FROM team_seats WHERE driver_profile_id = ?", (profile_id,)
        )
        seats = (await cursor.fetchone())[0]

    assert profile is not None, "the profile survives even when its division does not"
    assert seats == 0


async def test_reserve_driver_is_reseated(db_path):
    """The reserve team pre-creates no seats, so its occupant needs one made again."""
    season_id = await _seed_setup_season(db_path)
    added = await add_test_driver(SERVER_ID, "Mock Sub", "Reserve", DIVISION, db_path)
    assert not isinstance(added, str), added

    await _snapshot(SeasonService(db_path), db_path, season_id)

    survivors = await list_test_drivers(SERVER_ID, DIVISION, db_path)
    assert [(d["display_name"], d["team_name"]) for d in survivors] == [
        ("Mock Sub", "Reserve")
    ]


# ── Real drivers ──────────────────────────────────────────────────────────
#
# The rule above is not about mock drivers. A season-setup command shapes divisions and
# rounds, and must not unplace anybody — a real driver placed with `/driver assign`
# during SETUP least of all.
#
# The snapshot used to capture `is_test_driver = 1` and restore it, while deleting a
# real driver's assignment by division and restoring nothing. The profile survived,
# so the league was not obviously missing anyone; the seat and the assignment were
# gone, and the loss surfaced far later as an empty lineup, a missing check-in, or a
# result submission rejecting a driver plainly in the league.


async def _seat_real_driver(db_path, season_id, *, team="Redline", seat_number=1):
    """Place a real driver the way `/driver assign` does: a seat and an assignment."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO driver_profiles "
            "(server_id, discord_user_id, current_state, former_driver, is_test_driver) "
            "VALUES (?, '123456789012345678', 'ASSIGNED', 0, 0)",
            (SERVER_ID,),
        )
        profile_id = cursor.lastrowid
        cursor = await db.execute(
            """
            SELECT ts.id, ti.division_id
            FROM team_seats ts
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            JOIN divisions d       ON d.id = ti.division_id
            WHERE d.season_id = ? AND ti.name = ? AND ts.seat_number = ?
            """,
            (season_id, team, seat_number),
        )
        seat_row = await cursor.fetchone()
        await db.execute(
            "UPDATE team_seats SET driver_profile_id = ? WHERE id = ?",
            (profile_id, seat_row["id"]),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments "
            "(driver_profile_id, season_id, division_id, team_seat_id) "
            "VALUES (?, ?, ?, ?)",
            (profile_id, season_id, seat_row["division_id"], seat_row["id"]),
        )
        await db.commit()
    return profile_id


async def _lineup_sees(db_path):
    """The drivers `/season review` and the attendance module would find."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT dp.id AS profile_id, ti.name AS team_name, ts.seat_number
            FROM driver_season_assignments dsa
            JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id
            JOIN team_seats ts      ON ts.id = dsa.team_seat_id
            JOIN team_instances ti  ON ti.id = ts.team_instance_id
            WHERE dp.current_state = 'ASSIGNED'
            ORDER BY dp.id
            """
        )
        return [dict(r) for r in await cursor.fetchall()]


async def test_a_real_driver_keeps_their_placement_across_a_snapshot(db_path):
    """The P1: `/round add` used to silently unplace every real driver in the season."""
    season_id = await _seed_setup_season(db_path)
    profile_id = await _seat_real_driver(db_path, season_id)
    assert len(await _lineup_sees(db_path)) == 1

    await _snapshot(SeasonService(db_path), db_path, season_id)

    seen = await _lineup_sees(db_path)
    assert len(seen) == 1, "the real driver was unplaced by a season-setup command"
    assert seen[0]["profile_id"] == profile_id
    assert seen[0]["team_name"] == "Redline"
    assert seen[0]["seat_number"] == 1


async def test_a_real_drivers_assignment_points_at_the_new_season(db_path):
    season_id = await _seed_setup_season(db_path)
    profile_id = await _seat_real_driver(db_path, season_id)

    new_season_id = await _snapshot(SeasonService(db_path), db_path, season_id)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT season_id, division_id, team_seat_id FROM driver_season_assignments "
            "WHERE driver_profile_id = ?",
            (profile_id,),
        )
        rows = await cursor.fetchall()
        cursor = await db.execute(
            "SELECT id FROM divisions WHERE season_id = ?", (new_season_id,)
        )
        new_div_id = (await cursor.fetchone())[0]

    assert len(rows) == 1
    assert rows[0]["season_id"] == new_season_id
    assert rows[0]["division_id"] == new_div_id
    assert rows[0]["team_seat_id"] is not None


async def test_real_and_mock_drivers_survive_together(db_path):
    """A test-mode league and a real one are restored by one pass, not two."""
    season_id = await _seed_setup_season(db_path)
    real_id = await _seat_real_driver(db_path, season_id, seat_number=1)
    mock = await add_test_driver(SERVER_ID, "Mock Alpha", "Redline", DIVISION, db_path)
    assert not isinstance(mock, str), mock

    await _snapshot(SeasonService(db_path), db_path, season_id)

    seen = {r["profile_id"] for r in await _lineup_sees(db_path)}
    assert seen == {real_id, mock["profile_id"]}


async def test_repeated_setup_commands_do_not_accumulate_real_assignments(db_path):
    season_id = await _seed_setup_season(db_path)
    profile_id = await _seat_real_driver(db_path, season_id)

    svc = SeasonService(db_path)
    for _ in range(3):
        season_id = await _snapshot(svc, db_path, season_id)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM driver_season_assignments WHERE driver_profile_id = ?",
            (profile_id,),
        )
        assignments = (await cursor.fetchone())[0]
        cursor = await db.execute(
            "SELECT COUNT(*) FROM team_seats WHERE driver_profile_id = ?", (profile_id,)
        )
        seats = (await cursor.fetchone())[0]

    assert assignments == 1
    assert seats == 1


async def test_a_real_driver_whose_division_is_dropped_is_unseated_not_deleted(db_path):
    """A division removed from the setup takes the seat, never the driver."""
    season_id = await _seed_setup_season(db_path)
    profile_id = await _seat_real_driver(db_path, season_id)

    await _snapshot(SeasonService(db_path), db_path, season_id, divisions=["Division 2"])

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM driver_profiles WHERE id = ?", (profile_id,)
        )
        profile = await cursor.fetchone()

    assert profile is not None, "a season-setup command must never delete a real driver"
    assert await _lineup_sees(db_path) == []


# ── Everything else the rebuild must carry ────────────────────────────────
#
# The snapshot drops every division, team, seat and round and re-inserts them with new
# row ids, so anything keyed on the old ids is lost unless it is saved before the
# teardown and restored after it. Each setting a league configures per division is
# pinned here, because the failure is silent: the command reports success and the
# configuration is simply gone.


async def _configure_everything(db_path, season_id):
    """Set every per-division and per-season option a league can configure."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM divisions WHERE season_id = ?", (season_id,)
        )
        division_id = (await cursor.fetchone())[0]
        await db.execute(
            "UPDATE divisions SET forecast_channel_id = 111, lineup_channel_id = 222, "
            "calendar_channel_id = 333 WHERE id = ?",
            (division_id,),
        )
        await db.execute(
            "INSERT INTO division_results_config (division_id, results_channel_id, "
            "standings_channel_id, reserves_in_standings, penalty_channel_id) "
            "VALUES (?, 666, 777, 0, 888)",
            (division_id,),
        )
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, server_id, "
            "rsvp_channel_id, attendance_channel_id) VALUES (?, ?, 999, 1010)",
            (division_id, SERVER_ID),
        )
        await db.execute(
            "INSERT INTO season_points_links (season_id, config_name) VALUES (?, 'Standard')",
            (season_id,),
        )
        await db.commit()


async def test_every_configured_channel_survives_a_setup_command(db_path):
    """A league configures its channels, then adds a round — and keeps them all."""
    season_id = await _seed_setup_season(db_path)
    await _configure_everything(db_path, season_id)

    # The weather channel travels in the PendingConfig rather than being saved and
    # restored here, so the snapshot is given it the way `_pending_to_division_models`
    # gives it — from `PendingDivision.channel_id`.
    new_season_id = await _snapshot(
        SeasonService(db_path), db_path, season_id, forecast_channel_id=111
    )

    async with get_connection(db_path) as db:
        division = dict(
            await (
                await db.execute(
                    "SELECT * FROM divisions WHERE season_id = ?", (new_season_id,)
                )
            ).fetchone()
        )
        results = await (
            await db.execute(
                "SELECT * FROM division_results_config WHERE division_id = ?",
                (division["id"],),
            )
        ).fetchone()
        attendance = await (
            await db.execute(
                "SELECT * FROM attendance_division_config WHERE division_id = ?",
                (division["id"],),
            )
        ).fetchone()
        links = await (
            await db.execute(
                "SELECT config_name FROM season_points_links WHERE season_id = ?",
                (new_season_id,),
            )
        ).fetchall()

    # Weather, via the PendingConfig; lineup and calendar, saved and restored by name.
    assert division["forecast_channel_id"] == 111, "weather forecast channel lost"
    assert division["lineup_channel_id"] == 222, "lineup channel lost"
    assert division["calendar_channel_id"] == 333, "calendar channel lost"

    assert results is not None, "results and standings configuration lost"
    assert int(results["results_channel_id"]) == 666
    assert int(results["standings_channel_id"]) == 777
    assert int(results["penalty_channel_id"]) == 888
    assert results["reserves_in_standings"] == 0, "a non-default flag reverted"

    assert attendance is not None, "attendance configuration lost"
    assert int(attendance["rsvp_channel_id"]) == 999
    assert int(attendance["attendance_channel_id"]) == 1010

    assert [r["config_name"] for r in links] == ["Standard"], "points configs lost"


async def test_the_configuration_survives_repeated_setup_commands(db_path):
    """Building a calendar is many commands, not one — each re-runs the whole rebuild."""
    season_id = await _seed_setup_season(db_path)
    await _configure_everything(db_path, season_id)

    svc = SeasonService(db_path)
    for _ in range(3):
        season_id = await _snapshot(svc, db_path, season_id)

    async with get_connection(db_path) as db:
        division = dict(
            await (
                await db.execute(
                    "SELECT * FROM divisions WHERE season_id = ?", (season_id,)
                )
            ).fetchone()
        )
        results = await (
            await db.execute(
                "SELECT COUNT(*) FROM division_results_config WHERE division_id = ?",
                (division["id"],),
            )
        ).fetchone()

    assert division["lineup_channel_id"] == 222
    assert division["calendar_channel_id"] == 333
    assert results[0] == 1, "one config row per division, not zero and not duplicated"
