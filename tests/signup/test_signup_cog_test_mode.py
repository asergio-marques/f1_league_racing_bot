"""No real driver signs up while the server is in test mode.

Both ends are refused: the driver-facing Sign Up button, and `/signup open`, so an admin
cannot post a signup window that nobody could use. The callbacks run with Discord stubbed —
no gateway, no server, no running bot — and the command guards are unwrapped the way the
other cog suites unwrap them.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from leaguebot.signup.cogs import signup_cog
from leaguebot.signup.services import signup_views
from leaguebot.core.models.server_config import ServerConfig
from leaguebot.core.models.driver_profile import DriverState
from tests.signup.test_wizard_changes import START_KIND, _changes_of, _driver, _league
from tests.support.change_queue import acknowledgement, updated_reply
from tests.support.season_league import SIGNING_UP
from tests.support.undecorate import undecorate

SERVER_ID = 4242

# ── Stubs ─────────────────────────────────────────────────────────────────


class _Response:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def is_done(self) -> bool:
        """Whether the interaction has been answered, as Discord's response reports it."""
        return bool(self.messages)

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
        driver_service=SimpleNamespace(
            get_profile=AsyncMock(return_value=None),
            current_account=AsyncMock(side_effect=lambda a: str(a)),
        ),
        wizard_service=SimpleNamespace(start_wizard=AsyncMock(return_value=None)),
        signup_module_service=SimpleNamespace(get_config=AsyncMock(return_value=None)),
        module_service=SimpleNamespace(is_signup_enabled=AsyncMock(return_value=True)),
        output_router=SimpleNamespace(post_log=AsyncMock()),
    )


def _told(press: Any) -> str:
    """All Kimi was told: the acknowledgement, then the reply as updated."""
    return "\n".join(part for part in (acknowledgement(press), updated_reply(press)) if part)


def _unwrap(cmd):
    """The innermost callback, bypassing whatever tier guard the command wears."""
    return undecorate(cmd)


async def _open(bot) -> _Interaction:
    interaction = _Interaction()
    cog = signup_cog.SignupCog(bot)
    await _unwrap(signup_cog.SignupCog.signup_open)(cog, interaction)
    return interaction


# ── The Sign Up button ────────────────────────────────────────────────────


class TestTheSignUpButton:
    """Since #439 (slice 6b, C75) the button is handed the change queue when it is built (A23)
    and a press asks ``signup.start`` of it; the test-mode refusal is that change's check, made
    as it is asked and before every other, so these press it on a real queue: the league is
    ``tests/signup/test_wizard_changes.py``'s, at Signups with the window open, Kimi (105)
    pressing Sign Up in the signup channel. That a press with test mode off reaches the wizard
    is pinned by ``test_wizard_changes.py``'s
    ``test_sign_up_is_acknowledged_and_creates_the_channel_and_welcome_as_jobs``."""

    @staticmethod
    async def _press(league: Any) -> Any:
        press = _driver(league)
        await signup_views.SignupButtonView(league.bot.change_queue).signup_button.callback(press)
        return press

    async def test_it_is_refused_under_test_mode(self, tmp_path):
        """Test mode is on and Kimi presses Sign Up: he is told signups are unavailable in test
        mode, no signup is asked of the queue, and one refusal line names the button, Kimi and
        test mode."""
        league = await _league(tmp_path)
        await league.set_test_mode(True)

        press = await self._press(league)

        assert "test mode" in _told(press)
        assert not await _changes_of(league, START_KIND)
        lines = [line for line in league.bot.log_channel.sent if "⛔" in line]
        assert len(lines) == 1, lines
        assert lines[0].startswith("⛔ the “Sign Up” button refused for"), lines[0]
        assert f"<@{SIGNING_UP}>" in lines[0] and "test mode" in lines[0], lines[0]

    async def test_the_driver_state_is_not_even_read(self, tmp_path):
        """Rejection at the earliest moment: the refusal precedes every other check. Kimi is
        already part-way through a signup, which would be refused on its own, but with test mode
        on he is told of test mode, not of his signup in progress."""
        league = await _league(tmp_path)
        await league.set_test_mode(True)
        await league.write("DELETE FROM driver_profiles WHERE discord_user_id = ?", str(SIGNING_UP))
        await league.write(
            "INSERT INTO driver_profiles (discord_user_id, current_state) VALUES (?, ?)",
            str(SIGNING_UP), DriverState.PENDING_SIGNUP_COMPLETION.value,
        )

        press = await self._press(league)

        assert "test mode" in _told(press)
        assert "signup in progress" not in _told(press)
        assert not await _changes_of(league, START_KIND)


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
