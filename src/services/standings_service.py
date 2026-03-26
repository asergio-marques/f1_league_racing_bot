"""standings_service.py — Driver and team standings computation and persistence."""
from __future__ import annotations

import json
import logging
from collections import defaultdict

from db.database import get_connection
from models.points_config import PointsConfigEntry, PointsConfigFastestLap, SessionType
from models.session_result import DriverSessionResult, OutcomeModifier
from models.standings_snapshot import DriverStandingsSnapshot, TeamStandingsSnapshot

log = logging.getLogger(__name__)

# Session types that count for team standings (per constitution XII).
_TEAM_POINTS_SESSIONS: frozenset[SessionType] = frozenset(
    {SessionType.FEATURE_RACE, SessionType.SPRINT_RACE}
)


# ---------------------------------------------------------------------------
# Points computation
# ---------------------------------------------------------------------------

def compute_points_for_session(
    driver_rows: list[DriverSessionResult],
    config_entries: list[PointsConfigEntry],
    fl_config: PointsConfigFastestLap | None,
    session_type: SessionType,
) -> list[DriverSessionResult]:
    """Compute and assign points_awarded / fastest_lap_bonus for each driver row.

    Mutates the rows in-place and returns the list.

    Rules:
    - DNS / DSQ → 0 position points, 0 FL bonus.
    - DNF → 0 position points; eligible for FL bonus if within position limit.
    - CLASSIFIED → position points from config; eligible for FL bonus.
    FL eligibility also requires that the driver's ``fastest_lap`` field is non-null.
    """
    # Build position → points lookup
    pos_to_pts: dict[int, int] = {e.position: e.points for e in config_entries}

    # Detect the fastest lap holder (race sessions only)
    fl_holder: int | None = None
    if session_type.is_race:
        fl_holder = detect_fastest_lap(driver_rows, session_type)

    for row in driver_rows:
        # Position points
        if row.outcome in (OutcomeModifier.CLASSIFIED,):
            row.points_awarded = pos_to_pts.get(row.finishing_position, 0)
        else:
            row.points_awarded = 0

        # FL bonus
        row.fastest_lap_bonus = 0
        if fl_config is not None and row.driver_user_id == fl_holder:
            is_eligible_outcome = row.outcome.is_fl_eligible
            within_limit = (
                fl_config.fl_position_limit is None
                or row.finishing_position <= fl_config.fl_position_limit
            )
            if is_eligible_outcome and within_limit:
                row.fastest_lap_bonus = fl_config.fl_points

    return driver_rows


def detect_fastest_lap(
    driver_rows: list[DriverSessionResult],
    session_type: SessionType,  # noqa: ARG001 — kept for API clarity
) -> int | None:
    """Return the driver_user_id of the driver who set the fastest lap, or None.

    Compares the raw ``fastest_lap`` string values, which are in M:SS.mmm or
    SS.mmm or H:MM:SS.mmm format. Lower parsed time wins.
    """
    def _to_ms(t: str) -> float:
        """Parse a lap-time string to total milliseconds; return infinity on failure."""
        try:
            parts = t.split(":")
            if len(parts) == 2:
                mins = float(parts[0])
                secs_ms = float(parts[1])
                return mins * 60_000 + secs_ms * 1_000
            elif len(parts) == 3:
                # H:MM:SS.mmm
                hours = float(parts[0])
                mins = float(parts[1])
                secs_ms = float(parts[2])
                return hours * 3_600_000 + mins * 60_000 + secs_ms * 1_000
            else:
                return float(t) * 1_000
        except (ValueError, IndexError):
            return float("inf")

    best_ms = float("inf")
    best_driver: int | None = None

    for row in driver_rows:
        if row.fastest_lap and row.fastest_lap.upper() not in {"N/A", "DNS", "DNF", "DSQ"}:
            t = _to_ms(row.fastest_lap)
            if t < best_ms:
                best_ms = t
                best_driver = row.driver_user_id

    return best_driver


# ---------------------------------------------------------------------------
# Driver standings
# ---------------------------------------------------------------------------

async def compute_driver_standings(
    db_path: str,
    division_id: int,
    up_to_round_id: int,
) -> list[DriverStandingsSnapshot]:
    """Aggregate driver points for all rounds up to and including *up_to_round_id*.

    Sort order (FR-028):
    1. total_points DESC
    2. Feature Race P1 count DESC, Feature Race P2 count DESC, ... (all positions)
    3. For tie after all finish-counts: driver who FIRST achieved the highest
       diverging position wins (first_finish_rounds comparison).

    Returns snapshots with standing_position assigned from 1.
    """
    async with get_connection(db_path) as db:
        # Fetch all non-superseded driver session results up to the target round
        cursor = await db.execute(
            """
            SELECT dsr.driver_user_id,
                   dsr.finishing_position,
                   dsr.points_awarded,
                   dsr.fastest_lap_bonus,
                   dsr.outcome,
                   sr.session_type,
                   r.id AS round_id,
                   r.round_number
            FROM driver_session_results dsr
            JOIN session_results sr ON sr.id = dsr.session_result_id
            JOIN rounds r ON r.id = sr.round_id
            WHERE r.division_id = ?
              AND r.id <= ?
              AND r.round_number <= (
                  SELECT round_number FROM rounds WHERE id = ?
              )
              AND dsr.is_superseded = 0
              AND sr.status = 'ACTIVE'
            ORDER BY r.round_number
            """,
            (division_id, up_to_round_id, up_to_round_id),
        )
        rows = await cursor.fetchall()

    # Aggregate
    total_points: dict[int, int] = defaultdict(int)
    # finish_counts[driver][position] = count of Feature Race finishes at that position
    finish_counts: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    # first_finish_rounds[driver][position] = earliest round where driver finished at position
    first_finish_rounds: dict[int, dict[int, int]] = defaultdict(dict)

    for row in rows:
        uid: int = row["driver_user_id"]
        pts = (row["points_awarded"] or 0) + (row["fastest_lap_bonus"] or 0)
        total_points[uid] += pts

        session_type = SessionType(row["session_type"])
        if session_type is SessionType.FEATURE_RACE:
            pos: int = row["finishing_position"]
            round_num: int = row["round_number"]
            finish_counts[uid][pos] = finish_counts[uid].get(pos, 0) + 1
            existing = first_finish_rounds[uid].get(pos)
            if existing is None or round_num < existing:
                first_finish_rounds[uid][pos] = round_num

    all_drivers = set(total_points) | set(finish_counts)

    # Include all non-reserve drivers in the division even if they have no results yet.
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT dp.discord_user_id
            FROM team_seats ts
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            JOIN driver_profiles dp ON dp.id = ts.driver_profile_id
            WHERE ti.division_id = ?
              AND ti.is_reserve = 0
              AND ts.driver_profile_id IS NOT NULL
            """,
            (division_id,),
        )
        seated_rows = await cursor.fetchall()
    for r in seated_rows:
        uid = int(r["discord_user_id"])
        if uid not in total_points:
            total_points[uid] = 0
        all_drivers.add(uid)

    # Compute once across all drivers so every sort-key vector has the same
    # length — prevents tuple length mismatch corrupting tiebreak comparisons.
    global_max_pos = max(
        (max(fc.keys(), default=0) for fc in finish_counts.values()),
        default=0,
    )

    def _sort_key(uid: int) -> tuple:
        pts = total_points.get(uid, 0)
        fc = finish_counts.get(uid, {})
        ffr = first_finish_rounds.get(uid, {})
        # Build per-position tiebreak vectors — use negative counts (descending)
        # and positive first_round (ascending for tiebreak: earlier is better)
        count_vec = tuple(-fc.get(p, 0) for p in range(1, global_max_pos + 1))
        first_vec = tuple(ffr.get(p, 999999) for p in range(1, global_max_pos + 1))
        return (-pts, count_vec, first_vec)

    sorted_drivers = sorted(all_drivers, key=_sort_key)

    snapshots: list[DriverStandingsSnapshot] = []
    for i, uid in enumerate(sorted_drivers, start=1):
        fc = dict(finish_counts.get(uid, {}))
        ffr = dict(first_finish_rounds.get(uid, {}))
        snapshots.append(
            DriverStandingsSnapshot(
                id=0,
                round_id=up_to_round_id,
                division_id=division_id,
                driver_user_id=uid,
                standing_position=i,
                total_points=total_points.get(uid, 0),
                finish_counts=fc,
                first_finish_rounds=ffr,
            )
        )

    return snapshots


# ---------------------------------------------------------------------------
# Team standings
# ---------------------------------------------------------------------------

async def compute_team_standings(
    db_path: str,
    division_id: int,
    up_to_round_id: int,
) -> list[TeamStandingsSnapshot]:
    """Aggregate team points for all Feature Race and Sprint Race sessions up to
    *up_to_round_id*.

    Sort order mirrors driver standings (FR-029): total_points DESC then finish-count
    tiebreaks; tiebreak uses Feature Race finishes only.

    Returns snapshots with standing_position assigned from 1.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT dsr.team_role_id,
                   dsr.finishing_position,
                   dsr.points_awarded,
                   dsr.fastest_lap_bonus,
                   sr.session_type,
                   r.round_number
            FROM driver_session_results dsr
            JOIN session_results sr ON sr.id = dsr.session_result_id
            JOIN rounds r ON r.id = sr.round_id
            WHERE r.division_id = ?
              AND r.id <= ?
              AND r.round_number <= (
                  SELECT round_number FROM rounds WHERE id = ?
              )
              AND dsr.is_superseded = 0
              AND sr.status = 'ACTIVE'
              AND sr.session_type IN ('FEATURE_RACE', 'SPRINT_RACE')
            ORDER BY r.round_number
            """,
            (division_id, up_to_round_id, up_to_round_id),
        )
        rows = await cursor.fetchall()

    total_points: dict[int, int] = defaultdict(int)
    finish_counts: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    first_finish_rounds: dict[int, dict[int, int]] = defaultdict(dict)

    for row in rows:
        tid: int = row["team_role_id"]
        pts = (row["points_awarded"] or 0) + (row["fastest_lap_bonus"] or 0)
        total_points[tid] += pts

        session_type = SessionType(row["session_type"])
        if session_type is SessionType.FEATURE_RACE:
            pos: int = row["finishing_position"]
            rnum: int = row["round_number"]
            finish_counts[tid][pos] = finish_counts[tid].get(pos, 0) + 1
            existing = first_finish_rounds[tid].get(pos)
            if existing is None or rnum < existing:
                first_finish_rounds[tid][pos] = rnum

    all_teams = set(total_points) | set(finish_counts)

    # Include all non-reserve team instances in the division even if they have no results yet.
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT trc.role_id
            FROM team_instances ti
            JOIN divisions d ON d.id = ti.division_id
            JOIN seasons s ON s.id = d.season_id
            JOIN team_role_configs trc
              ON trc.server_id = s.server_id AND trc.team_name = ti.name
            WHERE ti.division_id = ?
              AND ti.is_reserve = 0
            """,
            (division_id,),
        )
        team_rows = await cursor.fetchall()
    for r in team_rows:
        tid = int(r["role_id"])
        if tid not in total_points:
            total_points[tid] = 0
        all_teams.add(tid)

    # Compute once across all teams so every sort-key vector has the same
    # length — prevents tuple length mismatch corrupting tiebreak comparisons.
    global_max_pos = max(
        (max(fc.keys(), default=0) for fc in finish_counts.values()),
        default=0,
    )

    def _sort_key(tid: int) -> tuple:
        pts = total_points.get(tid, 0)
        fc = finish_counts.get(tid, {})
        ffr = first_finish_rounds.get(tid, {})
        count_vec = tuple(-fc.get(p, 0) for p in range(1, global_max_pos + 1))
        first_vec = tuple(ffr.get(p, 999999) for p in range(1, global_max_pos + 1))
        return (-pts, count_vec, first_vec)

    sorted_teams = sorted(all_teams, key=_sort_key)

    snapshots: list[TeamStandingsSnapshot] = []
    for i, tid in enumerate(sorted_teams, start=1):
        fc = dict(finish_counts.get(tid, {}))
        ffr = dict(first_finish_rounds.get(tid, {}))
        snapshots.append(
            TeamStandingsSnapshot(
                id=0,
                round_id=up_to_round_id,
                division_id=division_id,
                team_role_id=tid,
                standing_position=i,
                total_points=total_points.get(tid, 0),
                finish_counts=fc,
                first_finish_rounds=ffr,
            )
        )

    return snapshots


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

async def persist_snapshots(
    db_path: str,
    driver_snaps: list[DriverStandingsSnapshot],
    team_snaps: list[TeamStandingsSnapshot],
) -> None:
    """INSERT OR REPLACE all snapshot rows into the database."""
    from services.driver_service import resolve_driver_profile_id

    async with get_connection(db_path) as db:
        # Cache server_id per division_id (all snaps in a batch are typically one division)
        _server_id_cache: dict[int, int | None] = {}

        for snap in driver_snaps:
            if snap.division_id not in _server_id_cache:
                cursor = await db.execute(
                    "SELECT s.server_id FROM divisions d "
                    "JOIN seasons s ON s.id = d.season_id WHERE d.id = ?",
                    (snap.division_id,),
                )
                div_row = await cursor.fetchone()
                _server_id_cache[snap.division_id] = div_row["server_id"] if div_row else None
            server_id = _server_id_cache.get(snap.division_id)
            snap_profile_id: int | None = None
            if server_id is not None:
                snap_profile_id = await resolve_driver_profile_id(
                    server_id, snap.driver_user_id, db
                )
            await db.execute(
                """
                INSERT INTO driver_standings_snapshots
                    (round_id, division_id, driver_user_id, standing_position, total_points,
                     finish_counts, first_finish_rounds, standings_message_id, driver_profile_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(round_id, division_id, driver_user_id)
                DO UPDATE SET
                    standing_position = excluded.standing_position,
                    total_points = excluded.total_points,
                    finish_counts = excluded.finish_counts,
                    first_finish_rounds = excluded.first_finish_rounds,
                    driver_profile_id = excluded.driver_profile_id
                """,
                (
                    snap.round_id,
                    snap.division_id,
                    snap.driver_user_id,
                    snap.standing_position,
                    snap.total_points,
                    json.dumps(snap.finish_counts),
                    json.dumps(snap.first_finish_rounds),
                    snap.standings_message_id,
                    snap_profile_id,
                ),
            )
        for snap in team_snaps:
            await db.execute(
                """
                INSERT INTO team_standings_snapshots
                    (round_id, division_id, team_role_id, standing_position, total_points,
                     finish_counts, first_finish_rounds)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(round_id, division_id, team_role_id)
                DO UPDATE SET
                    standing_position = excluded.standing_position,
                    total_points = excluded.total_points,
                    finish_counts = excluded.finish_counts,
                    first_finish_rounds = excluded.first_finish_rounds
                """,
                (
                    snap.round_id,
                    snap.division_id,
                    snap.team_role_id,
                    snap.standing_position,
                    snap.total_points,
                    json.dumps(snap.finish_counts),
                    json.dumps(snap.first_finish_rounds),
                ),
            )
        await db.commit()


async def compute_and_persist_round(
    db_path: str,
    round_id: int,
    division_id: int,
) -> None:
    """Compute and persist driver + team standings snapshots for a round."""
    driver_snaps = await compute_driver_standings(db_path, division_id, round_id)
    team_snaps = await compute_team_standings(db_path, division_id, round_id)
    await persist_snapshots(db_path, driver_snaps, team_snaps)


async def cascade_recompute_from_round(
    db_path: str,
    division_id: int,
    from_round_id: int,
) -> None:
    """Recompute and persist snapshots for all rounds from *from_round_id* onwards.

    Fetches all rounds >= the from_round's round_number, ordered ascending,
    and calls compute_and_persist_round for each.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT id FROM rounds
            WHERE division_id = ?
              AND round_number >= (
                  SELECT round_number FROM rounds WHERE id = ?
              )
              AND status != 'CANCELLED'
            ORDER BY round_number
            """,
            (division_id, from_round_id),
        )
        round_rows = await cursor.fetchall()

    for row in round_rows:
        await compute_and_persist_round(db_path, row["id"], division_id)
