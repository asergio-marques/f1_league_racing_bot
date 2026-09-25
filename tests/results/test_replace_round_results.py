"""Swapping a round's results for a resubmission's, all at once.

Issue #210. Pressing **Resubmit Initial Results** deleted the round's results on the spot, and
the league was left with none while the new ones were pasted — for ever, as it turned out,
because the collection never ran. The rule since decided is that a resubmission *supersedes*
the results: the earlier ones stand, published and counted, until every session has been
entered again.

**So the replacement is a single transaction.** `replace_round_results` deletes the old
sessions, inserts the new, scores them and clears the channel's `resubmitting` flag, and
commits once. `test_a_failure_part_way_keeps_the_old_results` is the one to read before
splitting it: any commit in the middle is a window in which a crash leaves the round holding
half of each.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from services.result_submission_service import (  # noqa: E402
    CollectedSession,
    replace_round_results,
)
from services.season_service import SeasonImmutableError  # noqa: E402
from tests.support.teams import seed_team_instances  # noqa: E402

SERVER_ID = 14210
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
OLD_DRIVER = 900
NEW_A = 101
NEW_B = 102


def _race_row(user_id: int, position: int) -> dict:
    return {
        "driver_user_id": user_id,
        "team_instance_id": 3001,
        "finishing_position": position,
        "outcome": "CLASSIFIED",
        "total_time": "1:30:00.000" if position == 1 else "+5.000",
        "fastest_lap": "1:20.000",
        "ingame_penalties": None,
    }


def _qualifying_row(user_id: int, position: int) -> dict:
    return {
        "driver_user_id": user_id,
        "team_instance_id": 3001,
        "finishing_position": position,
        "outcome": "CLASSIFIED",
        "tyre": "Soft",
        "best_lap": "1:19.000",
        "gap": None,
    }


def _collected(*, qualifying_status: str = "ACTIVE") -> list[CollectedSession]:
    quali_rows = (
        [] if qualifying_status == "CANCELLED"
        else [_qualifying_row(NEW_A, 1), _qualifying_row(NEW_B, 2)]
    )
    return [
        CollectedSession(
            SessionType.FEATURE_QUALIFYING, qualifying_status,
            None if qualifying_status == "CANCELLED" else "Standard", 77, quali_rows,
        ),
        CollectedSession(
            SessionType.FEATURE_RACE, "ACTIVE", "Standard", 77,
            [_race_row(NEW_A, 1), _race_row(NEW_B, 2)],
        ),
    ]


async def _make_db(tmp_path, *, name: str, season_status: str = "ACTIVE") -> str:
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
        await seed_team_instances(db, DIVISION_ID, 3001)
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, status) "
            "VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', 'NORMAL', 'AWAITING_REPORT_VERDICTS')",
            (ROUND_ID, DIVISION_ID),
        )
        for session_type in ("FEATURE_QUALIFYING", "FEATURE_RACE"):
            for position, points in ((1, 25), (2, 18)):
                await db.execute(
                    "INSERT INTO season_points_entries "
                    "(season_id, config_name, session_type, position, points) "
                    "VALUES (?, 'Standard', ?, ?, ?)",
                    (SEASON_ID, session_type, position, points),
                )
        # The results being replaced: one race, one driver, already scored.
        await db.execute(
            "INSERT INTO session_results (id, round_id, division_id, session_type, status, "
            "config_name) VALUES (1, ?, ?, 'FEATURE_RACE', 'ACTIVE', 'Standard')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, team_instance_id, "
            "finishing_position, points_awarded) VALUES (1, ?, 3001, 1, 25)",
            (OLD_DRIVER,),
        )
        await db.execute(
            "INSERT INTO round_submission_channels (round_id, channel_id, created_at, "
            "in_penalty_review, results_posted, resubmitting) "
            "VALUES (?, 700, '2026-02-01T00:00:00+00:00', 1, 1, 1)",
            (ROUND_ID,),
        )
        await db.commit()
    return db_path


async def _race_points(db_path: str) -> dict[int, int]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, points_awarded FROM race_session_results"
        )
        return {r[0]: r[1] for r in await cursor.fetchall()}


async def _sessions(db_path: str) -> list[tuple]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT session_type, status FROM session_results ORDER BY session_type"
        )
        return [tuple(r) for r in await cursor.fetchall()]


async def _flags(db_path: str) -> tuple[int, int, int]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT in_penalty_review, results_posted, resubmitting "
            "FROM round_submission_channels WHERE round_id = ?",
            (ROUND_ID,),
        )
        return tuple(await cursor.fetchone())


async def test_the_new_results_replace_the_old_in_one_go(tmp_path):
    db_path = await _make_db(tmp_path, name="replace_swap")

    await replace_round_results(db_path, ROUND_ID, DIVISION_ID, SEASON_ID, _collected())

    assert await _sessions(db_path) == [
        ("FEATURE_QUALIFYING", "ACTIVE"),
        ("FEATURE_RACE", "ACTIVE"),
    ]
    assert await _race_points(db_path) == {NEW_A: 25, NEW_B: 18}


async def test_the_channel_stops_resubmitting_and_the_new_results_await_posting(tmp_path):
    """The round stays in penalty review. `results_posted` is cleared so a crash after the
    swap posts the new results on restart instead of assuming the old posting covers them."""
    db_path = await _make_db(tmp_path, name="replace_flags")

    await replace_round_results(db_path, ROUND_ID, DIVISION_ID, SEASON_ID, _collected())

    assert await _flags(db_path) == (1, 0, 0)


async def test_a_cancelled_session_is_saved_without_points(tmp_path):
    db_path = await _make_db(tmp_path, name="replace_cancelled")

    await replace_round_results(
        db_path, ROUND_ID, DIVISION_ID, SEASON_ID, _collected(qualifying_status="CANCELLED")
    )

    assert ("FEATURE_QUALIFYING", "CANCELLED") in await _sessions(db_path)


async def test_an_archived_season_refuses_the_swap_and_keeps_the_old_results(tmp_path):
    db_path = await _make_db(tmp_path, name="replace_archived", season_status="COMPLETED")

    with pytest.raises(SeasonImmutableError):
        await replace_round_results(db_path, ROUND_ID, DIVISION_ID, SEASON_ID, _collected())

    assert await _race_points(db_path) == {OLD_DRIVER: 25}
    assert await _flags(db_path) == (1, 1, 1)


async def test_a_failure_part_way_keeps_the_old_results(tmp_path):
    """The qualifying session is written and scored before the race fails. Nothing of it may
    survive, and the round must still hold exactly what it held before."""
    db_path = await _make_db(tmp_path, name="replace_partway")
    scoring = AsyncMock(side_effect=[True, RuntimeError("disk")])

    with patch("services.result_submission_service._apply_points_in_tx", new=scoring):
        with pytest.raises(RuntimeError):
            await replace_round_results(
                db_path, ROUND_ID, DIVISION_ID, SEASON_ID, _collected()
            )

    assert await _sessions(db_path) == [("FEATURE_RACE", "ACTIVE")]
    assert await _race_points(db_path) == {OLD_DRIVER: 25}
    assert await _flags(db_path) == (1, 1, 1)
