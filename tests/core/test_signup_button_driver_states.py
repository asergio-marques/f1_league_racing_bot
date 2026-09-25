"""The Sign Up button, pressed by a member who already holds a driver profile.

Issue #226: the callback read ``profile.driver_state``, a field ``DriverProfile`` does not
carry — it is ``current_state``. The read happens *before* the comparison, so every member
holding a profile got an ``AttributeError`` and Discord's bare "This interaction failed":
no wizard, no refusal, nothing in the log channel.

The driver a league loses to this is the one it most wants back. The driver pass at a
season's end resets everyone to Not Signed Up and then deletes the profiles of those who
never raced, but **keeps** the profile of anyone carrying the former-driver flag — which is
everyone who actually raced. That kept profile is enough to crash the button, so from season
two onwards the returning racers are locked out while a newcomer with no profile walks in.

The suite could not have caught it: the only test that pressed the button stubbed
``get_profile`` to return ``None``, so ``profile is not None`` short-circuited and the
attribute was never touched. These tests press it with a profile present, one per state.

Every test is ``async def``: the callback builds a ``discord.ui.View``, and apt's discord.py
2.5.0 calls ``asyncio.get_running_loop()`` in ``View.__init__`` (see CLAUDE.md, Testing).
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.signup.cogs import signup_cog  # noqa: E402
from leaguebot.core.models.driver_profile import DriverProfile, DriverState  # noqa: E402
from leaguebot.core.models.server_config import ServerConfig  # noqa: E402

SERVER_ID = 4242
USER_ID = "77"


# ── Stubs ─────────────────────────────────────────────────────────────────


class _Response:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.deferred = False

    async def defer(self, **kwargs):
        self.deferred = True

    async def send_message(self, content, **kwargs):
        self.messages.append(content)


class _Interaction:
    def __init__(self) -> None:
        self.guild_id = SERVER_ID
        self.guild = SimpleNamespace(id=SERVER_ID)
        self.response = _Response()
        self.followup = SimpleNamespace(send=AsyncMock())
        self.user = SimpleNamespace(display_name="Tester", id=int(USER_ID))
        self.client = None

    @property
    def reply(self) -> str:
        assert self.response.messages, "the button replied with nothing"
        return self.response.messages[-1]


def _profile(state: DriverState, *, former_driver: bool = False) -> DriverProfile:
    return DriverProfile(
        id=1,
        discord_user_id=USER_ID,
        current_state=state,
        former_driver=former_driver,
    )


def _bot(profile: DriverProfile | None):
    """A bot whose driver service hands back *profile*, test mode off."""
    return SimpleNamespace(
        config_service=SimpleNamespace(
            get_server_config=AsyncMock(
                return_value=ServerConfig(
                    server_id=SERVER_ID,
                    interaction_role_id=1,
                    interaction_channel_id=2,
                    log_channel_id=3,
                    test_mode_active=False,
                )
            )
        ),
        driver_service=SimpleNamespace(
            get_profile=AsyncMock(return_value=profile),
            current_account=AsyncMock(side_effect=lambda a: str(a)),
        ),
        wizard_service=SimpleNamespace(start_wizard=AsyncMock(return_value=None)),
    )


async def _press_the_button(bot) -> _Interaction:
    interaction = _Interaction()
    interaction.client = bot
    view = signup_cog.SignupButtonView()
    await view.signup_button.callback(interaction)
    return interaction


# ── The defect itself ─────────────────────────────────────────────────────


class TestAReturningRacer:
    """What a league meets on the first day of its second season."""

    async def test_a_returning_racer_reaches_the_wizard(self):
        """The state the season-end driver pass leaves a driver who raced: reset to Not
        Signed Up, profile kept because the former-driver flag is set. Before #226 was
        fixed this raised AttributeError and the driver saw 'This interaction failed'."""
        bot = _bot(_profile(DriverState.NOT_SIGNED_UP, former_driver=True))

        await _press_the_button(bot)

        bot.wizard_service.start_wizard.assert_awaited()

    async def test_a_driver_pending_deletion_also_reaches_the_wizard(self):
        """Not Signed Up without the flag — a profile awaiting the next driver pass. It is
        still a profile, so it crashed the button just as the former driver's did."""
        bot = _bot(_profile(DriverState.NOT_SIGNED_UP, former_driver=False))

        await _press_the_button(bot)

        bot.wizard_service.start_wizard.assert_awaited()

    async def test_a_member_with_no_profile_still_reaches_the_wizard(self):
        """The one press that always worked. Pinned so the fix does not trade it away."""
        bot = _bot(None)

        await _press_the_button(bot)

        bot.wizard_service.start_wizard.assert_awaited()


# ── The refusals, which no driver had ever seen ───────────────────────────


class TestTheRefusals:
    """Both messages were written, specified and unreachable — every press that should
    have produced one produced the crash instead."""

    @pytest.mark.parametrize("state", sorted(signup_cog.IN_PROGRESS_STATES, key=lambda s: s.value))
    async def test_a_driver_mid_signup_is_told_so(self, state):
        bot = _bot(_profile(state))

        interaction = await _press_the_button(bot)

        assert "signup in progress" in interaction.reply
        bot.wizard_service.start_wizard.assert_not_awaited()

    @pytest.mark.parametrize("state", sorted(signup_cog.APPROVED_STATES, key=lambda s: s.value))
    async def test_an_approved_driver_is_told_so(self, state):
        bot = _bot(_profile(state))

        interaction = await _press_the_button(bot)

        assert "already been approved" in interaction.reply
        bot.wizard_service.start_wizard.assert_not_awaited()


# ── The class of defect, not just this instance ───────────────────────────


class TestEveryState:
    @pytest.mark.parametrize("state", sorted(DriverState, key=lambda s: s.value))
    async def test_no_state_crashes_the_button(self, state):
        """Whatever state a driver stands in, pressing the button produces *something* —
        a wizard or a refusal. #226 was the case where it produced neither, and the
        driver was left with Discord's bare 'This interaction failed'."""
        bot = _bot(_profile(state))

        interaction = await _press_the_button(bot)

        started = bot.wizard_service.start_wizard.await_count
        assert interaction.response.messages or started, (
            f"a driver at {state.value} got no wizard and no message"
        )

    async def test_the_two_refusal_sets_exhaust_the_states(self):
        """The guard on the bare ``else`` in the callback.

        The approved arm is an ``else``, not an ``elif``, so that a state belonging to
        neither set gets a refusal rather than falling through into the wizard. That is
        only safe while these two sets cover every state that is not Not Signed Up.

        If you are here because this test failed, you have added a driver state — most
        likely a ban, with the stewarding module. Decide which refusal it earns and put
        it in that set. Do not delete this assertion: without it the new state silently
        gets 'Your signup has already been approved' and signs nobody up.
        """
        covered = signup_cog.IN_PROGRESS_STATES | signup_cog.APPROVED_STATES

        assert covered == set(DriverState) - {DriverState.NOT_SIGNED_UP}
        assert not (signup_cog.IN_PROGRESS_STATES & signup_cog.APPROVED_STATES)


# ── A past account of a driver (issue #243) ───────────────────────────────


class TestAPastAccount:
    """An account a driver has since moved on from signs nobody up."""

    async def test_a_past_account_is_refused_and_pointed_at_the_current_one(self):
        bot = _bot(None)
        bot.driver_service.current_account = AsyncMock(return_value="777")

        interaction = await _press_the_button(bot)

        assert "past account of a driver" in interaction.reply
        assert "<@777>" in interaction.reply
        bot.wizard_service.start_wizard.assert_not_awaited()
        bot.driver_service.get_profile.assert_not_awaited()

    async def test_the_current_account_is_not_mistaken_for_a_past_one(self):
        bot = _bot(None)

        await _press_the_button(bot)

        bot.driver_service.current_account.assert_awaited_once_with(USER_ID)
        bot.wizard_service.start_wizard.assert_awaited()
