"""The penalty review once the job its controls belong to has moved on.

Issue #402. Every control of a round's penalty review acts on a `PenaltyReviewState` held in
memory, and nothing they ran asked whether the stage that state was built for was still the
round's current one. So the approval message's **Approve** finalised a round on results a
resubmission was replacing, the review prompt's **Remove** said a penalty was removed through the
appeals stage when it had been applied, and a second **Approve** ran the whole approval again.

**One check answers for every control**, `_review_moved_on`. A first pass is current while its
reports are not already being approved, the round still awaits its report verdicts, no
resubmission is collecting, and its prompt is the one the channel records — the last is how a
review is told apart from the one that replaced it, since each review posts its own prompt and
records it. Each of the four is a test here, against the production schema.

**An amendment is FINAL throughout**, so none of the four applies to it. Its review closes once
its reports are approved, pardons included, as a first pass's does (decided 2026-09-23). Its
pardons are still written only when its appeals are approved, as everything an amendment commits
is (#345), but they are changed in its report stage alone.

**The approval message confirms the review as it stood when it was posted**, and its Approve
commits the review as it stands when pressed. So it is withdrawn whenever the review changes, a
second one replaces the first, and its buttons act only on the message the review records.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
from leaguebot.results.models.points_config import SessionType  # noqa: E402
from leaguebot.results.services.penalty_service import StagedPenalty  # noqa: E402
from leaguebot.results.services.penalty_wizard import (  # noqa: E402
    AddPardonModal,
    ApprovalView,
    PenaltyReviewState,
    PenaltyReviewView,
    StagedPardon,
    _refresh_prompt,
    _review_moved_on,
    _show_approval_step,
)

SERVER_ID = 14402
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
CHANNEL_ID = 700
PROMPT_ID = 880001


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    round_status: str = "AWAITING_REPORT_VERDICTS",
    resubmitting: int = 0,
    prompt_message_id: int | None = PROMPT_ID,
    closed: int = 0,
) -> str:
    db_path = os.path.join(str(tmp_path), "moves_on.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
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
            "in_penalty_review, results_posted, resubmitting, prompt_message_id, closed) "
            "VALUES (?, ?, '2026-02-01T00:00:00+00:00', 1, 1, ?, ?, ?)",
            (ROUND_ID, CHANNEL_ID, resubmitting, prompt_message_id, closed),
        )
        await db.commit()
    return db_path


def _state(db_path: str, *, prompt_message_id: int | None = PROMPT_ID) -> PenaltyReviewState:
    state = PenaltyReviewState(
        round_id=ROUND_ID,
        division_id=DIVISION_ID,
        submission_channel_id=CHANNEL_ID,
        session_types_present=[SessionType.FEATURE_RACE],
        db_path=db_path,
        bot=MagicMock(),
        round_number=3,
        division_name="Pro",
    )
    state.prompt_message_id = prompt_message_id
    return state


# ---------------------------------------------------------------------------
# A first pass
# ---------------------------------------------------------------------------


async def test_a_review_awaiting_its_reports_is_current(tmp_path):
    db_path = await _make_db(tmp_path)

    assert await _review_moved_on(_state(db_path)) is None


async def test_a_review_whose_reports_are_approved_has_moved_on(tmp_path):
    """**The review prompt stayed up, and worked, through the appeals stage.** Its Remove said a
    penalty was removed that had been applied, and its Approve ran the approval a second time.
    A first pass writes its pardons as its reports are approved, so they close with them."""
    db_path = await _make_db(tmp_path, round_status="AWAITING_APPEAL_VERDICTS")

    refusal = await _review_moved_on(_state(db_path))

    assert refusal is not None
    assert "already been approved" in refusal
    assert "appeals are reviewed below" in refusal


@pytest.mark.parametrize("status", ["FINAL", "CANCELLED"])
async def test_a_settled_round_has_moved_on(tmp_path, status):
    """Disabling the results module closes every round still awaiting a review (#167), and a
    client still holding the prompt can press it."""
    db_path = await _make_db(tmp_path, round_status=status, closed=1)

    refusal = await _review_moved_on(_state(db_path))

    assert refusal is not None
    assert "review is over" in refusal


async def test_a_review_being_resubmitted_has_moved_on(tmp_path):
    """**The approval message outlived the resubmission**, and its Approve finalised the round on
    the results the manager had just said were wrong."""
    db_path = await _make_db(tmp_path, resubmitting=1)

    refusal = await _review_moved_on(_state(db_path))

    assert refusal is not None
    assert "being resubmitted" in refusal


async def test_a_review_replaced_by_a_newer_prompt_has_moved_on(tmp_path):
    """**Cancelling a resubmission posts a fresh review**, and left the old approval message
    working beside it, holding the state from before. Round and channel look exactly as they did,
    so only the prompt the channel records tells the two reviews apart."""
    db_path = await _make_db(tmp_path, prompt_message_id=PROMPT_ID + 1)

    refusal = await _review_moved_on(_state(db_path))

    assert refusal is not None
    assert "replaced by a newer one" in refusal


async def test_a_review_being_approved_has_moved_on(tmp_path):
    """**The controls stay on screen while the approval draws the round's graphics**, which on the
    Pi is a long time, and nothing in the database says the review is closing until it moves the
    round on. A second Approve pressed then ran the whole approval twice."""
    db_path = await _make_db(tmp_path)
    state = _state(db_path)
    state.approving = True

    refusal = await _review_moved_on(state)

    assert refusal is not None
    assert "being approved" in refusal


# ---------------------------------------------------------------------------
# An amendment
# ---------------------------------------------------------------------------


def _amendment(db_path: str, *, reports_approved: bool) -> PenaltyReviewState:
    state = _state(db_path, prompt_message_id=990001)
    state.is_amendment = True
    state.reports_approved = reports_approved
    return state


async def test_an_amendments_reports_close_once_approved(tmp_path):
    """Its Remove said a report was removed after the stage that applied it was approved, and its
    Add staged one that would never be applied."""
    db_path = await _make_db(tmp_path, round_status="FINAL", closed=1)

    assert await _review_moved_on(_amendment(db_path, reports_approved=False)) is None
    refusal = await _review_moved_on(_amendment(db_path, reports_approved=True))
    assert refusal is not None
    assert "already approved" in refusal


@pytest.mark.parametrize("control", ["pardon_btn", "pw_pardon_remove_0", "AddPardonModal"])
async def test_an_amendments_pardons_close_with_its_reports(tmp_path, control):
    """**As a first pass's do** (decided 2026-09-23). They stayed open through an amendment's
    appeals, because that is when it writes them; the specs had them changed in the report stage
    alone. Each of the three pardon controls is driven here, with the real check behind it."""
    db_path = await _make_db(tmp_path, round_status="FINAL", closed=1)
    state = _amendment(db_path, reports_approved=True)
    state.staged_pardons = [
        StagedPardon(
            driver_user_id=4001, driver_profile_id=31, attendance_id=41,
            pardon_type="ABSENT", justification="Ill", grantor_id=77,
        )
    ]
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    with patch(
        "leaguebot.results.services.penalty_wizard._is_league_manager", new=AsyncMock(return_value=True)
    ), patch("leaguebot.results.services.penalty_wizard._refresh_prompt", new=AsyncMock()) as refresh:
        if control == "AddPardonModal":
            await AddPardonModal(state).on_submit(interaction)
            replied = interaction.followup.send.await_args.args[0]
        else:
            view = PenaltyReviewView(state)
            if control == "pardon_btn":
                await type(view).pardon_btn(view, interaction, MagicMock())
            else:
                await next(
                    c for c in view.children if c.custom_id == control
                ).callback(interaction)
            replied = interaction.response.send_message.await_args.args[0]

    assert "nor its pardons" in replied
    assert len(state.staged_pardons) == 1
    interaction.response.send_modal.assert_not_awaited()
    refresh.assert_not_awaited()


# ---------------------------------------------------------------------------
# The approval message
# ---------------------------------------------------------------------------


class _Channel:
    """The submission channel, keeping what the review posts to it and deletes from it."""

    def __init__(self) -> None:
        self.posted: list[int] = []
        self.deleted: list[int] = []

    async def send(self, content, view=None):
        message = MagicMock()
        message.id = 990001 + len(self.posted)
        self.posted.append(message.id)
        return message

    async def fetch_message(self, message_id):
        message = MagicMock()
        message.id = message_id
        message.edit = AsyncMock()

        async def _delete():
            self.deleted.append(message_id)

        message.delete = _delete
        return message


def _in_channel(state: PenaltyReviewState) -> _Channel:
    channel = _Channel()
    state.bot.get_channel = MagicMock(return_value=channel)
    return channel


async def test_a_second_approval_message_replaces_the_first(tmp_path):
    """Pressing **No Penalties / Confirm** twice left two approval messages standing, each of
    which approved the round."""
    state = _state(await _make_db(tmp_path))
    channel = _in_channel(state)

    await _show_approval_step(MagicMock(), state)
    first = state.approval_message_id
    await _show_approval_step(MagicMock(), state)

    assert channel.deleted == [first]
    assert state.approval_message_id == channel.posted[-1] != first


async def test_changing_the_staged_list_withdraws_the_approval_message(tmp_path):
    """**It said there were no penalties, and applied one.** **Make Changes** went back to the
    review and left the approval message up; a penalty staged then was applied by its Approve."""
    state = _state(await _make_db(tmp_path))
    channel = _in_channel(state)
    await _show_approval_step(MagicMock(), state)
    approval = state.approval_message_id

    state.staged.append(
        StagedPenalty(
            driver_user_id=4001, session_type=SessionType.FEATURE_RACE,
            penalty_type="TIME", penalty_seconds=5,
        )
    )
    await _refresh_prompt(state)

    assert channel.deleted == [approval]
    assert state.approval_message_id is None


@pytest.mark.parametrize("button", ["make_changes_btn", "approve_btn"])
@pytest.mark.parametrize("recorded", [None, 990002], ids=["withdrawn", "replaced"])
async def test_an_approval_message_no_longer_current_approves_nothing(tmp_path, button, recorded):
    """A withdrawn message leaves the channel, but a client already showing it can still press
    it — and one left behind by a resubmission, or by a second **No Penalties / Confirm**, was
    never withdrawn at all. Only the message the review records acts."""
    state = _state(await _make_db(tmp_path))
    state.approval_message_id = recorded
    view = ApprovalView(state)
    interaction = MagicMock()
    interaction.message.id = 990001
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()

    with patch(
        "leaguebot.results.services.penalty_wizard._is_league_manager", new=AsyncMock(return_value=True)
    ), patch(
        "leaguebot.results.services.result_submission_service.finalize_penalty_review", new=AsyncMock()
    ) as finalise, patch("leaguebot.results.services.penalty_wizard._refresh_prompt", new=AsyncMock()) as refresh:
        await getattr(type(view), button)(view, interaction, MagicMock())

    assert "withdrawn" in interaction.response.send_message.await_args.args[0]
    finalise.assert_not_awaited()
    refresh.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
