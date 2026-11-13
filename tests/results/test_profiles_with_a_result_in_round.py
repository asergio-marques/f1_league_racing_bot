"""Results' reader of who raced a round, read on a connection the caller holds.

Attendance scores a round from who raced it, and asks results through
`profiles_with_a_result_in_round_on` in place of its own join of results' tables (architecture.md,
"The database").
"""
from __future__ import annotations

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.services.standings.standings_service import (
    profiles_with_a_result_in_round_on,
)

DIVISION_ID = 4
TEAM = 9
A, B, C, D, E = 1, 2, 3, 4, 5


async def _seed(path: str) -> tuple[int, int, int]:
    """Season 1 is active with one division and three rounds. In round 1, driver A has an active
    feature race result, which he did not finish; driver B an active qualifying result; driver C
    a result in a sprint race whose results were superseded; and an entry carrying no profile
    stands in the feature race too. In round 2, driver D has an active race result. Round 3
    holds no result. Driver E races nothing. Returns the three rounds' ids."""
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 1)"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier) "
            "VALUES (?, 1, 'Pro', 10, 1)",
            (DIVISION_ID,),
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, "
            "is_reserve) VALUES (?, ?, 'Alpha', 'Alpha Motorsport', 2, 0)",
            (TEAM, DIVISION_ID),
        )
        for profile in (A, B, C, D, E):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (?, ?, 'ASSIGNED')",
                (profile, str(100 + profile)),
            )
        rounds = []
        for number in (1, 2, 3):
            cursor = await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, scheduled_at) "
                "VALUES (?, ?, 'NORMAL', '2026-02-01T18:00:00+00:00')",
                (DIVISION_ID, number),
            )
            rounds.append(int(cursor.lastrowid))

        async def _session(round_id: int, session_type: str, status: str) -> int:
            cursor = await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status) "
                "VALUES (?, ?, ?, ?)",
                (round_id, DIVISION_ID, session_type, status),
            )
            return int(cursor.lastrowid)

        async def _race(session: int, profile: int | None, outcome: str = "CLASSIFIED") -> None:
            await db.execute(
                "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                "team_instance_id, finishing_position, outcome, driver_profile_id) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (session, 100 + (profile or 0), TEAM, outcome, profile),
            )

        feature = await _session(rounds[0], "FEATURE_RACE", "ACTIVE")
        await _race(feature, A, "DNF")
        await _race(feature, None)
        qualifying = await _session(rounds[0], "FEATURE_QUALIFYING", "ACTIVE")
        await db.execute(
            "INSERT INTO qualifying_session_results (session_result_id, driver_user_id, "
            "team_instance_id, finishing_position, driver_profile_id) VALUES (?, ?, ?, 1, ?)",
            (qualifying, 100 + B, TEAM, B),
        )
        await _race(await _session(rounds[0], "SPRINT_RACE", "SUPERSEDED"), C)
        await _race(await _session(rounds[1], "FEATURE_RACE", "ACTIVE"), D)
        await db.commit()
    return rounds[0], rounds[1], rounds[2]


async def test_the_drivers_with_an_active_result_in_a_round_are_those_who_raced_it(tmp_path):
    """Round 1 was raced by A, who did not finish, and by B, who has only a qualifying result: a
    superseded result (C's), an entry with no profile and another round's result (D's) do not
    count. Round 2 was raced by D alone, and round 3 by nobody."""
    path = str(tmp_path / "league.db")
    first, second, third = await _seed(path)

    async with get_connection(path) as db:
        assert await profiles_with_a_result_in_round_on(db, first) == {A, B}
        assert await profiles_with_a_result_in_round_on(db, second) == {D}
        assert await profiles_with_a_result_in_round_on(db, third) == set()
