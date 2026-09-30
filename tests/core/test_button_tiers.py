"""Every button the bot posts asks the tier its action belongs to.

The tiers govern the action, not only the command. Four checks are pinned here, none of which
had any test at all before issue #116 — which is how three of them came to be wrong.

**The signup review panel** is posted publicly into the driver's own signup channel, and the
driver can read it. Its check is the only thing between a driver and approving their own
signup.

**The penalty and appeals reviews** are entirely button-driven. Their gate asked for the
interaction role alone, so a league admin who did not also hold that role was refused all
thirteen buttons of a round they were entitled to judge.

**The points-configuration select** asked nothing whatever, and is posted publicly into the
submission and amendment channels from three call sites. Anyone who could read one of those
channels could decide which configuration scored the session, and the first press won.

**The season-approval button** asked for Discord's Administrator permission, which stopped
being a tier when both tiers became roles.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.models.server_config import ServerConfig

SERVER_ID = 4242
MANAGER_ROLE = 222
ADMIN_ROLE = 444


def _config(*, admin_role: int | None = ADMIN_ROLE) -> ServerConfig:
    return ServerConfig(
        server_id=SERVER_ID,
        interaction_role_id=MANAGER_ROLE,
        league_admin_role_id=admin_role,
        interaction_channel_id=111,
        log_channel_id=333,
    )


def _role(role_id: int) -> MagicMock:
    role = MagicMock()
    role.id = role_id
    role.name = f"role-{role_id}"
    return role


def _member(*roles: int) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = 7
    member.roles = [_role(r) for r in roles]
    member.guild_permissions = MagicMock()
    member.guild_permissions.administrator = True  # never a route to a tier
    return member


def _interaction(member: MagicMock) -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.guild.id = SERVER_ID
    interaction.user = member
    interaction.client.output_router.post_log = AsyncMock(return_value=None)
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    return interaction


# ── The points-configuration select ───────────────────────────────────────


async def _press_config_select(member, config):
    from leaguebot.results.services.result_submission_service import _ConfigSelectView

    view = _ConfigSelectView(["Standard", "Sprint"], config)
    interaction = _interaction(member)
    await view.children[0].callback(interaction)
    return view, interaction


@pytest.mark.parametrize("roles", [(MANAGER_ROLE,), (ADMIN_ROLE,)], ids=["manager", "admin"])
async def test_either_tier_may_choose_the_points_configuration(roles):
    view, _ = await _press_config_select(_member(*roles), _config())
    assert view.selected == "Standard"


async def test_a_bystander_may_not_choose_the_points_configuration():
    """The view is posted publicly, so a bystander is exactly who this stops."""
    view, interaction = await _press_config_select(_member(), _config())

    assert view.selected is None
    assert "league managers" in interaction.response.send_message.call_args.args[0]


async def test_the_points_configuration_select_refuses_when_it_cannot_read_the_config():
    """A view that cannot tell who is pressing must not guess permissively."""
    view, _ = await _press_config_select(_member(MANAGER_ROLE), None)
    assert view.selected is None


# Every outcome of the choice is recorded (#482). The view is told which session of which round
# it chooses for, and names the button pressed and that session in its lines.

_SESSION = "the Feature Race of round 3 (Pro)"
_NOT_YET_RECORDED = (
    "#482: the points-configuration choice is not told its session and records nothing"
)


async def _choose_as(member, config):
    from leaguebot.results.services.result_submission_service import _ConfigSelectView

    member.display_name = "Alex"
    view = _ConfigSelectView(["Standard", "Sprint"], config, session=_SESSION)
    interaction = _interaction(member)
    await view.children[0].callback(interaction)
    return view, [call.args[0] for call in interaction.client.output_router.post_log.await_args_list]


@pytest.mark.xfail(strict=True, reason=_NOT_YET_RECORDED)
@pytest.mark.parametrize("config", ["bystander", "unreadable"])
async def test_a_refused_choice_of_points_configuration_is_recorded(config):
    """Alex presses “Standard” on the points-configuration choice for the Feature Race of
    round 3 (Pro), either holding no league tier or where the server configuration cannot be
    read. The reply is today's, nothing is chosen, and exactly one line records the refusal,
    naming the button, the session, Alex and the reason."""
    member = _member() if config == "bystander" else _member(MANAGER_ROLE)
    view, lines = await _choose_as(member, _config() if config == "bystander" else None)

    assert view.selected is None
    (line,) = lines
    assert line.startswith("⛔ the “Standard” button"), line
    assert _SESSION in line
    assert line.endswith(
        " refused for Alex (<@7>) — Only league managers can choose the points configuration."
    ), line


@pytest.mark.xfail(strict=True, reason=_NOT_YET_RECORDED)
async def test_a_choice_of_points_configuration_writes_one_line():
    """The league manager Alex presses “Standard” on the points-configuration choice for the
    Feature Race of round 3 (Pro). The configuration is chosen, and exactly one line records
    it, naming Alex, the configuration and the session."""
    view, lines = await _choose_as(_member(MANAGER_ROLE), _config())

    assert view.selected == "Standard"
    (line,) = lines
    assert not line.startswith("⛔"), line
    assert "Alex (<@7>)" in line and "Standard" in line and _SESSION in line, line


# ── The signup review panel ───────────────────────────────────────────────


async def _may_review(member, config):
    from leaguebot.signup.cogs.admin_review_cog import _may_review_signup

    interaction = _interaction(member)
    interaction.client.config_service.get_server_config = AsyncMock(return_value=config)
    return await _may_review_signup(interaction)


@pytest.mark.parametrize("roles", [(MANAGER_ROLE,), (ADMIN_ROLE,)], ids=["manager", "admin"])
async def test_either_tier_may_action_a_signup_review(roles):
    assert await _may_review(_member(*roles), _config()) is True


async def test_the_driver_may_not_action_their_own_signup_review():
    """The panel is posted in the driver's own channel, which they can read."""
    assert await _may_review(_member(), _config()) is False


async def test_a_signup_review_refuses_when_the_config_cannot_be_read():
    from leaguebot.signup.cogs.admin_review_cog import _may_review_signup

    interaction = _interaction(_member(MANAGER_ROLE))
    interaction.client.config_service.get_server_config = AsyncMock(
        side_effect=RuntimeError("database is locked")
    )

    assert await _may_review_signup(interaction) is False


# ── The penalty and appeals reviews ───────────────────────────────────────


async def _is_lm(member, config):
    from leaguebot.results.services.penalty_wizard import _is_league_manager

    bot = MagicMock()
    bot.config_service.get_server_config = AsyncMock(return_value=config)
    return await _is_league_manager(_interaction(member), ":memory:", bot)


async def test_a_league_admin_may_press_a_penalty_review_button():
    """Refused before issue #116: the gate read the interaction role alone."""
    assert await _is_lm(_member(ADMIN_ROLE), _config()) is True


async def test_a_league_manager_may_press_a_penalty_review_button():
    assert await _is_lm(_member(MANAGER_ROLE), _config()) is True


async def test_a_bystander_may_not_press_a_penalty_review_button():
    assert await _is_lm(_member(), _config()) is False
