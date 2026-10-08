"""The writes of a season's approval, on the save they are handed (#439, slice 4a).

The approval is one save on the change queue: the sessions of every round, the commitment of the
placements, the points snapshot and the season's move to Ongoing commit together or not at all. So
each of core's three writes has an `_on` form, writing on the connection it is handed and
committing nothing; the queue's step commits once, after the last of them.

Each test writes, reads the write back on the same connection, and then rolls back: what it finds
afterwards on a fresh connection is what a failure later in the save would leave, which is nothing.

The functions are imported inside each test, so this file collects while they are unbuilt.
"""
from __future__ import annotations

import os

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.round import RoundFormat
from leaguebot.core.models.session import SESSIONS_BY_FORMAT

SERVER_ID = 10439
SEASON_ID = 7
DIVISION_ID = 11
ROUND_ID = 21


async def _make_db(tmp_path, *, stage: str = "PLACEMENTS") -> str:
    """Season 3 (id 7) in *stage*, Pro with one round, and two placements not yet committed."""
    db_path = os.path.join(str(tmp_path), "on_forms.db")
    await run_migrations(db_path)
    status = "SETUP" if stage == "PLACEMENTS" else "ACTIVE"
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status, stage) "
            "VALUES (?, 3, '2026-11-01', ?, ?)",
            (SEASON_ID, status, stage),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id, status) "
            "VALUES (?, ?, 'Pro', 1, 801, 'SETUP')",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format) "
            "VALUES (?, ?, 1, '2026-12-01T18:00:00+00:00', 'NORMAL')",
            (ROUND_ID, DIVISION_ID),
        )
        for profile_id, user_id in ((1, "101"), (2, "102")):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (?, ?, 'ASSIGNED')",
                (profile_id, user_id),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                "division_id, committed) VALUES (?, ?, ?, 0)",
                (profile_id, SEASON_ID, DIVISION_ID),
            )
        await db.commit()
    return db_path


async def _session_types(db, round_id: int = ROUND_ID) -> list[str]:
    cursor = await db.execute(
        "SELECT session_type FROM sessions WHERE round_id = ? ORDER BY id", (round_id,)
    )
    return [row["session_type"] for row in await cursor.fetchall()]


async def _committed(db) -> list[int]:
    cursor = await db.execute(
        "SELECT committed FROM driver_season_assignments WHERE season_id = ? "
        "ORDER BY driver_profile_id",
        (SEASON_ID,),
    )
    return [row["committed"] for row in await cursor.fetchall()]


async def _season(db) -> tuple[str, str]:
    cursor = await db.execute("SELECT status, stage FROM seasons WHERE id = ?", (SEASON_ID,))
    row = await cursor.fetchone()
    return row["status"], row["stage"]


async def test_sessions_are_replaced_not_added_on_the_save_handed(tmp_path):
    """Round 1 already holds a sprint's sessions, left by an earlier save. Making its sessions
    for a normal round, twice, on one connection leaves the normal round's set once; and nothing
    of it outlasts a rollback."""
    from leaguebot.core.services.season_service import create_sessions_for_round_on

    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        for session_type in SESSIONS_BY_FORMAT[RoundFormat.SPRINT]:
            await db.execute(
                "INSERT INTO sessions (round_id, session_type) VALUES (?, ?)",
                (ROUND_ID, session_type.value),
            )
        await db.commit()

    expected = [st.value for st in SESSIONS_BY_FORMAT[RoundFormat.NORMAL]]
    async with get_connection(db_path) as db:
        await create_sessions_for_round_on(db, ROUND_ID, RoundFormat.NORMAL)
        await create_sessions_for_round_on(db, ROUND_ID, RoundFormat.NORMAL)
        assert await _session_types(db) == expected
        await db.rollback()

    async with get_connection(db_path) as db:
        assert await _session_types(db) == [
            st.value for st in SESSIONS_BY_FORMAT[RoundFormat.SPRINT]
        ], "nothing is committed: the save's own commit is the queue's"


async def test_placements_are_committed_on_the_save_handed(tmp_path):
    """Lewis and Max are placed in Pro, neither committed. Committing the season's placements on
    a connection marks both committed and answers 2; nothing of it outlasts a rollback."""
    from leaguebot.core.services.season_service import commit_placements_on

    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        assert await commit_placements_on(db, SEASON_ID) == 2
        assert await _committed(db) == [1, 1]
        await db.rollback()

    async with get_connection(db_path) as db:
        assert await _committed(db) == [0, 0]


@pytest.mark.parametrize(
    "stage,moves",
    [
        pytest.param("PLACEMENTS", True, id="in placements"),
        pytest.param("ONGOING", False, id="already ongoing"),
    ],
)
async def test_the_season_moves_to_ongoing_only_from_placements(tmp_path, stage, moves):
    """A season in Placements moves to Ongoing, its divisions active with it, and the call
    answers True; a season already Ongoing is left as it is and the call answers False. Nothing
    of it outlasts a rollback."""
    from leaguebot.core.services.season_service import transition_to_active_on

    db_path = await _make_db(tmp_path, stage=stage)
    async with get_connection(db_path) as db:
        before = await _season(db)
        assert await transition_to_active_on(db, SEASON_ID) is moves
        assert await _season(db) == ("ACTIVE", "ONGOING")
        cursor = await db.execute("SELECT status FROM divisions WHERE id = ?", (DIVISION_ID,))
        division_status = (await cursor.fetchone())["status"]
        assert division_status == ("ACTIVE" if moves else "SETUP")
        await db.rollback()

    async with get_connection(db_path) as db:
        assert await _season(db) == before
