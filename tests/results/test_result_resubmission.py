"""Restarting a round's result submission after the results turned out to be wrong.

Issue #208. `enter_resubmit_flow` is what a league manager reaches by pressing **Resubmit** in
the penalty review, and it was unexecuted. It throws away every penalty staged against the
round's results and asks for the results again.

**Everything discarded is logged before it is discarded.** The staged penalties are written
into the calculation log with the driver, session, type and seconds of each — because once they
are gone there is no other record that they were ever staged, and a manager who pressed the
button by mistake would otherwise have no way to reconstruct what they had been reviewing.
`test_the_discarded_penalties_are_logged_in_full` is what holds that, and the ordering matters:
the log is written while `state.staged` is still populated.

**A failure to log must not block the resubmission.** Both audit writes are wrapped, because
the log channel may be missing or unreachable and a manager who has decided the results are
wrong must still be able to replace them. The alternative — refusing to resubmit because the
audit failed — would leave the round stuck with results the league knows to be wrong.

**The old results are superseded, not deleted** (issue #210). Pressing the button deleted the
round's results on the spot, and the collection that should have replaced them never ran. The
results now stand, published and counted, until every session has been entered again;
`test_pressing_resubmit_keeps_the_round_s_results` holds that. The round stays in penalty review
and the channel is flagged `resubmitting`, which lets the pastes past the review channel's
message guard. The review prompt is taken down, and any approval message with it (#402), so
nobody can approve the results being replaced or start a second resubmission over the first.

The collection loop itself is started as a background task and is stubbed here. It is driven
with a fake `wait_for` in `test_resubmission_collection.py`.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
# The results pipeline's session types, not the weather module's — `penalty_service`
# and `result_submission_service` both import this one, and these are the values
# `session_results.session_type` actually holds.
from leaguebot.results.models.points_config import SessionType  # noqa: E402
from leaguebot.results.services.penalty_service import StagedPenalty  # noqa: E402
from leaguebot.results.services.result_submission_service import (  # noqa: E402
    ResubmissionCancelView,
    enter_resubmit_flow,
)

SERVER_ID = 9908
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 5
SUBMISSION_CHANNEL_ID = 770501
ACTOR_ID = 77
DRIVER_A = 4001
ANNOUNCEMENT_ID = 880210


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, results: int = 2) -> str:
    db_path = os.path.join(str(tmp_path), "resubmit.db")
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
            "VALUES (?, ?, 'Division 1', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at) VALUES (?, ?, 3, 'NORMAL', 'Silverstone Circuit', '2026-06-01')",
            (ROUND_ID, DIVISION_ID),
        )
        # One row per session type: `session_results` is unique on (round, session type),
        # which is the shape a real round has — a qualifying and a race, not two races.
        for index, session_type in enumerate(
            ("FEATURE_QUALIFYING", "FEATURE_RACE")[:results], start=1
        ):
            await db.execute(
                "INSERT INTO session_results "
                "(id, round_id, division_id, session_type, status) "
                "VALUES (?, ?, ?, ?, 'ACTIVE')",
                (index, ROUND_ID, DIVISION_ID, session_type),
            )
        await db.execute(
            "INSERT INTO round_submission_channels "
            "(round_id, channel_id, in_penalty_review, results_posted, created_at) "
            "VALUES (?, ?, 1, 1, ?)",
            (
                ROUND_ID,
                SUBMISSION_CHANNEL_ID,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
    return db_path


def _state(
    db_path: str, *, staged=(), pardons=(), channel=None, prompt_message_id=None,
    approval_message_id=None,
):
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.db_path = db_path
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)
    bot.get_channel = MagicMock(return_value=channel)
    return SimpleNamespace(
        bot=bot,
        round_id=ROUND_ID,
        division_id=DIVISION_ID,
        division_name="Division 1",
        submission_channel_id=SUBMISSION_CHANNEL_ID,
        staged=list(staged),
        staged_pardons=list(pardons),
        prompt_message_id=prompt_message_id,
        approval_message_id=approval_message_id,
    )


def _interaction():
    interaction = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _channel(*, prompt_gone: bool = False):
    channel = MagicMock()
    announcement = MagicMock()
    announcement.id = ANNOUNCEMENT_ID
    channel.send = AsyncMock(return_value=announcement)
    prompt = MagicMock()
    prompt.delete = AsyncMock()
    channel.fetch_message = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404), "gone") if prompt_gone else None,
        return_value=prompt,
    )
    channel._prompt = prompt
    return channel


def _logged(state) -> str:
    return "\n".join(
        str(call.args[0]) for call in state.bot.output_router.post_log.await_args_list
    )


async def _run(state, interaction):
    """Call the flow with the background collection task stubbed out.

    What is under test here is everything the button does before the loop starts. The loop
    itself is driven with a fake `wait_for` in `test_resubmission_collection.py`. Stubbing it
    here is half of why issue #210 — a loop that raised on its first line — went unnoticed.
    """
    with patch(
        "leaguebot.results.services.result_submission_service._resubmit_collection_task",
        new=AsyncMock(return_value=None),
    ):
        await enter_resubmit_flow(interaction, state)
        # Let the created task run and finish, so the event loop is clean on exit.
        await asyncio.sleep(0)


async def _session_result_count(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM session_results WHERE round_id = ?", (ROUND_ID,)
        )
        return (await cursor.fetchone())["n"]


async def _flags(db_path: str) -> tuple[int, int, int]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT in_penalty_review, results_posted, resubmitting "
            "FROM round_submission_channels WHERE round_id = ?",
            (ROUND_ID,),
        )
        row = await cursor.fetchone()
    return row["in_penalty_review"], row["results_posted"], row["resubmitting"]


def _penalty(seconds: int = 5, penalty_type: str = "TIME") -> StagedPenalty:
    return StagedPenalty(
        driver_user_id=DRIVER_A,
        session_type=SessionType.FEATURE_RACE,
        penalty_type=penalty_type,  # type: ignore[arg-type]
        penalty_seconds=seconds,
    )


def _pardon(pardon_type: str = "ABSENT", justification: str = "Ill"):
    from leaguebot.results.services.penalty_wizard import StagedPardon

    return StagedPardon(
        driver_user_id=DRIVER_A, driver_profile_id=31, attendance_id=41,
        pardon_type=pardon_type, justification=justification, grantor_id=ACTOR_ID,
    )


# ---------------------------------------------------------------------------
# What is kept, and what is thrown away
# ---------------------------------------------------------------------------


async def test_pressing_resubmit_keeps_the_round_s_results(tmp_path):
    """Issue #210. They stand until every session has been entered again. Deleting them here
    left the round with no results at all, and nothing to put them back."""
    db_path = await _make_db(tmp_path, results=2)
    state = _state(db_path, channel=_channel())

    await _run(state, _interaction())

    assert await _session_result_count(db_path) == 2


async def test_resubmitting_flags_the_channel(tmp_path):
    """The round is still in review with its results posted; `resubmitting` is what lets the
    pastes through the review channel's guard."""
    db_path = await _make_db(tmp_path)
    state = _state(db_path, channel=_channel())

    await _run(state, _interaction())

    assert await _flags(db_path) == (1, 1, 1)


async def test_the_penalty_prompt_is_taken_down_when_resubmission_starts(tmp_path):
    """Left up, its Approve would finalise the results being replaced, and its Resubmit would
    start a second collector reading the same channel."""
    db_path = await _make_db(tmp_path)
    channel = _channel()
    state = _state(db_path, channel=channel, prompt_message_id=4242)

    await _run(state, _interaction())

    channel.fetch_message.assert_awaited_once_with(4242)
    channel._prompt.delete.assert_awaited_once()


async def test_the_approval_message_is_taken_down_when_resubmission_starts(tmp_path):
    """**#402, as reported.** The prompt went and the approval message stayed, and its Approve
    finalised the round on the results the manager had just said were wrong."""
    db_path = await _make_db(tmp_path)
    channel = _channel()
    state = _state(db_path, channel=channel, prompt_message_id=4242, approval_message_id=4243)

    await _run(state, _interaction())

    assert [c.args for c in channel.fetch_message.await_args_list] == [(4242,), (4243,)]
    assert channel._prompt.delete.await_count == 2
    assert state.approval_message_id is None


async def test_a_prompt_already_gone_does_not_stop_the_resubmission(tmp_path):
    db_path = await _make_db(tmp_path)
    state = _state(db_path, channel=_channel(prompt_gone=True), prompt_message_id=4242)

    await _run(state, _interaction())

    assert await _flags(db_path) == (1, 1, 1)


async def test_the_staged_penalties_are_discarded(tmp_path):
    """They were staged against results that no longer exist."""
    db_path = await _make_db(tmp_path)
    state = _state(db_path, staged=[_penalty(), _penalty(10)], channel=_channel())

    await _run(state, _interaction())

    assert state.staged == []


async def test_the_staged_pardons_are_discarded(tmp_path):
    """The log says they are, and the state outlives the prompt: an approval message still
    standing holds it, and must not grant what the resubmission threw away (#356)."""
    db_path = await _make_db(tmp_path)
    state = _state(db_path, pardons=[_pardon()], channel=_channel())

    await _run(state, _interaction())

    assert state.staged_pardons == []


# ---------------------------------------------------------------------------
# What is written down first
# ---------------------------------------------------------------------------


async def test_the_discarded_penalties_are_logged_in_full(tmp_path):
    """Once they are gone there is no other record they were ever staged, so a manager who
    pressed the button by mistake could not reconstruct what they had been reviewing. The
    log is written while `state.staged` is still populated."""
    db_path = await _make_db(tmp_path)
    state = _state(db_path, staged=[_penalty(5), _penalty(None, "DSQ")], channel=_channel())

    await _run(state, _interaction())

    logged = _logged(state)
    assert "RESULTS_RESUBMISSION_STAGED_DISCARD" in logged
    assert "discarded_count: 2" in logged
    assert str(DRIVER_A) in logged
    assert "DSQ" in logged


async def test_the_discard_log_is_machine_readable(tmp_path):
    """It is JSON so the detail survives being read back, rather than being a sentence
    somebody has to parse by eye."""
    db_path = await _make_db(tmp_path)
    state = _state(db_path, staged=[_penalty(5)], channel=_channel())

    await _run(state, _interaction())

    line = next(
        l for l in _logged(state).splitlines() if l.strip().startswith("discarded:")
    )
    payload = json.loads(line.split("discarded:", 1)[1].strip())
    assert payload[0]["driver_user_id"] == DRIVER_A
    assert payload[0]["penalty_seconds"] == 5


async def test_the_discarded_pardons_are_logged_in_full(tmp_path):
    """The entry listed the penalties it threw away and not the pardons, which went with them
    unrecorded (#356). They are written as the penalties are, count and detail."""
    db_path = await _make_db(tmp_path)
    state = _state(
        db_path, staged=[_penalty(5)],
        pardons=[_pardon("ABSENT", "Ill"), _pardon("NO_RSVP", "Power cut")],
        channel=_channel(),
    )

    await _run(state, _interaction())

    logged = _logged(state)
    assert "discarded_count: 1" in logged
    assert "discarded_pardons_count: 2" in logged
    line = next(
        l for l in logged.splitlines() if l.strip().startswith("discarded_pardons:")
    )
    payload = json.loads(line.split("discarded_pardons:", 1)[1].strip())
    assert payload == [
        {"driver_user_id": DRIVER_A, "pardon_type": "ABSENT", "justification": "Ill"},
        {"driver_user_id": DRIVER_A, "pardon_type": "NO_RSVP", "justification": "Power cut"},
    ]


async def test_the_resubmission_itself_is_logged(tmp_path):
    db_path = await _make_db(tmp_path)
    state = _state(db_path, channel=_channel())

    await _run(state, _interaction())

    assert "RESULTS_RESUBMISSION | Started" in _logged(state)


async def test_the_resubmission_counts_the_pardons_it_discarded(tmp_path):
    db_path = await _make_db(tmp_path)
    state = _state(db_path, pardons=[_pardon()], channel=_channel())

    await _run(state, _interaction())

    started = next(
        call.args[0] for call in state.bot.output_router.post_log.await_args_list
        if "RESULTS_RESUBMISSION | Started" in call.args[0]
    )
    assert "Previous staged penalties discarded: 0" in started
    assert "Previous staged pardons discarded: 1" in started


async def test_a_resubmission_with_nothing_staged_still_logs(tmp_path):
    """A manager may resubmit because the results were wrong rather than because a penalty
    was — the round still changed and the log still has to say so."""
    db_path = await _make_db(tmp_path)
    state = _state(db_path, staged=[], channel=_channel())

    await _run(state, _interaction())

    assert "discarded_count: 0" in _logged(state)


async def test_a_failing_log_does_not_block_the_resubmission(tmp_path):
    """The log channel may be missing or unreachable, and a manager who has decided the
    results are wrong must still be able to replace them — refusing would leave the round
    stuck with results the league knows to be wrong."""
    db_path = await _make_db(tmp_path)
    state = _state(db_path, staged=[_penalty()], channel=_channel())
    state.bot.output_router.post_log = AsyncMock(side_effect=RuntimeError("no channel"))

    await _run(state, _interaction())

    assert await _session_result_count(db_path) == 2
    assert await _flags(db_path) == (1, 1, 1)


# ---------------------------------------------------------------------------
# What the league is told
# ---------------------------------------------------------------------------


async def test_the_division_is_told_in_the_submission_channel(tmp_path):
    """The division is about to be asked for results again; without this the request
    arrives with no explanation."""
    db_path = await _make_db(tmp_path)
    channel = _channel()
    state = _state(db_path, channel=channel)

    await _run(state, _interaction())

    channel.send.assert_awaited_once()
    announcement = channel.send.await_args.args[0]
    assert "resubmission started" in announcement.lower()
    assert "stand until every session has been entered again" in announcement


async def test_the_announcement_carries_the_cancel_button(tmp_path):
    db_path = await _make_db(tmp_path)
    channel = _channel()
    state = _state(db_path, channel=channel)

    await _run(state, _interaction())

    view = channel.send.await_args.kwargs["view"]
    assert isinstance(view, ResubmissionCancelView)
    assert view.message is channel.send.return_value


async def test_the_announcement_is_recorded_for_the_restart_sweep(tmp_path):
    """A restart ends the resubmission, and the sweep needs the message to take the button
    down — nothing is listening for it any more."""
    db_path = await _make_db(tmp_path)
    state = _state(db_path, channel=_channel())

    await _run(state, _interaction())

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT resubmit_prompt_message_id FROM round_submission_channels WHERE round_id = ?",
            (ROUND_ID,),
        )
        assert (await cursor.fetchone())[0] == ANNOUNCEMENT_ID


async def test_the_manager_is_told_privately_what_to_do_next(tmp_path):
    db_path = await _make_db(tmp_path)
    interaction = _interaction()
    state = _state(db_path, channel=_channel())

    await _run(state, interaction)

    interaction.followup.send.assert_awaited_once()
    assert "Resubmission started" in interaction.followup.send.await_args.args[0]


async def test_a_missing_submission_channel_refuses_and_changes_nothing(tmp_path):
    """The channel may have been deleted by hand. With nowhere to collect in, the review is all
    the round has — so its staged penalties are kept and the manager is told why."""
    db_path = await _make_db(tmp_path)
    state = _state(db_path, staged=[_penalty()], channel=None)
    interaction = _interaction()

    with patch(
        "leaguebot.results.services.result_submission_service._resubmit_collection_task", new=AsyncMock()
    ) as task:
        await enter_resubmit_flow(interaction, state)

    assert len(state.staged) == 1
    assert await _flags(db_path) == (1, 1, 0)
    assert "submission channel could not be found" in interaction.followup.send.await_args.args[0]
    task.assert_not_called()
