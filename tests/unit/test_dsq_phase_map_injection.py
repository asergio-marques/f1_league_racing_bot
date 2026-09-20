"""Stating which rows were disqualified, instead of reading it from the verdict tables (#345).

The post-race and appeal `DSQ` marks on a results table come from `penalty_records` and
`appeal_records` alone. Nothing else can produce them: the stored penalty columns hold
milliseconds, and a disqualification is recorded as zero, so a classification with the marks
stripped out looks exactly like one where nobody was disqualified.

That is fine while the tables agree with the classification being drawn. The amendment replay
breaks that assumption deliberately: it posts what it has computed **before** committing any of
it, so at the moment of posting the tables still describe the round being replaced. Reading them
there would draw the *old* classification's marks onto the new one — a driver disqualified in
the round as raced would keep the mark in a corrected classification that no longer disqualifies
them.

So the map becomes something a caller may supply. Every caller that posts from committed state
omits it and reads the database exactly as before; the replay passes the map it computed.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from services import results_post_service  # noqa: E402

ROUND_ID = 61
DIVISION_ID = 31
SEASON_ID = 21


async def _seed(tmp_path, name: str) -> tuple[str, int, list]:
    """One feature race, two drivers, with the first disqualified by a penalty verdict."""
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 5, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, status) "
            "VALUES (?, ?, 1, '2026-02-01T18:00:00+00:00', 'NORMAL', 'FINAL')",
            (ROUND_ID, DIVISION_ID),
        )
        session = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (ROUND_ID, DIVISION_ID),
        )
        session_id = session.lastrowid
        row_ids = []
        for position, driver in enumerate((101, 102), start=1):
            cursor = await db.execute(
                "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                "team_role_id, finishing_position) VALUES (?, ?, 3001, ?)",
                (session_id, driver, position),
            )
            row_ids.append(cursor.lastrowid)
        await db.execute(
            "INSERT INTO penalty_records (race_result_id, penalty_type, description, "
            "justification, applied_by, applied_at) VALUES (?, 'DSQ', 'Ignored a flag', "
            "'Disqualified', '77', '2026-02-02T00:00:00+00:00')",
            (row_ids[0],),
        )
        await db.commit()
    return db_path, session_id, row_ids


def _driver_rows(row_ids):
    return [
        SimpleNamespace(
            id=row_ids[i], driver_user_id=driver, team_role_id=3001,
            finishing_position=i + 1, outcome="CLASSIFIED", base_time_ms=None,
            laps_behind=None, ingame_time_penalties_ms=0, postrace_time_penalties_ms=0,
            appeal_time_penalties_ms=0, fastest_lap=None, fastest_lap_bonus=0,
            points_awarded=0, total_time_ms=None,
        )
        for i, driver in enumerate((101, 102))
    ]


async def _post(db_path, session_id, driver_rows, **kwargs) -> dict:
    """Post a session and return what the formatter was asked to draw."""
    channel = MagicMock()
    sent = SimpleNamespace(id=7001)

    async def _send(*_a, **_kw):
        return sent

    channel.send = AsyncMock(side_effect=_send)
    session_result = SimpleNamespace(
        id=session_id, round_id=ROUND_ID, division_id=DIVISION_ID,
        session_type="FEATURE_RACE", status="ACTIVE", config_name="Standard",
        submitted_by=1, submitted_at="2026-02-01T20:00:00+00:00", results_message_id=None,
    )
    seen: dict = {}

    def _format(rows, points_map, **kw):
        seen["dsq_phase_map"] = kw.get("dsq_phase_map")
        return "table"

    with patch.object(results_post_service.results_formatter, "format_race_table", _format):
        await results_post_service.post_session_results(
            db_path, session_result, driver_rows, {}, channel, MagicMock(),
            1, "Silverstone", "Final Results", is_sprint=False, **kwargs,
        )
    return seen


async def test_the_map_is_read_from_the_database_when_none_is_given(tmp_path):
    """Every caller posting from committed state keeps the behaviour it always had."""
    db_path, session_id, row_ids = await _seed(tmp_path, "dsq_default")

    seen = await _post(db_path, session_id, _driver_rows(row_ids))

    assert seen["dsq_phase_map"] == {row_ids[0]: "PENALTY"}


async def test_a_supplied_map_is_used_instead_of_the_database(tmp_path):
    """The replay's own map wins, which is the whole point of the parameter."""
    db_path, session_id, row_ids = await _seed(tmp_path, "dsq_supplied")

    seen = await _post(
        db_path, session_id, _driver_rows(row_ids),
        dsq_phase_map={row_ids[1]: "APPEAL"},
    )

    assert seen["dsq_phase_map"] == {row_ids[1]: "APPEAL"}


async def test_an_empty_map_clears_the_marks_rather_than_falling_back(tmp_path):
    """`{}` is a statement, not an absence.

    A corrected classification that disqualifies nobody must draw no marks, even while the
    tables still describe the round it replaces. Treating an empty map as "nothing supplied"
    would reinstate exactly the stale marks the parameter exists to avoid.
    """
    db_path, session_id, row_ids = await _seed(tmp_path, "dsq_empty")

    seen = await _post(db_path, session_id, _driver_rows(row_ids), dsq_phase_map={})

    assert seen["dsq_phase_map"] == {}
