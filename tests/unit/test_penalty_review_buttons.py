"""The penalty review's buttons — the five a league manager presses to finish a round.

Issue #208. `penalty_wizard.py`'s rendering and permission gate are covered elsewhere; this file
takes the buttons themselves, which are the only way a round leaves the penalty pass.

**Every button carries a restart guard.** The view is persistent and outlives the process, but
its `state` — the staged penalties, the round context — is in memory and does not. A button
pressed after a restart therefore finds `state is None`, and must say so rather than raise
inside Discord's interaction handler where the manager would see only "this interaction failed".
The guard is written out in each of the five rather than shared, which is exactly the shape
where one gets missed, so `test_every_button_survives_a_restart` drives all five.

**Every button re-checks the permission** for the same reason the panel does: a review channel
is visible to both league tiers, and the buttons never disappear.

**Approving with nothing staged is refused, and pointed at the other button.** The two are
genuinely different acts — approving a round *with* penalties, and finalising one that has none
— and a manager pressing Approve on an empty list has almost certainly meant the other. Letting
it through would finalise the round by a path that expects penalties to apply.

**Clearing a staged list asks first, and says how many.** The list is an evening's work and
discarding it cannot be undone; the count is what makes the warning real rather than
boilerplate. `test_confirming_the_clear_discards_the_staged_list` and its cancel counterpart sit
either side.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import services.penalty_wizard as pw  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from services.penalty_service import StagedPenalty  # noqa: E402
from services.penalty_wizard import (  # noqa: E402
    PenaltyReviewState,
    PenaltyReviewView,
    _ConfirmClearView,
)

SERVER_ID = 12208
ROUND_ID = 5
DIVISION_ID = 11
DRIVER = 4001

#: Every button on the review, by attribute name.
BUTTONS = [
    "add_penalty_btn",
    "no_penalties_btn",
    "approve_btn",
    "resubmit_btn",
    "pardon_btn",
]


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _penalty(seconds: int = 5) -> StagedPenalty:
    return StagedPenalty(
        driver_user_id=DRIVER,
        session_type=SessionType.FEATURE_RACE,
        penalty_type="TIME",
        penalty_seconds=seconds,
    )


def _state(*, staged=()) -> PenaltyReviewState:
    """A review state whose bot answers the permission gate.

    `_is_league_manager` awaits `config_service.get_server_config` before consulting
    `is_league_manager`, so a bare `MagicMock` bot fails on the await rather than on the
    permission — which would test the wrong thing even where it happened to pass.
    """
    bot = MagicMock()
    bot.config_service = MagicMock()
    bot.config_service.get_server_config = AsyncMock(return_value=MagicMock())

    state = PenaltyReviewState(
        round_id=ROUND_ID,
        division_id=DIVISION_ID,
        submission_channel_id=1,
        session_types_present=[SessionType.FEATURE_RACE],
        db_path=":memory:",
        bot=bot,
    )
    state.round_number = 3
    state.division_name = "Division 1"
    state.staged = list(staged)
    return state


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = 77
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


@pytest.fixture(autouse=True)
def _permitted(monkeypatch):
    """Permit by default; the refusal is exercised explicitly where it is the subject."""
    monkeypatch.setattr(pw, "_may_review_signup", AsyncMock(return_value=True), raising=False)
    monkeypatch.setattr(pw, "is_league_manager", lambda config, member: True)


def _approval_step():
    return patch("services.penalty_wizard._show_approval_step", new=AsyncMock(return_value=None))


async def _press(view, name: str, interaction):
    await getattr(type(view), name)(view, interaction, MagicMock())


# ---------------------------------------------------------------------------
# The restart guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("button", BUTTONS)
async def test_every_button_survives_a_restart(button):
    """The view is persistent and outlives the process; its state does not. Without this
    the manager would see only "this interaction failed" and have no idea why."""
    view = PenaltyReviewView(None)
    interaction = _interaction()

    await _press(view, button, interaction)

    assert "bot was restarted" in _replied(interaction)


@pytest.mark.parametrize("button", BUTTONS)
async def test_the_restart_message_says_what_to_wait_for(button):
    """The prompt refreshes itself, so the manager has something to wait for rather than
    a failure to report."""
    view = PenaltyReviewView(None)
    interaction = _interaction()

    await _press(view, button, interaction)

    assert "prompt to refresh" in _replied(interaction)


# ---------------------------------------------------------------------------
# The permission gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("button", BUTTONS)
async def test_every_button_refuses_somebody_without_the_tier(monkeypatch, button):
    """A review channel is visible to both league tiers and the buttons never disappear,
    so each press is checked rather than the panel's posting being trusted."""
    monkeypatch.setattr(pw, "is_league_manager", lambda config, member: False)
    view = PenaltyReviewView(_state(staged=[_penalty()]))
    interaction = _interaction()

    with _approval_step():
        await _press(view, button, interaction)

    assert "Only league managers" in _replied(interaction)


# ---------------------------------------------------------------------------
# Add penalty
# ---------------------------------------------------------------------------


async def test_add_penalty_offers_a_session_to_choose():
    """A round has several sessions and a penalty belongs to one; the modal cannot be
    opened until that is known."""
    view = PenaltyReviewView(_state())
    interaction = _interaction()

    await _press(view, "add_penalty_btn", interaction)

    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.kwargs["view"] is not None


# ---------------------------------------------------------------------------
# No penalties / confirm
# ---------------------------------------------------------------------------


async def test_an_empty_list_goes_straight_to_approval():
    """Nothing to discard, so nothing to confirm — asking would be a confirmation with no
    consequence, which is how confirmations stop being read."""
    view = PenaltyReviewView(_state(staged=[]))
    interaction = _interaction()

    with _approval_step() as approval:
        await _press(view, "no_penalties_btn", interaction)

    approval.assert_awaited_once()


async def test_a_staged_list_is_confirmed_before_it_is_cleared():
    """It is an evening's work and discarding it cannot be undone."""
    view = PenaltyReviewView(_state(staged=[_penalty(), _penalty(10)]))
    interaction = _interaction()

    with _approval_step() as approval:
        await _press(view, "no_penalties_btn", interaction)

    approval.assert_not_awaited()
    assert interaction.response.send_message.await_args.kwargs["view"] is not None


async def test_the_confirmation_says_how_many_would_be_discarded():
    """The count is what makes the warning real rather than boilerplate."""
    view = PenaltyReviewView(_state(staged=[_penalty(), _penalty(10)]))
    interaction = _interaction()

    await _press(view, "no_penalties_btn", interaction)

    assert "**2**" in _replied(interaction)


async def test_the_confirmation_says_what_will_happen_to_the_round():
    view = PenaltyReviewView(_state(staged=[_penalty()]))
    interaction = _interaction()

    await _press(view, "no_penalties_btn", interaction)

    replied = _replied(interaction)
    assert "discard" in replied
    assert "without any penalties" in replied


# ---------------------------------------------------------------------------
# The clear confirmation
# ---------------------------------------------------------------------------


async def test_confirming_the_clear_discards_the_staged_list():
    state = _state(staged=[_penalty(), _penalty(10)])
    view = _ConfirmClearView(state)
    interaction = _interaction()

    with _approval_step() as approval:
        await type(view).confirm_btn(view, interaction, MagicMock())

    assert state.staged == []
    approval.assert_awaited_once()


async def test_cancelling_the_clear_keeps_every_penalty():
    state = _state(staged=[_penalty(), _penalty(10)])
    view = _ConfirmClearView(state)
    interaction = _interaction()

    with _approval_step() as approval:
        await type(view).cancel_btn(view, interaction, MagicMock())

    assert len(state.staged) == 2
    approval.assert_not_awaited()
    assert "kept intact" in _replied(interaction)


@pytest.mark.parametrize("button", ["confirm_btn", "cancel_btn"])
async def test_the_clear_confirmation_checks_the_tier_too(monkeypatch, button):
    """It is reached from a button that already checked, which makes it easy to assume —
    but a different person can press this one."""
    monkeypatch.setattr(pw, "is_league_manager", lambda config, member: False)
    state = _state(staged=[_penalty()])
    view = _ConfirmClearView(state)
    interaction = _interaction()

    with _approval_step():
        await getattr(type(view), button)(view, interaction, MagicMock())

    assert len(state.staged) == 1
    assert "Only league managers" in _replied(interaction)


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------


async def test_approving_with_penalties_finalises_the_review():
    view = PenaltyReviewView(_state(staged=[_penalty()]))
    interaction = _interaction()

    with patch(
        "services.result_submission_service.finalize_penalty_review",
        new=AsyncMock(return_value=None),
    ) as finalise:
        await _press(view, "approve_btn", interaction)

    finalise.assert_awaited_once()


async def test_approving_with_nothing_staged_is_refused():
    """Approving a round *with* penalties and finalising one that has none are different
    acts, and a manager pressing Approve on an empty list has almost certainly meant the
    other."""
    view = PenaltyReviewView(_state(staged=[]))
    interaction = _interaction()

    with patch(
        "services.result_submission_service.finalize_penalty_review",
        new=AsyncMock(return_value=None),
    ) as finalise:
        await _press(view, "approve_btn", interaction)

    finalise.assert_not_awaited()
    assert "No penalties are staged" in _replied(interaction)


async def test_the_empty_approval_refusal_names_the_other_button():
    view = PenaltyReviewView(_state(staged=[]))
    interaction = _interaction()

    await _press(view, "approve_btn", interaction)

    assert "No Penalties / Confirm" in _replied(interaction)


# ---------------------------------------------------------------------------
# Resubmit and pardon
# ---------------------------------------------------------------------------


async def test_resubmit_enters_the_resubmission_flow():
    view = PenaltyReviewView(_state(staged=[_penalty()]))
    interaction = _interaction()

    with patch(
        "services.result_submission_service.enter_resubmit_flow",
        new=AsyncMock(return_value=None),
    ) as resubmit:
        await _press(view, "resubmit_btn", interaction)

    resubmit.assert_awaited_once()


async def test_the_pardon_button_opens_the_pardon_modal():
    """A pardon waives an attendance penalty and needs a justification, so it is a form
    rather than a button."""
    view = PenaltyReviewView(_state())
    interaction = _interaction()

    await _press(view, "pardon_btn", interaction)

    interaction.response.send_modal.assert_awaited_once()
