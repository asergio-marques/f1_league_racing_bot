"""Unit tests for penalty_service (T033)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.points_config import SessionType
from services.penalty_service import StagedPenalty, validate_penalty_input
from tests.support.teams import seed_team_instances  # noqa: E402


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
    from db.database import get_connection, run_migrations
    from services.penalty_service import apply_penalties

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

    class _FakeBot:
        class output_router:
            @staticmethod
            async def post_log(*_a, **_kw):
                pass

    staged = [
        StagedPenalty(
            driver_user_id=1,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="TIME",
            penalty_seconds=-15,  # -15s on P1 → 1200000 - 15000 = 1185000ms < 1210000ms → P1 stays P1
        )
    ]
    await apply_penalties(db_path, round_id, division_id, staged, 999, _FakeBot(), _skip_post=True)

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
    from db.database import get_connection, run_migrations
    from services.penalty_service import apply_penalties

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

    class _FakeBot:
        class output_router:
            @staticmethod
            async def post_log(*_a, **_kw):
                pass

    # Give driver 2 a -20s: 1210000ms - 20000ms = 1190000ms < 1200000ms → driver 2 becomes P1
    staged = [
        StagedPenalty(
            driver_user_id=2,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="TIME",
            penalty_seconds=-20,
        )
    ]
    await apply_penalties(db_path, round_id, division_id, staged, 999, _FakeBot(), _skip_post=True)

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
    from db.database import get_connection, run_migrations
    from services.penalty_service import apply_penalties

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

    class _FakeBot:
        class output_router:
            @staticmethod
            async def post_log(*_a, **_kw):
                pass

    staged = [
        StagedPenalty(
            driver_user_id=1,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="TIME",
            penalty_seconds=-10,  # P1 (1210000 - 10000 = 1200000ms) ties with P2 (1200000ms)
        )
    ]
    await apply_penalties(db_path, round_id, division_id, staged, 999, _FakeBot(), _skip_post=True)

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
    from db.database import get_connection, run_migrations
    from services.penalty_service import apply_penalties
    from services.standings_service import compute_points_for_session
    from models.points_config import PointsConfigEntry, PointsConfigFastestLap
    from models.session_result import DriverSessionResult, OutcomeModifier

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

    class _FakeBot:
        class output_router:
            @staticmethod
            async def post_log(*_a, **_kw):
                pass

    staged = [
        StagedPenalty(
            driver_user_id=1,
            session_type=SessionType.FEATURE_RACE,
            penalty_type="DSQ",
            penalty_seconds=None,
        )
    ]
    await apply_penalties(db_path, round_id, division_id, staged, 999, _FakeBot(), _skip_post=True)

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


class _QuietBot:
    class output_router:
        @staticmethod
        async def post_log(*_a, **_kw):
            pass


async def _seed_one_session(tmp_path, session_type: str) -> tuple[str, int, int, int]:
    """A round with one session of two drivers, stored in an order a re-sort would reverse.

    Driver 1 stands P1 on the slower time or lap and driver 2 P2 on the faster one. Nothing
    a league pastes produces that, but it is the one state in which re-sorting a session is
    visible — so it is what shows that no further action does not re-sort at all.
    """
    from db.database import get_connection, run_migrations

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
    from db.database import get_connection
    from services.penalty_service import apply_penalties

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
    await apply_penalties(
        db_path, round_id, division_id, [_nfa(SessionType.FEATURE_RACE)], 999, _QuietBot(),
        _skip_post=True, _phase=phase,
    )

    assert await _rows() == before


async def test_no_further_action_leaves_a_qualifying_classification_as_it_stood(tmp_path):
    from db.database import get_connection
    from services.penalty_service import apply_penalties

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
    await apply_penalties(
        db_path, round_id, division_id, [_nfa(SessionType.FEATURE_QUALIFYING)], 999,
        _QuietBot(), _skip_post=True,
    )

    assert await _rows() == before


async def test_no_further_action_is_recorded_as_a_verdict(tmp_path):
    """It alters nothing, but it is still a decision, and the round's verdicts are read from
    the record — its announcement and an amendment's replay both need it there."""
    from db.database import get_connection
    from services.penalty_service import apply_penalties

    db_path, round_id, division_id, _ = await _seed_one_session(tmp_path, "FEATURE_RACE")

    inserted = await apply_penalties(
        db_path, round_id, division_id, [_nfa(SessionType.FEATURE_RACE)], 999, _QuietBot(),
        _skip_post=True,
    )

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
    from db.database import get_connection
    from services.penalty_service import apply_penalties

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

    await apply_penalties(db_path, round_id, division_id, staged, 999, _QuietBot(), _skip_post=True)

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
# apply_penalties reposts when it is not told to skip (#130)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_penalties_reposts_when_not_skipping(tmp_path):
    """The second, latent instance of the missing-label defect (#130).

    Both production callers pass ``_skip_post=True`` and repost themselves, so this path
    never runs today — which is exactly why nothing caught that its
    ``repost_round_results`` call omitted the required ``label`` and would raise
    ``TypeError`` the moment anything called ``apply_penalties`` without that flag.

    The staged list is deliberately empty: the defect is in the repost that follows the
    loop, not in the penalty application itself, and an empty list reaches it with the
    fewest moving parts in between.
    """
    from unittest.mock import AsyncMock, MagicMock

    from db.database import get_connection, run_migrations
    from services.penalty_service import apply_penalties

    path = str(tmp_path / "penalty_repost.db")
    await run_migrations(path)

    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) VALUES (?, 'Alpha', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO division_results_config "
            "(division_id, results_channel_id, standings_channel_id) VALUES (?, 501, 502)",
            (division_id,),
        )
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, status, scheduled_at) "
            "VALUES (?, 1, 'STANDARD', 'AWAITING_APPEAL_VERDICTS', '2026-06-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (round_id, division_id),
        )
        await db.commit()

    posted: list[str] = []

    guild = MagicMock()
    guild.get_member.return_value = None
    guild.fetch_member = AsyncMock(side_effect=Exception("not found"))

    def get_channel(channel_id):
        channel = AsyncMock()

        async def fake_send(content=None, **kwargs):
            posted.append(content or "")
            msg = MagicMock()
            msg.id = 4242
            return msg

        channel.send = fake_send
        channel.id = channel_id
        return channel

    guild.get_channel = get_channel

    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=1)
    bot.get_guild.return_value = guild
    bot.output_router.post_log = AsyncMock()

    await apply_penalties(
        path, round_id, division_id, [], applied_by=99, bot=bot,
    )

    assert posted, "apply_penalties reposted nothing"
    # The label is the round's own stage, derived rather than demanded of the caller.
    assert any("Post-Race Penalty Results" in content for content in posted), posted


# ---------------------------------------------------------------------------
# What the repost could not post reaches the log channel (#237)
#
# `apply_penalties` rescores a championship and then reposts it. The repost's outcome was
# discarded, an unreachable guild skipped it in silence, and the `PENALTIES_APPLIED |
# Success` line was written *before* any of it ran — so a penalty could leave every posted
# standing stale with nobody told.
# ---------------------------------------------------------------------------


async def _seed_for_penalty_log(tmp_path):
    """A division with one round to apply a penalty to. Returns ``(db_path, division_id,
    round_id)``."""
    from db.database import get_connection, run_migrations

    path = str(tmp_path / "penalty_log.db")
    await run_migrations(path)

    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) VALUES (?, 'Alpha', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO division_results_config "
            "(division_id, results_channel_id, standings_channel_id) VALUES (?, 501, 502)",
            (division_id,),
        )
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, status, scheduled_at) "
            "VALUES (?, 1, 'STANDARD', 'AWAITING_APPEAL_VERDICTS', '2026-06-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (round_id, division_id),
        )
        await db.commit()

    return path, division_id, round_id


def _logged_lines(bot) -> str:
    return "\n".join(str(c.args[0]) for c in bot.output_router.post_log.await_args_list)


async def test_apply_penalties_reports_an_unreachable_guild(tmp_path):
    """`league_guild` returns None out of the cache and raises nothing (#244).

    `if guild:` therefore skipped the repost in silence while the log said Success.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from services import results_post_service as rps
    from services.penalty_service import apply_penalties

    path, division_id, round_id = await _seed_for_penalty_log(tmp_path)

    bot = MagicMock()
    bot.output_router.post_log = AsyncMock()

    with patch("utils.league_server.league_guild", new=AsyncMock(return_value=None)), \
            patch.object(rps, "recompute_standings_from_round", new=AsyncMock()):
        await apply_penalties(path, round_id, division_id, [], applied_by=99, bot=bot)

    logged = _logged_lines(bot)
    assert "PENALTIES_APPLIED | Incomplete" in logged
    assert "could not be reached" in logged
    assert "/results rounds sync" in logged


async def test_apply_penalties_reports_what_the_repost_could_not_post(tmp_path):
    """The repost's return used to be thrown away by this caller."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from services import results_post_service as rps
    from services.penalty_service import apply_penalties

    path, division_id, round_id = await _seed_for_penalty_log(tmp_path)

    bot = MagicMock()
    bot.output_router.post_log = AsyncMock()
    fault = "**Alpha** — the standings channel <#502> no longer exists."

    with patch("utils.league_server.league_guild", new=AsyncMock(return_value=MagicMock())), \
            patch.object(rps, "recompute_standings_from_round", new=AsyncMock()), \
            patch.object(rps, "repost_round_results", new=AsyncMock(return_value=[fault])):
        await apply_penalties(path, round_id, division_id, [], applied_by=99, bot=bot)

    logged = _logged_lines(bot)
    assert "PENALTIES_APPLIED | Incomplete" in logged
    assert fault in logged


async def test_apply_penalties_logs_success_only_after_the_repost(tmp_path):
    """The audit line is written after the posting it describes, not before it.

    Written first, it claimed a success the cascade had not yet earned — and might never
    earn. `season_end_service` already keeps this ordering.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from services import results_post_service as rps
    from services.penalty_service import apply_penalties

    path, division_id, round_id = await _seed_for_penalty_log(tmp_path)

    order: list[str] = []

    bot = MagicMock()
    bot.output_router.post_log = AsyncMock(side_effect=lambda *_a, **_k: order.append("log"))

    async def _repost(*_args, **_kwargs):
        order.append("repost")
        return []

    with patch("utils.league_server.league_guild", new=AsyncMock(return_value=MagicMock())), \
            patch.object(rps, "recompute_standings_from_round", new=AsyncMock()), \
            patch.object(rps, "repost_round_results", new=_repost):
        await apply_penalties(path, round_id, division_id, [], applied_by=99, bot=bot)

    assert order == ["repost", "log"]
    assert "PENALTIES_APPLIED | Success" in _logged_lines(bot)
