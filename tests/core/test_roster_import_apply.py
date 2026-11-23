"""Creating a whole roster at once, or creating none of it.

The parser has its own file; what is checked here is everything that needs the database.
Two rules carry the weight:

* **The IDs the file names are the IDs written.** Every sibling generator script — results,
  check-ins — keys on them absolutely, and `add_test_driver`'s `MAX + 1` allocation lines
  up with the file only on a clean season. If this ever drifts back to allocating its own,
  a results file would name drivers that do not exist and nothing would say so.
* **A fault creates nobody.** A half-applied roster is a state no sibling script can work
  against, and re-pasting the fixed file would duplicate whatever landed the first time.
"""
from __future__ import annotations

import pytest

from leaguebot.framework.db.database import get_connection, run_migrations
from leaguebot.core.utils.roster_import import SYNTHETIC_ID_BASE, parse_roster_csv


SERVER_ID = 7700

HEADER = "ID,Driver name"


@pytest.fixture
async def season(tmp_path):
    """A SETUP season with two divisions, each holding a two-seat team and a reserve."""
    path = str(tmp_path / "roster.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, test_mode_active) "
            "VALUES (?, 1, 2, 3, 1)",
            (SERVER_ID,),
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-03-01', 'SETUP', 1)"
        )
        season_id = cursor.lastrowid
        for tier, name in ((1, "Elite"), (2, "Challenger")):
            cursor = await db.execute(
                "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
                "VALUES (?, ?, ?, 'ACTIVE', ?)",
                (season_id, name, 100 + tier, tier),
            )
            division_id = cursor.lastrowid
            for team, seats, reserve in (("Alpine", 2, 0), ("Reserve", 0, 1)):
                cursor = await db.execute(
                    "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (division_id, team, team, seats, reserve),
                )
                team_id = cursor.lastrowid
                for seat in range(1, seats + 1):
                    await db.execute(
                        "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, ?)",
                        (team_id, seat),
                    )
        await db.commit()
    return path


def _csv(*rows: str) -> str:
    return "\n".join([HEADER, *rows])


def _row(offset, name):
    return f"{SYNTHETIC_ID_BASE + offset},{name}"


async def _bulk(path, drivers):
    """Create *drivers* through the roster's writer, on a connection of the test's own, committed
    as a change's save commits it."""
    from leaguebot.core.services.test_mode.test_roster_service import add_test_drivers_in_bulk_on

    async with get_connection(path) as db:
        result = await add_test_drivers_in_bulk_on(db, drivers)
        await db.commit()
    return result


async def _apply(path, text):
    drivers, errors = parse_roster_csv(text)
    assert errors == [], f"the parser refused before the apply: {errors}"
    return await _bulk(path, drivers)


async def _created(path) -> list[tuple[str, str]]:
    """Every fake driver's ID and name, by ID."""
    async with get_connection(path) as db:
        cursor = await db.execute(
            "SELECT discord_user_id AS uid, test_display_name AS name FROM driver_profiles "
            "WHERE is_test_driver = 1 ORDER BY discord_user_id"
        )
        return [(r["uid"], r["name"]) for r in await cursor.fetchall()]


# ── The happy path ────────────────────────────────────────────────────────


async def test_a_roster_is_created_at_not_signed_up_and_unseated(season):
    """The core specification's fake drivers, created at Not Signed Up: a roster of Quicksilver
    and Badger is imported into a season holding free seats in both divisions. Both are fake
    drivers at Not Signed Up, in the file's order; no seat is taken and nobody is assigned to a
    division of the season."""
    created, errors = await _apply(season, _csv(_row(1, "Quicksilver"), _row(2, "Badger")))

    assert errors == []
    assert created == 2
    assert [name for _uid, name in await _created(season)] == ["Quicksilver", "Badger"]
    async with get_connection(season) as db:
        cursor = await db.execute("SELECT DISTINCT current_state FROM driver_profiles")
        assert [r[0] for r in await cursor.fetchall()] == ["NOT_SIGNED_UP"]
        cursor = await db.execute(
            "SELECT COUNT(*) FROM team_seats WHERE driver_profile_id IS NOT NULL"
        )
        assert (await cursor.fetchone())[0] == 0
        cursor = await db.execute("SELECT COUNT(*) FROM driver_season_assignments")
        assert (await cursor.fetchone())[0] == 0


@pytest.mark.parametrize("first, second", [(7, 9), (40, 41)],
                         ids=["7 and 9", "40 and 41, not consecutive with 1"])
async def test_the_ids_written_are_the_ids_the_file_named(season, first, second):
    """The rule every sibling generator script depends on. The ids are kept even when they are
    not consecutive from 1: `MAX + 1` allocation would have renumbered 40 and 41 to 1 and 2."""
    await _apply(season, _csv(_row(first, "Quicksilver"), _row(second, "Badger")))

    assert [uid for uid, _name in await _created(season)] == [
        str(SYNTHETIC_ID_BASE + first),
        str(SYNTHETIC_ID_BASE + second),
    ]


async def test_every_fault_is_reported_at_once(season):
    """Which is what importing the same file twice looks like. Quicksilver and Badger are imported, then the same two IDs again under other names: the
    second import creates nobody and names both IDs as already on this server."""
    await _apply(season, _csv(_row(1, "Quicksilver"), _row(2, "Badger")))

    created, errors = await _apply(season, _csv(_row(1, "Kestrel"), _row(2, "Wolf")))

    assert created == 0
    assert errors == [
        f"Line 2: a driver with ID {SYNTHETIC_ID_BASE + 1} is already on this server.",
        f"Line 3: a driver with ID {SYNTHETIC_ID_BASE + 2} is already on this server.",
    ]
    assert [name for _uid, name in await _created(season)] == ["Quicksilver", "Badger"]


async def test_no_season_is_reported(tmp_path):
    path = str(tmp_path / "empty.db")
    await run_migrations(path)
    drivers, _ = parse_roster_csv(_csv(_row(1, "Quicksilver")))

    created, errors = await _bulk(path, drivers)

    assert created == 0
    assert "No active or setup season" in errors[0]


# ── The real file ─────────────────────────────────────────────────────────


async def test_the_import_is_what_roster_add_would_have_produced(season):
    """Created drivers must be indistinguishable from ones added one at a time, or the
    rest of test mode — listing, signup, placement — would treat them differently: a fake driver
    at Not Signed Up, never a former driver."""
    await _apply(season, _csv(_row(1, "Quicksilver")))

    async with get_connection(season) as db:
        cursor = await db.execute(
            "SELECT current_state, former_driver, is_test_driver FROM driver_profiles"
        )
        row = await cursor.fetchone()

    assert row["current_state"] == "NOT_SIGNED_UP"
    assert row["former_driver"] == 0
    assert row["is_test_driver"] == 1
