"""The approval message names the attendance pardons its Approve grants.

Issue #403. The review prompt's **✅ Approve** stays greyed out until a penalty is staged, so a
round whose review holds only attendance pardons is committed through **No Penalties / Confirm**
and the approval message that button posts. That message was built from the staged penalties
alone. Both ways to it arrive with none — the button only when nothing is staged, its
confirmation only after clearing the list — so it always read "No penalties staged" and named no
pardon, while its Approve granted every one. The attendance specification has the pardons shown
together with the staged penalties; the prompt showed them, and the last message a manager reads
before committing did not.

**An amendment reaches it the same way.** Its report stage is the same screen, reloaded with the
pardons the round already carries, and an amendment whose sessions carry no report goes on
through the same button.

**The justification stays out of it.** It is for the log channel alone, the prompt does not show
it, and the approval message is posted to the same channel.

The texts are read here from the messages as posted, against the production schema, with the
real currency check behind each button.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
from leaguebot.results.models.points_config import SessionType  # noqa: E402
from leaguebot.results.services.penalty_service import StagedPenalty  # noqa: E402
from leaguebot.results.services.penalty_wizard import (  # noqa: E402
    PenaltyReviewState,
    PenaltyReviewView,
    StagedPardon,
    _ConfirmClearView,
    _show_approval_step,
)

SERVER_ID = 14403
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
CHANNEL_ID = 700
PROMPT_ID = 880001


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path, *, round_status: str = "AWAITING_REPORT_VERDICTS", closed: int = 0
) -> str:
    db_path = os.path.join(str(tmp_path), "approval_pardons.db")
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
            "VALUES (?, ?, '2026-02-01T00:00:00+00:00', 1, 1, 0, ?, ?)",
            (ROUND_ID, CHANNEL_ID, PROMPT_ID, closed),
        )
        await db.commit()
    return db_path


def _pardons() -> list[StagedPardon]:
    return [
        StagedPardon(
            driver_user_id=4001, driver_profile_id=31, attendance_id=41,
            pardon_type="ABSENT", justification="Hospital visit", grantor_id=77,
        ),
        StagedPardon(
            driver_user_id=4002, driver_profile_id=32, attendance_id=42,
            pardon_type="NO_RSVP", justification="Travelling abroad", grantor_id=77,
        ),
    ]


def _penalty() -> StagedPenalty:
    return StagedPenalty(
        driver_user_id=4003, session_type=SessionType.FEATURE_RACE,
        penalty_type="TIME", penalty_seconds=5,
    )


def _state(db_path: str, *, pardons: list[StagedPardon] | None = None) -> PenaltyReviewState:
    state = PenaltyReviewState(
        round_id=ROUND_ID,
        division_id=DIVISION_ID,
        submission_channel_id=CHANNEL_ID,
        session_types_present=[SessionType.FEATURE_RACE],
        db_path=db_path,
        bot=MagicMock(),
        staged_pardons=pardons or [],
        round_number=3,
        division_name="Pro",
    )
    state.prompt_message_id = PROMPT_ID
    return state


class _Channel:
    """The submission channel, keeping the text of every message the review posts to it."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, content, view=None):
        self.sent.append(content)
        message = MagicMock()
        message.id = 990000 + len(self.sent)
        return message

    async def fetch_message(self, message_id):
        message = MagicMock()
        message.delete = AsyncMock()
        return message


def _in_channel(state: PenaltyReviewState) -> _Channel:
    channel = _Channel()
    state.bot.get_channel = MagicMock(return_value=channel)
    return channel


def _interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _a_league_manager():
    return patch(
        "leaguebot.results.services.penalty_wizard._is_league_manager", new=AsyncMock(return_value=True)
    )


def _names_both_pardons(message: str) -> bool:
    return (
        "**Staged Attendance Pardons (2):**" in message
        and "<@4001> — **ABSENT**" in message
        and "<@4002> — **NO_RSVP**" in message
    )


# ---------------------------------------------------------------------------
# The message
# ---------------------------------------------------------------------------


async def test_the_approval_message_lists_the_staged_pardons(tmp_path):
    """**It said nothing was staged, and granted two pardons.**"""
    state = _state(await _make_db(tmp_path), pardons=_pardons())
    channel = _in_channel(state)

    await _show_approval_step(MagicMock(), state)

    (message,) = channel.sent
    assert "No penalties staged" in message
    assert _names_both_pardons(message)
    assert "pardons granted" in message


async def test_the_approval_message_withholds_a_pardons_justification(tmp_path):
    """The justification is for the log channel alone, and this message is posted in the open."""
    state = _state(await _make_db(tmp_path), pardons=_pardons())
    channel = _in_channel(state)

    await _show_approval_step(MagicMock(), state)

    (message,) = channel.sent
    assert "Hospital visit" not in message
    assert "Travelling abroad" not in message


async def test_the_approval_message_with_nothing_staged_names_no_pardon_and_does_not_finalise(
    tmp_path,
):
    """Approving it opens the appeals; the round is final only once they are approved."""
    state = _state(await _make_db(tmp_path))
    channel = _in_channel(state)

    await _show_approval_step(MagicMock(), state)

    (message,) = channel.sent
    assert "No penalties staged" in message
    assert "Attendance Pardons" not in message
    assert "appeals" in message
    assert "finali" not in message.lower()


# ---------------------------------------------------------------------------
# The ways to it
# ---------------------------------------------------------------------------


async def test_no_penalties_confirm_with_only_pardons_staged_names_them(tmp_path):
    """The issue's reproduction. **✅ Approve** on the prompt is greyed out with only pardons
    staged, so this button is the one the manager has."""
    state = _state(await _make_db(tmp_path), pardons=_pardons())
    channel = _in_channel(state)
    view = PenaltyReviewView(state)
    assert next(c for c in view.children if c.custom_id == "pw_approve").disabled

    with _a_league_manager():
        await type(view).no_penalties_btn(view, _interaction(), MagicMock())

    (message,) = channel.sent
    assert _names_both_pardons(message)


async def test_clearing_the_penalties_keeps_the_pardons_on_the_approval_message(tmp_path):
    """Confirming the clear discards the penalties and nothing else, and the message says so."""
    state = _state(await _make_db(tmp_path), pardons=_pardons())
    state.staged.append(_penalty())
    channel = _in_channel(state)
    view = _ConfirmClearView(state)

    with _a_league_manager():
        await type(view).confirm_btn(view, _interaction(), MagicMock())

    assert state.staged == []
    (message,) = channel.sent
    assert "No penalties staged" in message
    assert "<@4003>" not in message
    assert _names_both_pardons(message)


async def test_an_amendments_approval_message_lists_the_pardons_it_reopened(tmp_path):
    """An amendment's report stage shows back the pardons the round carries, and approving it
    settles them. With no report among the amended sessions, this is the way on."""
    state = _state(await _make_db(tmp_path, round_status="FINAL", closed=1), pardons=_pardons())
    state.is_amendment = True
    channel = _in_channel(state)
    view = PenaltyReviewView(state)

    with _a_league_manager():
        await type(view).no_penalties_btn(view, _interaction(), MagicMock())

    (message,) = channel.sent
    assert _names_both_pardons(message)
    assert "appeals" in message
