"""Core's writer of a round's status on a handed connection, `set_round_status_on` (#439), and its
reader of the active season's live rounds, `rounds_of_the_active_season`.

The results module moves a round through its review on the save a change type is handed, so the
writer commits nothing, and it is guarded as every write of a round's status is: a stale caller may
not drag a round backwards, and a settled round is never reopened (issue #167).
"""
from __future__ import annotations

import os

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.round import ROUND_CANCELLABLE, RoundStatus
from leaguebot.core.services.seasons.season_service import (
    rounds_of_the_active_season,
    set_round_status_on,
)

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
                "'2026-06-01T00:00:00+00:00', ?)",
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


@pytest.mark.xfail(strict=True, reason="#287: the reader hands back the live rounds whose start cannot be read")
async def test_rounds_of_the_active_season_keeps_only_live_rounds(tmp_path) -> None:
    """Weather's start-up catch-up of missed phases asks for the rounds of the ACTIVE season that
    are still live, earliest first and, at the same moment, by id: a cancelled round and a round
    of a cancelled division are left out. A live round whose stored moment cannot be read is not
    passed over in silence: its id is handed back apart, so that the catch-up reports it as that
    round's failure. A season still in SETUP is not the active one, and its rounds are left out
    too; a league holds one live season at a time (`idx_seasons_one_live`), so it is judged in a
    database of its own.

    Rounds 36, 31, 35 are inserted out of order: 35 races first, 31 and 36 share a moment and are
    ordered by id."""
    db_path = os.path.join(str(tmp_path), "active_season.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (1, 1, '2026-01-01', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id, status) "
            "VALUES (11, 1, 'Pro', 1, 555, 'ACTIVE'), (12, 1, 'Am', 2, 556, 'CANCELLED')"
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, scheduled_at, "
            "status) VALUES "
            "(36, 11, 5, 'NORMAL', 'Suzuka', '2026-06-01T18:00:00+00:00', 'NOT_RUN'), "
            "(31, 11, 1, 'NORMAL', 'Silverstone', '2026-06-01T18:00:00+00:00', 'NOT_RUN'), "
            "(35, 11, 4, 'NORMAL', 'Zandvoort', '2026-04-01T18:00:00+00:00', 'NOT_RUN'), "
            "(32, 11, 2, 'NORMAL', 'Monza', '2026-05-01T18:00:00+00:00', 'CANCELLED'), "
            "(33, 12, 1, 'NORMAL', 'Spa', '2026-05-01T18:00:00+00:00', 'NOT_RUN'), "
            "(34, 11, 3, 'NORMAL', 'Imola', 'not a moment', 'NOT_RUN')"
        )
        await db.commit()

    rounds, unreadable = await rounds_of_the_active_season(db_path)
    assert [each.id for each in rounds] == [35, 31, 36]
    assert unreadable == [34]

    setup_path = os.path.join(str(tmp_path), "setup_season.db")
    await run_migrations(setup_path)
    async with get_connection(setup_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (1, 1, '2026-01-01', 'SETUP')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (11, 1, 'Pro', 1, 555)"
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, scheduled_at, "
            "status) VALUES (31, 11, 1, 'NORMAL', 'Silverstone', '2026-06-01T18:00:00+00:00', "
            "'NOT_RUN')"
        )
        await db.commit()

    assert await rounds_of_the_active_season(setup_path) == ([], [])
