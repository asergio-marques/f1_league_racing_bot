"""A driver's roles belong to their current account (issue #243).

`PlacementService.driver_role_ids` reads what the driver's standing entitles them to — the
driver role while Unassigned or Assigned, and the division and team roles of each confirmed
placement in the live season — from the league's own record, so it answers even when the
account holding the roles has left. `/driver reassign` moves them, as steps after its save, to
the new current account from the one it replaced where that one is still in the server (E21,
E22); `test_driver_reassign.py` pins a role Discord refuses there, and `test_role_moves.py` the
step itself.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.placement_service import PlacementService

SERVER_ID = 2438
DRIVER_ROLE, DIVISION_ROLE, TEAM_ROLE = 7001, 7002, 7003
OLD, NEW = "6301", "6302"


async def _league(tmp_path, *, state: str = "ASSIGNED", committed: int = 1,
                   test: bool = False) -> tuple[str, int]:
    """A driver seated in the live season's one division, in team Alpha."""
    db_path = os.path.join(str(tmp_path), "roles.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "UPDATE server_configs SET driver_role_id = ?",
            (DRIVER_ROLE,),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2026-09-17', 'ACTIVE', 1)"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier) "
            "VALUES (1, 1, 'Pro', ?, 1)",
            (DIVISION_ROLE,),
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (1, 1, 'Alpha', 'Alpha', 2, 0)"
        )
        await db.execute(
            "INSERT INTO team_role_configs (team_name, role_id, updated_at) VALUES ('Alpha', ?, '2031-03-04T05:06:07+00:00')",
            (TEAM_ROLE,),
        )
        cursor = await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state, "
            "is_test_driver) VALUES (?, ?, ?)",
            (NEW, state, int(test)),
        )
        profile_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (1, 1, 1, ?)",
            (profile_id,),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id, "
            "team_seat_id, committed) VALUES (?, 1, 1, 1, ?)",
            (profile_id, committed),
        )
        await db.commit()
    return db_path, profile_id


@pytest.mark.parametrize(
    "state, committed, test, expected",
    [
        pytest.param("ASSIGNED", 1, False, {DRIVER_ROLE, DIVISION_ROLE, TEAM_ROLE},
                     id="assigned"),
        pytest.param("UNASSIGNED", 0, False, {DRIVER_ROLE}, id="unconfirmed placement"),
        pytest.param("NOT_SIGNED_UP", 0, False, set(), id="not signed up"),
        pytest.param("ASSIGNED", 1, True, set(), id="test mode"),
    ],
)
async def test_a_driver_holds_the_roles_their_standing_entitles_them_to(
    tmp_path, state, committed, test, expected
):
    """An assigned driver holds the driver, division and team roles; an unconfirmed placement
    grants no division or team role; a driver not signed up holds no driver role (E22); and a
    test-mode driver holds no roles."""
    db_path, pid = await _league(tmp_path, state=state, committed=committed, test=test)
    assert await PlacementService(db_path).driver_role_ids(pid) == expected


async def _lewis_reassigned(tmp_path, *, left: bool = False):
    """The season in Placements; Lewis (101), Assigned, his seat in Pro at Ferrari confirmed, holds
    the driver, Pro and Ferrari roles. The league manager runs `/driver reassign` of Lewis onto
    9001, and the queue runs it; where *left*, 101 has left the server first. Gives the league and
    the reply."""
    from tests.core.test_driver_reassign import _ask_reassign, _NEW, _queue_league
    from tests.support.change_queue import run_queue
    from tests.support.season_league import (
        DRIVER_ROLE, FERRARI_ROLE, LEWIS, PRO_ROLE, profile_id,
    )

    league = await _queue_league(tmp_path)
    await league.write(
        "UPDATE driver_profiles SET current_state = 'ASSIGNED' WHERE id = ?", profile_id(LEWIS)
    )
    await league.write(
        "UPDATE driver_season_assignments SET committed = 1 WHERE driver_profile_id = ?",
        profile_id(LEWIS),
    )
    league.roles_held[LEWIS] = {DRIVER_ROLE, PRO_ROLE, FERRARI_ROLE}
    league.bot.image_drawing.discard_portraits = AsyncMock(return_value=0)
    if left:
        league.absent.add(LEWIS)
    asked = await _ask_reassign(league, LEWIS, _NEW)
    await run_queue(league.bot)
    return league, asked


async def test_the_roles_move_from_the_replaced_account_to_the_current_one(tmp_path):
    """E22 through `/driver reassign`. Lewis (101), Assigned, his seat in Pro at Ferrari confirmed,
    holds the driver, Pro and Ferrari roles; he is reassigned onto 9001. Every role his standing
    entitles him to is taken from 101 and given to 9001, and the reply is today's success alone."""
    from tests.core.test_driver_reassign import _NEW
    from tests.support.season_league import DRIVER_ROLE, FERRARI_ROLE, LEWIS, PRO_ROLE
    from tests.support.settings_league import last_said

    league, asked = await _lewis_reassigned(tmp_path)

    expected = {DRIVER_ROLE, PRO_ROLE, FERRARI_ROLE}
    assert set(league.revoked[LEWIS]) == expected
    assert set(league.granted[_NEW]) == expected
    assert "⚠️" not in last_said(asked)


async def test_a_replaced_account_that_has_left_is_skipped_quietly(tmp_path):
    """E21 through `/driver reassign`. Lewis (101), Assigned, his seat in Pro at Ferrari confirmed,
    has left the server when he is reassigned onto 9001. The driver, Pro and Ferrari roles are given
    to 9001 from the league's record; nothing is taken from 101, and nothing is reported as not
    done."""
    from tests.core.test_driver_reassign import _NEW
    from tests.support.season_league import DRIVER_ROLE, FERRARI_ROLE, LEWIS, PRO_ROLE
    from tests.support.settings_league import last_said

    league, asked = await _lewis_reassigned(tmp_path, left=True)

    assert set(league.granted[_NEW]) == {DRIVER_ROLE, PRO_ROLE, FERRARI_ROLE}
    assert LEWIS not in league.revoked
    assert "⚠️" not in last_said(asked)
