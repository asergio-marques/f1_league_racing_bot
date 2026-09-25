"""Writing one submitted session, alone or inside a caller's transaction.

Issue #210. `save_session_result` commits each session as it is submitted, which is right for
a first submission. A resubmission has to replace a round's results in one go instead — the
earlier results stand until the last session is in — so the write is split into
`_save_session_result_in_tx`, which does the same work on the caller's connection and leaves
the commit to it. `replace_round_results` is the caller that depends on that.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
from leaguebot.results.models.points_config import SessionType  # noqa: E402
from leaguebot.results.services.result_submission_service import (  # noqa: E402
    _save_session_result_in_tx,
    save_session_result,
)
from leaguebot.core.services.season_service import SeasonImmutableError  # noqa: E402
from tests.support.teams import seed_team_instances  # noqa: E402

SERVER_ID = 13210
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
DRIVER_A = 101


def _race_row(user_id: int = DRIVER_A, position: int = 1) -> dict:
    return {
        "driver_user_id": user_id,
        "team_instance_id": 3001,
        "finishing_position": position,
        "outcome": "CLASSIFIED",
        "total_time": "1:30:00.000",
        "fastest_lap": "1:20.000",
        "ingame_penalties": None,
        "postrace_penalty": "N/A",
        "appeal_penalty": "N/A",
    }


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
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format) "
            "VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', 'NORMAL')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state, former_driver) "
            "VALUES (31, ?, 'ASSIGNED', 0)",
            (str(DRIVER_A),),
        )
        await db.commit()
    return db_path


async def _counts(db_path: str) -> tuple[int, int]:
    async with get_connection(db_path) as db:
        headers = await (await db.execute("SELECT COUNT(*) FROM session_results")).fetchone()
        rows = await (await db.execute("SELECT COUNT(*) FROM race_session_results")).fetchone()
    return headers[0], rows[0]


async def test_a_session_is_saved_with_its_drivers(tmp_path):
    db_path = await _make_db(tmp_path, name="save_session")

    await save_session_result(
        db_path, ROUND_ID, DIVISION_ID, SessionType.FEATURE_RACE, "ACTIVE", "Standard",
        77, [_race_row(), _race_row(102, 2)],
    )

    assert await _counts(db_path) == (1, 2)


async def test_submitting_a_result_marks_nobody_a_former_driver(tmp_path):
    """The round is still awaiting its verdicts, so nobody has raced it yet (#216).

    The flag used to go up here, for every driver in the paste, the moment it was submitted —
    so a driver pasted in by mistake and taken out by a resubmission stayed a former driver for
    ever, and their profile survived a season end it should not have. It is set instead from
    the round's final results, when the round becomes FINAL.
    """
    db_path = await _make_db(tmp_path, name="save_session_former")

    await save_session_result(
        db_path, ROUND_ID, DIVISION_ID, SessionType.FEATURE_RACE, "ACTIVE", "Standard",
        77, [_race_row()],
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT former_driver FROM driver_profiles WHERE id = 31")
        assert (await cursor.fetchone())["former_driver"] == 0


async def test_saving_inside_a_transaction_writes_nothing_until_it_commits(tmp_path):
    """Otherwise a resubmission that failed on its second session would leave the round with
    one new session beside the old ones."""
    db_path = await _make_db(tmp_path, name="save_session_tx")

    async with get_connection(db_path) as db:
        await _save_session_result_in_tx(
            db, ROUND_ID, DIVISION_ID, SessionType.FEATURE_RACE, "ACTIVE", "Standard",
            77, [_race_row()],
        )
        await db.rollback()

    assert await _counts(db_path) == (0, 0)


async def test_an_archived_season_refuses_the_save(tmp_path):
    db_path = await _make_db(tmp_path, name="save_session_archived", season_status="COMPLETED")

    with pytest.raises(SeasonImmutableError):
        await save_session_result(
            db_path, ROUND_ID, DIVISION_ID, SessionType.FEATURE_RACE, "ACTIVE", None,
            77, [_race_row()],
        )

    assert await _counts(db_path) == (0, 0)
