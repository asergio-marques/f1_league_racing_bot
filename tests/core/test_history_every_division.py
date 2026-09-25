"""A driver's history lists every division they took part in during a season (issue #220).

A driver takes part in a division once a placement of theirs in it is committed. Moving,
releasing or sacking them afterwards changes where they sit, not what they were part of: the
season's end writes a history entry for each such division, and a division the driver left
is in their history beside the one they finished in.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection  # noqa: E402
from services.season_end_service import _write_driver_history_entries  # noqa: E402
from tests.unit.test_driver_move import (  # noqa: E402
    AM,
    PRO,
    PROFILE_ID,
    SEASON_ID,
    SERVER_ID,
    _guild,
    _move,
    _seat,
    _service,
    db_path,  # noqa: F401 — the fixture
)


async def _memberships(db_path) -> list[tuple[int, int]]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT division_id, driver_profile_id FROM driver_division_memberships ORDER BY 1, 2"
        )
        return [tuple(row) for row in await cursor.fetchall()]


async def _history(db_path) -> list[str]:
    bot = SimpleNamespace(db_path=db_path)
    await _write_driver_history_entries(SimpleNamespace(id=SEASON_ID, season_number=1), bot)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT division_name FROM driver_history_entries WHERE driver_profile_id = ? "
            "ORDER BY division_name",
            (PROFILE_ID,),
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


async def test_a_driver_released_from_a_division_keeps_its_history(db_path):
    await _seat(db_path, PRO, "Alpha")
    await _seat(db_path, AM, "Bravo")
    service = _service(db_path)
    service.get_team_role_config = AsyncMock(return_value=None)

    await service.release_driver(
        driver_profile_id=PROFILE_ID, division_id=AM,
        season_id=SEASON_ID, acting_user_id=1, acting_user_name="Manager", guild=_guild(),
        discord_user_id="4242",
    )

    assert await _history(db_path) == ["Am", "Pro"]


async def test_a_sacked_driver_keeps_the_history_of_the_divisions_they_raced_in(db_path):
    await _seat(db_path, PRO, "Alpha")
    service = _service(db_path)
    service.revoke_all_placement_roles = AsyncMock()
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=None)
    guild.fetch_member = AsyncMock(return_value=None)

    await service.sack_driver(PROFILE_ID, SEASON_ID, 1, "Manager", guild, "4242")

    assert await _history(db_path) == ["Pro"]


async def test_a_driver_deleted_takes_their_memberships_with_them(db_path):
    """The driver pass deletes a driver who never raced; the archive keeps nothing of them."""
    from services.season_lifecycle_service import delete_driver_profiles

    await _seat(db_path, PRO, "Alpha")
    async with get_connection(db_path) as db:
        await delete_driver_profiles(db, [PROFILE_ID], keep_history=False)
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
            "VALUES (?, 1, 'NORMAL', '2026-01-01T18:00:00')",
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
