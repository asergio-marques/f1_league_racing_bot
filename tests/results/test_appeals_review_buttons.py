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
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
from leaguebot.results.models.points_config import SessionType  # noqa: E402
from leaguebot.results.services.penalty_wizard import (  # noqa: E402
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
    interaction = MagicMock()
    interaction.message.id = APPROVAL_MESSAGE_ID
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = 77
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_modal = AsyncMock()
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


async def test_going_back_keeps_the_corrections(tmp_path):
    """The whole purpose of the question: a steward who pressed Confirm by habit gets their
    work back."""
    db_path = await _make_db(tmp_path, name="appeals_goback")
    state = _state(db_path, appeals=[_penalty()])
    view = _AppealsConfirmClearView(state=state)

    with patch(
        "leaguebot.results.services.result_submission_service.finalize_appeals_review", new=AsyncMock()
    ) as finalise:
        await _press(view, "cancel_btn", _interaction())

    assert len(state.staged_appeals) == 1
    finalise.assert_not_awaited()


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
