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

**Every control that changes the review first asks whether it is still the round's current one**
(#402). The review outlives the stage it was built for — through a resubmission, through the
appeals stage, while an approval is still being applied — and a control pressed then must say
why it cannot act and change nothing. The check itself is tested against the database in
`test_review_moves_on.py`; here it is stubbed, and
`test_every_control_refuses_once_the_review_has_moved_on` drives every control that asks it.

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

import leaguebot.results.services.penalty_wizard as pw  # noqa: E402
from leaguebot.results.models.points_config import SessionType  # noqa: E402
from leaguebot.results.services.penalty_service import StagedPenalty  # noqa: E402
from leaguebot.results.services.penalty_wizard import (  # noqa: E402
    ApprovalView,
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
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
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


@pytest.fixture(autouse=True)
def _current(monkeypatch):
    """Every review is current by default; its moving on is exercised explicitly (#402).

    The real check reads the round from the database, which a state on `:memory:` does not have.
    """
    monkeypatch.setattr(pw, "_review_moved_on", AsyncMock(return_value=None))


def _approval_step():
    return patch("leaguebot.results.services.penalty_wizard._show_approval_step", new=AsyncMock(return_value=None))


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
        "leaguebot.results.services.result_submission_service.finalize_penalty_review",
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
        "leaguebot.results.services.result_submission_service.finalize_penalty_review",
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
        "leaguebot.results.services.result_submission_service.enter_resubmit_flow",
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


# ---------------------------------------------------------------------------
# Room for every Remove button, and the amendment's own controls (#345)
# ---------------------------------------------------------------------------


def _pardon(idx: int = 0):
    from leaguebot.results.services.penalty_wizard import StagedPardon

    return StagedPardon(
        driver_user_id=DRIVER + idx, driver_profile_id=31 + idx, attendance_id=41 + idx,
        pardon_type="ABSENT", justification="Ill", grantor_id=77,
    )


def _custom_ids(view) -> list[str]:
    return [item.custom_id for item in view.children]


@pytest.mark.parametrize("amendment", [False, True])
async def test_five_staged_penalties_still_draw_the_review(amendment):
    """**The fifth Remove button overflowed row 1**, which also holds Attendance Pardon, and the
    view raised `ValueError` as it was built — so a review with five penalties could not be shown
    at all. A first pass needed five penalties staged to reach it; an amendment reaches it by
    showing back five reports the round already carries."""
    state = _state(staged=[_penalty(s) for s in range(1, 6)])
    state.is_amendment = amendment

    view = PenaltyReviewView(state)

    assert [cid for cid in _custom_ids(view) if cid.startswith("pw_remove_")] == [
        f"pw_remove_{i}" for i in range(5)
    ]


async def test_more_penalties_than_there_is_room_for_still_draw_the_review():
    """Discord allows twenty-five components; the list beyond them is still listed and applied."""
    view = PenaltyReviewView(_state(staged=[_penalty(s) for s in range(1, 31)]))

    assert len(view.children) <= 25


async def test_an_amendment_offers_no_resubmission():
    """Resubmitting replaces every session of the round and sends it through a first-pass review —
    against a round already FINAL. An amendment corrects its classification in stage one."""
    state = _state(staged=[_penalty()])
    state.is_amendment = True

    assert pw._CID_RESUBMIT not in _custom_ids(PenaltyReviewView(state))
    assert pw._CID_RESUBMIT in _custom_ids(PenaltyReviewView(_state(staged=[_penalty()])))


@pytest.mark.parametrize("amendment", [False, True])
async def test_every_review_offers_a_remove_button_for_each_pardon(amendment):
    """A staged pardon is taken back as a staged penalty is, in a first pass as in an amendment
    (#356). Before, a first pass offered none, and the only way to drop a pardon staged by
    mistake was to resubmit the round, which threw away every staged penalty with it."""
    state = _state(staged=[_penalty()])
    state.staged_pardons = [_pardon(0), _pardon(1)]
    state.is_amendment = amendment

    ids = _custom_ids(PenaltyReviewView(state))

    assert "pw_pardon_remove_0" in ids and "pw_pardon_remove_1" in ids


async def test_five_penalties_and_two_pardons_all_get_a_remove_button():
    """The pardons' buttons follow the penalties' into whatever room row 1 has left."""
    state = _state(staged=[_penalty(s) for s in range(1, 6)])
    state.staged_pardons = [_pardon(0), _pardon(1)]

    ids = _custom_ids(PenaltyReviewView(state))

    assert [c for c in ids if "remove" in c] == [
        *(f"pw_remove_{i}" for i in range(5)), "pw_pardon_remove_0", "pw_pardon_remove_1",
    ]


@pytest.mark.parametrize("amendment", [False, True])
async def test_removing_a_pardon_takes_it_off_the_staged_list(amendment):
    state = _state(staged=[_penalty()])
    state.staged_pardons = [_pardon(0), _pardon(1)]
    state.is_amendment = amendment
    view = PenaltyReviewView(state)
    button = next(c for c in view.children if c.custom_id == "pw_pardon_remove_0")
    interaction = _interaction()

    with patch("leaguebot.results.services.penalty_wizard._refresh_prompt", new=AsyncMock()), patch(
        "leaguebot.results.services.penalty_wizard._shown", new=AsyncMock(return_value=DRIVER)
    ), patch("leaguebot.results.services.penalty_wizard._review_moved_on", new=AsyncMock(return_value=None)):
        await button.callback(interaction)

    assert [p.attendance_id for p in state.staged_pardons] == [42]
    assert "Removed pardon" in _replied(interaction)


async def test_a_pardon_cannot_be_removed_once_the_reports_are_approved():
    """**Approving a first pass's reports grants its pardons** (#356). Removing one after that
    would take it off a list nothing reads again and say it was removed, while the round went on
    carrying it. An amendment is where a granted pardon is changed."""
    state = _state(staged=[_penalty()])
    state.staged_pardons = [_pardon(0)]
    view = PenaltyReviewView(state)
    button = next(c for c in view.children if c.custom_id == "pw_pardon_remove_0")
    interaction = _interaction()
    refresh = AsyncMock()
    moved_on = AsyncMock(return_value="❌ already been approved")

    with patch("leaguebot.results.services.penalty_wizard._refresh_prompt", new=refresh), patch(
        "leaguebot.results.services.penalty_wizard._review_moved_on", new=moved_on
    ):
        await button.callback(interaction)

    assert len(state.staged_pardons) == 1
    assert "already been approved" in _replied(interaction)
    assert "Removed" not in _replied(interaction)
    refresh.assert_not_awaited()
    moved_on.assert_awaited_once_with(state)


async def test_the_appeals_review_draws_with_more_corrections_than_there_is_room_for():
    from leaguebot.results.services.penalty_wizard import AppealsReviewView

    state = _state()
    state.staged_appeals = [_penalty(s) for s in range(1, 31)]

    assert len(AppealsReviewView(state).children) <= 25


# ---------------------------------------------------------------------------
# A review that has moved on (#402)
# ---------------------------------------------------------------------------

#: Every control on the review that asks whether it is current. Remove buttons are named by their
#: custom ID.
GUARDED = [
    "add_penalty_btn",
    "no_penalties_btn",
    "resubmit_btn",
    "pw_remove_0",
    "pardon_btn",
    "pw_pardon_remove_0",
]


@pytest.mark.parametrize("control", GUARDED)
async def test_every_control_refuses_once_the_review_has_moved_on(monkeypatch, control):
    """**The review prompt stayed up, and worked, after the job it was posted for moved on** — so
    Remove said a penalty was removed through the appeals stage when it had been applied, and
    Resubmit started collecting a round already in appeals. Each control now says why it cannot
    act, and changes nothing: no list touched, no form opened, nothing posted or started."""
    moved_on = AsyncMock(return_value="❌ moved on")
    monkeypatch.setattr(pw, "_review_moved_on", moved_on)
    state = _state(staged=[_penalty()])
    state.staged_pardons = [_pardon(0)]
    view = PenaltyReviewView(state)
    interaction = _interaction()
    refresh = AsyncMock()

    with _approval_step() as approval, patch(
        "leaguebot.results.services.penalty_wizard._refresh_prompt", new=refresh
    ), patch(
        "leaguebot.results.services.result_submission_service.enter_resubmit_flow", new=AsyncMock()
    ) as resubmit:
        if control.startswith("pw_"):
            await next(c for c in view.children if c.custom_id == control).callback(interaction)
        else:
            await _press(view, control, interaction)

    assert _replied(interaction) == "❌ moved on"
    moved_on.assert_awaited_once_with(state)
    assert len(state.staged) == 1 and len(state.staged_pardons) == 1
    approval.assert_not_awaited()
    refresh.assert_not_awaited()
    resubmit.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()


@pytest.mark.parametrize(
    ("view_class", "button"),
    [(_ConfirmClearView, "confirm_btn"), (ApprovalView, "make_changes_btn")],
)
async def test_the_steps_on_the_way_to_approval_refuse_too(monkeypatch, view_class, button):
    """Clearing the list and going back to it are reached from a review that was current when
    they were posted, which says nothing about whether it still is."""
    monkeypatch.setattr(pw, "_review_moved_on", AsyncMock(return_value="❌ moved on"))
    state = _state(staged=[_penalty()])
    view = view_class(state)
    interaction = _interaction()
    # Pressed on the review's own approval message, so it is the review's stage that refuses.
    state.approval_message_id = interaction.message.id = 880401
    refresh = AsyncMock()

    with _approval_step() as approval, patch(
        "leaguebot.results.services.penalty_wizard._refresh_prompt", new=refresh
    ):
        await getattr(type(view), button)(view, interaction, MagicMock())

    assert _replied(interaction) == "❌ moved on"
    assert len(state.staged) == 1
    approval.assert_not_awaited()
    refresh.assert_not_awaited()
