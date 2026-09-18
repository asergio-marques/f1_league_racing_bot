"""`/driver release` — a committed driver released from one division (issue #220).

The driver keeps every other seat they hold. The division's role is revoked, the team's only
where no other seat maps to it, and the division's lineup is posted again. A driver's only seat
is not released — they are sacked or moved instead — and an uncommitted placement is unassigned.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from tests.unit.test_driver_move import (  # noqa: E402
    AM,
    PRO,
    PROFILE_ID,
    SEASON_ID,
    SERVER_ID,
    _guild,
    _seat,
    _service,
    _where,
    db_path,  # noqa: F401 — the fixture
)


async def _release(service, division_id):
    return await service.release_driver(
        server_id=SERVER_ID, driver_profile_id=PROFILE_ID, division_id=division_id,
        season_id=SEASON_ID, acting_user_id=1, acting_user_name="Manager", guild=_guild(),
        discord_user_id="4242",
    )


async def test_a_driver_is_released_from_one_division_and_keeps_the_other(db_path):
    await _seat(db_path, PRO, "Alpha")
    await _seat(db_path, AM, "Bravo")
    service = _service(db_path)
    service.get_team_role_config = AsyncMock(return_value=None)

    result = await _release(service, AM)

    assert result["division_name"] == "Am"
    assert await _where(db_path) == [(PRO, "Alpha")]
    service._revoke_roles.assert_awaited_once()
    service._refresh_lineup_post.assert_awaited_once()


async def test_a_drivers_only_seat_is_not_released(db_path):
    await _seat(db_path, PRO, "Alpha")

    with pytest.raises(ValueError, match="only seat"):
        await _release(_service(db_path), PRO)

    assert await _where(db_path) == [(PRO, "Alpha")]


async def test_an_unconfirmed_seat_elsewhere_does_not_make_a_confirmed_one_releasable(db_path):
    """Releasing it would leave the driver out of the championship; that is a move."""
    await _seat(db_path, PRO, "Alpha")
    await _seat(db_path, AM, "Bravo", committed=0)

    with pytest.raises(ValueError, match="only seat"):
        await _release(_service(db_path), PRO)

    assert sorted(await _where(db_path)) == [(PRO, "Alpha"), (AM, "Bravo")]


async def test_an_uncommitted_placement_is_not_released(db_path):
    await _seat(db_path, PRO, "Alpha")
    await _seat(db_path, AM, "Bravo", committed=0)

    with pytest.raises(ValueError, match="/driver unassign"):
        await _release(_service(db_path), AM)


async def test_a_division_the_driver_does_not_sit_in_is_refused(db_path):
    await _seat(db_path, PRO, "Alpha")

    with pytest.raises(ValueError, match="no seat in that division"):
        await _release(_service(db_path), AM)


@pytest.mark.parametrize("stage_name", ["PLACEMENTS", "PENDING_COMPLETION"])
async def test_the_command_is_refused_outside_the_ongoing_stages(stage_name):
    from cogs.driver_cog import DriverCog
    from models.season import SeasonStage
    from tests.support.undecorate import undecorate

    cog = DriverCog.__new__(DriverCog)
    cog.bot = MagicMock()
    cog.bot.driver_service.current_account = AsyncMock(side_effect=lambda a: str(a))
    cog.bot.season_service.get_confirmed_season = AsyncMock(
        return_value=SimpleNamespace(id=1, stage=SeasonStage(stage_name))
    )
    cog.bot.placement_service.release_driver = AsyncMock()
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    await undecorate(DriverCog.release)(cog, interaction, MagicMock(), "Pro")

    assert "only while the season is ongoing" in interaction.followup.send.await_args.args[0]
    cog.bot.placement_service.release_driver.assert_not_awaited()
