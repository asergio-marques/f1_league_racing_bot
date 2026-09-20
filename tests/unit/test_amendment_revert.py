"""Putting a round back when nobody approves the amendment's later stages (#345).

The amendment's **first** stage commits: the corrected classification is written and the round
scored from it, before its reports and appeals have been reviewed. The review views carry no
timeout, so a manager who pastes and then walks away leaves the round scored one way and posted
another — on "Provisional Results" indefinitely, with nothing that would later notice.

So the round is snapshotted before stage one writes, and put back if the stages go unapproved
past their deadline. Two routes reach the same revert: the expiry sweep, and restart recovery.

**Only the driver rows, the header and the verdict references are snapshotted.** The points and
the standings follow from the driver rows, so they are recomputed rather than stored — a snapshot
of derived data is a second copy to keep in step, and this one would be read weeks later.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from services.result_submission_service import (  # noqa: E402
    AMENDMENT_STAGE_TIMEOUT_SECONDS,
    revert_abandoned_amendment,
    snapshot_before_amendment,
    sweep_expired_amendments,
)

SEASON_ID = 91
DIVISION_ID = 92
ROUND_ID = 93
CHANNEL_ID = 9400


async def _db(tmp_path, name: str) -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 9, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, status, "
            "track_name) VALUES (?, ?, 4, '2026-02-01T18:00:00+00:00', 'NORMAL', 'FINAL', 'Spa')",
            (ROUND_ID, DIVISION_ID),
        )
        session = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "config_name, submitted_by) VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE', 'Standard', 55)",
            (ROUND_ID, DIVISION_ID),
        )
        for position, driver in enumerate((101, 102), start=1):
            await db.execute(
                "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                "team_role_id, finishing_position) VALUES (?, ?, 3001, ?)",
                (session.lastrowid, driver, position),
            )
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_type, created_at) "
            "VALUES (?, ?, 'FEATURE_RACE', '2026-02-02T00:00:00+00:00')",
            (ROUND_ID, CHANNEL_ID),
        )
        await db.commit()
    return db_path


async def _snapshot_row(db_path) -> dict:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT pre_amendment_state, expires_at FROM round_amend_channels "
            "WHERE round_id = ?",
            (ROUND_ID,),
        )
        return dict(await cursor.fetchone())


async def _drivers(db_path) -> list[tuple[int, int]]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, finishing_position FROM race_session_results "
            "ORDER BY finishing_position"
        )
        return [(r[0], r[1]) for r in await cursor.fetchall()]


async def _overwrite_the_classification(db_path) -> None:
    """Stand in for stage one: replace the driver rows, as the amendment does."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT id FROM session_results WHERE round_id = ?", (ROUND_ID,))
        session_id = (await cursor.fetchone())["id"]
        await db.execute(
            "DELETE FROM race_session_results WHERE session_result_id = ?", (session_id,)
        )
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, "
            "team_role_id, finishing_position) VALUES (?, 102, 3001, 1)",
            (session_id,),
        )
        await db.commit()


def _bot(db_path):
    bot = MagicMock()
    bot.db_path = db_path
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock()
    bot.get_guild = MagicMock(return_value=None)
    bot.config_service.get_league_server_id = AsyncMock(return_value=1)
    return bot


# ── The snapshot ──────────────────────────────────────────────────────────


async def test_the_round_is_snapshotted_before_it_is_overwritten(tmp_path):
    db_path = await _db(tmp_path, "snap_taken")

    await snapshot_before_amendment(db_path, ROUND_ID, SessionType.FEATURE_RACE)

    state = json.loads((await _snapshot_row(db_path))["pre_amendment_state"])
    assert [r["driver_user_id"] for r in state["driver_rows"]] == [101, 102]


async def test_the_snapshot_carries_a_deadline(tmp_path):
    """Without one nothing would ever revert it."""
    db_path = await _db(tmp_path, "snap_deadline")
    before = datetime.now(timezone.utc)

    await snapshot_before_amendment(db_path, ROUND_ID, SessionType.FEATURE_RACE)

    expires = datetime.fromisoformat((await _snapshot_row(db_path))["expires_at"])
    assert expires > before
    assert expires <= before + timedelta(seconds=AMENDMENT_STAGE_TIMEOUT_SECONDS + 5)


async def test_the_timeout_leaves_room_to_work_through_two_stages(tmp_path):
    """Pinned as a range: long enough not to hurry a manager, short enough not to outlive the
    evening the amendment was started in."""
    assert 600 <= AMENDMENT_STAGE_TIMEOUT_SECONDS <= 7200


# ── The revert ────────────────────────────────────────────────────────────


async def test_the_classification_is_put_back(tmp_path):
    """The league keeps the round it raced rather than a half-amended one."""
    db_path = await _db(tmp_path, "revert_rows")
    await snapshot_before_amendment(db_path, ROUND_ID, SessionType.FEATURE_RACE)
    await _overwrite_the_classification(db_path)
    assert await _drivers(db_path) == [(102, 1)]

    with patch("services.standings_service.cascade_recompute_from_round", new=AsyncMock()):
        assert await revert_abandoned_amendment(
            db_path, ROUND_ID, SessionType.FEATURE_RACE, _bot(db_path)
        ) is True

    assert await _drivers(db_path) == [(101, 1), (102, 2)]


async def test_the_standings_are_recomputed_rather_than_restored(tmp_path):
    """They follow from the driver rows, so a second stored copy would only drift."""
    db_path = await _db(tmp_path, "revert_cascade")
    await snapshot_before_amendment(db_path, ROUND_ID, SessionType.FEATURE_RACE)
    await _overwrite_the_classification(db_path)

    with patch(
        "services.standings_service.cascade_recompute_from_round", new=AsyncMock()
    ) as cascade:
        await revert_abandoned_amendment(
            db_path, ROUND_ID, SessionType.FEATURE_RACE, _bot(db_path)
        )

    cascade.assert_awaited_once()


async def test_the_snapshot_is_cleared_once_it_has_been_used(tmp_path):
    """A snapshot left behind would let a later sweep undo the round a second time."""
    db_path = await _db(tmp_path, "revert_clears")
    await snapshot_before_amendment(db_path, ROUND_ID, SessionType.FEATURE_RACE)

    with patch("services.standings_service.cascade_recompute_from_round", new=AsyncMock()):
        await revert_abandoned_amendment(
            db_path, ROUND_ID, SessionType.FEATURE_RACE, _bot(db_path)
        )

    assert (await _snapshot_row(db_path))["pre_amendment_state"] is None


async def test_an_amendment_abandoned_before_stage_one_has_nothing_to_undo(tmp_path):
    """Cancelled at the paste, or a restart before it landed. Reverting nothing must be safe."""
    db_path = await _db(tmp_path, "revert_nothing")

    assert await revert_abandoned_amendment(
        db_path, ROUND_ID, SessionType.FEATURE_RACE, _bot(db_path)
    ) is False
    assert await _drivers(db_path) == [(101, 1), (102, 2)]


# ── The sweep ─────────────────────────────────────────────────────────────


async def test_an_expired_amendment_is_reverted(tmp_path):
    db_path = await _db(tmp_path, "sweep_expired")
    await snapshot_before_amendment(db_path, ROUND_ID, SessionType.FEATURE_RACE)
    await _overwrite_the_classification(db_path)
    later = datetime.now(timezone.utc) + timedelta(
        seconds=AMENDMENT_STAGE_TIMEOUT_SECONDS + 60
    )

    with patch("services.standings_service.cascade_recompute_from_round", new=AsyncMock()):
        assert await sweep_expired_amendments(_bot(db_path), now=later) == 1

    assert await _drivers(db_path) == [(101, 1), (102, 2)]


async def test_an_amendment_still_within_its_deadline_is_left_alone(tmp_path):
    """**The one that matters**: a manager mid-review must not have it pulled from under them."""
    db_path = await _db(tmp_path, "sweep_live")
    await snapshot_before_amendment(db_path, ROUND_ID, SessionType.FEATURE_RACE)
    await _overwrite_the_classification(db_path)

    assert await sweep_expired_amendments(_bot(db_path), now=datetime.now(timezone.utc)) == 0
    assert await _drivers(db_path) == [(102, 1)]


async def test_the_revert_is_announced(tmp_path):
    """An amendment quietly undone would be worse than one left hanging."""
    db_path = await _db(tmp_path, "sweep_announced")
    await snapshot_before_amendment(db_path, ROUND_ID, SessionType.FEATURE_RACE)
    await _overwrite_the_classification(db_path)
    bot = _bot(db_path)
    later = datetime.now(timezone.utc) + timedelta(
        seconds=AMENDMENT_STAGE_TIMEOUT_SECONDS + 60
    )

    with patch("services.standings_service.cascade_recompute_from_round", new=AsyncMock()):
        await sweep_expired_amendments(bot, now=later)

    logged = "\n".join(str(c.args[0]) for c in bot.output_router.post_log.await_args_list)
    assert "AMEND_REVERTED" in logged
    assert "round: 4" in logged
    assert "/round results amend" in logged


async def test_a_round_with_no_deadline_is_not_swept(tmp_path):
    """A row written before stage one has no snapshot and no deadline."""
    db_path = await _db(tmp_path, "sweep_no_deadline")
    later = datetime.now(timezone.utc) + timedelta(days=1)

    assert await sweep_expired_amendments(_bot(db_path), now=later) == 0


# ── The sweep is actually armed ────────────────────────────────────────────


def test_the_sweep_is_scheduled_as_a_standing_job():
    """**A timeout nothing invokes is a promise the documentation would be lying about.**

    The revert machinery was written and tested before anything called it, so the half-hour
    deadline would never have fired and the README would have described behaviour the bot did
    not have. Pinned here rather than left to the startup tests, which do not look at what is
    armed.
    """
    import inspect

    from services import scheduler_service

    source = inspect.getsource(scheduler_service)
    assert "def schedule_amendment_sweep" in source
    assert "IntervalTrigger" in source

    import bot as bot_module

    startup = inspect.getsource(bot_module)
    assert "schedule_amendment_sweep()" in startup


def test_the_sweep_runs_often_enough_to_honour_the_deadline():
    """Sweeping less often than the deadline would let a lapsed amendment sit past it.

    Not exactly at the deadline — a round nobody is working on can wait a few minutes — but the
    interval has to be the smaller of the two or the timeout means nothing.
    """
    from services.scheduler_service import AMENDMENT_SWEEP_MINUTES

    assert AMENDMENT_SWEEP_MINUTES * 60 < AMENDMENT_STAGE_TIMEOUT_SECONDS


def test_the_sweep_is_re_armed_by_the_scheduler_not_by_its_own_callback():
    """A job that re-arms itself from inside its body stops for good the first time it raises.

    The same reasoning `schedule_portrait_refresh` gives for using a cron trigger.
    """
    import inspect

    from services import scheduler_service

    job = inspect.getsource(scheduler_service._amendment_sweep_job)
    assert "add_job" not in job
    assert "except Exception" in job
