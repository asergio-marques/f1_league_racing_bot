"""Showing a settled round's decisions back to the manager, so they can change them (#345).

Stage two of the amendment replay. The corrected classification has been written and posted as
initial results; what the round *decided* about that classification is reviewed next.

**The decisions are read back out of the database.** They were written when the round was
originally reviewed and nothing has held them in memory since. `load_staged_from_records` turns
the stored rows back into the staged entries the review screens already draw, so the manager
sees round 3's four penalties with a Remove button beside each — and a manager who changes
nothing approves exactly what was there before.

**The state is marked an amendment.** That is what stops approving either screen moving the
round: a first pass sets `AWAITING_APPEAL_VERDICTS`, then `FINAL`, finishes the division and may
wind the season down. An amended round is already FINAL and has been through all of it.

**The amendment channel is the review channel.** `submission_channel_id` names it, which is how
stage three reaches the same place without knowing it is an amendment at all.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from services.penalty_service import StagedPenalty  # noqa: E402
from services.result_submission_service import (  # noqa: E402
    run_amendment_review_stages,
)
from tests.support.teams import seed_team_instances  # noqa: E402

SEASON_ID = 31
DIVISION_ID = 41
ROUND_ID = 51
CHANNEL_ID = 6001


async def _db(tmp_path, name: str) -> tuple[str, int]:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 3, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await seed_team_instances(db, DIVISION_ID, 3001)
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, status) "
            "VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', 'NORMAL', 'FINAL')",
            (ROUND_ID, DIVISION_ID),
        )
        session = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (ROUND_ID, DIVISION_ID),
        )
        cursor = await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, "
            "team_instance_id, finishing_position) VALUES (?, 101, 3001, 1)",
            (session.lastrowid,),
        )
        await db.commit()
        return db_path, cursor.lastrowid


async def _penalty(db_path, result_id, *, justification="Wholly at fault"):
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO penalty_records (race_result_id, penalty_type, time_seconds, "
            "description, justification, applied_by, applied_at) "
            "VALUES (?, 'TIME', 5, 'Contact', ?, '77', '2026-02-02T00:00:00+00:00')",
            (result_id, justification),
        )
        await db.commit()


def _channel():
    channel = MagicMock()
    channel.id = CHANNEL_ID
    sent: list = []

    async def _send(content=None, **kwargs):
        message = MagicMock()
        message.id = 8001 + len(sent)
        sent.append((content, kwargs.get("view")))
        return message

    channel.send = AsyncMock(side_effect=_send)
    channel._sent = sent
    return channel


async def _run(db_path, channel=None, *, attendance=True):
    channel = channel or _channel()
    bot = MagicMock()
    bot.add_view = MagicMock()
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance)
    state = await run_amendment_review_stages(
        db_path, ROUND_ID, DIVISION_ID, channel, bot,
        round_number=3, division_name="Pro",
        session_types=[SessionType.FEATURE_RACE],
    )
    return state, channel, bot


async def test_the_rounds_existing_reports_are_shown_back(tmp_path):
    """The manager edits what was decided, rather than retyping it from memory."""
    db_path, result_id = await _db(tmp_path, "stage_hydrate")
    await _penalty(db_path, result_id)

    state, _, _ = await _run(db_path)

    assert len(state.staged) == 1
    assert state.staged[0].driver_user_id == 101
    assert state.staged[0].penalty_seconds == 5


async def test_the_justification_comes_back_with_the_report(tmp_path):
    """Approving unchanged has to preserve it; a blank would rewrite the round's record."""
    db_path, result_id = await _db(tmp_path, "stage_justification")
    await _penalty(db_path, result_id, justification="Forced a rival wide at turn four")

    state, _, _ = await _run(db_path)

    assert state.staged[0].justification == "Forced a rival wide at turn four"


async def test_the_state_is_marked_an_amendment(tmp_path):
    """**What stops approving the screens moving a settled round.**

    Without it the report stage would set `AWAITING_APPEAL_VERDICTS` on a FINAL round and the
    appeal stage would finish the division a second time.
    """
    db_path, _ = await _db(tmp_path, "stage_flag")

    state, _, _ = await _run(db_path)

    assert state.is_amendment is True


async def test_the_amendment_channel_is_the_review_channel(tmp_path):
    """Stage three reads `submission_channel_id` and posts there without knowing it differs."""
    db_path, _ = await _db(tmp_path, "stage_channel")

    state, _, _ = await _run(db_path)

    assert state.submission_channel_id == CHANNEL_ID


async def test_the_screen_is_posted_with_its_view(tmp_path):
    """A prompt with no view is a message the manager cannot act on."""
    db_path, result_id = await _db(tmp_path, "stage_posted")
    await _penalty(db_path, result_id)

    _, channel, bot = await _run(db_path)

    content, view = channel._sent[0]
    assert view is not None
    assert "Stage 2 of 3" in content
    bot.add_view.assert_called_once()


async def test_the_prompt_says_that_approving_changes_nothing_by_default(tmp_path):
    """A manager amending a lap time must not fear they are re-deciding the whole round."""
    db_path, _ = await _db(tmp_path, "stage_wording")

    _, channel, _ = await _run(db_path)

    content, _ = channel._sent[0]
    assert "keeps them exactly as they stand" in content


async def test_a_round_that_was_never_penalised_still_opens_the_stage(tmp_path):
    """The manager may want to *add* a report the original review missed."""
    db_path, _ = await _db(tmp_path, "stage_empty")

    state, channel, _ = await _run(db_path)

    assert state.staged == []
    assert channel._sent


async def test_the_prompt_message_is_remembered(tmp_path):
    """The refresh edits it in place as entries are added and removed."""
    db_path, _ = await _db(tmp_path, "stage_msgid")

    state, _, _ = await _run(db_path)

    assert state.prompt_message_id == 8001


# ---------------------------------------------------------------------------
# The round's pardons, only while attendance is on (#345)
# ---------------------------------------------------------------------------


async def _pardon(db_path) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (31, '101', 'ASSIGNED')"
        )
        await db.execute(
            "INSERT INTO driver_round_attendance (id, round_id, division_id, driver_profile_id, "
            "rsvp_status) VALUES (41, ?, ?, 31, 'NO_RSVP')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO attendance_pardons (attendance_id, pardon_type, justification, "
            "granted_by, granted_at) VALUES (41, 'NO_RSVP', 'Ill', 55, '2026-02-01T20:00:00+00:00')"
        )
        await db.commit()


async def test_the_rounds_pardons_are_shown_back_while_attendance_is_on(tmp_path):
    db_path, _ = await _db(tmp_path, "stage_pardons_on")
    await _pardon(db_path)

    state, _, _ = await _run(db_path, attendance=True)

    assert [p.pardon_type for p in state.staged_pardons] == ["NO_RSVP"]


async def test_the_rounds_pardons_are_not_offered_with_attendance_off(tmp_path):
    """The appeal stage writes them back only while the module is on; offered with it off, a
    pardon could be removed, the removal confirmed, and the round approved still carrying it."""
    db_path, _ = await _db(tmp_path, "stage_pardons_off")
    await _pardon(db_path)

    state, channel, _ = await _run(db_path, attendance=False)

    assert state.staged_pardons == []
    view = channel._sent[-1][1]
    assert not [c for c in view.children if "pardon_remove" in str(getattr(c, "custom_id", ""))]
