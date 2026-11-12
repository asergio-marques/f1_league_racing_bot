"""A division's teams, read on a handed connection (#284, S7).

`TeamService.division_teams_on` is the core half of what a module's query of `team_instances`
asked when it ordered a division's standings or opened its grid.
"""
from __future__ import annotations

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.drivers.team_service import TeamService


async def _seed(path: str) -> None:
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (1, 1, '2026-01-01', 'ACTIVE')"
        )
        for division_id, name, tier in ((11, "Pro", 1), (12, "Am", 2), (13, "Open", 3)):
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
                "VALUES (?, 1, ?, ?, 555)",
                (division_id, name, tier),
            )
        # (instance, division, shorthand, full name, seats, is_reserve): Pro's two teams and its
        # reserve team added in that order, and one team of Am's.
        for instance_id, division_id, name, full_name, seats, reserve in (
            (1, 11, "Zed", "Zed Racing", 2, 0),
            (2, 11, "Reserve", "Reserve Pool", -1, 1),
            (3, 11, "Alpha", "Alpha Motorsport", 3, 0),
            (4, 12, "Zed", "Zed Racing", 2, 0),
        ):
            await db.execute(
                "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, "
                "is_reserve) VALUES (?, ?, ?, ?, ?, ?)",
                (instance_id, division_id, name, full_name, seats, reserve),
            )
        await db.commit()


async def test_a_divisions_teams_are_read_in_the_order_added_and_no_others(tmp_path):
    """Pro holds Zed, its reserve team and Alpha: all three come back, in that order, with the
    names, the seats and the reserve flag they hold, and not Am's Zed."""
    path = str(tmp_path / "league.db")
    await _seed(path)
    async with get_connection(path) as db:
        teams = await TeamService.division_teams_on(db, 11)
    assert [(t.id, t.name, t.full_name, t.max_seats, t.is_reserve) for t in teams] == [
        (1, "Zed", "Zed Racing", 2, False),
        (2, "Reserve", "Reserve Pool", -1, True),
        (3, "Alpha", "Alpha Motorsport", 3, False),
    ]
    assert {t.division_id for t in teams} == {11}


async def test_a_division_with_no_team_or_none_reads_an_empty_list(tmp_path):
    path = str(tmp_path / "league.db")
    await _seed(path)
    async with get_connection(path) as db:
        assert await TeamService.division_teams_on(db, 13) == []
        assert await TeamService.division_teams_on(db, 99) == []
