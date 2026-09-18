"""Committed and uncommitted placements (issue #220).

A placement made with `/driver assign` is uncommitted: it stands outside the championship,
granting no role and posting no lineup, until placements are confirmed. Mid-season, the assign
and unassign commands are kept to uncommitted drivers, a committed one being moved or released
instead. A placement written without saying takes the default its season implies.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection  # noqa: E402
from services.placement_service import PlacementService  # noqa: E402
from tests.unit.test_placement_assign import (  # noqa: E402
    DIVISION_ID,
    PROFILE_ID,
    SEASON_ID,
    SERVER_ID,
    _make_db,
    _service,
)


def _guild():
    member = MagicMock()
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=member)
    guild.fetch_member = AsyncMock(return_value=member)
    return guild


async def _assign(service, **kwargs):
    service._refresh_lineup_post = AsyncMock(return_value=None)
    service._grant_roles = AsyncMock(return_value=None)
    service.get_team_role_config = AsyncMock(return_value=None)
    with patch.object(PlacementService, "_guard_test_mode", new=AsyncMock(return_value=None)):
        return await service.assign_driver(
            server_id=SERVER_ID,
            driver_profile_id=PROFILE_ID,
            division_id=DIVISION_ID,
            team_name="Alpha",
            season_id=SEASON_ID,
            acting_user_id=1,
            acting_user_name="Manager",
            guild=_guild(),
            discord_user_id="4242",
            **kwargs,
        )


async def _unassign(service, **kwargs):
    service._refresh_lineup_post = AsyncMock(return_value=None)
    service._revoke_roles = AsyncMock(return_value=None)
    service.get_team_role_config = AsyncMock(return_value=None)
    return await service.unassign_driver(
        server_id=SERVER_ID,
        driver_profile_id=PROFILE_ID,
        division_id=DIVISION_ID,
        season_id=SEASON_ID,
        acting_user_id=1,
        acting_user_name="Manager",
        guild=_guild(),
        discord_user_id="4242",
        **kwargs,
    )


async def _committed(db_path) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT committed FROM driver_season_assignments WHERE driver_profile_id = ?",
            (PROFILE_ID,),
        )
        return (await cursor.fetchone())["committed"]


async def _set_season_status(db_path, status: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute("UPDATE seasons SET status = ? WHERE id = ?", (status, SEASON_ID))
        await db.commit()


# ── Assigning ──────────────────────────────────────────────────────────────────────


async def test_an_uncommitted_placement_grants_no_role_and_posts_no_lineup(tmp_path):
    db_path = await _make_db(tmp_path)
    service = _service(db_path)

    result = await _assign(service, committed=False)

    assert result["committed"] is False
    assert await _committed(db_path) == 0
    service._grant_roles.assert_not_awaited()
    service._refresh_lineup_post.assert_not_awaited()


async def test_a_placement_in_a_confirmed_season_defaults_to_committed(tmp_path):
    db_path = await _make_db(tmp_path)  # the fixture season is ACTIVE
    service = _service(db_path)

    result = await _assign(service)

    assert result["committed"] is True
    service._grant_roles.assert_awaited_once()
    service._refresh_lineup_post.assert_awaited_once()


async def test_a_placement_in_a_season_in_setup_defaults_to_uncommitted(tmp_path):
    db_path = await _make_db(tmp_path)
    await _set_season_status(db_path, "SETUP")

    await _assign(_service(db_path))

    assert await _committed(db_path) == 0


async def test_a_committed_driver_is_refused_where_only_uncommitted_ones_may_be_placed(tmp_path):
    db_path = await _make_db(tmp_path)
    service = _service(db_path)
    await _assign(service)  # committed, in the ACTIVE fixture season
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (12, ?, 'Division 2', 2, 556)",
            (SEASON_ID,),
        )
        await db.commit()

    with pytest.raises(ValueError, match="confirmed placement"):
        with patch.object(PlacementService, "_guard_test_mode", new=AsyncMock(return_value=None)):
            await service.assign_driver(
                server_id=SERVER_ID, driver_profile_id=PROFILE_ID, division_id=12,
                team_name="Alpha", season_id=SEASON_ID, acting_user_id=1,
                acting_user_name="Manager", guild=_guild(), discord_user_id="4242",
                committed=False, uncommitted_only=True,
            )


# ── Unassigning ────────────────────────────────────────────────────────────────────


async def test_removing_an_uncommitted_placement_revokes_nothing_and_posts_nothing(tmp_path):
    db_path = await _make_db(tmp_path)
    service = _service(db_path)
    await _assign(service, committed=False)

    await _unassign(service)

    service._revoke_roles.assert_not_awaited()
    service._refresh_lineup_post.assert_not_awaited()


async def test_removing_a_committed_placement_revokes_its_roles(tmp_path):
    db_path = await _make_db(tmp_path)
    service = _service(db_path)
    await _assign(service)

    await _unassign(service)

    service._revoke_roles.assert_awaited_once()
    service._refresh_lineup_post.assert_awaited_once()


async def test_a_committed_placement_is_refused_where_only_uncommitted_ones_may_be_removed(tmp_path):
    db_path = await _make_db(tmp_path)
    service = _service(db_path)
    await _assign(service)

    with pytest.raises(ValueError, match="/driver move"):
        await _unassign(service, uncommitted_only=True)

    assert await _committed(db_path) == 1


# ── The commands ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("command", ["assign", "unassign"])
@pytest.mark.parametrize("stage_name", ["CONFIGURATION", "SIGNUPS", "ONGOING", "PENDING_COMPLETION"])
async def test_the_placement_commands_are_refused_outside_the_placing_stages(command, stage_name):
    from types import SimpleNamespace

    from cogs.driver_cog import DriverCog
    from models.season import SeasonStage
    from tests.support.undecorate import undecorate

    cog = DriverCog.__new__(DriverCog)
    cog.bot = MagicMock()
    # Any account names the driver (issue #243); these tests name the current one.
    cog.bot.driver_service.current_account = AsyncMock(side_effect=lambda a: str(a))
    cog.bot.season_service.get_setup_or_active_season = AsyncMock(
        return_value=SimpleNamespace(id=1, stage=SeasonStage(stage_name))
    )
    cog.bot.placement_service.assign_driver = AsyncMock()
    cog.bot.placement_service.unassign_driver = AsyncMock()
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    args = (MagicMock(), "Division 1", "Alpha") if command == "assign" else (MagicMock(), "Division 1")
    await undecorate(getattr(DriverCog, command))(cog, interaction, *args)

    assert "available only while the season is in placements" in (
        interaction.followup.send.await_args.args[0]
    )
    cog.bot.placement_service.assign_driver.assert_not_awaited()
    cog.bot.placement_service.unassign_driver.assert_not_awaited()


async def test_a_committed_placement_of_a_member_who_left_grants_nothing(tmp_path):
    """The placement is recorded; a member Discord cannot find has no role to be granted."""
    import discord

    db_path = await _make_db(tmp_path)
    service = _service(db_path)
    service._refresh_lineup_post = AsyncMock(return_value=None)
    service._grant_roles = AsyncMock(return_value=None)
    service.get_team_role_config = AsyncMock(return_value=None)
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=None)
    guild.fetch_member = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Member")
    )

    with patch.object(PlacementService, "_guard_test_mode", new=AsyncMock(return_value=None)):
        result = await service.assign_driver(
            server_id=SERVER_ID, driver_profile_id=PROFILE_ID, division_id=DIVISION_ID,
            team_name="Alpha", season_id=SEASON_ID, acting_user_id=1,
            acting_user_name="Manager", guild=guild, discord_user_id="4242",
        )

    assert result["committed"] is True
    service._grant_roles.assert_not_awaited()
    service._refresh_lineup_post.assert_awaited_once()
