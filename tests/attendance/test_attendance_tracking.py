"""Unit tests for attendance tracking pipeline — 033-attendance-tracking.

Covers FR-001–FR-031 as enumerated in research.md §8.
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.attendance.services.attendance_service import (
    record_attendance_from_results,
    record_attendance_from_results_full_recompute,
    distribute_attendance_points,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_db(tmp_path, name: str = "test.db") -> str:
    """A migrated database holding season 1, active, and its division 10.

    The penalties are 2 for failing to check in, 1 for an absence and 3 for a no-show, set
    here rather than left to the schema's defaults of 1/1/1: every expected value in this
    file is written against them, and three different numbers are what tell the rules apart.
    """
    db_file = str(tmp_path / name)
    await run_migrations(db_file)
    async with get_connection(db_file) as db:
        await db.execute(
            "INSERT INTO attendance_config "
            "(id, module_enabled, no_rsvp_penalty, absent_penalty, no_show_penalty) "
            "VALUES (1, 1, 2, 1, 3)"
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status) VALUES (1, '2026-01-01', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id) "
            "VALUES (10, 1, 'Pro', 3010)"
        )
        await db.commit()
    return db_file


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# DB fixtures helpers
# ---------------------------------------------------------------------------

async def _setup_division(db, *, division_id=10, full_team_id=1, reserve_team_id=2):
    """Create one full-time team and one reserve team for division_id=10."""
    await db.execute(
        "INSERT INTO team_instances (id, division_id, is_reserve, name, full_name) VALUES (?, ?, 0, 'Full Team', 'Full Team')",
        (full_team_id, division_id),
    )
    await db.execute(
        "INSERT INTO team_instances (id, division_id, is_reserve, name, full_name) VALUES (?, ?, 1, 'Reserve', 'Reserve')",
        (reserve_team_id, division_id),
    )


async def _add_round(db, *, round_id, round_number, division_id=10, status="NOT_RUN"):
    """Insert a round of the division, a week apart by number."""
    await db.execute(
        "INSERT INTO rounds (id, division_id, round_number, format, scheduled_at, status) "
        "VALUES (?, ?, ?, 'NORMAL', ?, ?)",
        (round_id, division_id, round_number, f"2026-06-{round_number * 7:02d}T18:00:00", status),
    )


async def _add_driver(db, *, profile_id, user_id, team_instance_id, division_id=10, season_id=1):
    """Insert a driver profile, seat, and assignment.

    A plain INSERT: an `OR IGNORE` here once swallowed a NOT NULL failure and discarded
    every driver this file seeded, and the tests still passed on the empty tables (#244).
    """
    await db.execute(
        "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
        "VALUES (?, ?, 'ASSIGNED')",
        (profile_id, str(user_id)),
    )
    seat_id = profile_id * 100
    await db.execute(
        "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
        "VALUES (?, ?, ?, ?)",
        (seat_id, team_instance_id, profile_id, profile_id),
    )
    await db.execute(
        "INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id, team_seat_id) VALUES (?, ?, ?, ?)",
        (profile_id, season_id, division_id, seat_id),
    )


async def _add_dra(db, *, round_id, division_id=10, driver_profile_id, rsvp_status="NO_RSVP"):
    """Insert a driver_round_attendance row."""
    await db.execute(
        "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, rsvp_status) VALUES (?, ?, ?, ?)",
        (round_id, division_id, driver_profile_id, rsvp_status),
    )


async def _add_session_result(db, *, round_id, driver_profile_id, user_id):
    """Classify the driver in the round's race, which is what attending it means.

    A round has one race session, so a second driver joins the first one's classification
    rather than opening a session of their own.
    """
    await db.execute(
        "INSERT OR IGNORE INTO session_results (round_id, division_id, session_type, status) "
        "SELECT id, division_id, 'FEATURE_RACE', 'ACTIVE' FROM rounds WHERE id = ?",
        (round_id,),
    )
    session = await (await db.execute(
        "SELECT id FROM session_results WHERE round_id = ? AND session_type = 'FEATURE_RACE'",
        (round_id,),
    )).fetchone()
    team = await (await db.execute(
        "SELECT team_instance_id FROM team_seats WHERE driver_profile_id = ?",
        (driver_profile_id,),
    )).fetchone()
    await db.execute(
        "INSERT INTO race_session_results "
        "(session_result_id, driver_profile_id, driver_user_id, team_instance_id, "
        "finishing_position) "
        "SELECT ?, ?, ?, ?, COUNT(*) + 1 FROM race_session_results WHERE session_result_id = ?",
        (session[0], driver_profile_id, user_id, team[0], session[0]),
    )


# ---------------------------------------------------------------------------
# 1. test_record_attendance_sets_attended_flags  (FR-001)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_attendance_sets_attended_flags(tmp_path):
    """FR-001: attended=1 for drivers with results, attended=0 for absent drivers."""
    db_file = await _make_db(tmp_path)
    async with get_connection(db_file) as db:
        await _setup_division(db)

        # Driver 1 attends; Driver 2 absent
        await _add_driver(db, profile_id=1, user_id=1001, team_instance_id=1)
        await _add_driver(db, profile_id=2, user_id=1002, team_instance_id=1)
        await _add_round(db, round_id=1, round_number=1)
        await _add_dra(db, round_id=1, driver_profile_id=1)
        await _add_dra(db, round_id=1, driver_profile_id=2)
        await _add_session_result(db, round_id=1, driver_profile_id=1, user_id=1001)
        await db.commit()

    await record_attendance_from_results(db_file, round_id=1, division_id=10)

    async with get_connection(db_file) as db:
        cur = await db.execute(
            "SELECT driver_profile_id, attended FROM driver_round_attendance WHERE round_id = 1 ORDER BY driver_profile_id"
        )
        rows = await cur.fetchall()

    assert rows[0]["attended"] == 1  # Driver 1 attended
    assert rows[1]["attended"] == 0  # Driver 2 absent


# ---------------------------------------------------------------------------
# 2. test_record_attendance_excludes_reserve_team_drivers  (FR-002)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_attendance_excludes_reserve_team_drivers(tmp_path):
    """FR-002: Reserve-team driver's DRA row is not updated."""
    db_file = await _make_db(tmp_path)
    async with get_connection(db_file) as db:
        await _setup_division(db)

        # Driver 3 is in the Reserve team
        await _add_driver(db, profile_id=3, user_id=1003, team_instance_id=2)  # Reserve
        await _add_round(db, round_id=1, round_number=1)
        await _add_dra(db, round_id=1, driver_profile_id=3)
        await _add_session_result(db, round_id=1, driver_profile_id=3, user_id=1003)
        await db.commit()

    await record_attendance_from_results(db_file, round_id=1, division_id=10)

    async with get_connection(db_file) as db:
        cur = await db.execute(
            "SELECT attended FROM driver_round_attendance WHERE driver_profile_id = 3"
        )
        row = await cur.fetchone()

    # attended should still be NULL — the reserve driver was skipped
    assert row["attended"] is None


# ---------------------------------------------------------------------------
# 3. test_record_attendance_upgrades_absent_to_present  (FR-003)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_attendance_upgrades_absent_to_present(tmp_path):
    """FR-003: A second call can flip 0→1 but never 1→0."""
    db_file = await _make_db(tmp_path)
    async with get_connection(db_file) as db:
        await _setup_division(db)
        await _add_driver(db, profile_id=1, user_id=1001, team_instance_id=1)
        await _add_driver(db, profile_id=2, user_id=1002, team_instance_id=1)
        await _add_round(db, round_id=1, round_number=1)
        await _add_dra(db, round_id=1, driver_profile_id=1)
        await _add_dra(db, round_id=1, driver_profile_id=2)
        # First call: driver 1 absent, driver 2 attended
        await _add_session_result(db, round_id=1, driver_profile_id=2, user_id=1002)
        await db.commit()

    await record_attendance_from_results(db_file, round_id=1, division_id=10)

    # Second call: driver 1 now has a result too (late session)
    async with get_connection(db_file) as db:
        await _add_session_result(db, round_id=1, driver_profile_id=1, user_id=1001)
        await db.commit()

    await record_attendance_from_results(db_file, round_id=1, division_id=10)

    async with get_connection(db_file) as db:
        cur = await db.execute(
            "SELECT driver_profile_id, attended FROM driver_round_attendance ORDER BY driver_profile_id"
        )
        rows = await cur.fetchall()

    assert rows[0]["attended"] == 1  # Driver 1 upgraded 0→1
    assert rows[1]["attended"] == 1  # Driver 2 remains 1


# ---------------------------------------------------------------------------
# 4. test_record_attendance_full_recompute_can_flip_to_absent  (FR-028)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_attendance_full_recompute_can_flip_to_absent(tmp_path):
    """Amendment recalculation may flip attended 1→0 (no upgrade-only constraint)."""
    db_file = await _make_db(tmp_path)
    async with get_connection(db_file) as db:
        await _setup_division(db)
        await _add_driver(db, profile_id=1, user_id=1001, team_instance_id=1)
        await _add_round(db, round_id=1, round_number=1)
        await _add_dra(db, round_id=1, driver_profile_id=1)
        # Initially attended
        await _add_session_result(db, round_id=1, driver_profile_id=1, user_id=1001)
        await db.commit()

    await record_attendance_from_results(db_file, round_id=1, division_id=10)

    # Remove result rows to simulate amendment correcting a wrong entry
    async with get_connection(db_file) as db:
        await db.execute("DELETE FROM race_session_results")
        await db.execute("DELETE FROM session_results")
        await db.commit()

    await record_attendance_from_results_full_recompute(db_file, round_id=1, division_id=10)

    async with get_connection(db_file) as db:
        cur = await db.execute("SELECT attended FROM driver_round_attendance WHERE driver_profile_id = 1")
        row = await cur.fetchone()

    assert row["attended"] == 0  # flipped 1→0 during amendment


# ---------------------------------------------------------------------------
# 6. Attendance point rules — one test per spec case (US3)
#
# Penalty config used throughout: no_rsvp=2  absent=1  no_show=3
#
# Spec:
#   Case A — NO_RSVP, attended             → no_rsvp_penalty (2)
#   Case B — NO_RSVP, did not attend       → no_rsvp_penalty + absent_penalty (2+1=3)
#   Case C — Any RSVP'd, attended          → 0
#   Case D — ACCEPTED, did not attend      → no_show_penalty (3)
#   Case E — TENTATIVE/DECLINED, absent    → absent_penalty (1)
# ---------------------------------------------------------------------------

async def _make_single_driver_db(tmp_path, *, rsvp_status: str, attended: int) -> str:
    """Return a DB path seeded with one full-time driver for round 1, division 10."""
    db_file = await _make_db(tmp_path, f"test_{rsvp_status}_{attended}.db")
    async with get_connection(db_file) as db:
        await _setup_division(db)
        await _add_round(db, round_id=1, round_number=1, status="AWAITING_APPEAL_VERDICTS")
        await _add_driver(db, profile_id=1, user_id=1001, team_instance_id=1)
        await db.execute(
            "INSERT INTO driver_round_attendance "
            "(round_id, division_id, driver_profile_id, rsvp_status, attended) "
            "VALUES (1, 10, 1, ?, ?)",
            (rsvp_status, attended),
        )
        await db.commit()
    return db_file


async def _points(db_file: str) -> int:
    async with get_connection(db_file) as db:
        cur = await db.execute(
            "SELECT points_awarded FROM driver_round_attendance WHERE driver_profile_id = 1"
        )
        row = await cur.fetchone()
    assert row is not None, "DRA row missing after distribution"
    return row["points_awarded"]


# Case A — Failure to check-in, attended → no_rsvp_penalty (2)

@pytest.mark.asyncio
async def test_points_case_a_no_rsvp_attended(tmp_path):
    """Case A: NO_RSVP + attended = no_rsvp_penalty (2)."""
    db = await _make_single_driver_db(tmp_path, rsvp_status="NO_RSVP", attended=1)
    await distribute_attendance_points(db, round_id=1, division_id=10)
    assert await _points(db) == 2


# Case B — NO_RSVP, did not attend → no_rsvp_penalty + absent_penalty (2+1=3)

@pytest.mark.asyncio
async def test_points_case_b_no_rsvp_absent(tmp_path):
    """Case B: NO_RSVP + absent = no_rsvp_penalty + absent_penalty (2+1=3)."""
    db = await _make_single_driver_db(tmp_path, rsvp_status="NO_RSVP", attended=0)
    await distribute_attendance_points(db, round_id=1, division_id=10)
    assert await _points(db) == 3


# Case C — Checked-in, attended → 0 (all three checked-in statuses)

@pytest.mark.asyncio
async def test_points_case_c_accepted_attended(tmp_path):
    """Case C: ACCEPTED + attended = 0."""
    db = await _make_single_driver_db(tmp_path, rsvp_status="ACCEPTED", attended=1)
    await distribute_attendance_points(db, round_id=1, division_id=10)
    assert await _points(db) == 0


@pytest.mark.asyncio
async def test_points_case_c_tentative_attended(tmp_path):
    """Case C: TENTATIVE + attended = 0."""
    db = await _make_single_driver_db(tmp_path, rsvp_status="TENTATIVE", attended=1)
    await distribute_attendance_points(db, round_id=1, division_id=10)
    assert await _points(db) == 0


@pytest.mark.asyncio
async def test_points_case_c_declined_attended(tmp_path):
    """Case C: DECLINED + attended = 0."""
    db = await _make_single_driver_db(tmp_path, rsvp_status="DECLINED", attended=1)
    await distribute_attendance_points(db, round_id=1, division_id=10)
    assert await _points(db) == 0


# Case D — Checked-in, did not attend → no_show_penalty (3)

@pytest.mark.asyncio
async def test_points_case_d_accepted_absent(tmp_path):
    """Case D: ACCEPTED + absent = no_show_penalty (3)."""
    db = await _make_single_driver_db(tmp_path, rsvp_status="ACCEPTED", attended=0)
    await distribute_attendance_points(db, round_id=1, division_id=10)
    assert await _points(db) == 3


@pytest.mark.asyncio
async def test_points_case_d_tentative_absent(tmp_path):
    """Case E: TENTATIVE + absent = absent_penalty (1)."""
    db = await _make_single_driver_db(tmp_path, rsvp_status="TENTATIVE", attended=0)
    await distribute_attendance_points(db, round_id=1, division_id=10)
    assert await _points(db) == 1


@pytest.mark.asyncio
async def test_points_case_d_declined_absent(tmp_path):
    """Case E: DECLINED + absent = absent_penalty (1)."""
    db = await _make_single_driver_db(tmp_path, rsvp_status="DECLINED", attended=0)
    await distribute_attendance_points(db, round_id=1, division_id=10)
    assert await _points(db) == 1


# ---------------------------------------------------------------------------
# 6b. test_point_distribution_all_scenarios  (US3 rules table — combined)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_point_distribution_all_scenarios(tmp_path):
    """US3 rules table: verify points_awarded for all RSVP × attendance combinations."""
    db_file = await _make_db(tmp_path)
    async with get_connection(db_file) as db:
        # Penalty config: no_rsvp=2, absent=1, no_show=3
        await _setup_division(db)
        await _add_round(db, round_id=1, round_number=1, status="AWAITING_APPEAL_VERDICTS")

        scenarios = [
            # (profile_id, rsvp_status, attended, expected_points)
            (1, "NO_RSVP",   1, 2),      # NO_RSVP, attended: no_rsvp only
            (2, "NO_RSVP",   0, 3),      # NO_RSVP, absent: no_rsvp + absent (2+1)
            (3, "ACCEPTED",  1, 0),      # ACCEPTED, attended: no penalty
            (4, "ACCEPTED",  0, 3),      # ACCEPTED, absent: no_show (3)
            (5, "TENTATIVE", 0, 1),      # TENTATIVE, absent: absent_penalty (1)
            (6, "DECLINED",  0, 1),      # DECLINED, absent: absent_penalty (1)
        ]

        for profile_id, rsvp, att, _ in scenarios:
            await _add_driver(db, profile_id=profile_id, user_id=1000 + profile_id, team_instance_id=1)
            await db.execute(
                "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, rsvp_status, attended) VALUES (1, 10, ?, ?, ?)",
                (profile_id, rsvp, att),
            )
        await db.commit()

    await distribute_attendance_points(db_file, round_id=1, division_id=10)

    expected = {1: 2, 2: 3, 3: 0, 4: 3, 5: 1, 6: 1}
    async with get_connection(db_file) as db:
        cur = await db.execute("SELECT driver_profile_id, points_awarded FROM driver_round_attendance ORDER BY driver_profile_id")
        rows = await cur.fetchall()

    for row in rows:
        assert row["points_awarded"] == expected[row["driver_profile_id"]], (
            f"profile {row['driver_profile_id']}: expected {expected[row['driver_profile_id']]}, got {row['points_awarded']}"
        )


# ---------------------------------------------------------------------------
# 7. test_point_distribution_with_pardons  (FR-013, FR-015)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_point_distribution_with_pardons(tmp_path):
    """FR-013/FR-015: pardons waive only their matching component."""
    db_file = await _make_db(tmp_path)
    async with get_connection(db_file) as db:
        await _setup_division(db)
        await _add_round(db, round_id=1, round_number=1, status="AWAITING_APPEAL_VERDICTS")

        # Driver: NO_RSVP + absent → base = 2+1 = 3; with NO_RSVP pardon → net = 1
        await _add_driver(db, profile_id=1, user_id=1001, team_instance_id=1)
        await db.execute(
            "INSERT INTO driver_round_attendance (id, round_id, division_id, driver_profile_id, rsvp_status, attended) VALUES (10, 1, 10, 1, 'NO_RSVP', 0)"
        )
        # Stage NO_RSVP pardon only
        await db.execute(
            "INSERT INTO attendance_pardons (attendance_id, pardon_type, justification, granted_by, granted_at) VALUES (10, 'NO_RSVP', 'test', 999, ?)",
            (_now_iso(),),
        )
        await db.commit()

    await distribute_attendance_points(db_file, round_id=1, division_id=10)

    async with get_connection(db_file) as db:
        cur = await db.execute("SELECT points_awarded FROM driver_round_attendance WHERE driver_profile_id = 1")
        row = await cur.fetchone()

    # no_rsvp_penalty (2) waived; no_no_show_penalty (1) still applied
    assert row["points_awarded"] == 1


# ---------------------------------------------------------------------------
# 8. test_total_points_after_accumulates_across_rounds  (FR-014)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_total_points_after_accumulates_across_rounds(tmp_path):
    """FR-014: total_points_after is cumulative across finalized rounds."""
    db_file = await _make_db(tmp_path)
    async with get_connection(db_file) as db:
        await _setup_division(db)
        await _add_driver(db, profile_id=1, user_id=1001, team_instance_id=1)

        # Round 1 — already finalized — driver earned 2 points
        await _add_round(db, round_id=1, round_number=1, status="AWAITING_APPEAL_VERDICTS")
        await db.execute(
            "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, rsvp_status, attended, points_awarded, total_points_after) VALUES (1, 10, 1, 'NO_RSVP', 1, 2, 2)"
        )

        # Round 2 — being finalized now
        await _add_round(db, round_id=2, round_number=2, status="AWAITING_APPEAL_VERDICTS")
        await db.execute(
            "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, rsvp_status, attended) VALUES (2, 10, 1, 'NO_RSVP', 1)"
        )
        await db.commit()

    await distribute_attendance_points(db_file, round_id=2, division_id=10)

    async with get_connection(db_file) as db:
        cur = await db.execute(
            "SELECT points_awarded, total_points_after FROM driver_round_attendance WHERE round_id = 2"
        )
        row = await cur.fetchone()

    assert row["points_awarded"] == 2       # no_rsvp_penalty from round 2
    assert row["total_points_after"] == 4   # 2 (round 1) + 2 (round 2)


# ---------------------------------------------------------------------------
# 11. Allocated-reserve no-show rules
#
# A reserve driver distributed into a full-time seat (assigned_team_id IS
# NOT NULL) who RSVPs ACCEPTED but does not appear in results receives
# no_show_penalty (3).  Non-allocated reserves and non-ACCEPTED statuses
# are unaffected.
# ---------------------------------------------------------------------------

async def _make_reserve_driver_db(
    tmp_path,
    *,
    attended: int,
    assigned_team_id: int | None,
    rsvp_status: str = "ACCEPTED",
    with_no_show_pardon: bool = False,
) -> str:
    db_file = await _make_db(tmp_path, f"reserve_{rsvp_status}_{attended}_{assigned_team_id}.db")
    async with get_connection(db_file) as db:
        await _setup_division(db)
        await _add_round(db, round_id=1, round_number=1, status="AWAITING_APPEAL_VERDICTS")
        # Driver seated in the Reserve team (is_reserve=1, team_instance_id=2)
        await _add_driver(db, profile_id=1, user_id=1001, team_instance_id=2)
        await db.execute(
            "INSERT INTO driver_round_attendance "
            "(id, round_id, division_id, driver_profile_id, rsvp_status, attended, assigned_team_id) "
            "VALUES (10, 1, 10, 1, ?, ?, ?)",
            (rsvp_status, attended, assigned_team_id),
        )
        if with_no_show_pardon:
            await db.execute(
                "INSERT INTO attendance_pardons "
                "(attendance_id, pardon_type, justification, granted_by, granted_at) "
                "VALUES (10, 'NO_SHOW', 'test', 999, ?)",
                (_now_iso(),),
            )
        await db.commit()
    return db_file


@pytest.mark.asyncio
async def test_allocated_reserve_accepted_absent_gets_no_show_penalty(tmp_path):
    """Allocated reserve + ACCEPTED + absent = no_show_penalty (3)."""
    db = await _make_reserve_driver_db(tmp_path, attended=0, assigned_team_id=1)
    await distribute_attendance_points(db, round_id=1, division_id=10)
    assert await _points(db) == 3


@pytest.mark.asyncio
async def test_allocated_reserve_accepted_attended_gets_zero(tmp_path):
    """Allocated reserve + ACCEPTED + attended = 0."""
    db = await _make_reserve_driver_db(tmp_path, attended=1, assigned_team_id=1)
    await distribute_attendance_points(db, round_id=1, division_id=10)
    assert await _points(db) == 0


@pytest.mark.asyncio
async def test_non_allocated_reserve_absent_unaffected(tmp_path):
    """Non-allocated reserve (assigned_team_id NULL) stays excluded from scoring."""
    db = await _make_reserve_driver_db(tmp_path, attended=0, assigned_team_id=None)
    await distribute_attendance_points(db, round_id=1, division_id=10)
    # points_awarded must remain NULL — the driver was not processed
    async with aiosqlite.connect(db) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT points_awarded FROM driver_round_attendance WHERE driver_profile_id = 1"
        )
        row = await cur.fetchone()
    assert row["points_awarded"] is None


@pytest.mark.asyncio
async def test_allocated_reserve_no_show_pardon_waives_penalty(tmp_path):
    """Allocated reserve + ACCEPTED + absent + NO_SHOW pardon = 0."""
    db = await _make_reserve_driver_db(
        tmp_path, attended=0, assigned_team_id=1, with_no_show_pardon=True
    )
    await distribute_attendance_points(db, round_id=1, division_id=10)
    assert await _points(db) == 0


# ---------------------------------------------------------------------------
# 15. test_amendment_recalculation_preserves_pardons  (FR-029)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_amendment_recalculation_preserves_pardons(tmp_path):
    """FR-029: existing attendance_pardons rows are not deleted during recalculation."""
    db_file = await _make_db(tmp_path)
    async with get_connection(db_file) as db:
        await _setup_division(db)
        await _add_driver(db, profile_id=1, user_id=1001, team_instance_id=1)
        await _add_round(db, round_id=1, round_number=1, status="AWAITING_APPEAL_VERDICTS")
        # DRA row with id=10 — driver was absent with NO_RSVP
        await db.execute(
            "INSERT INTO driver_round_attendance (id, round_id, division_id, driver_profile_id, rsvp_status, attended) VALUES (10, 1, 10, 1, 'NO_RSVP', 0)"
        )
        # Pre-existing pardon from before amendment
        await db.execute(
            "INSERT INTO attendance_pardons (attendance_id, pardon_type, justification, granted_by, granted_at) VALUES (10, 'NO_RSVP', 'valid reason', 999, ?)",
            (_now_iso(),),
        )
        await db.commit()

    # Simulate amendment: driver now appears in results
    async with get_connection(db_file) as db:
        await _add_session_result(db, round_id=1, driver_profile_id=1, user_id=1001)
        await db.commit()

    await record_attendance_from_results_full_recompute(db_file, round_id=1, division_id=10)
    await distribute_attendance_points(db_file, round_id=1, division_id=10)

    async with get_connection(db_file) as db:
        # Pardon must still exist
        cur = await db.execute("SELECT COUNT(*) AS cnt FROM attendance_pardons WHERE attendance_id = 10")
        row = await cur.fetchone()
        assert row["cnt"] == 1, "pardon was deleted during recalculation"

        # Points should reflect attended=1 + NO_RSVP pardon waiving no_rsvp_penalty
        # attended=1, rsvp=NO_RSVP → base=2; NO_RSVP pardon waives 2 → net=0
        cur2 = await db.execute("SELECT points_awarded FROM driver_round_attendance WHERE driver_profile_id = 1")
        row2 = await cur2.fetchone()
        assert row2["points_awarded"] == 0


# ---------------------------------------------------------------------------
# 7. The recalculation is one transaction, or none of it (#187)
#
# `recalculate_attendance_for_round` recomputes a round and then propagates the running
# total through every finalised round after it, one call apiece. Each call used to open
# and commit its own connection, so a failure in the middle of that loop left a division's
# attendance points correct up to one round and stale from the next on — and nothing a
# league manager can run re-runs the recalculation.
# ---------------------------------------------------------------------------


async def _make_two_round_db(tmp_path):
    """One full-time driver with attendance rows in two finalised rounds.

    Its ids are the database's own rather than `_make_db`'s fixed ones, because other files
    import it and read them from what it returns. The penalties are the same 2/1/3.

    Returns ``(db_path, division_id, round_ids)``.
    """
    db_file = str(tmp_path / "two_rounds.db")
    await run_migrations(db_file)

    async with get_connection(db_file) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        await db.execute(
            "INSERT INTO attendance_config "
            "(id, no_rsvp_penalty, absent_penalty, no_show_penalty) "
            "VALUES (1, 2, 1, 3)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) "
            "VALUES (?, 'Alpha', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO team_instances (division_id, name, full_name, is_reserve) "
            "VALUES (?, 'Full Team', 'Full Team', 0)",
            (division_id,),
        )
        team_instance_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) "
            "VALUES ('1001', 'ASSIGNED')"
        )
        profile_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
            "VALUES (?, 1, ?)",
            (team_instance_id, profile_id),
        )
        seat_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO driver_season_assignments "
            "(driver_profile_id, season_id, division_id, team_seat_id) VALUES (?, ?, ?, ?)",
            (profile_id, season_id, division_id, seat_id),
        )

        round_ids = []
        for round_number in (1, 2):
            cursor = await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, status, scheduled_at) "
                "VALUES (?, ?, 'STANDARD', 'AWAITING_APPEAL_VERDICTS', '2026-06-01T18:00:00')",
                (division_id, round_number),
            )
            round_id = cursor.lastrowid
            round_ids.append(round_id)
            await db.execute(
                "INSERT INTO driver_round_attendance "
                "(round_id, division_id, driver_profile_id, rsvp_status, attended) "
                "VALUES (?, ?, ?, 'NO_RSVP', 0)",
                (round_id, division_id, profile_id),
            )
        await db.commit()

    return db_file, division_id, round_ids


async def _awarded(db_file: str, round_id: int):
    async with get_connection(db_file) as db:
        cur = await db.execute(
            "SELECT points_awarded FROM driver_round_attendance WHERE round_id = ?",
            (round_id,),
        )
        row = await cur.fetchone()
    return None if row is None else row["points_awarded"]


@pytest.mark.asyncio
async def test_a_failed_propagation_leaves_no_attendance_points_behind(tmp_path, monkeypatch):
    """A failure part-way through the propagation writes nothing at all (#187).

    Before the fix each step committed on its own, so the first round's points were
    already persisted when the second round's call failed — leaving the division scored
    to a different rule from one round to the next, with no command to put it right.
    """
    from unittest.mock import AsyncMock

    from leaguebot.attendance.services import attendance_service

    db_file, division_id, round_ids = await _make_two_round_db(tmp_path)
    assert await _awarded(db_file, round_ids[0]) is None

    real = attendance_service.distribute_attendance_points
    calls: list[int] = []

    async def failing_after_the_first(db_path, round_id, division_id, *, db=None):
        calls.append(round_id)
        if len(calls) > 1:
            raise RuntimeError("the propagation failed part-way through")
        return await real(db_path, round_id, division_id, db=db)

    monkeypatch.setattr(
        attendance_service, "distribute_attendance_points", failing_after_the_first
    )
    monkeypatch.setattr(
        attendance_service, "post_attendance_sheet", AsyncMock()
    )
    monkeypatch.setattr(
        attendance_service, "enforce_attendance_sanctions", AsyncMock()
    )

    with pytest.raises(RuntimeError):
        await attendance_service.recalculate_attendance_for_round(
            bot=None, guild=None, db_path=db_file,
            round_id=round_ids[0], division_id=division_id, season_id=1,
        )

    assert len(calls) == 2, "the propagation did not reach a second round"
    assert await _awarded(db_file, round_ids[0]) is None, (
        "the first round's points were committed despite the recalculation failing"
    )


@pytest.mark.asyncio
async def test_a_whole_recalculation_still_lands(tmp_path, monkeypatch):
    """The other half of the same rule: nothing was broken making it atomic."""
    from unittest.mock import AsyncMock

    from leaguebot.attendance.services import attendance_service

    db_file, division_id, round_ids = await _make_two_round_db(tmp_path)

    monkeypatch.setattr(attendance_service, "post_attendance_sheet", AsyncMock())
    monkeypatch.setattr(attendance_service, "enforce_attendance_sanctions", AsyncMock())

    await attendance_service.recalculate_attendance_for_round(
        bot=None, guild=None, db_path=db_file,
        round_id=round_ids[0], division_id=division_id, season_id=1,
    )

    # NO_RSVP and did not attend: no_rsvp_penalty + absent_penalty (2 + 1).
    assert await _awarded(db_file, round_ids[0]) == 3
    assert await _awarded(db_file, round_ids[1]) == 3


@pytest.mark.asyncio
async def test_distribute_attendance_points_still_commits_on_its_own(tmp_path):
    """Every other caller passes no connection and must keep working unchanged."""
    db_file, division_id, round_ids = await _make_two_round_db(tmp_path)

    await distribute_attendance_points(
        db_file, round_id=round_ids[0], division_id=division_id
    )

    assert await _awarded(db_file, round_ids[0]) == 3


@pytest.mark.asyncio
async def test_a_shared_connection_is_not_committed_by_the_callee(tmp_path):
    """A function handed a connection is one step of somebody else's transaction."""
    from leaguebot.core.db.database import get_connection

    db_file, division_id, round_ids = await _make_two_round_db(tmp_path)

    async with get_connection(db_file) as db:
        await distribute_attendance_points(
            db_file, round_id=round_ids[0], division_id=division_id, db=db
        )
        # Deliberately not committed: the caller owns it and is abandoning it.

    assert await _awarded(db_file, round_ids[0]) is None
