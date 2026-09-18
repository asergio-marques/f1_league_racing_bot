"""Everything drawn or posted names a driver by the account they use now (issue #243).

`results_post_service._load_driver_rows` is the one reader the posted results — text and
graphic — and the standings graphic's per-round grid draw their rows from, so mapping there
covers all three. The standings themselves come from `compute_driver_standings`, pinned in
`test_standings_service.py`.

E12: a completed season drawn again after a driver changed account names the new one, and
not one stored row changes.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from services.results_post_service import _load_driver_rows  # noqa: E402

SERVER_ID = 2436
PAST, NOW, OTHER = 9101, 9102, 9103


async def _completed_season(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "drawn.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, start_date, status, season_number) "
            "VALUES (1, ?, '2026-01-01', 'COMPLETED', 1)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier) "
            "VALUES (1, 1, 'Pro', 1001, 1)"
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, scheduled_at) "
            "VALUES (1, 1, 1, 'NORMAL', '2026-01-20T18:00:00')"
        )
        for sr_id, session in ((1, "FEATURE_RACE"), (2, "FEATURE_QUALIFYING")):
            await db.execute(
                "INSERT INTO session_results (id, round_id, division_id, session_type, status) "
                "VALUES (?, 1, 1, ?, 'ACTIVE')",
                (sr_id, session),
            )
        for uid, pos in ((PAST, 1), (OTHER, 2)):
            await db.execute(
                "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                "team_role_id, finishing_position) VALUES (1, ?, 501, ?)",
                (uid, pos),
            )
            await db.execute(
                "INSERT INTO qualifying_session_results (session_result_id, driver_user_id, "
                "team_role_id, finishing_position) VALUES (2, ?, 501, ?)",
                (uid, pos),
            )
        cursor = await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state, "
            "former_driver) VALUES (?, 'NOT_SIGNED_UP', 1)",
            (str(PAST),),
        )
        await db.execute(
            "UPDATE driver_profiles SET discord_user_id = ? WHERE id = ?",
            (str(NOW), cursor.lastrowid),
        )
        await db.commit()
    return db_path


async def _stored(db_path: str) -> list[tuple]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT 'R', driver_user_id, finishing_position FROM race_session_results "
            "UNION ALL SELECT 'Q', driver_user_id, finishing_position "
            "FROM qualifying_session_results ORDER BY 1, 3"
        )
        return [tuple(r) for r in await cursor.fetchall()]


async def test_a_race_is_drawn_under_the_current_account(tmp_path):
    db_path = await _completed_season(tmp_path)

    rows = await _load_driver_rows(db_path, 1, SessionType.FEATURE_RACE)

    assert [r.driver_user_id for r in rows] == [NOW, OTHER]


async def test_a_qualifying_is_drawn_under_the_current_account(tmp_path):
    db_path = await _completed_season(tmp_path)

    rows = await _load_driver_rows(db_path, 2, SessionType.FEATURE_QUALIFYING)

    assert [r.driver_user_id for r in rows] == [NOW, OTHER]


async def test_drawing_a_completed_season_again_changes_nothing_stored(tmp_path):
    db_path = await _completed_season(tmp_path)
    before = await _stored(db_path)

    await _load_driver_rows(db_path, 1, SessionType.FEATURE_RACE)
    await _load_driver_rows(db_path, 2, SessionType.FEATURE_QUALIFYING)

    assert await _stored(db_path) == before
    assert ("R", PAST, 1) in before
