"""No real driver signs up while the server is in test mode.

Both ends are refused: the driver-facing Sign Up button, and `/signup open`, so an admin
cannot post a signup window that nobody could use. The callbacks run with Discord stubbed —
no gateway, no server, no running bot — and the command guards are unwrapped the way the
other cog suites unwrap them.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs import signup_cog  # noqa: E402
from models.server_config import ServerConfig  # noqa: E402

SERVER_ID = 4242


# ── Stubs ─────────────────────────────────────────────────────────────────


class _Response:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def defer(self, **kwargs):
        pass

    async def send_message(self, content, **kwargs):
        self.messages.append(content)


class _Interaction:
    def __init__(self) -> None:
        self.guild_id = SERVER_ID
        self.guild = SimpleNamespace(id=SERVER_ID)
        self.response = _Response()
        self.followup = SimpleNamespace(send=AsyncMock())
        self.user = SimpleNamespace(display_name="Tester", id=1)
        self.client = None

    @property
    def reply(self) -> str:
        assert self.response.messages, "the command replied with nothing"
        return self.response.messages[-1]


def _config(*, test_mode: bool) -> ServerConfig:
    return ServerConfig(
        server_id=SERVER_ID,
        interaction_role_id=1,
        interaction_channel_id=2,
        log_channel_id=3,
        test_mode_active=test_mode,
    )


def _bot(*, test_mode: bool):
    """A bot whose every signup dependency records that it was reached."""
    return SimpleNamespace(
        config_service=SimpleNamespace(
            get_server_config=AsyncMock(return_value=_config(test_mode=test_mode))
        ),
        driver_service=SimpleNamespace(get_profile=AsyncMock(return_value=None)),
        wizard_service=SimpleNamespace(start_wizard=AsyncMock(return_value=None)),
        signup_module_service=SimpleNamespace(get_config=AsyncMock(return_value=None)),
        output_router=SimpleNamespace(post_log=AsyncMock()),
    )


def _unwrap(cmd):
    """The innermost callback, bypassing channel_guard and admin_only."""
    return cmd.callback.__wrapped__.__wrapped__


async def _press_the_button(bot) -> _Interaction:
    interaction = _Interaction()
    interaction.client = bot
    view = signup_cog.SignupButtonView()
    await view.signup_button.callback(interaction)
    return interaction


async def _open(bot) -> _Interaction:
    interaction = _Interaction()
    cog = signup_cog.SignupCog(bot)
    await _unwrap(signup_cog.SignupCog.signup_open)(cog, interaction)
    return interaction


# ── The Sign Up button ────────────────────────────────────────────────────


class TestTheSignUpButton:
    async def test_it_is_refused_under_test_mode(self):
        bot = _bot(test_mode=True)

        interaction = await _press_the_button(bot)

        assert "test mode" in interaction.reply
        bot.wizard_service.start_wizard.assert_not_awaited()

    async def test_the_driver_state_is_not_even_read(self):
        """Rejection at the earliest moment: the refusal precedes every other check."""
        bot = _bot(test_mode=True)

        await _press_the_button(bot)

        bot.driver_service.get_profile.assert_not_awaited()

    async def test_it_reaches_the_wizard_when_test_mode_is_off(self):
        bot = _bot(test_mode=False)

        await _press_the_button(bot)

        bot.wizard_service.start_wizard.assert_awaited()


# ── /signup open ──────────────────────────────────────────────────────────


class TestSignupOpen:
    async def test_it_is_refused_under_test_mode(self):
        bot = _bot(test_mode=True)

        interaction = await _open(bot)

        assert "test mode" in interaction.reply
        bot.signup_module_service.get_config.assert_not_awaited()

    async def test_it_proceeds_when_test_mode_is_off(self):
        """Test mode off, it reaches the module config — unconfigured here, and says so."""
        bot = _bot(test_mode=False)

        interaction = await _open(bot)

        assert "Signup module is not configured" in interaction.reply
