"""Unit tests for standings_service (T031)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations
from models.points_config import PointsConfigEntry, PointsConfigFastestLap, SessionType
from models.session_result import DriverSessionResult, OutcomeModifier
from services.standings_service import compute_driver_standings, compute_team_standings, compute_points_for_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    driver_user_id: int,
    position: int,
    outcome: OutcomeModifier = OutcomeModifier.CLASSIFIED,
    fastest_lap: str | None = None,
) -> DriverSessionResult:
    return DriverSessionResult(
        id=0,
        session_result_id=1,
        driver_user_id=driver_user_id,
        finishing_position=position,
        team_role_id=999,
        tyre=None,
        best_lap=None,
        gap=None,
        total_time=None,
        fastest_lap=fastest_lap,
        time_penalties=None,
        outcome=outcome,
        points_awarded=0,
        fastest_lap_bonus=0,
        post_steward_total_time=None,
        post_race_time_penalties=None,
        is_superseded=False,
    )


def _make_race_entries(pts: list[int]) -> list[PointsConfigEntry]:
    return [
        PointsConfigEntry(
            id=i,
            config_id=1,
            session_type=SessionType.FEATURE_RACE,
            position=i,
            points=p,
        )
        for i, p in enumerate(pts, start=1)
    ]


# ---------------------------------------------------------------------------
# compute_points_for_session
# ---------------------------------------------------------------------------


def test_classified_gets_position_points():
    rows = [_make_row(1, position=1)]
    entries = _make_race_entries([25, 18, 15])
    result = compute_points_for_session(rows, entries, fl_config=None, session_type=SessionType.FEATURE_RACE)
    assert result[0].points_awarded == 25


def test_dnf_gets_zero_position_points():
    rows = [_make_row(1, position=3, outcome=OutcomeModifier.DNF)]
    entries = _make_race_entries([25, 18, 15])
    result = compute_points_for_session(rows, entries, fl_config=None, session_type=SessionType.FEATURE_RACE)
    assert result[0].points_awarded == 0


def test_dns_gets_nothing():
    rows = [_make_row(1, position=2, outcome=OutcomeModifier.DNS)]
    entries = _make_race_entries([25, 18, 15])
    result = compute_points_for_session(rows, entries, fl_config=None, session_type=SessionType.FEATURE_RACE)
    assert result[0].points_awarded == 0
    assert result[0].fastest_lap_bonus == 0


def test_dsq_gets_nothing():
    rows = [_make_row(1, position=1, outcome=OutcomeModifier.DSQ)]
    entries = _make_race_entries([25, 18, 15])
    result = compute_points_for_session(rows, entries, fl_config=None, session_type=SessionType.FEATURE_RACE)
    assert result[0].points_awarded == 0
    assert result[0].fastest_lap_bonus == 0


def test_fl_bonus_awarded_to_holder():
    rows = [
        _make_row(1, position=1, fastest_lap="1:23.456"),
        _make_row(2, position=2, fastest_lap="1:24.000"),
    ]
    entries = _make_race_entries([25, 18])
    fl_cfg = PointsConfigFastestLap(
        id=1, config_id=1,
        session_type=SessionType.FEATURE_RACE,
        fl_points=1, fl_position_limit=10,
    )
    result = compute_points_for_session(rows, entries, fl_config=fl_cfg, session_type=SessionType.FEATURE_RACE)
    # Driver 1 has the fastest lap (1:23.456 < 1:24.000)
    assert result[0].fastest_lap_bonus == 1
    assert result[1].fastest_lap_bonus == 0


def test_fl_position_limit_cutoff():
    """Driver outside position limit does not receive FL bonus."""
    rows = [
        _make_row(1, position=1, fastest_lap="1:24.000"),
        _make_row(2, position=6, fastest_lap="1:23.456"),  # fastest but outside limit
    ]
    entries = _make_race_entries([25, 18, 15, 12, 10, 8])
    fl_cfg = PointsConfigFastestLap(
        id=1, config_id=1,
        session_type=SessionType.FEATURE_RACE,
        fl_points=1, fl_position_limit=5,  # top-5 only
    )
    result = compute_points_for_session(rows, entries, fl_config=fl_cfg, session_type=SessionType.FEATURE_RACE)
    # Driver 2 is fastest but is at position 6 — outside top-5 limit
    assert result[0].fastest_lap_bonus == 0
    assert result[1].fastest_lap_bonus == 0


def test_dnf_eligible_for_fl():
    """A DNF driver who set the fastest lap should receive the FL bonus."""
    rows = [
        _make_row(1, position=1, fastest_lap="1:24.000"),
        _make_row(2, position=2, outcome=OutcomeModifier.DNF, fastest_lap="1:23.456"),
    ]
    entries = _make_race_entries([25, 18])
    fl_cfg = PointsConfigFastestLap(
        id=1, config_id=1,
        session_type=SessionType.FEATURE_RACE,
        fl_points=1, fl_position_limit=None,
    )
    result = compute_points_for_session(rows, entries, fl_config=fl_cfg, session_type=SessionType.FEATURE_RACE)
    assert result[0].fastest_lap_bonus == 0
    assert result[1].fastest_lap_bonus == 1


# ---------------------------------------------------------------------------
# compute_driver_standings — countback tiebreak
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "standings_test.db")
    await run_migrations(path)
    return path


@pytest.mark.asyncio
async def test_compute_driver_standings_countback(db_path):
    """Two drivers tied on points; one has a Feature Race P1, the other has P2 only.
    Driver with P1 must rank higher."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id) VALUES (?, 'Main', 777, 888)",
            (season_id,),
        )
        div_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at) "
            "VALUES (?, 1, 'NORMAL', '2026-01-01T18:00:00')",
            (div_id,),
        )
        round_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (round_id, div_id),
        )
        sr_id = cursor.lastrowid
        # Driver A: P1 (25 pts), Driver B: P2 (18 pts)
        # Add a second round where B scores 7 pts and A scores 0 → both on 25 pts total
        await db.execute(
            "INSERT INTO driver_session_results "
            "(session_result_id, driver_user_id, finishing_position, team_role_id, outcome, points_awarded, fastest_lap_bonus, is_superseded) "
            "VALUES (?, 111, 1, 999, 'CLASSIFIED', 25, 0, 0)",
            (sr_id,),
        )
        await db.execute(
            "INSERT INTO driver_session_results "
            "(session_result_id, driver_user_id, finishing_position, team_role_id, outcome, points_awarded, fastest_lap_bonus, is_superseded) "
            "VALUES (?, 222, 2, 998, 'CLASSIFIED', 18, 0, 0)",
            (sr_id,),
        )
        # Round 2 — driver B gets 7, driver A gets 0 → both on 25
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at) "
            "VALUES (?, 2, 'NORMAL', '2026-02-01T18:00:00')",
            (div_id,),
        )
        round2_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (round2_id, div_id),
        )
        sr2_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO driver_session_results "
            "(session_result_id, driver_user_id, finishing_position, team_role_id, outcome, points_awarded, fastest_lap_bonus, is_superseded) "
            "VALUES (?, 111, 10, 999, 'CLASSIFIED', 0, 0, 0)",
            (sr2_id,),
        )
        await db.execute(
            "INSERT INTO driver_session_results "
            "(session_result_id, driver_user_id, finishing_position, team_role_id, outcome, points_awarded, fastest_lap_bonus, is_superseded) "
            "VALUES (?, 222, 3, 998, 'CLASSIFIED', 7, 0, 0)",
            (sr2_id,),
        )
        await db.commit()

    snapshots = await compute_driver_standings(db_path, division_id=div_id, up_to_round_id=round2_id)
    # Both have 25 points total; driver A has a P1, driver B has best P2/P3
    uid_to_pos = {s.driver_user_id: s.standing_position for s in snapshots}
    assert uid_to_pos[111] < uid_to_pos[222], (
        "Driver A (P1 winner) should rank above Driver B despite equal total points"
    )


# ---------------------------------------------------------------------------
# Tiebreak tests — global_max_pos fix (C1 regression suite, T004–T008)
# ---------------------------------------------------------------------------

# Shared async helpers for DB setup.

async def _bootstrap(db, server_id: int = 1) -> tuple[int, int]:
    """Insert server_config + season + division. Return (div_id, season_id)."""
    await db.execute(
        "INSERT INTO server_configs "
        "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
        "VALUES (?, 10, 20, 30)",
        (server_id,),
    )
    cur = await db.execute(
        "INSERT INTO seasons (server_id, start_date, status, season_number) "
        "VALUES (?, '2026-01-01', 'ACTIVE', 1)",
        (server_id,),
    )
    season_id = cur.lastrowid
    cur = await db.execute(
        "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id) "
        "VALUES (?, 'Alpha', 777, 888)",
        (season_id,),
    )
    return cur.lastrowid, season_id


async def _round(db, div_id: int, n: int) -> int:
    cur = await db.execute(
        "INSERT INTO rounds (division_id, round_number, format, scheduled_at) "
        "VALUES (?, ?, 'NORMAL', '2026-01-01T18:00:00')",
        (div_id, n),
    )
    return cur.lastrowid


async def _session(db, round_id: int, div_id: int, session_type: str = "FEATURE_RACE") -> int:
    cur = await db.execute(
        "INSERT INTO session_results (round_id, division_id, session_type, status) "
        "VALUES (?, ?, ?, 'ACTIVE')",
        (round_id, div_id, session_type),
    )
    return cur.lastrowid


async def _result(db, sr_id: int, uid: int, pos: int, pts: int, team: int = 999) -> None:
    await db.execute(
        "INSERT INTO driver_session_results "
        "(session_result_id, driver_user_id, finishing_position, team_role_id, "
        "outcome, points_awarded, fastest_lap_bonus, is_superseded) "
        "VALUES (?, ?, ?, ?, 'CLASSIFIED', ?, 0, 0)",
        (sr_id, uid, pos, team, pts),
    )


@pytest.mark.asyncio
async def test_tiebreak_p2_count(db_path):
    """T004: A has 2 Feature Race P2 finishes, B has 1 (equal total pts). A ranks above B."""
    async with get_connection(db_path) as db:
        div_id, _ = await _bootstrap(db)
        r1 = await _round(db, div_id, 1)
        sr1 = await _session(db, r1, div_id)
        await _result(db, sr1, 111, pos=2, pts=14)
        await _result(db, sr1, 222, pos=2, pts=14)
        r2 = await _round(db, div_id, 2)
        sr2 = await _session(db, r2, div_id)
        await _result(db, sr2, 111, pos=2, pts=11)  # A: second P2
        await _result(db, sr2, 222, pos=3, pts=11)  # B: only P3 in round 2
        await db.commit()
    snaps = await compute_driver_standings(db_path, div_id, r2)
    uid_to_pos = {s.driver_user_id: s.standing_position for s in snaps}
    assert uid_to_pos[111] < uid_to_pos[222], (
        "Driver A (2× P2) should rank above Driver B (1× P2) on equal total points"
    )


@pytest.mark.asyncio
async def test_tiebreak_p3_vs_no_p3(db_path):
    """T005: A has 1 Feature Race P3 finish; B has equal pts from Sprint Race only
    (no Feature Race finishes → empty finish_counts). A ranks above B.

    This test exposes the pre-fix C1 defect where B's empty count vector
    compared as less-than A's non-empty vector, incorrectly placing B first.
    """
    async with get_connection(db_path) as db:
        div_id, _ = await _bootstrap(db)
        r1 = await _round(db, div_id, 1)
        sr_fr = await _session(db, r1, div_id, "FEATURE_RACE")
        await _result(db, sr_fr, 111, pos=3, pts=15)  # A: Feature Race P3
        # B has no Feature Race results in round 1
        r2 = await _round(db, div_id, 2)
        sr_sp = await _session(db, r2, div_id, "SPRINT_RACE")
        await _result(db, sr_sp, 222, pos=3, pts=15)  # B: Sprint Race P3 (not in finish_counts)
        await db.commit()
    snaps = await compute_driver_standings(db_path, div_id, r2)
    uid_to_pos = {s.driver_user_id: s.standing_position for s in snaps}
    assert uid_to_pos[111] < uid_to_pos[222], (
        "Driver A (Feature Race P3) should rank above Driver B (no Feature Race finishes)"
    )


@pytest.mark.asyncio
async def test_tiebreak_first_achieved_round(db_path):
    """T006: Both drivers have identical finish counts; A first achieved P2 in Round 1,
    B first achieved P2 in Round 2. A ranks above B."""
    async with get_connection(db_path) as db:
        div_id, _ = await _bootstrap(db)
        r1 = await _round(db, div_id, 1)
        sr1 = await _session(db, r1, div_id)
        await _result(db, sr1, 111, pos=2, pts=18)  # A: P2 in round 1
        await _result(db, sr1, 222, pos=3, pts=15)  # B: P3 in round 1
        r2 = await _round(db, div_id, 2)
        sr2 = await _session(db, r2, div_id)
        await _result(db, sr2, 111, pos=3, pts=15)  # A: P3 in round 2
        await _result(db, sr2, 222, pos=2, pts=18)  # B: P2 in round 2
        await db.commit()
    # Both: 33 pts, 1× P2, 1× P3. A's first P2 is in round 1; B's is in round 2.
    snaps = await compute_driver_standings(db_path, div_id, r2)
    uid_to_pos = {s.driver_user_id: s.standing_position for s in snaps}
    assert uid_to_pos[111] < uid_to_pos[222], (
        "Driver A (P2 first in Round 1) should rank above Driver B (P2 first in Round 2)"
    )


@pytest.mark.asyncio
async def test_tiebreak_teams_same_hierarchy(db_path):
    """T007: Two teams equal on total points; Team A's driver has a Feature Race P1,
    Team B's driver has only P2. Team A ranks above Team B."""
    async with get_connection(db_path) as db:
        div_id, _ = await _bootstrap(db)
        r1 = await _round(db, div_id, 1)
        sr1 = await _session(db, r1, div_id)
        # Same points, different positions — team_role_id differentiates teams
        await _result(db, sr1, 111, pos=1, pts=25, team=101)  # Team A driver: P1
        await _result(db, sr1, 222, pos=2, pts=25, team=102)  # Team B driver: P2 (same pts)
        await db.commit()
    snaps = await compute_team_standings(db_path, div_id, r1)
    tid_to_pos = {s.team_role_id: s.standing_position for s in snaps}
    assert tid_to_pos[101] < tid_to_pos[102], (
        "Team A (Feature Race P1) should rank above Team B (only P2) on equal total points"
    )


@pytest.mark.asyncio
async def test_tiebreak_cross_position_set(db_path):
    """T008: A has 1 P2 finish only (old max_pos=2); B has 1 P3 finish only
    (old max_pos=3). Equal total points. A ranks above B.

    Regression guard: verifies globally-padded vectors produce correct ordering
    when drivers' achieved position sets are disjoint.
    """
    async with get_connection(db_path) as db:
        div_id, _ = await _bootstrap(db)
        r1 = await _round(db, div_id, 1)
        sr1 = await _session(db, r1, div_id)
        await _result(db, sr1, 111, pos=2, pts=20)  # A: P2 only
        await _result(db, sr1, 222, pos=3, pts=20)  # B: P3 only; equal pts
        await db.commit()
    snaps = await compute_driver_standings(db_path, div_id, r1)
    uid_to_pos = {s.driver_user_id: s.standing_position for s in snaps}
    assert uid_to_pos[111] < uid_to_pos[222], (
        "Driver A (P2 finish) should rank above Driver B (P3 finish) on equal points"
    )


# ---------------------------------------------------------------------------
# Zero-point inclusion tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_driver_standings_includes_zero_pt_non_reserve(db_path):
    """Non-reserve drivers with no results appear in standings with 0 points."""
    async with get_connection(db_path) as db:
        div_id, _ = await _bootstrap(db, server_id=10)
        # Round with one result for driver 111
        r1 = await _round(db, div_id, 1)
        sr1 = await _session(db, r1, div_id)
        await _result(db, sr1, 111, pos=1, pts=25)
        # Driver 222 has a seat in a non-reserve team instance but no results
        cur = await db.execute(
            "INSERT INTO driver_profiles (server_id, discord_user_id, current_state) VALUES (10, 222, 'ACTIVE')"
        )
        dp_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) VALUES (?, 'Alpha', 2, 0)",
            (div_id,),
        )
        ti_id = cur.lastrowid
        await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) VALUES (?, 1, ?)",
            (ti_id, dp_id),
        )
        await db.commit()
    snaps = await compute_driver_standings(db_path, div_id, r1)
    uids = {s.driver_user_id for s in snaps}
    assert 222 in uids, "Non-reserve driver with no results must appear in standings"
    zero_snap = next(s for s in snaps if s.driver_user_id == 222)
    assert zero_snap.total_points == 0


@pytest.mark.asyncio
async def test_compute_driver_standings_excludes_zero_pt_reserve(db_path):
    """Reserve drivers with no results do NOT appear in standings."""
    async with get_connection(db_path) as db:
        div_id, _ = await _bootstrap(db, server_id=11)
        r1 = await _round(db, div_id, 1)
        sr1 = await _session(db, r1, div_id)
        await _result(db, sr1, 111, pos=1, pts=25)
        # Driver 333 is in a reserve team instance with no results
        cur = await db.execute(
            "INSERT INTO driver_profiles (server_id, discord_user_id, current_state) VALUES (11, 333, 'ACTIVE')"
        )
        dp_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) VALUES (?, 'Reserve', 2, 1)",
            (div_id,),
        )
        ti_id = cur.lastrowid
        await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) VALUES (?, 1, ?)",
            (ti_id, dp_id),
        )
        await db.commit()
    snaps = await compute_driver_standings(db_path, div_id, r1)
    uids = {s.driver_user_id for s in snaps}
    assert 333 not in uids, "Reserve driver with no results must NOT appear in standings"


@pytest.mark.asyncio
async def test_compute_team_standings_includes_zero_pt_team(db_path):
    """Non-reserve teams with no results appear in team standings with 0 points."""
    async with get_connection(db_path) as db:
        div_id, _ = await _bootstrap(db, server_id=12)
        r1 = await _round(db, div_id, 1)
        sr1 = await _session(db, r1, div_id)
        # Team role 555 scores points; team role 666 has a team_instance but no results
        await _result(db, sr1, 111, pos=1, pts=25, team=555)
        # Register team_role_config for both teams (server_id=12 from _bootstrap)
        await db.execute(
            "INSERT INTO team_role_configs (server_id, team_name, role_id) VALUES (12, 'TeamA', 555)"
        )
        await db.execute(
            "INSERT INTO team_role_configs (server_id, team_name, role_id) VALUES (12, 'TeamB', 666)"
        )
        # Create team instances for both in the division
        await db.execute(
            "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) VALUES (?, 'TeamA', 2, 0)",
            (div_id,),
        )
        await db.execute(
            "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) VALUES (?, 'TeamB', 2, 0)",
            (div_id,),
        )
        await db.commit()
    snaps = await compute_team_standings(db_path, div_id, r1)
    role_ids = {s.team_role_id for s in snaps}
    assert 666 in role_ids, "Zero-point team must appear in team standings"
    zero_snap = next(s for s in snaps if s.team_role_id == 666)
    assert zero_snap.total_points == 0

