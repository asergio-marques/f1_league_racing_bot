"""The one join every reader of a round's verdicts goes through (#345).

A verdict record stores neither its driver nor its session, only a reference to the driver's row
in whichever result table its session writes to. `select_verdicts` and `delete_verdicts` hold
that join once, for the seven readers that each wrote it out before. What they must get right is
the scope: a round, a round narrowed to some sessions, or one session — and both result tables
either way, since a qualifying verdict points at a qualifying row.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
from leaguebot.results.models.points_config import SessionType  # noqa: E402
from leaguebot.results.services.verdict_records import delete_verdicts, select_verdicts  # noqa: E402
from tests.support.teams import seed_team_instances  # noqa: E402

ROUND_ID = 21
OTHER_ROUND_ID = 22


async def _db(tmp_path) -> tuple[str, dict[str, int]]:
    """Round 21 with a race and a qualifying session, round 22 with a race; a verdict of each
    kind on each. Returns the session ids by name."""
    db_path = os.path.join(str(tmp_path), "verdicts.db")
    await run_migrations(db_path)
    sessions: dict[str, int] = {}
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (1, 1, '2026-01-01', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (11, 1, 'Pro', 1, 555)"
        )
        await seed_team_instances(db, 11, 3001)
        for round_id, number in ((ROUND_ID, 3), (OTHER_ROUND_ID, 4)):
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, status) "
                "VALUES (?, 11, ?, '2026-02-01T18:00:00+00:00', 'NORMAL', 'FINAL')",
                (round_id, number),
            )
        for name, round_id, session_type, driver in (
            ("race", ROUND_ID, "FEATURE_RACE", 101),
            ("quali", ROUND_ID, "FEATURE_QUALIFYING", 102),
            ("other", OTHER_ROUND_ID, "FEATURE_RACE", 103),
        ):
            session = await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status) "
                "VALUES (?, 11, ?, 'ACTIVE')",
                (round_id, session_type),
            )
            sessions[name] = session.lastrowid
            is_quali = session_type.endswith("QUALIFYING")
            result = await db.execute(
                f"INSERT INTO {'qualifying' if is_quali else 'race'}_session_results "
                "(session_result_id, driver_user_id, team_instance_id, finishing_position) "
                "VALUES (?, ?, 3001, 1)",
                (session.lastrowid, driver),
            )
            link = "qual_result_id" if is_quali else "race_result_id"
            await db.execute(
                f"INSERT INTO penalty_records ({link}, penalty_type, description, justification, "
                "applied_by, applied_at) VALUES (?, 'TIME', 'd', 'j', '9', '2026-02-01')",
                (result.lastrowid,),
            )
            await db.execute(
                f"INSERT INTO appeal_records ({link}, penalty_type, description, justification, "
                "submitted_by, submitted_at) VALUES (?, 'TIME', 'd', 'j', '9', '2026-02-01')",
                (result.lastrowid,),
            )
        await db.commit()
    return db_path, sessions


async def _drivers(db_path, table, **scope) -> list[tuple[int, str]]:
    async with get_connection(db_path) as db:
        rows = await select_verdicts(
            db, table, "r.driver_user_id AS driver, sr.session_type AS session", **scope
        )
    return sorted((row["driver"], row["session"]) for row in rows)


async def test_a_round_reaches_both_result_tables(tmp_path):
    db_path, _ = await _db(tmp_path)

    assert await _drivers(db_path, "penalty_records", round_id=ROUND_ID) == [
        (101, "FEATURE_RACE"), (102, "FEATURE_QUALIFYING"),
    ]


async def test_a_round_can_be_narrowed_to_some_sessions(tmp_path):
    db_path, _ = await _db(tmp_path)

    assert await _drivers(
        db_path, "appeal_records", round_id=ROUND_ID,
        session_types=[SessionType.FEATURE_QUALIFYING],
    ) == [(102, "FEATURE_QUALIFYING")]


async def test_one_session_is_its_own_scope(tmp_path):
    db_path, sessions = await _db(tmp_path)

    assert await _drivers(
        db_path, "penalty_records", session_result_id=sessions["other"]
    ) == [(103, "FEATURE_RACE")]


async def test_a_condition_of_the_callers_own_narrows_it_further(tmp_path):
    db_path, _ = await _db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE penalty_records SET announcement_message_id = 5001 WHERE id = 1")
        await db.commit()
        rows = await select_verdicts(
            db, "penalty_records", "v.announcement_message_id AS anchor",
            round_id=ROUND_ID, where=" AND v.announcement_message_id IS NOT NULL",
        )

    assert [row["anchor"] for row in rows] == ["5001"]


async def test_only_a_verdict_table_is_read(tmp_path):
    db_path, _ = await _db(tmp_path)
    async with get_connection(db_path) as db:
        with pytest.raises(ValueError):
            await select_verdicts(db, "driver_profiles", "*", round_id=ROUND_ID)


async def test_a_query_with_no_scope_is_refused(tmp_path):
    """Unscoped, it would read — or delete — every verdict of every round."""
    db_path, _ = await _db(tmp_path)
    async with get_connection(db_path) as db:
        with pytest.raises(ValueError):
            await delete_verdicts(db)


async def test_deleting_a_rounds_sessions_leaves_the_rest(tmp_path):
    db_path, _ = await _db(tmp_path)
    async with get_connection(db_path) as db:
        await delete_verdicts(db, round_id=ROUND_ID, session_types=[SessionType.FEATURE_RACE])
        await db.commit()

    for table in ("penalty_records", "appeal_records"):
        assert await _drivers(db_path, table, round_id=ROUND_ID) == [(102, "FEATURE_QUALIFYING")]
        assert await _drivers(db_path, table, round_id=OTHER_ROUND_ID) == [(103, "FEATURE_RACE")]


async def test_deleting_one_session_leaves_the_rest(tmp_path):
    db_path, sessions = await _db(tmp_path)
    async with get_connection(db_path) as db:
        await delete_verdicts(db, session_result_id=sessions["quali"])
        await db.commit()

    assert await _drivers(db_path, "appeal_records", round_id=ROUND_ID) == [(101, "FEATURE_RACE")]
