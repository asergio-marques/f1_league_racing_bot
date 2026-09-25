"""The appeals review prompt, and keeping both prompts in step with what is staged.

Issue #208, continuing `tests/results/test_penalty_wizard_rendering.py`. The appeals review is the
second of the two review passes a round goes through: penalties first, then — once the
post-race penalty results have been posted — appeals against them, and approving the appeals
pass is what makes a round final.

**The two prompts are deliberately separate and say different things.** The penalty prompt
lists the round's attendees and any staged pardons; the appeals prompt lists neither, because
by then attendance is settled and pardons belong to the earlier pass. A reader merging them
would put a pardon in front of a manager at the point they can no longer act on it, and put
the attendee list in front of them twice.

**A correction may give time back.** An appeal that succeeds reduces a penalty, so a negative
value is the normal case here in a way it is not in the penalty pass, and it has to render as
a credit rather than as a malformed penalty.

**The refresh is what keeps the message honest.** Staging a correction changes only in-memory
state, so the message a manager is reading would still show the old list unless the prompt is
edited — and they approve the round from that message. The view is rebuilt and re-registered
each time so the removal buttons keep matching the numbered list beside them; a refresh that
edited only the text would leave "Remove #2" pointing at whatever used to be second.

**A prompt that has gone is not an error.** The message can be deleted by hand mid-review, and
failing to edit it must not take down the review the manager is part-way through.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest


# The results pipeline's session types, not the weather module's — `penalty_service`
# and `result_submission_service` both import this one, and these are the values
# `session_results.session_type` actually holds.
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.penalty_service import StagedPenalty
from leaguebot.results.services.penalty_wizard import (
    PenaltyReviewState,
    _refresh_appeals_prompt,
    _render_appeals_prompt_content,
)

SERVER_ID = 10508
ROUND_ID = 5
DIVISION_ID = 11
PROMPT_ID = 900801
CHANNEL_ID = 770801
DRIVER_A = 4001
DRIVER_B = 4002


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _correction(
    driver_user_id: int = DRIVER_A,
    seconds: int | None = -5,
    penalty_type: str = "TIME",
    session_type: SessionType = SessionType.FEATURE_RACE,
) -> StagedPenalty:
    return StagedPenalty(
        driver_user_id=driver_user_id,
        session_type=session_type,
        penalty_type=penalty_type,  # type: ignore[arg-type]
        penalty_seconds=seconds,
    )


@pytest.fixture(autouse=True)
def _no_driver_changed_account(monkeypatch):
    """No driver here has changed account, so the prompt names each as staged (issue #243).

    The state points at no real database; the account map the prompt reads is the one query
    it makes, and naming by the current account is pinned in `test_add_penalty_modal.py`.
    """
    import leaguebot.results.services.penalty_wizard as penalty_wizard

    monkeypatch.setattr(
        penalty_wizard, "current_account_map_for_division", AsyncMock(return_value={})
    )


def _state(*, appeals=(), prompt_id: int | None = PROMPT_ID, channel=...):
    bot = MagicMock()
    resolved = MagicMock() if channel is ... else channel
    if resolved is not None:
        message = MagicMock()
        message.edit = AsyncMock(return_value=None)
        resolved.fetch_message = AsyncMock(return_value=message)
        resolved._message = message
    bot.get_channel = MagicMock(return_value=resolved)
    bot.add_view = MagicMock()

    state = PenaltyReviewState(
        round_id=ROUND_ID,
        division_id=DIVISION_ID,
        submission_channel_id=CHANNEL_ID,
        session_types_present=[SessionType.FEATURE_RACE],
        db_path=":memory:",
        bot=bot,
    )
    state.round_number = 3
    state.division_name = "Division 1"
    state.staged_appeals = list(appeals)
    state.appeals_prompt_message_id = prompt_id
    state._channel = resolved  # type: ignore[attr-defined]
    return state


# ---------------------------------------------------------------------------
# The appeals prompt
# ---------------------------------------------------------------------------


async def test_the_prompt_names_the_round_and_division():
    content = await _render_appeals_prompt_content(_state())

    assert "Appeals Review" in content
    assert "Round 3" in content
    assert "Division 1" in content


async def test_the_prompt_says_what_approving_will_do():
    """Approving the appeals pass makes the round final, which is the one irreversible
    step in the results pipeline."""
    content = await _render_appeals_prompt_content(_state())

    assert "finalise" in content


async def test_nothing_staged_says_so_rather_than_showing_a_blank():
    """A blank section reads as a rendering failure; a manager needs to be sure there is
    genuinely nothing staged before approving."""
    content = await _render_appeals_prompt_content(_state(appeals=()))

    assert "Staged Corrections:" in content
    assert "click Add Correction" in content


async def test_every_staged_correction_appears():
    """The manager finalises the round from this message, so a correction missing from it
    would be applied on approval without anybody having read it."""
    content = await _render_appeals_prompt_content(
        _state(appeals=[_correction(DRIVER_A, -5), _correction(DRIVER_B, -10)])
    )

    assert "Staged Corrections (2)" in content
    assert f"<@{DRIVER_A}>" in content
    assert f"<@{DRIVER_B}>" in content


async def test_a_correction_giving_time_back_reads_as_a_credit():
    """A successful appeal reduces a penalty, so a negative value is the normal case here
    — rendered with a bare `+` it would read as an additional penalty."""
    content = await _render_appeals_prompt_content(_state(appeals=[_correction(seconds=-5)]))

    assert "-5s" in content
    assert "+-5s" not in content


async def test_a_correction_adding_time_reads_as_a_penalty():
    """An appeal can go the other way — a steward reviewing an incident may increase it."""
    content = await _render_appeals_prompt_content(_state(appeals=[_correction(seconds=5)]))

    assert "+5s" in content


async def test_a_disqualification_correction_is_named_as_one():
    content = await _render_appeals_prompt_content(
        _state(appeals=[_correction(seconds=None, penalty_type="DSQ")])
    )

    assert "DSQ" in content


async def test_each_correction_is_numbered_for_removal():
    """The removal buttons are numbered, so the list has to agree with them or a manager
    removes a correction they meant to keep."""
    content = await _render_appeals_prompt_content(
        _state(appeals=[_correction(DRIVER_A), _correction(DRIVER_B)])
    )

    assert "Remove #1" in content
    assert "Remove #2" in content


async def test_the_session_a_correction_belongs_to_is_named():
    content = await _render_appeals_prompt_content(
        _state(appeals=[_correction(session_type=SessionType.FEATURE_QUALIFYING)])
    )

    assert "Feature Qualifying" in content


async def test_the_appeals_prompt_carries_no_attendee_list():
    """Attendance is settled by this point, and repeating it would put the manager back in
    the earlier pass."""
    content = await _render_appeals_prompt_content(_state())

    assert "Preliminary Attendees" not in content


async def test_the_appeals_prompt_carries_no_pardons():
    """Pardons belong to the penalty pass. Showing them here would put one in front of a
    manager at the point they can no longer act on it."""
    content = await _render_appeals_prompt_content(_state())

    assert "Pardon" not in content


# ---------------------------------------------------------------------------
# Keeping the message in step
# ---------------------------------------------------------------------------


async def test_the_prompt_is_edited_when_the_staged_list_changes():
    """Staging changes only in-memory state, so without this the manager would approve
    from a message showing the old list."""
    state = _state(appeals=[_correction()])

    await _refresh_appeals_prompt(state)

    state._channel._message.edit.assert_awaited_once()
    assert f"<@{DRIVER_A}>" in state._channel._message.edit.await_args.kwargs["content"]


async def test_the_view_is_rebuilt_so_the_buttons_match_the_list():
    """A refresh that edited only the text would leave "Remove #2" pointing at whatever
    used to be second."""
    state = _state(appeals=[_correction()])

    await _refresh_appeals_prompt(state)

    assert state._channel._message.edit.await_args.kwargs["view"] is not None


async def test_the_rebuilt_view_is_registered_against_the_message():
    """Persistent views are keyed by message id; a rebuilt view that was not registered
    would stop responding after the next restart."""
    state = _state(appeals=[_correction()])

    await _refresh_appeals_prompt(state)

    state.bot.add_view.assert_called_once()
    assert state.bot.add_view.call_args.kwargs["message_id"] == PROMPT_ID


async def test_a_review_with_no_prompt_posted_is_not_refreshed():
    """Nothing to edit — the prompt has not gone out yet."""
    state = _state(prompt_id=None)

    await _refresh_appeals_prompt(state)

    state.bot.get_channel.assert_not_called()


async def test_a_channel_the_bot_cannot_see_is_survived():
    state = _state(channel=None)

    await _refresh_appeals_prompt(state)  # must not raise

    state.bot.add_view.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        discord.NotFound(MagicMock(), "gone"),
        discord.HTTPException(MagicMock(), "boom"),
    ],
    ids=["not-found", "http-error"],
)
async def test_a_prompt_that_cannot_be_edited_does_not_stop_the_review(error, caplog):
    """The message can be deleted by hand mid-review. Failing here would take down the
    review the manager is part-way through, losing everything staged."""
    state = _state(appeals=[_correction()])
    state._channel.fetch_message = AsyncMock(side_effect=error)

    with caplog.at_level("WARNING"):
        await _refresh_appeals_prompt(state)

    assert "failed to edit appeals prompt" in caplog.text
