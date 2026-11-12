"""A round's facts with its division's and its season's, read on a handed connection (#284, S7).

`round_facts_on` is the core half of what a module's query joining its own table to `rounds`,
`divisions` and `seasons` asked in one statement: it returns the round as core's own tables hold it,
or None where the round is no longer there.
"""
from __future__ import annotations

from datetime import datetime, timezone

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.round import RoundFormat, RoundStatus
from leaguebot.core.models.season import SeasonStage, SeasonStatus
from leaguebot.core.services.seasons.season_service import (
    RoundFacts,
    division_on,
    final_round_ids_among_on,
    live_season_on,
    round_facts_on,
    rounds_from_on,
)


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


async def test_the_final_rounds_among_those_asked_are_the_ones_returned(tmp_path):
    path = str(tmp_path / "league.db")
    cancelled = await _seed(path)
    async with get_connection(path) as db:
        ids = []
        for number, status in ((6, "FINAL"), (7, "NOT_RUN"), (8, "FINAL")):
            cursor = await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, scheduled_at, status) "
                "VALUES (4, ?, 'NORMAL', '2026-03-01T18:00:00+00:00', ?)",
                (number, status),
            )
            ids.append(int(cursor.lastrowid))
        final_a, not_run, final_b = ids
        assert await final_round_ids_among_on(db, [cancelled, final_a, not_run, final_b]) == {
            final_a, final_b,
        }
        assert await final_round_ids_among_on(db, [final_b]) == {final_b}


async def test_no_final_round_among_those_asked_returns_the_empty_set(tmp_path):
    path = str(tmp_path / "league.db")
    cancelled = await _seed(path)
    async with get_connection(path) as db:
        assert await final_round_ids_among_on(db, [cancelled, cancelled + 50]) == set()
        assert await final_round_ids_among_on(db, []) == set()


async def test_the_division_is_read_on_the_handed_connection(tmp_path):
    path = str(tmp_path / "league.db")
    await _seed(path)
    async with get_connection(path) as db:
        division = await division_on(db, 4)
        assert division is not None
        assert (division.id, division.season_id, division.name, division.tier) == (4, 7, "Pro", 2)
        await db.execute("UPDATE divisions SET name = 'Elite' WHERE id = 4")
        renamed = await division_on(db, 4)
        assert renamed is not None and renamed.name == "Elite"


async def test_a_division_that_is_not_there_is_none(tmp_path):
    path = str(tmp_path / "league.db")
    await _seed(path)
    async with get_connection(path) as db:
        assert await division_on(db, 5) is None


async def test_the_rounds_from_one_forward_are_read_in_round_order_with_their_status(tmp_path):
    path = str(tmp_path / "league.db")
    await _seed(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, track_name, scheduled_at, "
            "status) VALUES (4, 7, 'NORMAL', 'Spa', '2026-03-01T18:00:00+00:00', 'FINAL'), "
            "(4, 6, 'NORMAL', 'Imola', '2026-02-15T18:00:00+00:00', 'NOT_RUN'), "
            "(4, 1, 'NORMAL', 'Baku', '2025-12-01T18:00:00+00:00', 'FINAL')"
        )
        from_id = (await (await db.execute("SELECT id FROM rounds WHERE round_number = 5")).fetchone())["id"]
        rounds = await rounds_from_on(db, 4, from_id)
        assert [(r.round_number, r.status) for r in rounds] == [
            (5, "CANCELLED"), (6, "NOT_RUN"), (7, "FINAL"),
        ]
        assert [r.track_name for r in rounds] == ["Monza", "Imola", "Spa"]


async def test_no_rounds_are_read_from_a_round_that_is_not_there(tmp_path):
    path = str(tmp_path / "league.db")
    round_id = await _seed(path)
    async with get_connection(path) as db:
        assert await rounds_from_on(db, 4, round_id + 50) == []


async def test_the_live_season_is_read_on_the_handed_connection_with_its_stage(tmp_path):
    path = str(tmp_path / "league.db")
    await _seed(path)
    async with get_connection(path) as db:
        season = await live_season_on(db)
        assert season is not None and season.id == 7
        assert season.stage != SeasonStage.PENDING_COMPLETION
        await db.execute("UPDATE seasons SET stage = 'PENDING_COMPLETION' WHERE id = 7")
        moved = await live_season_on(db)
        assert moved is not None and moved.stage == SeasonStage.PENDING_COMPLETION


async def test_no_live_season_is_none(tmp_path):
    path = str(tmp_path / "league.db")
    await _seed(path)
    async with get_connection(path) as db:
        await db.execute("UPDATE seasons SET status = 'COMPLETED' WHERE id = 7")
        assert await live_season_on(db) is None
