"""`/driver reject` — turning down an approved driver who has not been placed (issue #220).

Available where a window's drivers are settled: in Placements and in Ongoing, placements. The
driver returns to Not Signed Up and loses the driver role; their signup stays with the season.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.cogs.driver_cog import DriverCog  # noqa: E402
from leaguebot.core.models.driver_profile import DriverState  # noqa: E402
from leaguebot.core.models.season import SeasonStage  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 22100
ROLE_ID = 902


def _cog(stage: SeasonStage, state: DriverState = DriverState.UNASSIGNED) -> DriverCog:
    cog = DriverCog.__new__(DriverCog)
    cog.bot = MagicMock()
    # Any account names the driver (issue #243); these tests name the current one.
    cog.bot.driver_service.current_account = AsyncMock(side_effect=lambda a: str(a))
    cog.bot.season_service.get_setup_or_active_season = AsyncMock(
        return_value=SimpleNamespace(id=1, stage=stage)
    )
    cog.bot.driver_service.get_profile = AsyncMock(
        return_value=SimpleNamespace(id=5, current_state=state)
    )
    cog.bot.driver_service.transition = AsyncMock()
    cog.bot.signup_module_service.withdraw_approval = AsyncMock()
    cog.bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(driver_role_id=ROLE_ID)
    )
    cog.bot.output_router.post_log = AsyncMock()
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.guild.get_role = MagicMock(return_value=SimpleNamespace(id=ROLE_ID))
    return interaction


def _member():
    member = MagicMock()
    member.id = 4242
    member.display_name = "Racer"
    member.remove_roles = AsyncMock()
    return member


@pytest.mark.parametrize("stage", [SeasonStage.PLACEMENTS, SeasonStage.ONGOING_PLACEMENTS])
async def test_an_unassigned_driver_is_turned_down_and_loses_the_driver_role(stage):
    cog = _cog(stage)
    member = _member()

    await undecorate(DriverCog.reject)(cog, _interaction(), member)

    cog.bot.driver_service.transition.assert_awaited_once_with(
        "4242", DriverState.NOT_SIGNED_UP
    )
    member.remove_roles.assert_awaited_once()


@pytest.mark.parametrize(
    "stage", [SeasonStage.CONFIGURATION, SeasonStage.SIGNUPS, SeasonStage.ONGOING]
)
async def test_the_command_is_refused_outside_the_placing_stages(stage):
    cog = _cog(stage)
    interaction = _interaction()

    await undecorate(DriverCog.reject)(cog, interaction, _member())

    assert "available only while the season is in placements" in (
        interaction.followup.send.await_args.args[0]
    )
    cog.bot.driver_service.transition.assert_not_awaited()


@pytest.mark.parametrize(
    "state", [DriverState.ASSIGNED, DriverState.PENDING_ADMIN_APPROVAL, DriverState.NOT_SIGNED_UP]
)
async def test_only_an_unassigned_driver_is_turned_down(state):
    cog = _cog(SeasonStage.PLACEMENTS, state)
    interaction = _interaction()

    await undecorate(DriverCog.reject)(cog, interaction, _member())

    assert "not an Unassigned driver" in interaction.followup.send.await_args.args[0]
    cog.bot.driver_service.transition.assert_not_awaited()


async def test_turning_a_driver_down_withdraws_their_signups_approval():
    """Issue #243: the signup was rejected in the end, and outranks nothing any more."""
    cog = _cog(SeasonStage.PLACEMENTS)

    await undecorate(DriverCog.reject)(cog, _interaction(), _member())

    cog.bot.signup_module_service.withdraw_approval.assert_awaited_once_with(
        cog.bot.driver_service.get_profile.return_value.id
    )
