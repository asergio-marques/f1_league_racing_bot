"""Marking the verdict banner that heads an attendance sanction's card (#345).

A banner belongs to no verdict record, so its row in `verdict_banner_messages` is the only thing
that knows where it is. A sanction card posted under a banner marks that banner's row as heading
sanctions, so nothing that takes a round's verdicts down takes the banner over the card with them.

The re-announcing of a division's verdicts from a round forward, which this file tested until
#439, went with `republish_verdicts_from_round`: an amendment's verdicts are now announced by the
jobs of its appeals stage on the change queue (`test_amendment_stage_changes.py`).
"""
from __future__ import annotations

import os
from types import SimpleNamespace

from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.teams import seed_team_instances

SEASON_ID = 71
DIVISION_ID = 81
VERDICTS_CHANNEL = 6100


async def _seed(tmp_path, name: str, *, rounds=(1, 2, 3)) -> tuple[str, dict]:
    """A division of *rounds* rounds, each with one race and one driver."""
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    ids: dict = {}
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 7, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await seed_team_instances(db, DIVISION_ID, 3001)
        for number in rounds:
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
                "status) VALUES (?, ?, ?, ?, 'NORMAL', 'FINAL')",
                (number, DIVISION_ID, number, f"2026-0{number}-01T18:00:00+00:00"),
            )
            session = await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status) "
                "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
                (number, DIVISION_ID),
            )
            cursor = await db.execute(
                "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                "team_instance_id, finishing_position) VALUES (?, ?, 3001, 1)",
                (session.lastrowid, 100 + number),
            )
            ids[number] = cursor.lastrowid
        await db.commit()
    return db_path, ids

async def _banner(db_path, round_id: int, message_id: int, channel=VERDICTS_CHANNEL):
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO verdict_banner_messages (round_id, channel_id, message_id, posted_at) "
            "VALUES (?, ?, ?, '2026-02-02T00:00:00+00:00')",
            (round_id, str(channel), str(message_id)),
        )
        await db.commit()

async def test_a_sanction_card_marks_exactly_its_banner(tmp_path):
    from leaguebot.results.services.verdict_announcement_service import _mark_banner_over_sanction

    db_path, _ = await _seed(tmp_path, "banner_marked", rounds=(1, 2))
    for round_id, message_id in ((1, 6001), (1, 6003), (2, 6004)):
        await _banner(db_path, round_id, message_id)

    await _mark_banner_over_sanction(db_path, SimpleNamespace(id=6003))

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT message_id FROM verdict_banner_messages WHERE heads_sanctions = 1"
        )
        assert [row[0] for row in await cursor.fetchall()] == ["6003"]

async def test_a_card_under_no_banner_marks_nothing(tmp_path):
    from leaguebot.results.services.verdict_announcement_service import _mark_banner_over_sanction

    db_path, _ = await _seed(tmp_path, "banner_unmarked", rounds=(1,))
    await _banner(db_path, 1, 6001)

    await _mark_banner_over_sanction(db_path, None)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM verdict_banner_messages WHERE heads_sanctions = 1"
        )
        assert (await cursor.fetchone())[0] == 0
