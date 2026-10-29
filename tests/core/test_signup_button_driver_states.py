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

Since #439 (slice 6b, C75) the button is handed the change queue when it is built (A23) and a
press asks ``signup.start`` of it; these refusals are that change's check, made as it is asked, so
the tests press it on a real queue: the league is ``tests/signup/test_wizard_changes.py``'s, at
Signups with the window open, Kimi (105) pressing Sign Up in the signup channel. A press that
reaches the wizard is one whose ``signup.start`` is saved on the queue.

Every test is ``async def``: the callback builds a ``discord.ui.View``, and apt's discord.py
2.5.0 calls ``asyncio.get_running_loop()`` in ``View.__init__`` (see CLAUDE.md, Testing).
"""
from __future__ import annotations

from typing import Any

import pytest

from leaguebot.core.models.driver_profile import DriverState
from leaguebot.signup.services import signup_views
from tests.signup.test_wizard_changes import (
    START_KIND,
    _changes_of,
    _driver,
    _league,
)
from tests.support.change_queue import acknowledgement, updated_reply
from tests.support.season_league import SIGNING_UP, SeasonLeague

#: Kimi's current account, where his Sign Up press is made from a past one.
CURRENT_ACCOUNT = 777


# ── Helpers ───────────────────────────────────────────────────────────────


async def _holding(league: SeasonLeague, state: DriverState | None, *,
                   former_driver: bool = False) -> None:
    """Kimi holds a driver profile at *state* (none where *state* is None)."""
    await league.write("DELETE FROM driver_profiles WHERE discord_user_id = ?", str(SIGNING_UP))
    if state is not None:
        await league.write(
            "INSERT INTO driver_profiles (discord_user_id, current_state, former_driver) "
            "VALUES (?, ?, ?)",
            str(SIGNING_UP), state.value, int(former_driver),
        )


async def _press_the_button(league: SeasonLeague) -> Any:
    """Kimi presses Sign Up on a view handed the change queue (A23)."""
    press = _driver(league)
    view = signup_views.SignupButtonView(league.bot.change_queue)
    await view.signup_button.callback(press)
    return press


def _told(press: Any) -> str:
    return "\n".join(part for part in (acknowledgement(press), updated_reply(press)) if part)


async def _started(league: SeasonLeague) -> bool:
    return bool(await _changes_of(league, START_KIND))


def _assert_one_refusal_line(league: SeasonLeague, reason: str) -> None:
    """One line, naming the button, the member and why (core specification, "The record of
    what changed")."""
    lines = [line for line in league.bot.log_channel.sent if "⛔" in line]
    assert len(lines) == 1, lines
    assert "“Sign Up”" in lines[0] and f"<@{SIGNING_UP}>" in lines[0], lines[0]
    assert reason in lines[0], lines[0]


# ── The defect itself ─────────────────────────────────────────────────────


class TestAReturningRacer:
    """What a league meets on the first day of its second season."""

    async def test_a_returning_racer_reaches_the_wizard(self, tmp_path):
        """The state the season-end driver pass leaves a driver who raced: reset to Not
        Signed Up, profile kept because the former-driver flag is set. Before #226 was
        fixed this raised AttributeError and the driver saw 'This interaction failed'.
        Kimi, so placed, presses Sign Up: his signup is asked of the queue."""
        league = await _league(tmp_path)
        await _holding(league, DriverState.NOT_SIGNED_UP, former_driver=True)

        await _press_the_button(league)

        assert await _started(league)

    async def test_a_driver_pending_deletion_also_reaches_the_wizard(self, tmp_path):
        """Not Signed Up without the flag — a profile awaiting the next driver pass. It is
        still a profile, so it crashed the button just as the former driver's did."""
        league = await _league(tmp_path)
        await _holding(league, DriverState.NOT_SIGNED_UP)

        await _press_the_button(league)

        assert await _started(league)

    async def test_a_member_with_no_profile_still_reaches_the_wizard(self, tmp_path):
        """The one press that always worked. Pinned so the fix does not trade it away. It is
        also the contrast to a past account's refusal (#243): an account linked to no other is
        not mistaken for a past one, and its signup is asked of the queue."""
        league = await _league(tmp_path)
        await _holding(league, None)

        await _press_the_button(league)

        assert await _started(league)


# ── The refusals, which no driver had ever seen ───────────────────────────


class TestTheRefusals:
    """Both messages were written, specified and unreachable — every press that should
    have produced one produced the crash instead."""

    @pytest.mark.parametrize("state", sorted(signup_views.IN_PROGRESS_STATES, key=lambda s: s.value))
    async def test_a_driver_mid_signup_is_told_so(self, tmp_path, state):
        league = await _league(tmp_path)
        await _holding(league, state)

        press = await _press_the_button(league)

        assert "signup in progress" in _told(press)
        assert not await _started(league)
        _assert_one_refusal_line(league, "already have a signup in progress")

    @pytest.mark.parametrize("state", sorted(signup_views.APPROVED_STATES, key=lambda s: s.value))
    async def test_an_approved_driver_is_told_so(self, tmp_path, state):
        league = await _league(tmp_path)
        await _holding(league, state)

        press = await _press_the_button(league)

        assert "already been approved" in _told(press)
        assert not await _started(league)
        _assert_one_refusal_line(league, "already been approved")

    async def test_an_unconfigured_module_is_refused_and_recorded(self, tmp_path):
        """The signup module holds no configuration, so nothing is started: Kimi is told
        "❌ Signup module is not configured. Contact an admin.", the refusal is recorded, and
        no signup is asked of the queue."""
        league = await _league(tmp_path)
        await _holding(league, None)
        await league.write("DELETE FROM signup_module_config")

        press = await _press_the_button(league)

        assert "Signup module is not configured. Contact an admin." in _told(press)
        assert not await _started(league)
        _assert_one_refusal_line(league, "Signup module is not configured")


# ── The class of defect, not just this instance ───────────────────────────


class TestEveryState:
    @pytest.mark.parametrize("state", sorted(DriverState, key=lambda s: s.value))
    async def test_no_state_crashes_the_button(self, tmp_path, state):
        """Whatever state a driver stands in, pressing the button produces *something* —
        a signup asked of the queue or a refusal. #226 was the case where it produced
        neither, and the driver was left with Discord's bare 'This interaction failed'."""
        league = await _league(tmp_path)
        await _holding(league, state)

        press = await _press_the_button(league)

        assert _told(press) or await _started(league), (
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
        covered = signup_views.IN_PROGRESS_STATES | signup_views.APPROVED_STATES

        assert covered == set(DriverState) - {DriverState.NOT_SIGNED_UP}
        assert not (signup_views.IN_PROGRESS_STATES & signup_views.APPROVED_STATES)


# ── A past account of a driver (issue #243) ───────────────────────────────


class TestAPastAccount:
    """An account a driver has since moved on from signs nobody up."""

    async def test_a_past_account_is_refused_and_pointed_at_the_current_one(self, tmp_path):
        """Kimi's account (105) is a past one: his driver now plays as 777. He presses Sign Up
        from 105 and is told it is a past account of a driver, pointed at <@777>; the refusal
        is recorded and nothing is asked of the queue."""
        league = await _league(tmp_path)
        await _holding(league, None)
        await league.write(
            "INSERT INTO driver_profiles (discord_user_id, current_state) VALUES (?, ?)",
            str(CURRENT_ACCOUNT), DriverState.NOT_SIGNED_UP.value,
        )
        await league.write(
            "INSERT INTO driver_accounts (driver_profile_id, discord_user_id) "
            "SELECT id, ? FROM driver_profiles WHERE discord_user_id = ?",
            str(SIGNING_UP), str(CURRENT_ACCOUNT),
        )

        press = await _press_the_button(league)

        assert "past account of a driver" in _told(press)
        assert f"<@{CURRENT_ACCOUNT}>" in _told(press)
        assert not await _started(league)
        _assert_one_refusal_line(league, "past account of a driver")
