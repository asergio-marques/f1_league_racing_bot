"""Seating a whole roster at once, or seating none of it.

The parser has its own file; what is checked here is everything that needs the database.
Two rules carry the weight:

* **The IDs the file names are the IDs written.** Every sibling generator script — results,
  check-ins — keys on them absolutely, and `add_test_driver`'s `MAX + 1` allocation lines
  up with the file only on a clean season. If this ever drifts back to allocating its own,
  a results file would name drivers that do not exist and nothing would say so.
* **A fault seats nobody.** A half-applied roster is a state no sibling script can work
  against, and re-pasting the fixed file would duplicate whatever landed the first time.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.test_roster_service import add_test_drivers_in_bulk  # noqa: E402
from utils.roster_import import SYNTHETIC_ID_BASE, parse_roster_csv  # noqa: E402

SERVER_ID = 7700

HEADER = "ID,Driver name,Team,Division,Nationality"


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
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, '2026-03-01', 'SETUP', 1)",
            (SERVER_ID,),
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
                    "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
                    "VALUES (?, ?, ?, ?)",
                    (division_id, team, seats, reserve),
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


def _row(offset, name, team="Alpine", div="Elite", nat="British"):
    return f"{SYNTHETIC_ID_BASE + offset},{name},{team},{div},{nat}"


async def _apply(path, text):
    drivers, errors = parse_roster_csv(text)
    assert errors == [], f"the parser refused before the apply: {errors}"
    return await add_test_drivers_in_bulk(SERVER_ID, drivers, path)


async def _seated(path) -> list[tuple[str, str]]:
    async with get_connection(path) as db:
        cursor = await db.execute(
            "SELECT dp.discord_user_id AS uid, dp.test_display_name AS name "
            "FROM driver_season_assignments dsa "
            "JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id "
            "ORDER BY dp.discord_user_id"
        )
        return [(r["uid"], r["name"]) for r in await cursor.fetchall()]


# ── The happy path ────────────────────────────────────────────────────────


async def test_a_roster_is_seated(season):
    seated, errors = await _apply(season, _csv(_row(1, "Quicksilver"), _row(2, "Badger")))

    assert errors == []
    assert seated == 2
    assert [name for _uid, name in await _seated(season)] == ["Quicksilver", "Badger"]


async def test_the_ids_written_are_the_ids_the_file_named(season):
    """The rule every sibling generator script depends on."""
    await _apply(season, _csv(_row(7, "Quicksilver"), _row(9, "Badger")))

    assert [uid for uid, _name in await _seated(season)] == [
        str(SYNTHETIC_ID_BASE + 7),
        str(SYNTHETIC_ID_BASE + 9),
    ]


async def test_the_ids_are_kept_even_when_they_are_not_consecutive(season):
    """`MAX + 1` allocation would have renumbered these to 1 and 2."""
    await _apply(season, _csv(_row(40, "Quicksilver"), _row(41, "Badger")))

    assert [uid for uid, _name in await _seated(season)] == [
        str(SYNTHETIC_ID_BASE + 40),
        str(SYNTHETIC_ID_BASE + 41),
    ]


async def test_a_driver_is_seated_and_the_seat_is_taken(season):
    await _apply(season, _csv(_row(1, "Quicksilver")))

    async with get_connection(season) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS taken FROM team_seats WHERE driver_profile_id IS NOT NULL"
        )
        assert (await cursor.fetchone())["taken"] == 1


async def test_the_nationality_is_stored_canonically(season):
    """Accepted as the signup wizard accepts it — adjective, country or 'other'."""
    await _apply(season, _csv(_row(1, "Quicksilver", nat="united kingdom")))

    async with get_connection(season) as db:
        cursor = await db.execute("SELECT test_nationality FROM driver_profiles")
        assert (await cursor.fetchone())[0] == "British"


async def test_a_driver_with_no_nationality_is_seated_without_one(season):
    seated, errors = await _apply(season, _csv(_row(1, "Quicksilver", nat="")))

    assert errors == []
    assert seated == 1


async def test_several_divisions_are_seated_in_one_import(season):
    seated, errors = await _apply(
        season,
        _csv(_row(1, "Quicksilver"), _row(2, "Badger", div="Challenger")),
    )

    assert errors == []
    assert seated == 2


async def test_a_reserve_team_takes_as_many_as_it_is_given(season):
    """It has no seat limit, so seats are created as the drivers arrive."""
    seated, errors = await _apply(
        season,
        _csv(
            _row(1, "Quicksilver", team="Reserve"),
            _row(2, "Badger", team="Reserve"),
            _row(3, "Kestrel", team="Reserve"),
        ),
    )

    assert errors == []
    assert seated == 3


# ── Nothing is applied on a fault ─────────────────────────────────────────


async def test_an_unknown_division_seats_nobody(season):
    seated, errors = await _apply(
        season, _csv(_row(1, "Quicksilver"), _row(2, "Badger", div="Rookie"))
    )

    assert seated == 0
    assert await _seated(season) == [], "a partial roster was applied"
    assert any("Rookie" in problem for problem in errors)


async def test_an_unknown_team_seats_nobody(season):
    seated, errors = await _apply(season, _csv(_row(1, "Quicksilver", team="Ferrari")))

    assert seated == 0
    assert await _seated(season) == []
    assert any("ferrari" in problem.lower() for problem in errors)


async def test_a_bad_nationality_seats_nobody(season):
    seated, errors = await _apply(season, _csv(_row(1, "Quicksilver", nat="Martian")))

    assert seated == 0
    assert await _seated(season) == []
    assert any("Martian" in problem for problem in errors)


async def test_a_team_given_more_drivers_than_it_has_seats_is_refused(season):
    """Counted across the whole import: each row passes on its own, and three into a
    two-seat team does not."""
    seated, errors = await _apply(
        season,
        _csv(_row(1, "Quicksilver"), _row(2, "Badger"), _row(3, "Kestrel")),
    )

    assert seated == 0
    assert await _seated(season) == []
    assert any("free seat" in problem for problem in errors)


async def test_every_fault_is_reported_at_once(season):
    seated, errors = await _apply(
        season,
        _csv(_row(1, "Quicksilver", div="Rookie"), _row(2, "Badger", nat="Martian")),
    )

    assert seated == 0
    assert len(errors) >= 2


async def test_no_season_is_reported(tmp_path):
    path = str(tmp_path / "empty.db")
    await run_migrations(path)
    drivers, _ = parse_roster_csv(_csv(_row(1, "Quicksilver")))

    seated, errors = await add_test_drivers_in_bulk(SERVER_ID, drivers, path)

    assert seated == 0
    assert "No active or setup season" in errors[0]


# ── A division that already holds drivers ─────────────────────────────────


async def test_a_division_that_already_holds_drivers_is_refused(season):
    """The CSV describes a whole grid. Importing over a seated division would put seats
    somewhere the file does not describe, with the file still looking like the record."""
    await _apply(season, _csv(_row(1, "Quicksilver")))

    seated, errors = await _apply(season, _csv(_row(2, "Badger")))

    assert seated == 0
    assert any("already holds" in problem for problem in errors)
    assert len(await _seated(season)) == 1


async def test_an_empty_division_is_still_importable_beside_a_full_one(season):
    """Only the division named is refused, so the second half of a split roster lands."""
    await _apply(season, _csv(_row(1, "Quicksilver")))

    seated, errors = await _apply(season, _csv(_row(2, "Badger", div="Challenger")))

    assert errors == []
    assert seated == 1


async def test_an_id_already_on_the_server_is_refused(season):
    """Which is what importing the same file twice looks like."""
    await _apply(season, _csv(_row(1, "Quicksilver")))
    drivers, _ = parse_roster_csv(_csv(_row(1, "Quicksilver", div="Challenger")))

    seated, errors = await add_test_drivers_in_bulk(SERVER_ID, drivers, season)

    assert seated == 0
    assert any("already on this server" in problem for problem in errors)


# ── The real file ─────────────────────────────────────────────────────────


async def test_the_import_is_what_roster_add_would_have_produced(season):
    """Seated drivers must be indistinguishable from ones added one at a time, or the
    rest of test mode — listing, results, attendance — would treat them differently."""
    await _apply(season, _csv(_row(1, "Quicksilver")))

    async with get_connection(season) as db:
        cursor = await db.execute(
            "SELECT current_state, former_driver, is_test_driver FROM driver_profiles"
        )
        row = await cursor.fetchone()

    assert row["current_state"] == "ASSIGNED"
    assert row["former_driver"] == 0
    assert row["is_test_driver"] == 1
