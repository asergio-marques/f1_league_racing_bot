"""`/driver move` and `/driver release` — the command bodies, rung by rung (issue #220).

The services beneath them are tested against a real database in `test_driver_move.py` and
`test_driver_release.py`, which also pin the stage refusal. What those leave unexercised is the
rest of each command's ladder: a division that does not resolve, a member with no profile, a
refusal the service raises, and the reply and log line of a move or release that goes through.
The two commands copy that ladder rather than share it, so each rung is tested for both.

The reject command's own ladder is in `test_driver_reject.py`; the one branch it leaves — a
signed-up role Discord will not take back — is here, because a failed role removal must not
undo a rejection already written.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.driver_cog import DriverCog  # noqa: E402
from models.driver_profile import DriverState  # noqa: E402
from models.season import SeasonStage  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 22130
SEASON_ID = 7
PRO = (21, "Pro")
ACADEMY = (22, "Academy")
PROFILE_ID = 55
USER_ID = 4242


def _cog(
    *,
    stage: SeasonStage = SeasonStage.ONGOING,
    divisions: dict[str, tuple[int, str]] | None = None,
    profile: object | None = SimpleNamespace(id=PROFILE_ID),
) -> DriverCog:
    known = {"Pro": PRO, "Academy": ACADEMY} if divisions is None else divisions
    cog = DriverCog.__new__(DriverCog)
    cog.bot = MagicMock()
    # Any account names the driver (issue #243); these tests name the current one.
    cog.bot.driver_service.current_account = AsyncMock(side_effect=lambda a: str(a))
    cog.bot.season_service.get_confirmed_season = AsyncMock(
        return_value=SimpleNamespace(id=SEASON_ID, stage=stage)
    )
    cog.bot.placement_service.resolve_division = AsyncMock(
        side_effect=lambda season_id, name: known.get(name)
    )
    cog.bot.placement_service.move_driver = AsyncMock(
        return_value={
            "from_division": "Pro",
            "to_division": "Academy",
            "from_team": "Alpha",
            "to_team": "Reserve",
        }
    )
    cog.bot.placement_service.release_driver = AsyncMock(
        return_value={"division_name": "Academy"}
    )
    cog.bot.driver_service.get_profile = AsyncMock(return_value=profile)
    cog.bot.output_router.post_log = AsyncMock()
    return cog


def _interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = 1
    interaction.user.display_name = "Manager"
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _member() -> MagicMock:
    member = MagicMock()
    member.id = USER_ID
    member.display_name = "Racer"
    member.remove_roles = AsyncMock()
    return member


def _reply(interaction: MagicMock) -> str:
    return interaction.followup.send.await_args.args[0]


async def _move(cog, interaction, from_division="Pro", team="Reserve", to_division=None):
    await undecorate(DriverCog.move)(
        cog, interaction, _member(), from_division, team, to_division
    )


async def _release(cog, interaction, division="Academy"):
    await undecorate(DriverCog.release)(cog, interaction, _member(), division)


# ── /driver move ──────────────────────────────────────────────────────────────────────


async def test_a_move_with_no_confirmed_season_is_refused():
    cog = _cog()
    cog.bot.season_service.get_confirmed_season = AsyncMock(return_value=None)
    interaction = _interaction()

    await _move(cog, interaction)

    assert "only while the season is ongoing" in _reply(interaction)
    cog.bot.placement_service.move_driver.assert_not_awaited()


async def test_a_move_from_an_unknown_division_is_refused():
    cog = _cog()
    interaction = _interaction()

    await _move(cog, interaction, from_division="Nowhere")

    assert "Division **Nowhere** not found" in _reply(interaction)
    cog.bot.placement_service.move_driver.assert_not_awaited()


async def test_a_move_into_an_unknown_division_is_refused():
    cog = _cog()
    interaction = _interaction()

    await _move(cog, interaction, to_division="Nowhere")

    assert "Division **Nowhere** not found" in _reply(interaction)
    cog.bot.placement_service.move_driver.assert_not_awaited()


async def test_a_move_of_a_member_with_no_profile_is_refused():
    cog = _cog(profile=None)
    interaction = _interaction()

    await _move(cog, interaction)

    assert "No driver profile found for **Racer**" in _reply(interaction)
    cog.bot.placement_service.move_driver.assert_not_awaited()


async def test_a_refusal_the_service_raises_is_relayed_and_nothing_is_logged():
    cog = _cog()
    cog.bot.placement_service.move_driver = AsyncMock(
        side_effect=ValueError("**Reserve** in this division has no available seats.")
    )
    interaction = _interaction()

    await _move(cog, interaction)

    assert _reply(interaction) == "⛔ **Reserve** in this division has no available seats."
    cog.bot.output_router.post_log.assert_not_awaited()


async def test_a_move_within_one_division_passes_that_division_both_ways():
    cog = _cog()

    await _move(cog, _interaction(), from_division="Pro", team="Reserve")

    kwargs = cog.bot.placement_service.move_driver.await_args.kwargs
    assert (kwargs["from_division_id"], kwargs["to_division_id"]) == (PRO[0], PRO[0])
    assert kwargs["season_id"] == SEASON_ID
    assert kwargs["driver_profile_id"] == PROFILE_ID
    assert kwargs["discord_user_id"] == str(USER_ID)


@pytest.mark.parametrize(
    "stage", [SeasonStage.ONGOING, SeasonStage.ONGOING_SIGNUPS, SeasonStage.ONGOING_PLACEMENTS]
)
async def test_a_move_between_divisions_is_confirmed_and_logged(stage):
    cog = _cog(stage=stage)
    interaction = _interaction()

    await _move(cog, interaction, from_division="Pro", team="Reserve", to_division="Academy")

    kwargs = cog.bot.placement_service.move_driver.await_args.kwargs
    assert (kwargs["from_division_id"], kwargs["to_division_id"]) == (PRO[0], ACADEMY[0])
    assert _reply(interaction) == (
        "✅ Moved **Racer** from **Alpha** in **Pro** to **Reserve** in **Academy**."
    )
    log_line = cog.bot.output_router.post_log.await_args.args[0]
    assert "/driver move | Success" in log_line
    assert "from: Alpha, Pro" in log_line
    assert "to: Reserve, Academy" in log_line


# ── /driver release ───────────────────────────────────────────────────────────────────


async def test_a_release_with_no_confirmed_season_is_refused():
    cog = _cog()
    cog.bot.season_service.get_confirmed_season = AsyncMock(return_value=None)
    interaction = _interaction()

    await _release(cog, interaction)

    assert "only while the season is ongoing" in _reply(interaction)
    cog.bot.placement_service.release_driver.assert_not_awaited()


async def test_a_release_from_an_unknown_division_is_refused():
    cog = _cog()
    interaction = _interaction()

    await _release(cog, interaction, division="Nowhere")

    assert "Division **Nowhere** not found" in _reply(interaction)
    cog.bot.placement_service.release_driver.assert_not_awaited()


async def test_a_release_of_a_member_with_no_profile_is_refused():
    cog = _cog(profile=None)
    interaction = _interaction()

    await _release(cog, interaction)

    assert "No driver profile found for **Racer**" in _reply(interaction)
    cog.bot.placement_service.release_driver.assert_not_awaited()


async def test_a_release_the_service_refuses_is_relayed_and_nothing_is_logged():
    cog = _cog()
    cog.bot.placement_service.release_driver = AsyncMock(
        side_effect=ValueError("That is the driver's only seat. Sack or move them instead.")
    )
    interaction = _interaction()

    await _release(cog, interaction)

    assert _reply(interaction) == "⛔ That is the driver's only seat. Sack or move them instead."
    cog.bot.output_router.post_log.assert_not_awaited()


@pytest.mark.parametrize(
    "stage", [SeasonStage.ONGOING, SeasonStage.ONGOING_SIGNUPS, SeasonStage.ONGOING_PLACEMENTS]
)
async def test_a_release_is_confirmed_and_logged(stage):
    cog = _cog(stage=stage)
    interaction = _interaction()

    await _release(cog, interaction, division="Academy")

    kwargs = cog.bot.placement_service.release_driver.await_args.kwargs
    assert kwargs["division_id"] == ACADEMY[0]
    assert kwargs["season_id"] == SEASON_ID
    assert _reply(interaction) == "✅ Released **Racer** from **Academy**."
    log_line = cog.bot.output_router.post_log.await_args.args[0]
    assert "/driver release | Success" in log_line
    assert "division: Academy" in log_line


# ── /driver reject: a role Discord will not take back ─────────────────────────────────


async def test_a_rejection_stands_when_the_driver_role_cannot_be_removed():
    cog = DriverCog.__new__(DriverCog)
    cog.bot = MagicMock()
    # Any account names the driver (issue #243); these tests name the current one.
    cog.bot.driver_service.current_account = AsyncMock(side_effect=lambda a: str(a))
    cog.bot.season_service.get_setup_or_active_season = AsyncMock(
        return_value=SimpleNamespace(id=SEASON_ID, stage=SeasonStage.PLACEMENTS)
    )
    cog.bot.driver_service.get_profile = AsyncMock(
        return_value=SimpleNamespace(id=PROFILE_ID, current_state=DriverState.UNASSIGNED)
    )
    cog.bot.driver_service.transition = AsyncMock()
    cog.bot.signup_module_service.withdraw_approval = AsyncMock()
    cog.bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(driver_role_id=902)
    )
    cog.bot.output_router.post_log = AsyncMock()
    interaction = _interaction()
    interaction.guild.get_role = MagicMock(return_value=SimpleNamespace(id=902))
    member = _member()
    member.remove_roles = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(status=403, reason="Forbidden"), "missing")
    )

    await undecorate(DriverCog.reject)(cog, interaction, member)

    cog.bot.driver_service.transition.assert_awaited_once_with(
        str(USER_ID), DriverState.NOT_SIGNED_UP
    )
    assert _reply(interaction).startswith("✅ Turned down **Racer**")


# ── Any account names the driver (issue #243) ────────────────────────────────────────

CURRENT_ID = 5353


def _past_account_cog(current_member: MagicMock | None) -> tuple[DriverCog, MagicMock]:
    """A cog whose driver has moved from USER_ID to CURRENT_ID, and the guild behind it."""
    cog = _cog()
    cog.bot.driver_service.current_account = AsyncMock(return_value=str(CURRENT_ID))
    interaction = _interaction()
    interaction.guild.get_member = MagicMock(return_value=current_member)
    interaction.guild.fetch_member = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404), "gone")
    )
    return cog, interaction


async def test_a_command_given_a_past_account_acts_on_the_current_one():
    current = MagicMock(id=CURRENT_ID, display_name="Racer Now")
    cog, interaction = _past_account_cog(current)

    await _release(cog, interaction)

    interaction.guild.get_member.assert_called_once_with(CURRENT_ID)
    kwargs = cog.bot.placement_service.release_driver.await_args.kwargs
    assert kwargs["discord_user_id"] == str(CURRENT_ID)
    assert "Racer Now" in _reply(interaction)


async def test_a_past_account_whose_driver_has_left_the_server_is_refused():
    cog, interaction = _past_account_cog(None)

    await _move(cog, interaction)

    assert "past account" in _reply(interaction)
    assert f"<@{CURRENT_ID}>" in _reply(interaction)
    cog.bot.placement_service.move_driver.assert_not_awaited()
