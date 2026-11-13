"""Core's reader of where each round of a division stands, read on a connection the caller holds.

Attendance's scoring and its recalculation ask only a round's number and status of core, to know
which rounds are cancelled and which finalised rounds come before another, and read them through
`division_round_states_on` in place of their own reads of `rounds` (architecture.md, "The
database").
"""
from __future__ import annotations

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.seasons.season_service import division_round_states_on

PRO, AM = 4, 5


async def _seed(path: str) -> dict[int, int]:
    """Season 1 is active with Pro and Am. Pro holds round 3 (FINAL), round 1 (CANCELLED) and
    round 2, whose format is one the Round model no longer knows, inserted out of round order; Am
    holds a round 1 of its own. Returns each Pro round's id by its number."""
    await run_migrations(path)
    ids: dict[int, int] = {}
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 1)"
        )
        for division_id, name, tier in ((PRO, "Pro", 1), (AM, "Am", 2)):
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, mention_role_id, tier) "
                "VALUES (?, 1, ?, 10, ?)",
                (division_id, name, tier),
            )
        for number, form, status in ((3, "NORMAL", "FINAL"), (1, "SPRINT", "CANCELLED"),
                                     (2, "TIME_TRIAL", "NOT_RUN")):
            cursor = await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, scheduled_at, status) "
                "VALUES (?, ?, ?, '2026-02-01T18:00:00+00:00', ?)",
                (PRO, number, form, status),
            )
            ids[number] = int(cursor.lastrowid)
        await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at, status) "
            "VALUES (?, 1, 'NORMAL', '2026-02-01T18:00:00+00:00', 'FINAL')",
            (AM,),
        )
        await db.commit()
    return ids


async def test_division_round_states_on_reads_the_number_and_status_of_each_round(tmp_path):
    """Pro's three rounds are read as {round id: (number, status)} in round-number order, the
    round with the unknown format read without raising and Am's round left out; a division that
    is not there reads empty."""
    path = str(tmp_path / "league.db")
    ids = await _seed(path)

    async with get_connection(path) as db:
        states = await division_round_states_on(db, PRO)
        none = await division_round_states_on(db, 99)

    assert list(states.items()) == [
        (ids[1], (1, "CANCELLED")), (ids[2], (2, "NOT_RUN")), (ids[3], (3, "FINAL")),
    ]
    assert none == {}
