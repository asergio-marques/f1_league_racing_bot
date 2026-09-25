"""`/driver move` — a committed driver moved in one step (issue #220).

Full-time to Reserve, team to team, or between divisions: the old seat freed and the new one
taken together, the roles of the seat left revoked and those of the seat taken granted, and the
lineup of each division touched posted once. It replaces unassigning and assigning again, which
left the driver roleless in between, posted the lineup twice and let the seat be taken.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.placement_service import PlacementService  # noqa: E402

SERVER_ID = 22090
SEASON_ID = 1
PRO, AM = 11, 12
PROFILE_ID = 5
ROLES = {"Alpha": 501, "Bravo": 502, "Reserve": 509}


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "move.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number, stage) "
            "VALUES (?, '2026-09-17', 'ACTIVE', 1, 'ONGOING')",
            (SEASON_ID,),
        )
        instance_id = 100
        for division_id, name, role in ((PRO, "Pro", 1001), (AM, "Am", 1002)):
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, mention_role_id, tier, status) "
                "VALUES (?, ?, ?, ?, ?, 'ACTIVE')",
                (division_id, SEASON_ID, name, role, division_id - 10),
            )
            for team, reserve in (("Alpha", 0), ("Bravo", 0), ("Reserve", 1)):
                instance_id += 1
                await db.execute(
                    "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
                    "VALUES (?, ?, ?, ?, 2, ?)",
                    (instance_id, division_id, team, team, reserve),
                )
                for seat in (1, 2):
                    await db.execute(
                        "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, ?)",
                        (instance_id, seat),
                    )
        for team, role in ROLES.items():
            await db.execute(
                "INSERT INTO team_role_configs (team_name, role_id) VALUES (?, ?)",
                (team, role),
            )
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (?, '4242', 'ASSIGNED')",
            (PROFILE_ID,),
        )
        await db.commit()
    return path


async def _seat(db_path, division_id, team, profile_id=PROFILE_ID, *, committed=1):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT ts.id FROM team_seats ts JOIN team_instances ti ON ti.id = ts.team_instance_id "
            "WHERE ti.division_id = ? AND ti.name = ? AND ts.driver_profile_id IS NULL "
            "ORDER BY ts.seat_number LIMIT 1",
            (division_id, team),
        )
        seat_id = (await cursor.fetchone())["id"]
        await db.execute(
            "UPDATE team_seats SET driver_profile_id = ? WHERE id = ?", (profile_id, seat_id)
        )
        await db.execute(
            "INSERT INTO driver_season_assignments "
            "(driver_profile_id, season_id, division_id, team_seat_id, committed) "
            "VALUES (?, ?, ?, ?, ?)",
            (profile_id, SEASON_ID, division_id, seat_id, committed),
        )
        await db.commit()


async def _where(db_path, profile_id=PROFILE_ID):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT dsa.division_id, ti.name FROM driver_season_assignments dsa "
            "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
            "JOIN team_instances ti ON ti.id = ts.team_instance_id "
            "WHERE dsa.driver_profile_id = ? ORDER BY dsa.division_id",
            (profile_id,),
        )
        return [(r["division_id"], r["name"]) for r in await cursor.fetchall()]


def _service(db_path) -> PlacementService:
    service = PlacementService.__new__(PlacementService)
    service._db_path = db_path
    service._bot = None
    service._refresh_lineup_post = AsyncMock()
    service._grant_roles = AsyncMock()
    service._revoke_roles = AsyncMock()
    return service


def _guild():
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=MagicMock())
    return guild


async def _move(service, to_division, team, from_division=PRO):
    return await service.move_driver(
        driver_profile_id=PROFILE_ID, season_id=SEASON_ID,
        from_division_id=from_division, to_division_id=to_division, team_name=team,
        acting_user_id=1, acting_user_name="Manager", guild=_guild(), discord_user_id="4242",
    )


async def test_a_full_time_driver_is_moved_to_reserve_in_one_step(db_path):
    await _seat(db_path, PRO, "Alpha")
    service = _service(db_path)

    result = await _move(service, PRO, "Reserve")

    assert await _where(db_path) == [(PRO, "Reserve")]
    assert result["from_team"] == "Alpha" and result["to_team"] == "Reserve"
    service._revoke_roles.assert_awaited_once()
    assert service._revoke_roles.await_args.args[1:] == (ROLES["Alpha"],)
    assert service._grant_roles.await_args.args[1:] == (1001, ROLES["Reserve"])
    service._refresh_lineup_post.assert_awaited_once()


async def test_a_driver_is_promoted_to_another_division(db_path):
    await _seat(db_path, AM, "Bravo")
    service = _service(db_path)

    await _move(service, PRO, "Bravo", from_division=AM)

    assert await _where(db_path) == [(PRO, "Bravo")]
    # The division role swaps; the team role is kept, the driver sitting in Bravo still.
    assert service._revoke_roles.await_args.args[1:] == (1002,)
    assert service._grant_roles.await_args.args[1:] == (1001, ROLES["Bravo"])
    assert [c.args[1] for c in service._refresh_lineup_post.await_args_list] == [AM, PRO]


async def test_the_seat_left_is_freed(db_path):
    await _seat(db_path, PRO, "Alpha")

    await _move(_service(db_path), PRO, "Bravo")

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM team_seats ts JOIN team_instances ti "
            "ON ti.id = ts.team_instance_id WHERE ti.name = 'Alpha' AND ts.driver_profile_id IS NOT NULL"
        )
        assert (await cursor.fetchone())["n"] == 0


async def test_an_uncommitted_placement_is_refused(db_path):
    await _seat(db_path, PRO, "Alpha", committed=0)

    with pytest.raises(ValueError, match="not yet confirmed"):
        await _move(_service(db_path), PRO, "Reserve")

    assert await _where(db_path) == [(PRO, "Alpha")]


async def test_a_move_into_a_division_already_held_is_refused(db_path):
    await _seat(db_path, PRO, "Alpha")
    await _seat(db_path, AM, "Alpha")

    with pytest.raises(ValueError, match="already holds a seat"):
        await _move(_service(db_path), AM, "Bravo")


async def test_a_move_into_the_team_already_sat_in_is_refused(db_path):
    await _seat(db_path, PRO, "Alpha")

    with pytest.raises(ValueError, match="already sits in"):
        await _move(_service(db_path), PRO, "Alpha")


async def test_a_move_into_a_full_team_is_refused_and_changes_nothing(db_path):
    await _seat(db_path, PRO, "Alpha")
    async with get_connection(db_path) as db:
        for profile_id in (6, 7):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (?, ?, 'ASSIGNED')",
                (profile_id, str(profile_id)),
            )
        await db.commit()
    await _seat(db_path, PRO, "Bravo", 6)
    await _seat(db_path, PRO, "Bravo", 7)

    with pytest.raises(ValueError, match="no available seats"):
        await _move(_service(db_path), PRO, "Bravo")

    assert await _where(db_path) == [(PRO, "Alpha")]


@pytest.mark.parametrize("stage_name", ["PLACEMENTS", "PENDING_COMPLETION"])
async def test_the_command_is_refused_outside_the_ongoing_stages(stage_name):
    from cogs.driver_cog import DriverCog
    from models.season import SeasonStage
    from tests.support.undecorate import undecorate

    cog = DriverCog.__new__(DriverCog)
    cog.bot = MagicMock()
    # Any account names the driver (issue #243); these tests name the current one.
    cog.bot.driver_service.current_account = AsyncMock(side_effect=lambda a: str(a))
    cog.bot.season_service.get_confirmed_season = AsyncMock(
        return_value=SimpleNamespace(id=1, stage=SeasonStage(stage_name))
    )
    cog.bot.placement_service.move_driver = AsyncMock()
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    await undecorate(DriverCog.move)(cog, interaction, MagicMock(), "Pro", "Reserve")

    assert "only while the season is ongoing" in interaction.followup.send.await_args.args[0]
    cog.bot.placement_service.move_driver.assert_not_awaited()


async def test_a_move_for_a_profile_that_does_not_exist_is_refused(db_path):
    service = _service(db_path)

    with pytest.raises(ValueError, match="Driver profile not found"):
        await service.move_driver(
            driver_profile_id=999, season_id=SEASON_ID,
            from_division_id=PRO, to_division_id=PRO, team_name="Reserve",
            acting_user_id=1, acting_user_name="Manager", guild=_guild(),
            discord_user_id="4242",
        )


async def test_a_move_from_a_division_the_driver_does_not_sit_in_is_refused(db_path):
    await _seat(db_path, AM, "Alpha")

    with pytest.raises(ValueError, match="holds no seat in the division they are moved from"):
        await _move(_service(db_path), PRO, "Reserve", from_division=PRO)


async def test_a_member_not_cached_is_fetched_to_swap_their_roles(db_path):
    await _seat(db_path, PRO, "Alpha")
    service = _service(db_path)
    member = MagicMock()
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=None)
    guild.fetch_member = AsyncMock(return_value=member)

    await service.move_driver(
        driver_profile_id=PROFILE_ID, season_id=SEASON_ID,
        from_division_id=PRO, to_division_id=PRO, team_name="Reserve",
        acting_user_id=1, acting_user_name="Manager", guild=guild, discord_user_id="4242",
    )

    guild.fetch_member.assert_awaited_once_with(4242)
    assert service._grant_roles.await_args.args[0] is member


async def test_a_member_who_left_the_server_is_moved_without_roles(db_path):
    """The move is the league's record; a member Discord cannot find has no roles to swap."""
    import discord

    await _seat(db_path, PRO, "Alpha")
    service = _service(db_path)
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=None)
    guild.fetch_member = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Member")
    )

    await service.move_driver(
        driver_profile_id=PROFILE_ID, season_id=SEASON_ID,
        from_division_id=PRO, to_division_id=PRO, team_name="Reserve",
        acting_user_id=1, acting_user_name="Manager", guild=guild, discord_user_id="4242",
    )

    assert await _where(db_path) == [(PRO, "Reserve")]
    service._grant_roles.assert_not_awaited()
    service._revoke_roles.assert_not_awaited()
    service._refresh_lineup_post.assert_awaited_once()


# ── A team's role repointed: its seated drivers follow ────────────────────────────


def _role_guild(members: dict):
    guild = MagicMock()
    guild.get_member = MagicMock(side_effect=lambda uid: members.get(uid))
    guild.fetch_member = AsyncMock(side_effect=lambda uid: members.get(uid))
    return guild


async def test_every_confirmed_driver_of_the_team_has_the_old_role_swapped_for_the_new(db_path):
    """Issue #220: a team's role is repointed in any stage, and its drivers follow."""
    await _seat(db_path, PRO, "Alpha")
    service = _service(db_path)
    member = MagicMock()

    reached = await service.swap_team_role(
        "Alpha", ROLES["Alpha"], 777, _role_guild({4242: member})
    )

    assert reached == 1
    service._revoke_roles.assert_awaited_once_with(member, ROLES["Alpha"])
    service._grant_roles.assert_awaited_once_with(member, 777)


async def test_an_unconfirmed_driver_of_the_team_is_left_alone(db_path):
    """They hold no role until placements are confirmed."""
    await _seat(db_path, PRO, "Alpha", committed=0)
    service = _service(db_path)

    assert await service.swap_team_role(
        "Alpha", ROLES["Alpha"], 777, _role_guild({4242: MagicMock()})
    ) == 0
    service._grant_roles.assert_not_awaited()


async def test_a_role_cleared_is_taken_and_nothing_granted(db_path):
    await _seat(db_path, PRO, "Reserve")
    service = _service(db_path)

    await service.swap_team_role(
        "Reserve", ROLES["Reserve"], None, _role_guild({4242: MagicMock()})
    )

    service._revoke_roles.assert_awaited_once()
    service._grant_roles.assert_not_awaited()


async def test_the_same_role_or_no_guild_changes_nothing(db_path):
    await _seat(db_path, PRO, "Alpha")
    service = _service(db_path)

    assert await service.swap_team_role("Alpha", 5, 5, _role_guild({})) == 0
    assert await service.swap_team_role("Alpha", 5, 6, None) == 0
