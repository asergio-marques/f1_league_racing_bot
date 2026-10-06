"""Unit tests for penalty_service (T033)."""
from __future__ import annotations

import datetime

import pytest

from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.penalty_service import StagedPenalty, validate_penalty_input
from tests.support.teams import seed_team_instances

#: The time a penalty is stamped with where its staged penalty carries none of its own.
_NOW = datetime.datetime(2026, 3, 1, 20, 0, tzinfo=datetime.timezone.utc)


# ---------------------------------------------------------------------------
# validate_penalty_input
# ---------------------------------------------------------------------------


def test_validate_time_penalty_rejected_for_qualifying():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_QUALIFYING,
        penalty_value="+5",
    )
    assert isinstance(result, str)
    assert "qualifying" in result.lower() or "DSQ" in result


def test_validate_time_penalty_rejected_for_sprint_qualifying():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.SPRINT_QUALIFYING,
        penalty_value="5s",
    )
    assert isinstance(result, str)


def test_validate_dsq_accepted_for_qualifying():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_QUALIFYING,
        penalty_value="DSQ",
    )
    assert isinstance(result, StagedPenalty)
    assert result.penalty_type == "DSQ"
    assert result.penalty_seconds is None


def test_validate_time_penalty_accepted_for_race():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="+5",
    )
    assert isinstance(result, StagedPenalty)
    assert result.penalty_type == "TIME"
    assert result.penalty_seconds == 5


def test_validate_time_penalty_with_s_suffix():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="10s",
    )
    assert isinstance(result, StagedPenalty)
    assert result.penalty_seconds == 10


def test_validate_time_penalty_bare_integer():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="5",
    )
    assert isinstance(result, StagedPenalty)
    assert result.penalty_seconds == 5


def test_validate_dsq_accepted_for_race():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="DSQ",
    )
    assert isinstance(result, StagedPenalty)
    assert result.penalty_type == "DSQ"


def test_validate_invalid_penalty_value():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="notapenalty",
    )
    assert isinstance(result, str)


@pytest.mark.parametrize("typed", ["NFA", "nfa", " Nfa "])
@pytest.mark.parametrize(
    "session_type",
    [
        SessionType.FEATURE_RACE,
        SessionType.SPRINT_RACE,
        SessionType.FEATURE_QUALIFYING,
        SessionType.SPRINT_QUALIFYING,
    ],
)
def test_no_further_action_is_accepted_for_every_session(typed, session_type):
    """No further action clears a driver, so it carries no seconds and alters nothing — a
    qualifying incident can be cleared as well as a race one (#138)."""
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=session_type,
        penalty_value=typed,
    )
    assert isinstance(result, StagedPenalty)
    assert result.penalty_type == "NFA"
    assert result.penalty_seconds is None


def test_the_qualifying_refusal_names_no_further_action():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_QUALIFYING,
        penalty_value="+5s",
    )
    assert isinstance(result, str)
    assert "NFA" in result


def test_an_unreadable_value_names_no_further_action_among_the_forms():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="notapenalty",
    )
    assert isinstance(result, str)
    assert "NFA" in result


@pytest.mark.parametrize("typed", ["0", "0s", "+0s", "-0", "00"])
def test_validate_zero_rejected_pointing_to_nfa(typed):
    """A penalty of no seconds is no sanction (#138). It was accepted, applied and published
    as one — "0 seconds added" — which was the only way to say a driver had been cleared, and
    said it as a punishment. The refusal names the outcome the manager almost certainly meant.
    """
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value=typed,
        current_time_ms=1_200_000,
        current_time_penalty_s=5,
    )
    assert isinstance(result, str)
    assert "NFA" in result
    assert "no further action" in result


# ---------------------------------------------------------------------------
# DSQ supersedes TIME — logical reasoning test
# The StagedPenalty dataclass itself just records state; the "supersede" logic
# lives in the cog wizard. We verify the contract here by simulating how the
# wizard accumulates penalties: a DSQ for the same driver replaces a prior TIME.
# ---------------------------------------------------------------------------


def test_dsq_supersedes_time_in_staged_list():
    """Simulate wizard logic: adding DSQ after TIME for same driver/session."""
    staged: list[StagedPenalty] = []

    def _stage(driver_id: int, session: SessionType, value: str) -> None:
        result = validate_penalty_input(driver_id, session, value)
        assert isinstance(result, StagedPenalty)
        # Wizard logic: DSQ supersedes any existing penalty for same driver/session
        if result.penalty_type == "DSQ":
            staged[:] = [
                p for p in staged
                if not (p.driver_user_id == driver_id and p.session_type == session)
            ]
        staged.append(result)

    _stage(100, SessionType.FEATURE_RACE, "+5")
    assert len(staged) == 1
    assert staged[0].penalty_type == "TIME"

    # Now stage a DSQ — this should supersede the TIME
    _stage(100, SessionType.FEATURE_RACE, "DSQ")
    assert len(staged) == 1
    assert staged[0].penalty_type == "DSQ"


# ---------------------------------------------------------------------------
# T031 — new test cases: negative penalties, zero rejection, tiebreak, DSQ FL
# ---------------------------------------------------------------------------


def test_validate_negative_time_penalty():
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="-3s",
    )
    assert isinstance(result, StagedPenalty)
    assert result.penalty_type == "TIME"
    assert result.penalty_seconds == -3


def test_time_penalty_rejected_if_result_negative():
    # Driver has 5s race time; a -10s penalty would produce negative time
    current_time_ms = 5_000  # 5 seconds
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="-10",
        current_time_ms=current_time_ms,
    )
    assert isinstance(result, str)
    assert "negative" in result.lower()


def test_negative_penalty_rejected_when_exceeds_existing_time_penalty():
    """Negative penalty whose absolute value exceeds the driver's current penalty is rejected."""
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="-10s",
        current_time_penalty_s=5,
    )
    assert isinstance(result, str)
    assert "5s" in result


def test_negative_penalty_accepted_when_equal_to_existing_time_penalty():
    """Negative penalty exactly cancelling the driver's full existing penalty is valid."""
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="-5s",
        current_time_penalty_s=5,
    )
    assert isinstance(result, StagedPenalty)
    assert result.penalty_seconds == -5


def test_negative_penalty_accepted_when_less_than_existing_time_penalty():
    """Negative penalty smaller in magnitude than the existing penalty is valid."""
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="-3s",
        current_time_penalty_s=5,
    )
    assert isinstance(result, StagedPenalty)
    assert result.penalty_seconds == -3


def test_negative_penalty_rejected_when_no_existing_time_penalty():
    """Negative penalty is rejected when the driver has no existing time penalty (0s)."""
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="-5s",
        current_time_penalty_s=0,
    )
    assert isinstance(result, str)
    assert "0s" in result


def test_negative_penalty_check_skipped_when_current_penalty_unknown():
    """When current_time_penalty_s is None the check is skipped and the penalty is accepted."""
    result = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="-10s",
        current_time_penalty_s=None,
    )
    assert isinstance(result, StagedPenalty)
    assert result.penalty_seconds == -10


def test_negative_penalty_cumulative_second_reduction_rejected():
    """Simulate staging two successive -3s penalties when the driver has 3s applied.

    First -3s: effective remaining = 3 - 3 = 0  → accepted.
    Second -3s: effective remaining = 0          → rejected (nothing left to remove).
    """
    # First penalty: DB value = 3, no staged adjustments yet → passes.
    first = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="-3s",
        current_time_penalty_s=3,
    )
    assert isinstance(first, StagedPenalty)
    assert first.penalty_seconds == -3

    # Wizard now adjusts: DB (3) + staged (-3) = 0 effective remaining.
    effective_after_first = 3 + first.penalty_seconds  # = 0
    second = validate_penalty_input(
        driver_user_id=100,
        session_type=SessionType.FEATURE_RACE,
        penalty_value="-3s",
        current_time_penalty_s=effective_after_first,
    )
    assert isinstance(second, str)
    assert "0s" in second


async def test_apply_negative_penalty_reorders(tmp_path):
    """Driver with a -10s penalty moves above a driver with no penalty."""
    from leaguebot.core.db.database import get_connection, run_migrations
    from leaguebot.results.services.penalty_service import apply_penalties_on

    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id, log_channel_id) VALUES (1,10,20,30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) VALUES ('2026-01-01','ACTIVE',1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id) VALUES (?,?,777,888)",
            (season_id, "Main"),
        )
        division_id = cursor.lastrowid
        await seed_team_instances(db, division_id, 100, 200)
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at) VALUES (?,1,'NORMAL','2026-01-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) VALUES (?,?,'FEATURE_RACE','ACTIVE')",
            (round_id, division_id),
        )
        sr_id = cursor.lastrowid
        # P1 = driver 1 (20:00.000 = 1200000ms), P2 = driver 2 (20:10.000 = 1210000ms)
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, team_instance_id, finishing_position, outcome, base_time_ms, ingame_time_penalties_ms, postrace_time_penalties_ms, appeal_time_penalties_ms) "
            "VALUES (?,1,100,1,'CLASSIFIED',1200000,0,0,0)",
            (sr_id,),
        )
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, team_instance_id, finishing_position, outcome, base_time_ms, ingame_time_penalties_ms, postrace_time_penalties_ms, appeal_time_penalties_ms) "
            "VALUES (?,2,200,2,'CLASSIFIED',1210000,0,0,0)",
            (sr_id,),
        )
        await db.commit()

    staged = [
        StagedPenalty(
            driver_user_id=1,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="TIME",
            penalty_seconds=-15,  # -15s on P1 → 1200000 - 15000 = 1185000ms < 1210000ms → P1 stays P1
        )
    ]
    async with get_connection(db_path) as db:
        await apply_penalties_on(db, round_id, division_id, staged, 999, now=_NOW)
        await db.commit()

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, finishing_position FROM race_session_results "
            "WHERE session_result_id = ? ORDER BY finishing_position",
            (sr_id,),
        )
        rows = await cursor.fetchall()

    assert rows[0]["driver_user_id"] == 1
    assert rows[0]["finishing_position"] == 1
    assert rows[1]["driver_user_id"] == 2
    assert rows[1]["finishing_position"] == 2


async def test_apply_negative_penalty_reorders_move_up(tmp_path):
    """P2 driver with -15s total adjusted time moves to P1 if beats P1."""
    from leaguebot.core.db.database import get_connection, run_migrations
    from leaguebot.results.services.penalty_service import apply_penalties_on

    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id, log_channel_id) VALUES (1,10,20,30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) VALUES ('2026-01-01','ACTIVE',1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id) VALUES (?,?,777,888)",
            (season_id, "Main"),
        )
        division_id = cursor.lastrowid
        await seed_team_instances(db, division_id, 100, 200)
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at) VALUES (?,1,'NORMAL','2026-01-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) VALUES (?,?,'FEATURE_RACE','ACTIVE')",
            (round_id, division_id),
        )
        sr_id = cursor.lastrowid
        # P1 = driver 1 (20:00.000 = 1200000ms), P2 = driver 2 (20:10.000 = 1210000ms)
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, team_instance_id, finishing_position, outcome, base_time_ms, ingame_time_penalties_ms, postrace_time_penalties_ms, appeal_time_penalties_ms) "
            "VALUES (?,1,100,1,'CLASSIFIED',1200000,0,0,0)",
            (sr_id,),
        )
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, team_instance_id, finishing_position, outcome, base_time_ms, ingame_time_penalties_ms, postrace_time_penalties_ms, appeal_time_penalties_ms) "
            "VALUES (?,2,200,2,'CLASSIFIED',1210000,0,0,0)",
            (sr_id,),
        )
        await db.commit()

    # Give driver 2 a -20s: 1210000ms - 20000ms = 1190000ms < 1200000ms → driver 2 becomes P1
    staged = [
        StagedPenalty(
            driver_user_id=2,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="TIME",
            penalty_seconds=-20,
        )
    ]
    async with get_connection(db_path) as db:
        await apply_penalties_on(db, round_id, division_id, staged, 999, now=_NOW)
        await db.commit()

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, finishing_position FROM race_session_results "
            "WHERE session_result_id = ? ORDER BY finishing_position",
            (sr_id,),
        )
        rows = await cursor.fetchall()

    assert rows[0]["driver_user_id"] == 2, "Driver 2 should now be P1 after -20s penalty"
    assert rows[1]["driver_user_id"] == 1, "Driver 1 should now be P2"


async def test_tiebreak_identical_times_preserves_earlier_position(tmp_path):
    """Two drivers with identical post-penalty times keep their original order."""
    from leaguebot.core.db.database import get_connection, run_migrations
    from leaguebot.results.services.penalty_service import apply_penalties_on

    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id, log_channel_id) VALUES (1,10,20,30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) VALUES ('2026-01-01','ACTIVE',1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id) VALUES (?,?,777,888)",
            (season_id, "Main"),
        )
        division_id = cursor.lastrowid
        await seed_team_instances(db, division_id, 100, 200)
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at) VALUES (?,1,'NORMAL','2026-01-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) VALUES (?,?,'FEATURE_RACE','ACTIVE')",
            (round_id, division_id),
        )
        sr_id = cursor.lastrowid
        # P1 = driver 1 (20:10.000 = 1210000ms), P2 = driver 2 (20:00.000 = 1200000ms)
        # Give driver 1 a -10s penalty → 1210000 - 10000 = 1200000ms = same as driver 2
        # Tiebreak: original position → driver 1 stays P1
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, team_instance_id, finishing_position, outcome, base_time_ms, ingame_time_penalties_ms, postrace_time_penalties_ms, appeal_time_penalties_ms) "
            "VALUES (?,1,100,1,'CLASSIFIED',1210000,0,0,0)",
            (sr_id,),
        )
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, team_instance_id, finishing_position, outcome, base_time_ms, ingame_time_penalties_ms, postrace_time_penalties_ms, appeal_time_penalties_ms) "
            "VALUES (?,2,200,2,'CLASSIFIED',1200000,0,0,0)",
            (sr_id,),
        )
        await db.commit()

    staged = [
        StagedPenalty(
            driver_user_id=1,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="TIME",
            penalty_seconds=-10,  # P1 (1210000 - 10000 = 1200000ms) ties with P2 (1200000ms)
        )
    ]
    async with get_connection(db_path) as db:
        await apply_penalties_on(db, round_id, division_id, staged, 999, now=_NOW)
        await db.commit()

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, finishing_position FROM race_session_results "
            "WHERE session_result_id = ? ORDER BY finishing_position",
            (sr_id,),
        )
        rows = await cursor.fetchall()

    # Tiebreak: driver 1 had original position 1, driver 2 had position 2
    # So driver 1 should remain P1 even with identical times
    assert rows[0]["driver_user_id"] == 1, "Driver 1 should be P1 (tiebreak by original position)"
    assert rows[1]["driver_user_id"] == 2, "Driver 2 should be P2"


async def test_dsq_fastest_lap_not_redistributed(tmp_path):
    """AC7: DSQ on fastest-lap holder forfeits the bonus; no other driver gains it."""
    from leaguebot.core.db.database import get_connection, run_migrations
    from leaguebot.results.services.penalty_service import apply_penalties_on
    from leaguebot.results.services.standings_service import compute_points_for_session
    from leaguebot.results.models.points_config import PointsConfigEntry, PointsConfigFastestLap
    from leaguebot.results.models.session_result import DriverSessionResult, OutcomeModifier

    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id, log_channel_id) VALUES (1,10,20,30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) VALUES ('2026-01-01','ACTIVE',1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id) VALUES (?,?,777,888)",
            (season_id, "Main"),
        )
        division_id = cursor.lastrowid
        await seed_team_instances(db, division_id, 100, 200)
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at) VALUES (?,1,'NORMAL','2026-01-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) VALUES (?,?,'FEATURE_RACE','ACTIVE')",
            (round_id, division_id),
        )
        sr_id = cursor.lastrowid
        # Driver 1 = P1, has fastest lap
        # Driver 2 = P2, no fastest lap
        await db.execute(
            "INSERT INTO race_session_results "
            "(session_result_id, driver_user_id, team_instance_id, finishing_position, outcome, base_time_ms, ingame_time_penalties_ms, postrace_time_penalties_ms, appeal_time_penalties_ms, fastest_lap, fastest_lap_bonus) "
            "VALUES (?,1,100,1,'CLASSIFIED',1200000,0,0,0,'1:30.000',1)",
            (sr_id,),
        )
        await db.execute(
            "INSERT INTO race_session_results "
            "(session_result_id, driver_user_id, team_instance_id, finishing_position, outcome, base_time_ms, ingame_time_penalties_ms, postrace_time_penalties_ms, appeal_time_penalties_ms, fastest_lap, fastest_lap_bonus) "
            "VALUES (?,2,200,2,'CLASSIFIED',1210000,0,0,0,'1:31.000',0)",
            (sr_id,),
        )
        await db.commit()

    staged = [
        StagedPenalty(
            driver_user_id=1,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="DSQ",
            penalty_seconds=None,
        )
    ]
    async with get_connection(db_path) as db:
        await apply_penalties_on(db, round_id, division_id, staged, 999, now=_NOW)
        await db.commit()

    # Verify: after DSQ, driver 1's fast-lap bonus is 0
    # and driver 2 does NOT gain the bonus (not redistributed)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, outcome, fastest_lap_bonus FROM race_session_results "
            "WHERE session_result_id = ? ORDER BY finishing_position",
            (sr_id,),
        )
        rows = await cursor.fetchall()

    # DSQ driver's outcome should be set
    dsq_row = next(r for r in rows if r["driver_user_id"] == 1)
    assert dsq_row["outcome"] == "DSQ"

    # No OTHER driver should have their fastest_lap_bonus increased (not redistributed).
    # Driver 2 had fastest_lap_bonus=0 before and should still have 0 after.
    for row in rows:
        if row["driver_user_id"] != 1:
            assert (row["fastest_lap_bonus"] or 0) == 0, (
                f"Driver {row['driver_user_id']} should NOT receive the forfeited fastest-lap bonus"
            )


# ---------------------------------------------------------------------------
# No further action alters no classification (#138)
# ---------------------------------------------------------------------------


async def _seed_one_session(tmp_path, session_type: str) -> tuple[str, int, int, int]:
    """A round with one session of two drivers, stored in an order a re-sort would reverse.

    Driver 1 stands P1 on the slower time or lap and driver 2 P2 on the faster one. Nothing
    a league pastes produces that, but it is the one state in which re-sorting a session is
    visible — so it is what shows that no further action does not re-sort at all.
    """
    from leaguebot.core.db.database import get_connection, run_migrations

    db_path = str(tmp_path / "nfa.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id, log_channel_id) VALUES (1,10,20,30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) VALUES ('2026-01-01','ACTIVE',1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id) VALUES (?,?,777,888)",
            (season_id, "Main"),
        )
        division_id = cursor.lastrowid
        await seed_team_instances(db, division_id, 100, 200)
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at) VALUES (?,1,'NORMAL','2026-01-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) VALUES (?,?,?,'ACTIVE')",
            (round_id, division_id, session_type),
        )
        sr_id = cursor.lastrowid
        if session_type.endswith("QUALIFYING"):
            for driver, team, position, lap in ((1, 100, 1, "1:31.000"), (2, 200, 2, "1:30.000")):
                await db.execute(
                    "INSERT INTO qualifying_session_results (session_result_id, driver_user_id, team_instance_id, finishing_position, outcome, best_lap) "
                    "VALUES (?,?,?,?,'CLASSIFIED',?)",
                    (sr_id, driver, team, position, lap),
                )
        else:
            for driver, team, position, base_ms in ((1, 100, 1, 1_210_000), (2, 200, 2, 1_200_000)):
                await db.execute(
                    "INSERT INTO race_session_results (session_result_id, driver_user_id, team_instance_id, finishing_position, outcome, base_time_ms, ingame_time_penalties_ms, postrace_time_penalties_ms, appeal_time_penalties_ms) "
                    "VALUES (?,?,?,?,'CLASSIFIED',?,1000,0,0)",
                    (sr_id, driver, team, position, base_ms),
                )
        await db.commit()
    return db_path, round_id, division_id, sr_id


def _nfa(session_type: SessionType) -> StagedPenalty:
    return StagedPenalty(
        driver_user_id=1,
        session_type=session_type,
        penalty_type="NFA",
        penalty_seconds=None,
        description="Contact at turn one",
        justification="Racing incident",
    )


@pytest.mark.parametrize("phase", ["PENALTY", "APPEAL"])
async def test_no_further_action_leaves_a_race_classification_as_it_stood(tmp_path, phase):
    from leaguebot.core.db.database import get_connection
    from leaguebot.results.services.penalty_service import apply_penalties_on

    db_path, round_id, division_id, sr_id = await _seed_one_session(tmp_path, "FEATURE_RACE")
    columns = (
        "driver_user_id, finishing_position, outcome, base_time_ms, ingame_time_penalties_ms, "
        "postrace_time_penalties_ms, appeal_time_penalties_ms"
    )

    async def _rows():
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                f"SELECT {columns} FROM race_session_results WHERE session_result_id = ? "
                "ORDER BY driver_user_id",
                (sr_id,),
            )
            return [dict(r) for r in await cursor.fetchall()]

    before = await _rows()
    async with get_connection(db_path) as db:
        await apply_penalties_on(
            db, round_id, division_id, [_nfa(SessionType.FEATURE_RACE)], 999, now=_NOW,
            _phase=phase,
        )
        await db.commit()

    assert await _rows() == before


async def test_no_further_action_leaves_a_qualifying_classification_as_it_stood(tmp_path):
    from leaguebot.core.db.database import get_connection
    from leaguebot.results.services.penalty_service import apply_penalties_on

    db_path, round_id, division_id, sr_id = await _seed_one_session(
        tmp_path, "FEATURE_QUALIFYING"
    )

    async def _rows():
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT driver_user_id, finishing_position, outcome FROM qualifying_session_results "
                "WHERE session_result_id = ? ORDER BY driver_user_id",
                (sr_id,),
            )
            return [dict(r) for r in await cursor.fetchall()]

    before = await _rows()
    async with get_connection(db_path) as db:
        await apply_penalties_on(
            db, round_id, division_id, [_nfa(SessionType.FEATURE_QUALIFYING)], 999, now=_NOW,
        )
        await db.commit()

    assert await _rows() == before


async def test_no_further_action_is_recorded_as_a_verdict(tmp_path):
    """It alters nothing, but it is still a decision, and the round's verdicts are read from
    the record — its announcement and an amendment's replay both need it there."""
    from leaguebot.core.db.database import get_connection
    from leaguebot.results.services.penalty_service import apply_penalties_on

    db_path, round_id, division_id, _ = await _seed_one_session(tmp_path, "FEATURE_RACE")

    async with get_connection(db_path) as db:
        inserted = await apply_penalties_on(
            db, round_id, division_id, [_nfa(SessionType.FEATURE_RACE)], 999, now=_NOW,
        )
        await db.commit()

    assert [(r["driver_user_id"], r["penalty_type"], r["time_seconds"]) for r in inserted] == [
        (1, "NFA", None)
    ]
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT penalty_type, time_seconds, description, justification FROM penalty_records"
        )
        rows = [dict(r) for r in await cursor.fetchall()]
    assert rows == [
        {
            "penalty_type": "NFA",
            "time_seconds": None,
            "description": "Contact at turn one",
            "justification": "Racing incident",
        }
    ]


async def test_a_sanction_beside_no_further_action_still_reorders_its_session(tmp_path):
    """The guard is on sessions only no further action touches. A real sanction in the same
    session re-sorts it as it always has."""
    from leaguebot.core.db.database import get_connection
    from leaguebot.results.services.penalty_service import apply_penalties_on

    db_path, round_id, division_id, sr_id = await _seed_one_session(tmp_path, "FEATURE_RACE")
    staged = [
        _nfa(SessionType.FEATURE_RACE),
        StagedPenalty(
            driver_user_id=2,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="TIME",
            penalty_seconds=1,
        ),
    ]

    async with get_connection(db_path) as db:
        await apply_penalties_on(db, round_id, division_id, staged, 999, now=_NOW)
        await db.commit()

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id FROM race_session_results WHERE session_result_id = ? "
            "ORDER BY finishing_position",
            (sr_id,),
        )
        order = [r["driver_user_id"] for r in await cursor.fetchall()]
    # Driver 2, 1,202,000 ms with the second, is still ahead of driver 1's 1,211,000.
    assert order == [2, 1]


# ---------------------------------------------------------------------------
# apply_penalties_on applies and nothing more (#482, F1)
#
# Every caller is an approval or amendment stage that reposts the round and writes the
# action's one line itself, so apply_penalties_on neither reposts nor logs.
# ---------------------------------------------------------------------------


async def test_apply_penalties_on_neither_reposts_nor_logs(tmp_path):
    import inspect
    from unittest.mock import AsyncMock, patch

    from leaguebot.core.db.database import get_connection
    from leaguebot.results.services import results_post_service as rps
    from leaguebot.results.services.penalty_service import apply_penalties_on

    parameters = inspect.signature(apply_penalties_on).parameters
    assert "_skip_post" not in parameters
    assert "bot" not in parameters, "it is handed no bot, so it has no log channel to post to"

    db_path, round_id, division_id, _ = await _seed_one_session(tmp_path, "FEATURE_RACE")
    recompute = AsyncMock()
    repost = AsyncMock(return_value=[])
    staged = [
        StagedPenalty(
            driver_user_id=2,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="TIME",
            penalty_seconds=5,
        )
    ]

    with patch.object(rps, "recompute_standings_from_round", new=recompute), \
            patch.object(rps, "repost_round_results", new=repost):
        async with get_connection(db_path) as db:
            inserted = await apply_penalties_on(db, round_id, division_id, staged, 999, now=_NOW)
            await db.commit()

    assert [r["driver_user_id"] for r in inserted] == [2]
    recompute.assert_not_awaited()
    repost.assert_not_awaited()


# ---------------------------------------------------------------------------
# The writers on a handed connection, each taking the time (#439)
#
# A change's one save holds the penalties, the appeals and the records of where each verdict
# was posted, so none of these commits, and each is stamped with the queue's clock rather than
# reading one of its own.
# ---------------------------------------------------------------------------


async def test_apply_penalties_on_stamps_with_the_handed_time_and_commits_nothing(tmp_path):
    import datetime
    from unittest.mock import AsyncMock, patch

    from leaguebot.core.db.database import get_connection
    from leaguebot.results.services.penalty_service import apply_penalties_on
    from leaguebot.results.services.result_submission_service import _apply_staged_appeals_on
    from leaguebot.results.services.verdict_announcement_service import (
        _mark_banner_over_sanction_on,
        _record_announcement_on,
        _record_banner_on,
    )

    now = datetime.datetime(2026, 10, 5, 12, 0, tzinfo=datetime.timezone.utc)
    db_path, round_id, division_id, sr_id = await _seed_one_session(tmp_path, "FEATURE_RACE")
    staged = [
        StagedPenalty(
            driver_user_id=2,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="TIME",
            penalty_seconds=5,
        )
    ]

    async def _driver_rows(db):
        cursor = await db.execute(
            "SELECT driver_user_id, finishing_position, postrace_time_penalties_ms, "
            "appeal_time_penalties_ms FROM race_session_results ORDER BY driver_user_id"
        )
        return [tuple(row) for row in await cursor.fetchall()]

    async def _counts(db) -> dict[str, int]:
        found = {}
        for table, where in (
            ("penalty_records", ""),
            ("appeal_records", ""),
            ("verdict_banner_messages", ""),
            ("penalty_records", " WHERE announcement_message_id IS NOT NULL"),
        ):
            row = await (await db.execute(f"SELECT COUNT(*) FROM {table}{where}")).fetchone()
            found[table + where] = row[0]
        return found

    async with get_connection(db_path) as db:
        before = await _driver_rows(db)
        empty = await _counts(db)

        inserted = await apply_penalties_on(db, round_id, division_id, staged, 999, now=now)
        await _record_announcement_on(db, "penalty_records", inserted[0]["id"], 8700, 704)
        await _record_banner_on(db, round_id, 704, 8701, now=now)
        await _mark_banner_over_sanction_on(db, 8701)

        applied_at = (await (await db.execute(
            "SELECT applied_at, announcement_message_id FROM penalty_records")).fetchone())
        banner = (await (await db.execute(
            "SELECT posted_at, heads_sanctions FROM verdict_banner_messages")).fetchone())
        assert tuple(applied_at) == (now.isoformat(), "8700")
        assert tuple(banner) == (now.isoformat(), 1)
        await db.rollback()

    async with get_connection(db_path) as db:
        appeals = await _apply_staged_appeals_on(
            db, round_id, division_id, staged, 999, now=now,
        )
        submitted_at = (await (await db.execute(
            "SELECT submitted_at FROM appeal_records WHERE id = ?", (appeals[0]["id"],)
        )).fetchone())[0]
        assert submitted_at == now.isoformat()
        await db.rollback()

    async with get_connection(db_path) as db:
        assert await _counts(db) == empty
        assert await _driver_rows(db) == before

    # The session carries no points configuration: it is not scored, so a scorer that would
    # fail is never reached. Given one, the scorer's failure comes out of the call.
    failing = AsyncMock(side_effect=RuntimeError("the points could not be calculated"))
    target = "leaguebot.results.services.result_submission_service._apply_points_in_tx"
    with patch(target, new=failing):
        async with get_connection(db_path) as db:
            await _apply_staged_appeals_on(db, round_id, division_id, staged, 999, now=now)
            await db.rollback()
        failing.assert_not_awaited()

        async with get_connection(db_path) as db:
            await db.execute(
                "UPDATE session_results SET config_name = 'Standard' WHERE id = ?", (sr_id,)
            )
            await db.commit()
        async with get_connection(db_path) as db:
            with pytest.raises(RuntimeError):
                await _apply_staged_appeals_on(db, round_id, division_id, staged, 999, now=now)
            await db.rollback()
        failing.assert_awaited_once()


# ---------------------------------------------------------------------------
# What is staged travels on the queue as plain data (#439)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("decided", [{}, {"decided_by": "4242", "decided_at": "2026-09-01T18:00:00+00:00"}])
def test_a_staged_penalty_and_a_staged_pardon_round_trip_their_payloads(decided):
    """A change's payload is saved as JSON, so what is staged must come back from it as it went
    in: a decision read back from the round keeps its author and its time, and a pardon its."""
    import json

    from leaguebot.results.services.penalty_wizard import StagedPardon

    staged = StagedPenalty(
        driver_user_id=101,
        session_type=SessionType.FEATURE_RACE,
        penalty_type="TIME",
        penalty_seconds=5,
        description="Corner cutting",
        justification="Turn 4, lap 12",
        **decided,
    )
    assert StagedPenalty.from_payload(json.loads(json.dumps(staged.to_payload()))) == staged

    granted = {"granted_at": decided["decided_at"]} if decided else {}
    pardon = StagedPardon(
        driver_user_id=102, driver_profile_id=32, attendance_id=41, pardon_type="NO_RSVP",
        justification="Told us in advance", grantor_id=77, **granted,
    )
    assert StagedPardon.from_payload(json.loads(json.dumps(pardon.to_payload()))) == pardon
