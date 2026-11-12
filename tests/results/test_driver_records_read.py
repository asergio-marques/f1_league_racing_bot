"""Results answers core's questions about a driver's records from its own tables (#283).

Core reads no table of results' (architecture.md, "Each table is read and written by its owner
alone"). `/driver reassign` asks which divisions a driver's accounts raced in, and the history
written at a season's end or cancellation asks for a driver's last standing in a division: each
through a hook core declares, on the connection of the save that asks, which the builder makes
and results' wiring signs results up to.
"""
from __future__ import annotations

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.teams import seed_team_instances

A, B, C = "6501", "6502", "6503"
LEADER = "6464"
#: Seasons 1 and 3; season 1 holds Pro and Am, season 3 holds Pro.
S1_PRO, S1_AM, S3_PRO = 11, 12, 31


async def _league(tmp_path) -> str:
    """Three divisions, each with one round and one active feature race result set."""
    db_path = str(tmp_path / "records.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        for season in (1, 3):
            await db.execute(
                "INSERT INTO seasons (id, start_date, status, season_number) "
                "VALUES (?, '2026-01-01', 'COMPLETED', ?)",
                (season, season),
            )
        for division, season, name in ((S1_PRO, 1, "Pro"), (S1_AM, 1, "Am"), (S3_PRO, 3, "Pro")):
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, mention_role_id, tier) "
                "VALUES (?, ?, ?, 1001, 1)",
                (division, season, name),
            )
            await seed_team_instances(db, division, 501)
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, format, scheduled_at) "
                "VALUES (?, ?, 1, 'NORMAL', '2026-01-20T18:00:00+00:00')",
                (division, division),
            )
            await db.execute(
                "INSERT INTO session_results (id, round_id, division_id, session_type, status) "
                "VALUES (?, ?, ?, 'FEATURE_RACE', 'ACTIVE')",
                (division, division, division),
            )
        await db.commit()
    return db_path


async def _took_part(db, how: str, account: str, division: int) -> None:
    """*account* holds a race result, a qualifying result or a standing in *division*."""
    if how == "standing":
        await db.execute(
            "INSERT INTO driver_standings_snapshots (round_id, division_id, driver_user_id, "
            "standing_position, total_points) VALUES (?, ?, ?, 1, 25)",
            (division, division, int(account)),
        )
        return
    table = "race_session_results" if how == "race" else "qualifying_session_results"
    await db.execute(
        f"INSERT INTO {table} (session_result_id, driver_user_id, team_instance_id, "
        "finishing_position) VALUES (?, ?, 501, 1)",
        (division, int(account)),
    )


@pytest.mark.parametrize("how", ["race", "qualifying", "standing"])
async def test_results_answers_the_divisions_a_driver_s_accounts_raced_in(tmp_path, how):
    """A driver holds accounts A and B. A holds a race result, a qualifying result or a standing
    in Season 1 Pro; B a race result in Season 1 Am; C, an account not the driver's, raced Season 3
    Pro. Written on the reassign's save and not yet committed, results answers, on that save's
    connection, that A and B raced in Season 1 Pro and Season 1 Am, and that no account raced in
    anything where it is handed none."""
    from leaguebot.results.services.results_purge_service import divisions_raced_on

    db_path = await _league(tmp_path)
    async with get_connection(db_path) as db:
        await _took_part(db, how, A, S1_PRO)
        await _took_part(db, "race", B, S1_AM)
        await _took_part(db, "race", C, S3_PRO)

        assert await divisions_raced_on(db, [A, B]) == {S1_PRO, S1_AM}
        assert await divisions_raced_on(db, []) == set()


async def _standings(db, rows: list[tuple[int, str, int, int]]) -> None:
    """Season 1 Pro's rounds 2 to 4 and its standings: (round number, account, position, points)
    per row. The rounds are given ids against their numbers, so that the last round is the highest
    number, not the highest id."""
    for number in (2, 3, 4):
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, scheduled_at) "
            "VALUES (?, ?, ?, 'NORMAL', '2026-02-01T18:00:00+00:00')",
            (100 - number, S1_PRO, number),
        )
    for number, account, position, points in rows:
        await db.execute(
            "INSERT INTO driver_standings_snapshots (round_id, division_id, driver_user_id, "
            "standing_position, total_points) VALUES (?, ?, ?, ?, ?)",
            (100 - number, S1_PRO, int(account), position, points),
        )


async def test_results_answers_a_driver_s_last_standing_and_the_winner_s_points(tmp_path):
    """Season 1 Pro raced rounds 2 to 4 with standings. The driver stood 1st on 25 and 1st on 43
    after rounds 2 and 3 on account A, then changed account and stood 2nd on 88 after round 4 on
    B; the leader stood on 100 after round 4. Asked on the season end's save's connection, with the driver's
    accounts A and B, results answers the driver's last standing, 2nd on 88, and the winner's 100
    points. A driver of Pro with no standing (C) is answered no standing and the winner's 100, and
    a division with no standings (Season 1 Am) neither."""
    from leaguebot.results.services.standings.season_standings_posts import season_standing_on

    db_path = await _league(tmp_path)
    async with get_connection(db_path) as db:
        await _standings(db, [
            (2, A, 1, 25), (3, A, 1, 43), (4, B, 2, 88), (4, LEADER, 1, 100), (3, LEADER, 2, 40),
        ])

        assert tuple(await season_standing_on(db, S1_PRO, [A, B])) == ((2, 88), 100)
        assert tuple(await season_standing_on(db, S1_PRO, [C])) == (None, 100)
        assert tuple(await season_standing_on(db, S1_AM, [A, B])) == (None, None)


async def test_the_builder_signs_results_up_for_a_driver_s_records(tmp_path, monkeypatch):
    """Results answers which divisions a driver raced in and their season standing. The hooks as
    the builder makes them, signed up by results' wiring, ask results' own answers, each handed the
    save's connection and what core asks with (the driver's accounts; the division and the
    accounts), and hand back what results answers."""
    from tests.support.driver_records_hooks import divisions_raced_read, season_standing_read

    asked: list[tuple] = []

    async def raced(db, accounts):
        asked.append(("raced", db, accounts))
        return {S1_PRO}

    async def standing(db, division_id, accounts):
        asked.append(("standing", db, division_id, accounts))
        return ((2, 88), 100)

    monkeypatch.setattr(
        "leaguebot.results.services.results_purge_service.divisions_raced_on", raced
    )
    monkeypatch.setattr(
        "leaguebot.results.services.standings.season_standings_posts.season_standing_on", standing
    )
    db = object()

    assert await divisions_raced_read().call(db, [A, B]) == [{S1_PRO}]
    assert await season_standing_read().call(db, S1_PRO, [A, B]) == [((2, 88), 100)]
    assert asked == [("raced", db, [A, B]), ("standing", db, S1_PRO, [A, B])]
