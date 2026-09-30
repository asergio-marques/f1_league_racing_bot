"""The approval step and the appeals prompt — the two views that finish a round.

Issue #208. `ApprovalView` and `AppealsReviewView` were uncovered. Between them they take a
round from "the stewards have staged their penalties" to FINAL, which is the point after which
nothing about the round can be corrected except by amending it destructively.

**A view whose state did not survive a restart says so rather than acting.** These are
persistent views: Discord re-attaches the buttons after a restart but the wizard state is
in-memory and is rebuilt asynchronously, so for a few seconds a button exists with `state is
None`. Pressing one then must not raise — an unhandled exception in a component callback shows
a league "This interaction failed" and leaves them guessing — and it must not finalise anything
either. Every button is tested for it, because the guard is one `if` and it is the sort of thing
a reader deletes as defensive clutter.

**Only a league manager may press any of them.** The review happens in the submission channel,
which drivers can see; a driver confirming the appeals step would finalise a round over the
stewards' heads.

**An archived season cannot be finalised into.** A COMPLETED season is the league's published
record, and approving a review inside one would rewrite results already standing. The guard is
on Approve specifically, because that is the button that writes.

**Make Changes goes back without discarding anything.** A steward who reaches the approval step
and realises they have missed a penalty must be able to return to staging with the list intact —
otherwise the safe thing to do at that screen is to approve, which is exactly wrong.

**Confirming with corrections staged asks first; confirming with none does not.** "No Changes /
Confirm" means two different things depending on what is staged, and pressing it with a list
built up would otherwise silently discard that work. With nothing staged there is nothing to
lose and a confirmation would be a click for its own sake.

**A Remove button is one per staged correction and resolves its index at press time.** The list
shifts as entries are removed, so a button pointing past the end is answered rather than
removing the wrong correction — which would be invisible, because both are removals.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.penalty_wizard import (
    AppealsReviewView,
    ApprovalView,
    PenaltyReviewState,
    StagedPenalty,
    _AppealsConfirmClearView,
)

SERVER_ID = 11608
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
#: The approval message every interaction here is pressed on, and every state records as its
#: own. Its buttons act on no other (#402).
APPROVAL_MESSAGE_ID = 880401


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    name: str = "appeals",
    season_status: str = "ACTIVE",
    round_status: str = "AWAITING_APPEAL_VERDICTS",
) -> str:
    """*round_status* is the report stage's for the approval step, which belongs to that stage
    and refuses once the round has moved on to appeals (#402)."""
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', ?)",
            (SEASON_ID, season_status),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "status) VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', 'NORMAL', ?)",
            (ROUND_ID, DIVISION_ID, round_status),
        )
        await db.execute(
            "INSERT INTO round_submission_channels (round_id, channel_id, created_at, "
            "in_penalty_review, results_posted) "
            "VALUES (?, 700, '2026-02-01T00:00:00+00:00', 1, 1)",
            (ROUND_ID,),
        )
        await db.commit()
    return db_path


def _penalty(driver: int = 101) -> StagedPenalty:
    return StagedPenalty(
        driver_user_id=driver,
        session_type=SessionType.FEATURE_RACE,
        penalty_type="TIME",
        penalty_seconds=5,
        description="Corner cutting",
    )


def _state(db_path: str, *, appeals=None) -> PenaltyReviewState:
    bot = MagicMock()
    bot.db_path = db_path
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)
    return PenaltyReviewState(
        round_id=ROUND_ID,
        division_id=DIVISION_ID,
        submission_channel_id=700,
        session_types_present=[SessionType.FEATURE_RACE],
        db_path=db_path,
        bot=bot,
        staged_appeals=list(appeals or []),
        approval_message_id=APPROVAL_MESSAGE_ID,
        round_number=3,
        division_name="Pro",
    )


def _interaction():
    """A press by the league manager Alex (id 77), answering as Discord's does — not done until
    it replies, defers or opens a form — and connected to a log channel of its own."""
    answered = {"done": False}

    async def _answer(*_args, **_kwargs):
        answered["done"] = True

    interaction = MagicMock()
    interaction.message.id = APPROVAL_MESSAGE_ID
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = 77
    interaction.user.display_name = "Alex"
    interaction.client = MagicMock()
    interaction.client.output_router = MagicMock()
    interaction.client.output_router.post_log = AsyncMock(return_value=None)
    interaction.response = MagicMock()
    interaction.response.is_done = MagicMock(side_effect=lambda: answered["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.response.send_modal = AsyncMock(side_effect=_answer)
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


def _sent_view(interaction):
    for call in interaction.response.send_message.await_args_list:
        if "view" in call.kwargs:
            return call.kwargs["view"]
    return None


def _press(view, name, interaction):
    """A decorated button leaves the plain function on the class."""
    return getattr(type(view), name)(view, interaction, MagicMock())


def _manager(is_manager: bool = True):
    return patch(
        "leaguebot.results.services.penalty_wizard._is_league_manager",
        new=AsyncMock(return_value=is_manager),
    )


def _finalisers():
    """Patch both terminal calls and the two prompt refreshers."""
    return (
        patch(
            "leaguebot.results.services.result_submission_service.finalize_penalty_review", new=AsyncMock()
        ),
        patch(
            "leaguebot.results.services.result_submission_service.finalize_appeals_review", new=AsyncMock()
        ),
        patch("leaguebot.results.services.penalty_wizard._refresh_prompt", new=AsyncMock()),
        patch("leaguebot.results.services.penalty_wizard._refresh_appeals_prompt", new=AsyncMock()),
    )


#: (view class, button attribute) for every button guarded against a lost state.
RESTART_GUARDED = [
    (ApprovalView, "make_changes_btn"),
    (ApprovalView, "approve_btn"),
    (AppealsReviewView, "add_correction_btn"),
    (AppealsReviewView, "no_changes_btn"),
]


# ---------------------------------------------------------------------------
# A state that did not survive the restart
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("view_cls,button", RESTART_GUARDED)
async def test_a_button_with_no_state_says_so_rather_than_raising(view_cls, button):
    """Discord re-attaches the buttons after a restart but the state is rebuilt
    asynchronously, so for a few seconds a button exists with no state behind it. An
    unhandled exception there shows a league "This interaction failed" and nothing else."""
    view = view_cls(state=None)
    interaction = _interaction()

    await _press(view, button, interaction)

    assert "bot was restarted" in _replied(interaction)


@pytest.mark.parametrize("view_cls,button", RESTART_GUARDED)
async def test_a_button_with_no_state_finalises_nothing(view_cls, button):
    """Which is the half that matters: a round finalised from a half-recovered view would
    publish whatever the empty state contained."""
    view = view_cls(state=None)
    p1, p2, p3, p4 = _finalisers()

    with p1 as penalty, p2 as appeals, p3, p4:
        await _press(view, button, _interaction())

    penalty.assert_not_awaited()
    appeals.assert_not_awaited()


async def test_a_remove_button_with_no_state_says_so():
    """It is built from the state's own list, so a restart leaves buttons whose index
    refers to a list that no longer exists."""
    view = AppealsReviewView(state=None)
    interaction = _interaction()

    await view._make_remove_cb(0)(interaction)

    assert "bot was restarted" in _replied(interaction)


# ---------------------------------------------------------------------------
# Who may press
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("view_cls,button", RESTART_GUARDED)
async def test_only_a_league_manager_may_press(tmp_path, view_cls, button):
    """The review happens in the submission channel, which drivers can see. A driver
    confirming the appeals step would finalise a round over the stewards' heads."""
    db_path = await _make_db(tmp_path, name=f"lm_{view_cls.__name__}_{button}")
    view = view_cls(state=_state(db_path))
    interaction = _interaction()
    p1, p2, p3, p4 = _finalisers()

    with _manager(False), p1 as penalty, p2 as appeals, p3, p4:
        await _press(view, button, interaction)

    assert "Only league managers" in _replied(interaction)
    penalty.assert_not_awaited()
    appeals.assert_not_awaited()


async def test_only_a_league_manager_may_remove_a_correction(tmp_path):
    db_path = await _make_db(tmp_path, name="lm_remove")
    state = _state(db_path, appeals=[_penalty()])
    view = AppealsReviewView(state=state)
    interaction = _interaction()

    with _manager(False), patch(
        "leaguebot.results.services.penalty_wizard._refresh_appeals_prompt", new=AsyncMock()
    ):
        await view._make_remove_cb(0)(interaction)

    assert "Only league managers" in _replied(interaction)
    assert len(state.staged_appeals) == 1


# ---------------------------------------------------------------------------
# The approval step
# ---------------------------------------------------------------------------


async def test_make_changes_returns_to_staging_with_the_list_intact(tmp_path):
    """A steward who reaches approval and realises they have missed a penalty must be able
    to go back — otherwise the safe thing to do at that screen is to approve."""
    db_path = await _make_db(
        tmp_path, name="make_changes", round_status="AWAITING_REPORT_VERDICTS"
    )
    state = _state(db_path)
    state.staged.append(_penalty())
    view = ApprovalView(state=state)
    interaction = _interaction()

    with _manager(), patch(
        "leaguebot.results.services.penalty_wizard._refresh_prompt", new=AsyncMock()
    ) as refresh:
        await _press(view, "make_changes_btn", interaction)

    refresh.assert_awaited_once()
    assert len(state.staged) == 1
    assert "staged list is intact" in _replied(interaction)


async def test_approving_finalises_the_review(tmp_path):
    db_path = await _make_db(tmp_path, name="approve")
    view = ApprovalView(state=_state(db_path))

    with _manager(), patch(
        "leaguebot.results.services.result_submission_service.finalize_penalty_review", new=AsyncMock()
    ) as finalise:
        await _press(view, "approve_btn", _interaction())

    finalise.assert_awaited_once()


async def test_an_archived_season_cannot_be_approved_into(tmp_path):
    """A COMPLETED season is the league's published record, and approving a review inside
    one would rewrite results already standing."""
    db_path = await _make_db(tmp_path, name="approve_archived", season_status="COMPLETED")
    view = ApprovalView(state=_state(db_path))
    interaction = _interaction()

    with _manager(), patch(
        "leaguebot.results.services.result_submission_service.finalize_penalty_review", new=AsyncMock()
    ) as finalise:
        await _press(view, "approve_btn", interaction)

    assert "archived" in _replied(interaction)
    finalise.assert_not_awaited()


async def test_a_round_with_no_season_row_is_still_approvable(tmp_path):
    """The guard refuses an archived season, not an unresolvable one — a round whose chain
    cannot be read is a broken state, and refusing to finalise it would strand a review
    with nothing a steward could do about it."""
    db_path = await _make_db(tmp_path, name="approve_noseason")
    state = _state(db_path)
    state.round_id = 9999
    view = ApprovalView(state=state)

    with _manager(), patch(
        "leaguebot.results.services.result_submission_service.finalize_penalty_review", new=AsyncMock()
    ) as finalise:
        await _press(view, "approve_btn", _interaction())

    finalise.assert_awaited_once()


# ---------------------------------------------------------------------------
# The appeals prompt
# ---------------------------------------------------------------------------


async def test_a_remove_button_is_offered_for_each_staged_correction(tmp_path):
    db_path = await _make_db(tmp_path, name="appeals_buttons")
    state = _state(db_path, appeals=[_penalty(101), _penalty(102)])

    view = AppealsReviewView(state=state)

    assert [i.label for i in view.children if str(i.label).startswith("Remove")] == [
        "Remove #1",
        "Remove #2",
    ]


async def test_a_prompt_with_nothing_staged_offers_no_remove_buttons(tmp_path):
    db_path = await _make_db(tmp_path, name="appeals_nobuttons")

    view = AppealsReviewView(state=_state(db_path))

    assert [i for i in view.children if str(i.label).startswith("Remove")] == []


async def test_removing_a_correction_takes_it_off_the_list(tmp_path):
    db_path = await _make_db(tmp_path, name="appeals_remove")
    state = _state(db_path, appeals=[_penalty(101), _penalty(102)])
    view = AppealsReviewView(state=state)
    interaction = _interaction()

    with _manager(), patch(
        "leaguebot.results.services.penalty_wizard._refresh_appeals_prompt", new=AsyncMock()
    ) as refresh:
        await view._make_remove_cb(0)(interaction)

    assert [p.driver_user_id for p in state.staged_appeals] == [102]
    refresh.assert_awaited_once()


async def test_removing_a_correction_says_which_one_went(tmp_path):
    """Two corrections against the same driver differ only in their penalty, and a steward
    who removed the wrong one needs to see it immediately."""
    db_path = await _make_db(tmp_path, name="appeals_remove_says")
    state = _state(db_path, appeals=[_penalty(101)])
    view = AppealsReviewView(state=state)
    interaction = _interaction()

    with _manager(), patch(
        "leaguebot.results.services.penalty_wizard._refresh_appeals_prompt", new=AsyncMock()
    ):
        await view._make_remove_cb(0)(interaction)

    assert "101" in _replied(interaction)


async def test_a_remove_button_past_the_end_is_answered(tmp_path):
    """The list shifts as entries are removed, so a stale button must not remove the wrong
    correction — which would be invisible, because both are removals."""
    db_path = await _make_db(tmp_path, name="appeals_remove_stale")
    state = _state(db_path, appeals=[_penalty(101)])
    view = AppealsReviewView(state=state)
    interaction = _interaction()

    with _manager(), patch(
        "leaguebot.results.services.penalty_wizard._refresh_appeals_prompt", new=AsyncMock()
    ):
        await view._make_remove_cb(3)(interaction)

    assert "no longer exists" in _replied(interaction)
    assert len(state.staged_appeals) == 1


async def test_adding_a_correction_asks_which_session(tmp_path):
    """A round has up to four and a correction applies to one of them; the appeals flow
    stages into its own list, not the penalty one."""
    db_path = await _make_db(tmp_path, name="appeals_add")
    view = AppealsReviewView(state=_state(db_path))
    interaction = _interaction()

    with _manager():
        await _press(view, "add_correction_btn", interaction)

    assert "Select which session" in _replied(interaction)
    assert _sent_view(interaction) is not None


async def test_confirming_with_nothing_staged_finalises_at_once(tmp_path):
    """There is nothing to lose, and a confirmation would be a click for its own sake."""
    db_path = await _make_db(tmp_path, name="appeals_confirm_empty")
    view = AppealsReviewView(state=_state(db_path))

    with _manager(), patch(
        "leaguebot.results.services.result_submission_service.finalize_appeals_review", new=AsyncMock()
    ) as finalise:
        await _press(view, "no_changes_btn", _interaction())

    finalise.assert_awaited_once()


async def test_confirming_with_corrections_staged_asks_first(tmp_path):
    """The button means two different things depending on what is staged, and pressing it
    with a list built up would otherwise silently discard that work."""
    db_path = await _make_db(tmp_path, name="appeals_confirm_staged")
    view = AppealsReviewView(state=_state(db_path, appeals=[_penalty(), _penalty(102)]))
    interaction = _interaction()

    with _manager(), patch(
        "leaguebot.results.services.result_submission_service.finalize_appeals_review", new=AsyncMock()
    ) as finalise:
        await _press(view, "no_changes_btn", interaction)

    finalise.assert_not_awaited()
    assert "**2** staged correction(s)" in _replied(interaction)


async def test_the_confirmation_says_what_clearing_would_do(tmp_path):
    """"Confirm" alone reads as confirming the corrections, which is the opposite."""
    db_path = await _make_db(tmp_path, name="appeals_confirm_says")
    view = AppealsReviewView(state=_state(db_path, appeals=[_penalty()]))
    interaction = _interaction()

    with _manager():
        await _press(view, "no_changes_btn", interaction)

    replied = _replied(interaction)
    assert "discard all of them" in replied
    assert "without any appeal corrections" in replied


# ---------------------------------------------------------------------------
# Clearing the staged corrections
# ---------------------------------------------------------------------------


async def test_confirming_the_clear_discards_and_finalises(tmp_path):
    db_path = await _make_db(tmp_path, name="appeals_clear")
    state = _state(db_path, appeals=[_penalty()])
    view = _AppealsConfirmClearView(state=state)

    with _manager(), patch(
        "leaguebot.results.services.result_submission_service.finalize_appeals_review", new=AsyncMock()
    ) as finalise:
        await _press(view, "confirm_btn", _interaction())

    assert state.staged_appeals == []
    finalise.assert_awaited_once()


@pytest.mark.xfail(strict=True, reason="#482: going back from the clear is not recorded")
async def test_going_back_keeps_the_corrections(tmp_path):
    """The whole purpose of the question: a steward who pressed Confirm by habit gets their
    work back.

    Round 3's appeals review (division Pro) has one correction staged, and Alex is asked whether
    to clear it. Alex presses "No, go back". The correction stays staged, nothing is approved,
    and exactly one cancel line records it, naming the review and Alex, with what became of the
    list and what to do next beneath it."""
    db_path = await _make_db(tmp_path, name="appeals_goback")
    state = _state(db_path, appeals=[_penalty()])
    view = _AppealsConfirmClearView(state=state)
    interaction = _interaction()

    with _manager(True), patch(
        "leaguebot.results.services.result_submission_service.finalize_appeals_review", new=AsyncMock()
    ) as finalise:
        await _press(view, "cancel_btn", interaction)

    assert len(state.staged_appeals) == 1
    finalise.assert_not_awaited()
    (line,) = _all_lines(interaction, state)
    _assert_appeals_clear_abandoned(line, "cancelled by Alex (<@77>)")


async def test_only_a_league_manager_may_confirm_the_clear(tmp_path):
    db_path = await _make_db(tmp_path, name="appeals_clear_lm")
    state = _state(db_path, appeals=[_penalty()])
    view = _AppealsConfirmClearView(state=state)

    with _manager(False), patch(
        "leaguebot.results.services.result_submission_service.finalize_appeals_review", new=AsyncMock()
    ) as finalise:
        await _press(view, "confirm_btn", _interaction())

    assert len(state.staged_appeals) == 1
    finalise.assert_not_awaited()


# ---------------------------------------------------------------------------
# Every refusal is recorded (#482)
#
# The core specification's "The record of what changed": a refusal replies as today and writes
# one line naming the member, what was refused and why. A button names itself and the review it
# belongs to; one pressed after a restart has no review to name.
# ---------------------------------------------------------------------------

_OF_THE_APPEALS_REVIEW = "of the appeals review of round 3 (Pro)"
_APPEALS_RESTARTED = "⚠️ The bot was restarted. Please wait for the appeals prompt to refresh."
_APPEALS_NOT_A_MANAGER = "⛔ Only league managers can interact with the penalty review."
_ENTRY_GONE = "⚠️ That entry no longer exists (the list may have changed)."
_NOTHING_TO_APPROVE = (
    "⚠️ No corrections are staged. Use **No Changes / Confirm** to finalise without corrections."
)
_APPEALS_BUTTONS = ["➕ Add Correction", "No Changes / Confirm", "✅ Approve"]
_APPEALS_CLEAR = "Yes, clear and proceed with no corrections"


def _appeals_refusal(case_id, kind, label, reply, *, restarted=False, manager=True,
                     staged=1, remaining=None):
    return pytest.param(
        kind, label, reply, restarted, manager, staged, remaining,
        id=case_id,
    )


_APPEALS_REFUSALS = [
    *(
        _appeals_refusal(f"restarted-{label}", "review", label, _APPEALS_RESTARTED,
                         restarted=True)
        for label in [*_APPEALS_BUTTONS, "Remove #1"]
    ),
    *(
        _appeals_refusal(f"not-a-manager-{label}", "review", label, _APPEALS_NOT_A_MANAGER,
                         manager=False)
        for label in [*_APPEALS_BUTTONS, "Remove #1"]
    ),
    _appeals_refusal("not-a-manager-clear", "clear", _APPEALS_CLEAR, _APPEALS_NOT_A_MANAGER,
                     manager=False),
    _appeals_refusal("entry-gone-Remove #1", "review", "Remove #1", _ENTRY_GONE, remaining=0),
    _appeals_refusal("nothing-staged-Approve", "review", "✅ Approve", _NOTHING_TO_APPROVE,
                     staged=0),
]


@pytest.mark.parametrize(
    "kind, label, reply, restarted, manager, staged, remaining", _APPEALS_REFUSALS
)
async def test_every_refused_press_of_the_appeals_review_is_recorded(
    tmp_path, kind, label, reply, restarted, manager, staged, remaining
):
    """Alex presses a button of round 3's appeals review (division Pro, awaiting appeal
    verdicts, one correction staged) — the review itself or the clear confirmation — and is
    refused: after a restart, without the tier, on a Remove whose correction has already gone,
    or approving with nothing staged. The reply is today's, nothing is staged, removed or
    approved, and exactly one line records the refusal, naming the button, the review, Alex and
    the reason."""
    db_path = await _make_db(tmp_path, name="appeals_refusals")
    state = None if restarted else _state(db_path, appeals=[_penalty()] * staged)
    if kind == "clear":
        view = _AppealsConfirmClearView(state=state)
    else:
        view = AppealsReviewView(state=state)
    if restarted and label.startswith("Remove"):
        press = view._make_remove_cb(0)
    else:
        press = next(
            item for item in view.children if getattr(item, "label", None) == label
        ).callback
    if remaining is not None:
        del state.staged_appeals[remaining:]
    interaction = _interaction()
    p1, p2, p3, p4 = _finalisers()

    with _manager(manager), p1 as penalty, p2 as appeals, p3 as refresh, p4 as refresh_appeals:
        await press(interaction)

    penalty.assert_not_awaited()
    appeals.assert_not_awaited()
    refresh.assert_not_awaited()
    refresh_appeals.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    if state is not None:
        assert len(state.staged_appeals) == (staged if remaining is None else remaining)
    (replied,) = [
        call.args[0] for call in interaction.response.send_message.await_args_list
    ] + [call.args[0] for call in interaction.followup.send.await_args_list]
    assert replied == reply
    reason = reply.split(" ", 1)[1]
    (line,) = [call.args[0] for call in interaction.client.output_router.post_log.await_args_list]
    if restarted:
        assert line.startswith(f"⛔ the “{label}” button"), line
        assert line.endswith(f" refused for Alex (<@77>) — {reason}"), line
    else:
        assert line == (
            f"⛔ the “{label}” button {_OF_THE_APPEALS_REVIEW} refused for Alex (<@77>) — {reason}"
        )


@pytest.mark.xfail(
    strict=True,
    reason="#482: the appeals controls still act once the appeals are approved (D3)",
)
@pytest.mark.parametrize(
    "kind, label",
    [
        *(("review", label) for label in [*_APPEALS_BUTTONS, "Remove #1"]),
        ("clear", _APPEALS_CLEAR),
    ],
)
async def test_every_appeals_control_refuses_once_the_appeals_are_approved(
    tmp_path, kind, label
):
    """**D3.** Round 3's appeals (division Pro) have been approved and the round is FINAL, but
    the appeals prompt and its clear confirmation are still on screen with one correction
    staged. Alex, a league manager, presses one of their buttons. It is refused: nothing is
    staged, removed, cleared or approved, and exactly one line records the refusal, naming the
    button, the review, Alex and the reason."""
    db_path = await _make_db(tmp_path, name="appeals_approved", round_status="FINAL")
    state = _state(db_path, appeals=[_penalty()])
    view = (
        _AppealsConfirmClearView(state=state) if kind == "clear"
        else AppealsReviewView(state=state)
    )
    button = next(item for item in view.children if getattr(item, "label", None) == label)
    interaction = _interaction()
    p1, p2, p3, p4 = _finalisers()

    with _manager(True), p1 as penalty, p2 as appeals, p3 as refresh, p4 as refresh_appeals:
        await button.callback(interaction)

    penalty.assert_not_awaited()
    appeals.assert_not_awaited()
    refresh.assert_not_awaited()
    refresh_appeals.assert_not_awaited()
    assert len(state.staged_appeals) == 1
    assert _sent_view(interaction) is None
    (replied,) = [
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
    ]
    reason = replied.splitlines()[0].split(" ", 1)[1]
    (line,) = [call.args[0] for call in interaction.client.output_router.post_log.await_args_list]
    assert line == (
        f"⛔ the “{label}” button {_OF_THE_APPEALS_REVIEW} refused for Alex (<@77>) — {reason}"
    )


# ---------------------------------------------------------------------------
# Every press is recorded (#482)
#
# The owner's decision "Every press": each press that changes the appeals review writes one line
# in the success form, naming the member, the review and what was removed or cleared.
# ---------------------------------------------------------------------------


def _all_lines(interaction, state) -> list[str]:
    """Every line written, through the press's client or through the review's bot."""
    return [
        call.args[0]
        for call in interaction.client.output_router.post_log.await_args_list
        + state.bot.output_router.post_log.await_args_list
    ]


@pytest.mark.xfail(strict=True, reason="#482: the appeals review press writes no line")
@pytest.mark.parametrize(
    "kind, label",
    [("review", "Remove #1"), ("clear", _APPEALS_CLEAR)],
    ids=["remove-correction", "clear"],
)
async def test_every_press_of_the_appeals_review_writes_one_line(tmp_path, kind, label):
    """Round 3's appeals review (division Pro) has two +5s corrections staged, for drivers 101
    and 102. Alex, a league manager, presses Remove #1, or "Yes, clear and proceed with no
    corrections" (the approval itself stubbed). The press does what it does today, and exactly
    one line records it, naming Alex, the button, the review and what was removed or cleared."""
    db_path = await _make_db(tmp_path, name=f"appeals_press_{kind}")
    state = _state(db_path, appeals=[_penalty(101), _penalty(102)])
    view = (
        _AppealsConfirmClearView(state=state) if kind == "clear"
        else AppealsReviewView(state=state)
    )
    button = next(item for item in view.children if getattr(item, "label", None) == label)
    interaction = _interaction()
    p1, p2, p3, p4 = _finalisers()

    with _manager(True), p1, p2 as appeals, p3, p4:
        await button.callback(interaction)

    removed = ["<@101>", "<@102>"] if kind == "clear" else ["<@101>"]
    assert [p.driver_user_id for p in state.staged_appeals] == ([] if kind == "clear" else [102])
    assert appeals.await_count == (1 if kind == "clear" else 0)
    (line,) = _all_lines(interaction, state)
    assert not line.startswith(("⛔", "↩️", "⌛")), line
    for fragment in ("Alex (<@77>)", label, "appeals review of round 3 (Pro)", "+5s", *removed):
        assert fragment in line, (fragment, line)


async def test_no_changes_writes_no_line_beside_the_approval_s_own(tmp_path):
    """Round 3's appeals review (division Pro) has nothing staged. Alex presses No Changes /
    Confirm, which approves the appeals at once; the approval (stubbed) writes its own line. The
    press writes no second one: one action, one line."""
    db_path = await _make_db(tmp_path, name="appeals_no_changes_line")
    state = _state(db_path)
    view = AppealsReviewView(state=state)
    interaction = _interaction()

    async def _approved(approving, _state, **_kwargs):
        await approving.client.output_router.post_log("the approval's own line")

    with _manager(True), patch(
        "leaguebot.results.services.result_submission_service.finalize_appeals_review",
        new=AsyncMock(side_effect=_approved),
    ) as finalise:
        await _press(view, "no_changes_btn", interaction)

    finalise.assert_awaited_once()
    assert _all_lines(interaction, state) == ["the approval's own line"]


# ---------------------------------------------------------------------------
# The clear confirmation's cancel and lapse are recorded (#482)
# ---------------------------------------------------------------------------

_APPEALS_CLEAR_KEPT = "Nothing was cleared; the staged list stands."


def _assert_appeals_clear_abandoned(line: str, ending: str) -> None:
    """*line* is one cancel or lapse line of the clear confirmation of round 3's appeals review
    (Pro), ending its first line with *ending*, and saying beneath that nothing was cleared and
    what to do next."""
    first, *detail = line.splitlines()
    assert first.startswith(("↩️ ", "⌛ ")), line
    assert "appeals review of round 3 (Pro)" in first, line
    assert first.endswith(ending), line
    assert _APPEALS_CLEAR_KEPT in "\n".join(detail), line
    assert not detail[-1].strip().endswith(_APPEALS_CLEAR_KEPT), f"no next step: {line}"


@pytest.mark.xfail(strict=True, reason="#482: the clear confirmation's lapse is not recorded")
async def test_an_appeals_clear_confirmation_left_to_lapse_is_recorded(tmp_path):
    """Round 3's appeals review (division Pro) has one correction staged. Alex presses No
    Changes / Confirm and is asked whether to clear it, then answers nothing until the question
    lapses. The correction stays staged, nothing is approved, and exactly one lapse line records
    it, naming the review and Alex as the one who started it, with what became of the list and
    what to do next beneath it."""
    db_path = await _make_db(tmp_path, name="appeals_clear_lapse")
    state = _state(db_path, appeals=[_penalty()])
    view = AppealsReviewView(state=state)
    interaction = _interaction()
    interaction.edit_original_response = AsyncMock()

    with _manager(True), patch(
        "leaguebot.results.services.result_submission_service.finalize_appeals_review", new=AsyncMock()
    ) as finalise:
        await _press(view, "no_changes_btn", interaction)
        await _sent_view(interaction).on_timeout()

    assert len(state.staged_appeals) == 1
    finalise.assert_not_awaited()
    (line,) = _all_lines(interaction, state)
    assert line.startswith("⌛ "), line
    _assert_appeals_clear_abandoned(line, "lapsed unconfirmed (started by Alex (<@77>))")
