"""Core's writer of a round's status on a handed connection, `set_round_status_on` (#439).

The results module moves a round through its review on the save a change type is handed, so the
writer commits nothing, and it is guarded as every write of a round's status is: a stale caller may
not drag a round backwards, and a settled round is never reopened (issue #167).
"""
from __future__ import annotations

import os

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.round import ROUND_CANCELLABLE, RoundStatus
from leaguebot.core.services.season_service import set_round_status_on

SERVER_ID = 12608
DIVISION_ID = 11

#: Round id -> the status it is seeded in.
SEEDED = {
    21: RoundStatus.AWAITING_RESULTS.value,
    22: RoundStatus.FINAL.value,
    23: RoundStatus.CANCELLED.value,
}


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "round_status.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (1, 1, '2026-01-01', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, 1, 'Pro', 1, 555)",
            (DIVISION_ID,),
        )
        for number, (round_id, status) in enumerate(SEEDED.items(), start=1):
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
                "scheduled_at, status) VALUES (?, ?, ?, 'NORMAL', 'Silverstone', "
                "'2026-06-01', ?)",
                (round_id, DIVISION_ID, number, status),
            )
        await db.commit()
    return db_path


async def _statuses(db_path: str) -> dict[int, str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT id, status FROM rounds ORDER BY id")
        return {row["id"]: row["status"] for row in await cursor.fetchall()}


async def test_set_round_status_on_respects_its_guard(tmp_path) -> None:
    """Only a round inside the guard moves, and the call says so; a FINAL or CANCELLED round is
    left as it is. Without `only_from` a round that is not terminal moves and a terminal one does
    not. Nothing is saved until the caller commits."""
    db_path = await _make_db(tmp_path)

    async with get_connection(db_path) as db:
        guarded = {
            round_id: await set_round_status_on(
                db, round_id, RoundStatus.AWAITING_REPORT_VERDICTS, only_from=ROUND_CANCELLABLE,
            )
            for round_id in SEEDED
        }
        assert await _statuses(db_path) == SEEDED
        await db.commit()

    assert guarded == {21: True, 22: False, 23: False}
    assert await _statuses(db_path) == {
        21: RoundStatus.AWAITING_REPORT_VERDICTS.value,
        22: RoundStatus.FINAL.value,
        23: RoundStatus.CANCELLED.value,
    }

    async with get_connection(db_path) as db:
        unguarded = {
            round_id: await set_round_status_on(
                db, round_id, RoundStatus.AWAITING_APPEAL_VERDICTS,
            )
            for round_id in SEEDED
        }
        assert (await _statuses(db_path))[21] == RoundStatus.AWAITING_REPORT_VERDICTS.value
        await db.commit()

    assert unguarded == {21: True, 22: False, 23: False}
    assert await _statuses(db_path) == {
        21: RoundStatus.AWAITING_APPEAL_VERDICTS.value,
        22: RoundStatus.FINAL.value,
        23: RoundStatus.CANCELLED.value,
    }
