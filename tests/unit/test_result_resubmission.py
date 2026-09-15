"""Restarting a round's result submission after the results turned out to be wrong.

Issue #208. `enter_resubmit_flow` is what a league manager reaches by pressing **Resubmit** in
the penalty review, and it was unexecuted. It is the most destructive button in the results
module: it throws away a round's submitted results and every penalty staged against them, and
asks the division to submit again.

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

**The old results are deleted, not superseded in place.** `session_results` rows for the round
go entirely, and the submission channel's `in_penalty_review` and `results_posted` flags are
cleared, so the round is returned to the state it was in before anything was submitted. Leaving
either flag set would make the channel refuse the new submission it has just asked for.

The collection loop itself is started as a background task and is not exercised here — it needs
a live Discord channel to read messages from, which `CLAUDE.md` puts outside this suite.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.session import SessionType  # noqa: E402
from services.penalty_service import StagedPenalty  # noqa: E402
from services.result_submission_service import enter_resubmit_flow  # noqa: E402

SERVER_ID = 9908
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 5
SUBMISSION_CHANNEL_ID = 770501
ACTOR_ID = 77
DRIVER_A = 4001


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
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (?, ?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID, SERVER_ID),
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
            ("FULL_QUALIFYING", "FULL_RACE")[:results], start=1
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


def _state(db_path: str, *, staged=(), channel=None):
    bot = MagicMock()
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
    )


def _interaction():
    interaction = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _channel():
    channel = MagicMock()
    channel.send = AsyncMock(return_value=None)
    return channel


def _logged(state) -> str:
    return "\n".join(
        str(call.args[1]) for call in state.bot.output_router.post_log.await_args_list
    )


async def _run(state, interaction):
    """Call the flow with the background collection task stubbed out.

    The loop it starts reads messages from a live Discord channel, which `CLAUDE.md` puts
    outside this suite; what is under test is everything the button does before it.
    """
    with patch(
        "services.result_submission_service._resubmit_collection_task",
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


async def _flags(db_path: str) -> tuple[int, int]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT in_penalty_review, results_posted FROM round_submission_channels "
            "WHERE round_id = ?",
            (ROUND_ID,),
        )
        row = await cursor.fetchone()
    return row["in_penalty_review"], row["results_posted"]


def _penalty(seconds: int = 5, penalty_type: str = "TIME") -> StagedPenalty:
    return StagedPenalty(
        driver_user_id=DRIVER_A,
        session_type=SessionType.FULL_RACE,
        penalty_type=penalty_type,  # type: ignore[arg-type]
        penalty_seconds=seconds,
    )


# ---------------------------------------------------------------------------
# What is thrown away
# ---------------------------------------------------------------------------


async def test_the_round_s_results_are_deleted(tmp_path):
    """Deleted rather than superseded in place — the round goes back to the state it was
    in before anything was submitted."""
    db_path = await _make_db(tmp_path, results=2)
    state = _state(db_path, channel=_channel())

    await _run(state, _interaction())

    assert await _session_result_count(db_path) == 0


async def test_the_submission_channel_is_reopened(tmp_path):
    """Both flags are cleared. Either left set would make the channel refuse the new
    submission it has just asked the division for."""
    db_path = await _make_db(tmp_path)
    state = _state(db_path, channel=_channel())

    await _run(state, _interaction())

    assert await _flags(db_path) == (0, 0)


async def test_the_staged_penalties_are_discarded(tmp_path):
    """They were staged against results that no longer exist."""
    db_path = await _make_db(tmp_path)
    state = _state(db_path, staged=[_penalty(), _penalty(10)], channel=_channel())

    await _run(state, _interaction())

    assert state.staged == []


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


async def test_the_resubmission_itself_is_logged(tmp_path):
    db_path = await _make_db(tmp_path)
    state = _state(db_path, channel=_channel())

    await _run(state, _interaction())

    assert "RESULTS_RESUBMISSION | Started" in _logged(state)


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

    assert await _session_result_count(db_path) == 0
    assert await _flags(db_path) == (0, 0)


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
    assert "resubmission started" in channel.send.await_args.args[0].lower()


async def test_the_manager_is_told_privately_what_to_do_next(tmp_path):
    db_path = await _make_db(tmp_path)
    interaction = _interaction()
    state = _state(db_path, channel=_channel())

    await _run(state, interaction)

    interaction.followup.send.assert_awaited_once()
    assert "Resubmission started" in interaction.followup.send.await_args.args[0]


async def test_a_missing_submission_channel_does_not_stop_the_reset(tmp_path):
    """The channel may have been deleted by hand. The results still have to go, or the
    round is left holding results nobody can replace."""
    db_path = await _make_db(tmp_path)
    state = _state(db_path, channel=None)

    await _run(state, _interaction())

    assert await _session_result_count(db_path) == 0
    assert await _flags(db_path) == (0, 0)
