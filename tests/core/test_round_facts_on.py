"""A round's facts with its division's and its season's, read on a handed connection (#284, S7).

`round_facts_on` is the core half of what a module's query joining its own table to `rounds`,
`divisions` and `seasons` asked in one statement: it returns the round as core's own tables hold it,
or None where the round is no longer there.
"""
from __future__ import annotations

from datetime import datetime, timezone

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.round import RoundFormat, RoundStatus
from leaguebot.core.models.season import SeasonStatus
from leaguebot.core.services.seasons.season_service import RoundFacts, round_facts_on


async def _seed(path: str) -> int:
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (7, '2026-01-01', 'ACTIVE', 3)"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier) "
            "VALUES (4, 7, 'Pro', 10, 2)"
        )
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, track_name, scheduled_at, "
            "status, not_held) VALUES (4, 5, 'SPRINT', 'Monza', '2026-02-01T18:00:00+00:00', "
            "'CANCELLED', 1)"
        )
        await db.commit()
        return int(cursor.lastrowid)


async def test_the_round_is_read_with_its_division_and_season(tmp_path):
    path = str(tmp_path / "league.db")
    round_id = await _seed(path)
    async with get_connection(path) as db:
        facts = await round_facts_on(db, round_id)
    assert facts == RoundFacts(
        round_id=round_id, round_number=5, status=RoundStatus.CANCELLED,
        format=RoundFormat.SPRINT, track_name="Monza",
        scheduled_at=datetime(2026, 2, 1, 18, tzinfo=timezone.utc), not_held=True,
        division_id=4, division_name="Pro", division_tier=2, season_id=7, season_number=3,
        season_status=SeasonStatus.ACTIVE,
    )


async def test_a_round_that_is_not_there_is_none(tmp_path):
    path = str(tmp_path / "league.db")
    round_id = await _seed(path)
    async with get_connection(path) as db:
        assert await round_facts_on(db, round_id + 1) is None


async def test_what_the_handed_connection_has_written_is_read_back(tmp_path):
    path = str(tmp_path / "league.db")
    round_id = await _seed(path)
    async with get_connection(path) as db:
        await db.execute("UPDATE rounds SET status = 'FINAL', not_held = 0 WHERE id = ?", (round_id,))
        await db.execute("UPDATE seasons SET status = 'COMPLETED' WHERE id = 7")
        facts = await round_facts_on(db, round_id)
    assert facts is not None
    assert (facts.status, facts.not_held, facts.season_status) == (
        RoundStatus.FINAL, False, SeasonStatus.COMPLETED,
    )
