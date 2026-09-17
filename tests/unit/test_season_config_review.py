"""`/season config-review` and the confirmation of a season's configuration (issue #220).

A season begins in Configuration. The configuration review checks everything that can be
checked before the season has divisions, and ends with a button that, once pressed, fixes the
configuration for the season and moves it on: to Waiting where the signup module is enabled,
to Placements where it is not or the season runs in test mode.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import (  # noqa: E402
    PendingConfig,
    SeasonCog,
    _ConfirmConfigurationView,
)
from models.season import InvalidStageTransition, SeasonStage  # noqa: E402
from models.server_config import ServerConfig  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 7
SEASON_ID = 31
REVIEWER = 4242
ADMIN_ROLE = 444


def _server_config(*, test_mode: bool = False) -> ServerConfig:
    config = ServerConfig(
        server_id=SERVER_ID,
        interaction_role_id=222,
        league_admin_role_id=ADMIN_ROLE,
        interaction_channel_id=111,
        log_channel_id=333,
    )
    config.test_mode_active = test_mode
    return config


def _signup_config(*, channel=1, base=2, complete=3):
    return SimpleNamespace(
        signup_channel_id=channel, base_role_id=base, signed_up_role_id=complete
    )


def _bot(
    *,
    signup=False,
    results=False,
    images=False,
    signup_config=None,
    test_mode=False,
    stage=SeasonStage.CONFIGURATION,
) -> MagicMock:
    bot = MagicMock()
    bot.db_path = ":memory:"
    bot.module_service.is_signup_enabled = AsyncMock(return_value=signup)
    bot.module_service.is_results_enabled = AsyncMock(return_value=results)
    bot.module_service.is_images_enabled = AsyncMock(return_value=images)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    bot.module_service.is_weather_enabled = AsyncMock(return_value=False)
    bot.signup_module_service.get_config = AsyncMock(
        return_value=signup_config if signup_config is not None else _signup_config()
    )
    bot.config_service.get_server_config = AsyncMock(
        return_value=_server_config(test_mode=test_mode)
    )
    bot.season_service.get_stage = AsyncMock(return_value=stage)
    bot.season_service.set_stage = AsyncMock()
    bot.output_router.post_log = AsyncMock()
    return bot


def _cog(bot: MagicMock) -> SeasonCog:
    cog = SeasonCog(bot)
    cog._pending[REVIEWER] = PendingConfig(
        server_id=SERVER_ID, season_id=SEASON_ID, season_number=4, game_edition=25
    )
    # The team names are checked by a helper of their own, pinned elsewhere.
    cog._team_name_problems = AsyncMock(return_value=[])
    return cog


def _interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = REVIEWER
    interaction.user.display_name = "Manager"
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


# ── What the configuration review checks ────────────────────────────────────────────


async def test_a_plain_configuration_has_no_faults():
    cog = _cog(_bot())
    assert await cog._configuration_faults(SERVER_ID, SEASON_ID) == []


async def test_the_signup_module_needs_its_channel_and_both_roles():
    bot = _bot(signup=True, signup_config=_signup_config(channel=None, base=None, complete=None))
    cog = _cog(bot)

    faults = await cog._configuration_faults(SERVER_ID, SEASON_ID)

    assert len(faults) == 3
    assert any("signup channel" in f for f in faults)
    assert any("base role" in f for f in faults)
    assert any("complete role" in f for f in faults)


async def test_a_disabled_signup_module_is_not_checked():
    bot = _bot(signup=False, signup_config=_signup_config(channel=None))
    assert await _cog(bot)._configuration_faults(SERVER_ID, SEASON_ID) == []


async def test_team_names_of_the_server_list_are_checked():
    cog = _cog(_bot())
    cog._team_name_problems = AsyncMock(return_value=["**!!!** reduces to nothing"])

    faults = await cog._configuration_faults(SERVER_ID, SEASON_ID)

    cog._team_name_problems.assert_awaited_once_with(SERVER_ID, None)
    assert faults == ["Team name: **!!!** reduces to nothing"]


async def test_the_results_module_needs_a_points_configuration(tmp_path):
    from db.database import run_migrations

    db_path = str(tmp_path / "config_review.db")
    await run_migrations(db_path)
    bot = _bot(results=True)
    bot.db_path = db_path
    cog = _cog(bot)
    cog._missing_points_config_problems = AsyncMock(return_value=["Ghost"])
    cog._points_ordering_problems = AsyncMock(return_value=["Standard: 2nd above 1st"])

    faults = await cog._configuration_faults(SERVER_ID, SEASON_ID)

    assert any("No points configuration is attached" in f for f in faults)
    assert any("**Ghost** does not exist" in f for f in faults)
    assert any("out of order" in f for f in faults)


async def test_the_image_module_faults_are_included():
    bot = _bot(images=True)
    cog = _cog(bot)
    cog._image_configuration_faults = AsyncMock(return_value=["Inkscape is not installed."])

    assert await cog._configuration_faults(SERVER_ID, SEASON_ID) == [
        "Inkscape is not installed."
    ]


# ── The command ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("stage", [SeasonStage.WAITING, SeasonStage.PLACEMENTS, None])
async def test_the_review_is_refused_outside_configuration(stage):
    bot = _bot(stage=stage)
    cog = _cog(bot)
    interaction = _interaction()

    await undecorate(SeasonCog.season_config_review)(cog, interaction)

    reply = interaction.response.send_message.await_args.args[0]
    assert "no season in configuration" in reply
    interaction.response.defer.assert_not_awaited()


# ── Confirming ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "signup, test_mode, expected",
    [
        (True, False, SeasonStage.WAITING),
        (False, False, SeasonStage.PLACEMENTS),
        (True, True, SeasonStage.PLACEMENTS),
    ],
)
async def test_confirming_moves_the_season_on(signup, test_mode, expected):
    bot = _bot(signup=signup, test_mode=test_mode)
    cog = _cog(bot)
    interaction = _interaction()

    await cog._do_confirm_configuration(interaction)

    bot.season_service.set_stage.assert_awaited_once_with(SEASON_ID, expected)
    assert "confirmed" in interaction.followup.send.await_args.args[0]


async def test_confirming_refuses_on_a_fault_found_afresh():
    bot = _bot(signup=True, signup_config=_signup_config(channel=None))
    cog = _cog(bot)
    interaction = _interaction()

    await cog._do_confirm_configuration(interaction)

    bot.season_service.set_stage.assert_not_awaited()
    assert "Nothing has been confirmed" in interaction.followup.send.await_args.args[0]


async def test_confirming_a_season_that_has_moved_on_confirms_nothing():
    bot = _bot()
    bot.season_service.set_stage = AsyncMock(side_effect=InvalidStageTransition("moved"))
    cog = _cog(bot)
    interaction = _interaction()

    await cog._do_confirm_configuration(interaction)

    assert "Nothing has been confirmed" in interaction.followup.send.await_args.args[0]
    bot.output_router.post_log.assert_not_awaited()


# ── The button ─────────────────────────────────────────────────────────────────────


def _member(user_id: int, league_admin: bool = False):
    import discord

    member = MagicMock(spec=discord.Member)
    member.id = user_id
    role = MagicMock()
    role.id = ADMIN_ROLE
    member.roles = [role] if league_admin else []
    return member


def _view():
    cog = MagicMock()
    cog._do_confirm_configuration = AsyncMock()
    cog.bot.db_path = "/nonexistent/nowhere.db"
    cog.bot.config_service.get_server_config = AsyncMock(return_value=_server_config())
    view = _ConfirmConfigurationView(cog, REVIEWER)
    view._server_id = SERVER_ID
    view._season_id = SEASON_ID
    return view, cog


async def test_the_reviewer_may_confirm():
    view, cog = _view()
    interaction = _interaction()
    interaction.user = _member(REVIEWER)

    await _ConfirmConfigurationView.approve(view, interaction, MagicMock())

    cog._do_confirm_configuration.assert_awaited_once()


async def test_another_league_manager_may_not_confirm():
    view, cog = _view()
    interaction = _interaction()
    interaction.user = _member(99)

    await _ConfirmConfigurationView.approve(view, interaction, MagicMock())

    cog._do_confirm_configuration.assert_not_awaited()
    assert "Nothing has been confirmed" in interaction.response.send_message.await_args.args[0]


async def test_an_expired_configuration_review_names_its_own_command():
    view, _ = _view()
    message = MagicMock()
    message.delete = AsyncMock()
    message.channel.send = AsyncMock()
    view._message = message

    await view.on_timeout()

    assert "/season config-review" in message.channel.send.await_args.args[0]
