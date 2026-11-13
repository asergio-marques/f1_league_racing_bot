"""A driver's history lists every division they took part in during a season (issue #220).

A driver takes part in a division once a placement of theirs in it is committed. Moving,
releasing or sacking them afterwards changes where they sit, not what they were part of: the
season's end writes a history entry for each such division, and a division the driver left
is in their history beside the one they finished in.
"""
from __future__ import annotations

import pytest

from leaguebot.core.db.database import get_connection
from tests.core.test_driver_move import (
    _NOW,
    AM,
    PRO,
    PROFILE_ID,
    SEASON_ID,
    SERVER_ID,
    _move,
    _seat,
    _service,
    db_path,  # noqa: F401 — the fixture
)
from tests.support.delete_hooks import DRIVER_HOOKS, delete_hooks


async def _memberships(db_path) -> list[tuple[int, int]]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT division_id, driver_profile_id FROM driver_division_memberships ORDER BY 1, 2"
        )
        return [tuple(row) for row in await cursor.fetchall()]


async def _history(db_path, season_id=SEASON_ID, profile_id=PROFILE_ID) -> list[str]:
    """The season's history written on one connection and committed, as a season's end save
    does: the divisions it names for the driver *profile_id*."""
    from leaguebot.core.services.seasons.season_end_service import write_driver_history_entries_on
    from tests.support.driver_records_hooks import season_standing_read

    async with get_connection(db_path) as db:
        await write_driver_history_entries_on(
            db, season_id, 1,
            season_standing_read=season_standing_read(),
        )
        await db.commit()
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT division_name FROM driver_history_entries WHERE driver_profile_id = ? "
            "ORDER BY division_name",
            (profile_id,),
        )
        return [row[0] for row in await cursor.fetchall()]


async def test_a_committed_placement_makes_the_driver_part_of_the_division(db_path):
    await _seat(db_path, PRO, "Alpha")

    assert await _memberships(db_path) == [(PRO, PROFILE_ID)]


async def test_an_uncommitted_placement_makes_nobody_part_of_anything(db_path):
    await _seat(db_path, PRO, "Alpha", committed=0)

    assert await _memberships(db_path) == []


async def test_confirming_a_placement_makes_the_driver_part_of_its_division(db_path):
    await _seat(db_path, PRO, "Alpha", committed=0)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE driver_season_assignments SET committed = 1")
        await db.commit()

    assert await _memberships(db_path) == [(PRO, PROFILE_ID)]


async def test_a_driver_moved_to_another_division_has_history_in_both(db_path):
    await _seat(db_path, PRO, "Alpha")

    await _move(_service(db_path), AM, "Bravo")

    assert await _memberships(db_path) == [(PRO, PROFILE_ID), (AM, PROFILE_ID)]
    assert await _history(db_path) == ["Am", "Pro"]


async def test_a_driver_released_from_a_division_keeps_its_history(tmp_path):
    """`ongoing_league`'s season: Lewis sits, confirmed, at Ferrari in Pro and at McLaren in Am.
    The league admin runs `/driver release` of Lewis from Am and the queue runs: he sits at
    Ferrari in Pro alone, and the season's history still names both Am and Pro for him."""
    from tests.core.test_driver_release import _ask_release, _lewis_seats, _twice_seated
    from tests.support.change_queue import run_queue
    from tests.support.season_league import LEWIS
    from tests.support.season_league import SEASON_ID as LEAGUE_SEASON_ID

    league = await _twice_seated(tmp_path)

    await _ask_release(league, LEWIS)
    await run_queue(league.bot)

    assert await _lewis_seats(league) == [("Pro", "Ferrari", True)]
    assert await _history(league.db_path, LEAGUE_SEASON_ID, profile_id=41) == ["Am", "Pro"]


async def test_a_sacked_driver_keeps_the_history_of_the_divisions_they_raced_in(db_path):
    await _seat(db_path, PRO, "Alpha")

    async with get_connection(db_path) as db:
        await _service(db_path).sack_on(
            db, driver_profile_id=PROFILE_ID, season_id=SEASON_ID, acting_user_id=1,
            acting_user_name="Manager", now=_NOW,
        )
        await db.commit()

    assert await _history(db_path) == ["Pro"]


async def test_a_driver_deleted_takes_their_memberships_with_them(db_path):
    """The driver pass deletes a driver who never raced; the archive keeps nothing of them."""
    from leaguebot.core.services.seasons.season_lifecycle_service import delete_driver_profiles

    await _seat(db_path, PRO, "Alpha")
    async with get_connection(db_path) as db:
        await delete_driver_profiles(
            db, [PROFILE_ID], keep_history=False, **delete_hooks(*DRIVER_HOOKS)
        )
        await db.commit()

    assert await _memberships(db_path) == []


async def test_a_driver_who_changed_account_after_the_last_round_keeps_their_standing(db_path):
    """E40 (issue #243): the final round's standing stands under the account the driver left.

    The history entry reads it all the same, and names the driver by the account they use now.
    """
    await _seat(db_path, PRO, "Alpha")
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT discord_user_id FROM driver_profiles WHERE id = ?", (PROFILE_ID,)
        )
        past = (await cursor.fetchone())[0]
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at) "
            "VALUES (?, 1, 'NORMAL', '2026-01-01T18:00:00+00:00')",
            (PRO,),
        )
        await db.execute(
            "INSERT INTO driver_standings_snapshots "
            "(round_id, division_id, driver_user_id, standing_position, total_points) "
            "VALUES (?, ?, ?, 3, 42)",
            (cursor.lastrowid, PRO, int(past)),
        )
        await db.execute(
            "UPDATE driver_profiles SET discord_user_id = '777001' WHERE id = ?", (PROFILE_ID,)
        )
        await db.commit()

    assert await _history(db_path) == ["Pro"]
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT discord_user_id, final_position, final_points FROM driver_history_entries"
        )
        assert [tuple(r) for r in await cursor.fetchall()] == [("777001", 3, 42)]


async def _entries_on(db) -> list[str]:
    cursor = await db.execute(
        "SELECT division_name FROM driver_history_entries WHERE driver_profile_id = ? "
        "ORDER BY division_name",
        (PROFILE_ID,),
    )
    return [row[0] for row in await cursor.fetchall()]


async def test_the_history_on_the_save_handed_commits_nothing(db_path):
    """A driver placed in Pro and moved to Am. A season's end writes their history inside the
    save that records its end: written on the connection it is handed, an entry for each
    division is there on that connection, and once the save is let go uncommitted none is."""
    from leaguebot.core.services.seasons.season_end_service import write_driver_history_entries_on
    from tests.support.driver_records_hooks import season_standing_read

    await _seat(db_path, PRO, "Alpha")
    await _move(_service(db_path), AM, "Bravo")

    async with get_connection(db_path) as db:
        await write_driver_history_entries_on(
            db, SEASON_ID, 1,
            season_standing_read=season_standing_read(),
        )
        assert await _entries_on(db) == ["Am", "Pro"]
        await db.rollback()

    async with get_connection(db_path) as db:
        assert await _entries_on(db) == []
