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

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

import leaguebot.results.services.penalty_wizard as pw
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.penalty_service import StagedPenalty
from leaguebot.results.services.penalty_wizard import (
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
    """A press by the league manager Alex (id 77), answering as Discord's does — not done until
    it replies, defers or opens a form — and connected to a log channel of its own."""
    answered = {"done": False}

    async def _answer(*_args, **_kwargs):
        answered["done"] = True

    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = 77
    interaction.user.display_name = "Alex"
    interaction.client = MagicMock()
    interaction.client.output_router = MagicMock()
    interaction.client.output_router.post_log = AsyncMock(return_value=None)
    interaction.response = MagicMock()
    interaction.response.is_done = MagicMock(side_effect=lambda: answered["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.send_modal = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
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


@pytest.mark.xfail(strict=True, reason="#482: the clear leaves the old list on the prompt (D5)")
async def test_confirming_the_clear_redraws_the_prompt_with_nothing_staged():
    """**D5.** Round 3's penalty review (Division 1) has two penalties staged. Alex presses No
    Penalties / Confirm and then "Yes, clear and proceed with no penalties". The list is cleared
    and the prompt is redrawn from it, listing nothing staged, before the approval question is
    posted — redrawn after, it would withdraw the question it had just posted."""
    state = _state(staged=[_penalty(), _penalty(10)])
    view = _ConfirmClearView(state)
    order: list[str] = []

    async def _redrawn(drawn_state):
        order.append(f"prompt redrawn with {len(drawn_state.staged)} staged")

    async def _asked(*_args, **_kwargs):
        order.append("approval question posted")

    with patch(
        "leaguebot.results.services.penalty_wizard._refresh_prompt",
        new=AsyncMock(side_effect=_redrawn),
    ), patch(
        "leaguebot.results.services.penalty_wizard._show_approval_step",
        new=AsyncMock(side_effect=_asked),
    ):
        await type(view).confirm_btn(view, _interaction(), MagicMock())

    assert state.staged == []
    assert order == ["prompt redrawn with 0 staged", "approval question posted"]


@pytest.mark.xfail(strict=True, reason="#482: cancelling the clear is not recorded")
async def test_cancelling_the_clear_keeps_every_penalty():
    """Round 3's penalty review (Division 1) has two penalties staged, and Alex is asked whether
    to clear them. Alex presses "Cancel — keep penalties". Both penalties stay staged, no
    approval question is posted, Alex is told they were kept, and exactly one cancel line
    records it, naming the review and Alex, with what became of the list and what to do next
    beneath it."""
    state = _state(staged=[_penalty(), _penalty(10)])
    view = _ConfirmClearView(state)
    interaction = _interaction()
    state.bot.output_router = interaction.client.output_router

    with _approval_step() as approval:
        await type(view).cancel_btn(view, interaction, MagicMock())

    assert len(state.staged) == 2
    approval.assert_not_awaited()
    assert "kept intact" in _replied(interaction)
    (line,) = [call.args[0] for call in interaction.client.output_router.post_log.await_args_list]
    _assert_clear_abandoned(line, "cancelled by Alex (<@77>)")


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


# ---------------------------------------------------------------------------
# Every refusal is recorded (#482)
#
# The core specification's "The record of what changed": a refusal replies as today and writes
# one line naming the member, what was refused and why. A button names itself and the review it
# belongs to; one pressed after a restart has no review to name.
# ---------------------------------------------------------------------------

_OF_THE_REVIEW = "of the penalty review of round 3 (Division 1)"
_RESTARTED = "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh."
_NOT_A_MANAGER = "⛔ Only league managers can interact with the penalty review."
_MOVED_ON = "⏳ This round's reports are being approved."
_WITHDRAWN = (
    "❌ This approval message was withdrawn when the review changed or moved on, so it can "
    "no longer be used."
)
_ARCHIVED = "❌ This season is archived (COMPLETED) and cannot be modified."
_NOTHING_STAGED = "⚠️ No penalties are staged."
_APPROVAL_MESSAGE = 900

#: The penalty review's buttons, by label, and the view each is on.
_REVIEW_BUTTONS = [
    "➕ Add Penalty",
    "No Penalties / Confirm",
    "✅ Approve",
    "🔄 Resubmit Initial Results",
    "🏳️ Attendance Pardon",
]
_APPROVAL_BUTTONS = ["✏️ Make Changes", "✅ Approve"]
_CLEAR_BUTTONS = ["Yes, clear and proceed with no penalties", "Cancel — keep penalties"]


def _reviewed_state():
    """Round 3 of Division 1 under review, one penalty and one pardon staged, its approval
    question posted as message 900."""
    state = _state(staged=[_penalty()])
    state.staged_pardons = [_pardon(0)]
    state.approval_message_id = _APPROVAL_MESSAGE
    return state


def _view(kind: str, state):
    return {
        "review": PenaltyReviewView,
        "approval": ApprovalView,
        "clear": _ConfirmClearView,
    }[kind](state)


def _not_a_manager(monkeypatch):
    monkeypatch.setattr(pw, "is_league_manager", lambda config, member: False)


def _moved_on(monkeypatch):
    monkeypatch.setattr(pw, "_review_moved_on", AsyncMock(return_value=_MOVED_ON))


def _archived(monkeypatch):
    """The round's season reads as COMPLETED."""

    class _Cursor:
        async def fetchone(self):
            return {"season_status": "COMPLETED"}

    class _Db:
        async def execute(self, *_args):
            return _Cursor()

    @asynccontextmanager
    async def _connection(_path):
        yield _Db()

    monkeypatch.setattr("leaguebot.core.db.database.get_connection", _connection)


def _refusal(case_id, kind, label, reply, *, setup=None, restarted=False, on_message=True,
             nothing_staged=False):
    return pytest.param(
        kind, label, reply, setup, restarted, on_message, nothing_staged,
        id=case_id,
    )


_REFUSALS = [
    *(
        _refusal(f"restarted-review-{label}", "review", label, _RESTARTED, restarted=True)
        for label in _REVIEW_BUTTONS
    ),
    *(
        _refusal(f"restarted-approval-{label}", "approval", label, _RESTARTED, restarted=True)
        for label in _APPROVAL_BUTTONS
    ),
    *(
        _refusal(f"not-a-manager-review-{label}", "review", label, _NOT_A_MANAGER,
                 setup=_not_a_manager)
        for label in [*_REVIEW_BUTTONS, "Remove #1", "Remove Pardon #1"]
    ),
    *(
        _refusal(f"not-a-manager-approval-{label}", "approval", label, _NOT_A_MANAGER,
                 setup=_not_a_manager)
        for label in _APPROVAL_BUTTONS
    ),
    *(
        _refusal(f"not-a-manager-clear-{label}", "clear", label, _NOT_A_MANAGER,
                 setup=_not_a_manager)
        for label in _CLEAR_BUTTONS
    ),
    *(
        _refusal(f"moved-on-review-{label}", "review", label, _MOVED_ON, setup=_moved_on)
        for label in [
            "➕ Add Penalty", "No Penalties / Confirm", "🔄 Resubmit Initial Results",
            "🏳️ Attendance Pardon", "Remove #1", "Remove Pardon #1",
        ]
    ),
    _refusal("moved-on-approval-Make Changes", "approval", "✏️ Make Changes", _MOVED_ON,
             setup=_moved_on),
    _refusal("moved-on-clear-confirm", "clear", _CLEAR_BUTTONS[0], _MOVED_ON, setup=_moved_on),
    *(
        _refusal(f"withdrawn-approval-{label}", "approval", label, _WITHDRAWN, on_message=False)
        for label in _APPROVAL_BUTTONS
    ),
    _refusal("archived-approval-Approve", "approval", "✅ Approve", _ARCHIVED, setup=_archived),
    _refusal("nothing-staged-review-Approve", "review", "✅ Approve", _NOTHING_STAGED,
             nothing_staged=True),
]


@pytest.mark.parametrize(
    "kind, label, reply, setup, restarted, on_message, nothing_staged", _REFUSALS
)
async def test_every_refused_press_of_the_penalty_review_is_recorded(
    monkeypatch, kind, label, reply, setup, restarted, on_message, nothing_staged
):
    """Alex presses a button of round 3's penalty review (Division 1) — the review itself, its
    approval question, or the clear confirmation — and is refused: after a restart, without
    the tier, once the review has moved on, on a withdrawn approval message, in an archived
    season, or approving with nothing staged. The reply is today's, and exactly one line
    records the refusal, naming the button, the review, Alex and the reason."""
    if setup is not None:
        setup(monkeypatch)
    state = None if restarted else _reviewed_state()
    if nothing_staged:
        state.staged = []
    view = _view(kind, state)
    button = next(item for item in view.children if getattr(item, "label", None) == label)
    interaction = _interaction()
    interaction.message.id = _APPROVAL_MESSAGE if on_message else _APPROVAL_MESSAGE + 1

    with _approval_step() as approval, patch(
        "leaguebot.results.services.result_submission_service.finalize_penalty_review",
        new=AsyncMock(return_value=None),
    ) as finalise, patch(
        "leaguebot.results.services.result_submission_service.enter_resubmit_flow",
        new=AsyncMock(return_value=None),
    ) as resubmit:
        await button.callback(interaction)

    approval.assert_not_awaited()
    finalise.assert_not_awaited()
    resubmit.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    (replied,) = [
        call.args[0] for call in interaction.response.send_message.await_args_list
    ] + [call.args[0] for call in interaction.followup.send.await_args_list]
    assert replied.startswith(reply)
    reason = replied.splitlines()[0].split(" ", 1)[1]
    (line,) = [call.args[0] for call in interaction.client.output_router.post_log.await_args_list]
    if restarted:
        assert line.startswith(f"⛔ the “{label}” button"), line
        assert line.endswith(f" refused for Alex (<@77>) — {reason}"), line
    else:
        assert line == f"⛔ the “{label}” button {_OF_THE_REVIEW} refused for Alex (<@77>) — {reason}"


# ---------------------------------------------------------------------------
# Every press is recorded (#482)
#
# The owner's decision "Every press": each press that changes the review writes one line in the
# success form, naming the member, the review and what was staged, removed or cleared. The
# review's bot is the one the press came through, so a line lands in one place whichever of the
# two writes it.
# ---------------------------------------------------------------------------

_PRESS_NOT_YET_RECORDED = "#482: the review press writes no line in the log channel"


def _press_case(case_id, kind, label, staged, *named):
    return pytest.param(
        kind, label, staged, named,
        id=case_id,
        marks=pytest.mark.xfail(strict=True, reason=_PRESS_NOT_YET_RECORDED),
    )


_PRESSES = [
    _press_case("remove-penalty", "review", "Remove #1", (5,), "+5s", f"<@{DRIVER}>"),
    _press_case("remove-pardon", "review", "Remove Pardon #1", (5,), "ABSENT", f"<@{DRIVER}>"),
    _press_case("no-penalties-posts-the-question", "review", "No Penalties / Confirm", ()),
    _press_case("clear", "clear", _CLEAR_BUTTONS[0], (5, 10), "+5s", "+10s"),
    _press_case("make-changes", "approval", "✏️ Make Changes", (5,)),
]


def _pressed_by_alex(state, channel):
    """Alex's press on the review's approval message, the review's channel answering with
    *channel*, and the review's bot writing where the press's does."""
    interaction = _interaction()
    interaction.message.id = _APPROVAL_MESSAGE
    state.bot.get_channel = MagicMock(return_value=channel)
    state.bot.output_router = interaction.client.output_router
    return interaction


@pytest.mark.parametrize("kind, label, staged, named", _PRESSES)
async def test_every_press_of_the_penalty_review_writes_one_line(kind, label, staged, named):
    """Round 3's penalty review (Division 1) has the penalties given staged and one ABSENT
    pardon. Alex, a league manager, presses Remove #1 or Remove Pardon #1, No Penalties /
    Confirm with nothing staged (posting the approval question), "Yes, clear and proceed with no
    penalties", or Make Changes on the approval question. The press does what it does today,
    and exactly one line records it, naming Alex, the button, the review and what was removed
    or cleared."""
    state = _state(staged=[_penalty(s) for s in staged])
    state.staged_pardons = [_pardon(0)]
    state.approval_message_id = _APPROVAL_MESSAGE if kind == "approval" else None
    channel = MagicMock()
    channel.send = AsyncMock(return_value=MagicMock(id=_APPROVAL_MESSAGE))
    view = _view(kind, state)
    button = next(item for item in view.children if getattr(item, "label", None) == label)
    interaction = _pressed_by_alex(state, channel)

    with patch(
        "leaguebot.results.services.penalty_wizard._refresh_prompt", new=AsyncMock()
    ), patch(
        "leaguebot.results.services.penalty_wizard._shown", new=AsyncMock(return_value=DRIVER)
    ):
        await button.callback(interaction)

    if label == "Remove #1":
        assert state.staged == []
    if label == "Remove Pardon #1":
        assert state.staged_pardons == []
    if kind == "clear" or label == "No Penalties / Confirm":
        assert state.staged == []
        channel.send.assert_awaited_once()
    (line,) = [call.args[0] for call in interaction.client.output_router.post_log.await_args_list]
    assert not line.startswith(("⛔", "↩️", "⌛")), line
    for fragment in ("Alex (<@77>)", label, "penalty review of round 3 (Division 1)", *named):
        assert fragment in line, (fragment, line)


@pytest.mark.xfail(
    strict=True,
    reason="#482: No Penalties / Confirm with the channel unreachable posts nothing and tells nobody",
)
async def test_no_penalties_with_the_channel_unreachable_is_answered_and_recorded():
    """Round 3's penalty review (Division 1) has nothing staged, and its submission channel
    (4455) can no longer be reached. Alex presses No Penalties / Confirm. No approval question
    is posted; Alex is told it could not be posted, and exactly one ⛔ line records the refusal,
    naming the button, the review, Alex and the channel. No success line is written."""
    state = _state(staged=[])
    state.submission_channel_id = 4455
    view = PenaltyReviewView(state)
    interaction = _pressed_by_alex(state, None)

    with patch(
        "leaguebot.results.services.penalty_wizard._shown", new=AsyncMock(return_value=DRIVER)
    ):
        await _press(view, "no_penalties_btn", interaction)

    assert state.approval_message_id is None
    assert "could not be posted" in _replied(interaction)
    (line,) = [call.args[0] for call in interaction.client.output_router.post_log.await_args_list]
    assert line.startswith(
        f"⛔ the “No Penalties / Confirm” button {_OF_THE_REVIEW} refused for Alex (<@77>) — "
    ), line
    assert "4455" in line, line


# ---------------------------------------------------------------------------
# The clear confirmation's cancel and lapse are recorded (#482)
#
# The core specification's "The record of what changed": a cancel and a lapse each write one
# line naming the member, with what became of the change and what to do next beneath it.
# ---------------------------------------------------------------------------

_CLEAR_KEPT = "Nothing was cleared; the staged list stands."


def _assert_clear_abandoned(line: str, ending: str) -> None:
    """*line* is one cancel or lapse line of the clear confirmation of round 3's penalty review
    (Division 1), ending its first line with *ending*, and saying beneath that nothing was
    cleared and what to do next."""
    first, *detail = line.splitlines()
    assert first.startswith(("↩️ ", "⌛ ")), line
    assert "penalty review of round 3 (Division 1)" in first, line
    assert first.endswith(ending), line
    assert _CLEAR_KEPT in "\n".join(detail), line
    assert not detail[-1].strip().endswith(_CLEAR_KEPT), f"no next step: {line}"


@pytest.mark.xfail(strict=True, reason="#482: the clear confirmation's lapse is not recorded")
async def test_a_clear_confirmation_left_to_lapse_is_recorded():
    """Round 3's penalty review (Division 1) has two penalties staged. Alex presses No
    Penalties / Confirm and is asked whether to clear them, then answers nothing until the
    question lapses. Both penalties stay staged, and exactly one lapse line records it, naming
    the review and Alex as the one who started it, with what became of the list and what to do
    next beneath it."""
    state = _state(staged=[_penalty(), _penalty(10)])
    view = PenaltyReviewView(state)
    interaction = _interaction()
    state.bot.output_router = interaction.client.output_router
    interaction.edit_original_response = AsyncMock()

    with _approval_step() as approval:
        await _press(view, "no_penalties_btn", interaction)
        asked = interaction.response.send_message.await_args.kwargs["view"]
        await asked.on_timeout()

    assert len(state.staged) == 2
    approval.assert_not_awaited()
    (line,) = [call.args[0] for call in interaction.client.output_router.post_log.await_args_list]
    assert line.startswith("⌛ "), line
    _assert_clear_abandoned(line, "lapsed unconfirmed (started by Alex (<@77>))")
