"""standings_service.py — Driver and team standings computation and persistence."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Mapping

from db.database import get_connection
from models.points_config import PointsConfigEntry, PointsConfigFastestLap, SessionType
from models.session_result import OutcomeModifier
from models.standings_snapshot import DriverStandingsSnapshot, TeamStandingsSnapshot

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Points computation
# ---------------------------------------------------------------------------

def compute_points_for_session(
    driver_rows: list[DriverSessionResult],
    config_entries: list[PointsConfigEntry],
    fl_config: PointsConfigFastestLap | None,
    session_type: SessionType,
    fl_override: int | None = None,
) -> list[DriverSessionResult]:
    """Compute and assign points_awarded / fastest_lap_bonus for each driver row.

    Mutates the rows in-place and returns the list.

    Rules:
    - DNS / DSQ → 0 position points, 0 FL bonus.
    - DNF → 0 position points; eligible for FL bonus if within position limit.
    - CLASSIFIED → position points from config; eligible for FL bonus.
    FL eligibility also requires that the driver's ``fastest_lap`` field is non-null.

    ``fl_override`` — when provided, designates the fastest-lap holder directly,
    bypassing automatic time-based detection.
    """
    # Build position → points lookup
    pos_to_pts: dict[int, int] = {e.position: e.points for e in config_entries}

    # Detect the fastest lap holder (race sessions only)
    fl_holder: int | None = None
    if session_type.is_race:
        if fl_override is not None:
            fl_holder = fl_override
        else:
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
    display_names: Mapping[int, str] | None = None,
) -> list[DriverStandingsSnapshot]:
    """Aggregate driver points for all rounds up to and including *up_to_round_id*.

    Sort order (FR-028):
    1. total_points DESC
    2. Feature Race P1 count DESC, Feature Race P2 count DESC, ... (all positions)
    3. For tie after all finish-counts: driver who FIRST achieved the highest
       diverging position wins (first_finish_rounds comparison).
    4. A driver who has taken part in a session ranks above one who has taken part in none.
    5. The final tiebreak: alphabetically by team, the reserve team after every named team
       and a driver holding no seat after the reserves; alphabetically by driver within the
       team; and ascending user id where even that ties.

    Step 5 exists because the four above it can all come out equal — two drivers on nought
    at the start of a season is the ordinary case — and what then decided the order was
    ``sorted()`` falling back on set iteration over Discord snowflakes. That is not an order,
    it just looks like one: adding an unrelated driver to the division rehashes the set and
    can swap two already-published positions. The last step is what makes the order *total*,
    since display names are not unique on Discord and two drivers can genuinely share one
    (#143, and the rule ``opening_driver_standings`` already applied to the season's opening).

    *display_names* are the names the output will actually draw, resolved from Discord by the
    caller; the order is taken on the same string, as a grid ordered on one name while
    displaying another reads as simply broken. A driver the caller could not resolve falls
    back to their id, as everywhere else.

    Returns snapshots with standing_position assigned from 1.
    """
    names = display_names or {}
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT driver_user_id, finishing_position, points_awarded,
                   fastest_lap_bonus, outcome, session_type, round_id, round_number
            FROM (
                SELECT rsr.driver_user_id, rsr.finishing_position,
                       rsr.points_awarded, rsr.fastest_lap_bonus, rsr.outcome,
                       sr.session_type, r.id AS round_id, r.round_number
                FROM race_session_results rsr
                JOIN session_results sr ON sr.id = rsr.session_result_id
                JOIN rounds r ON r.id = sr.round_id
                WHERE r.division_id = ?
                  AND r.id <= ?
                  AND r.round_number <= (SELECT round_number FROM rounds WHERE id = ?)
                  AND sr.status = 'ACTIVE'
                UNION ALL
                SELECT qsr.driver_user_id, qsr.finishing_position,
                       qsr.points_awarded, 0 AS fastest_lap_bonus, qsr.outcome,
                       sr.session_type, r.id AS round_id, r.round_number
                FROM qualifying_session_results qsr
                JOIN session_results sr ON sr.id = qsr.session_result_id
                JOIN rounds r ON r.id = sr.round_id
                WHERE r.division_id = ?
                  AND r.id <= ?
                  AND r.round_number <= (SELECT round_number FROM rounds WHERE id = ?)
                  AND sr.status = 'ACTIVE'
            )
            ORDER BY round_number
            """,
            (
                division_id, up_to_round_id, up_to_round_id,
                division_id, up_to_round_id, up_to_round_id,
            ),
        )
        rows = await cursor.fetchall()
        # A result keeps the account it was recorded under, and a driver who changed account
        # mid-season stands under two. Both are theirs and are counted as one driver, under
        # the account they use now (issue #243).
        from services.driver_service import current_account_map_for_division

        current_of = await current_account_map_for_division(db, division_id)

    # Aggregate
    total_points: dict[int, int] = defaultdict(int)
    # finish_counts[driver][position] = count of Feature Race CLASSIFIED finishes at that position
    finish_counts: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    # first_finish_rounds[driver][position] = earliest round where driver was CLASSIFIED at position
    first_finish_rounds: dict[int, dict[int, int]] = defaultdict(dict)
    # Any driver that has at least one session result (even a 0-point DNF)
    race_participants: set[int] = set()

    for row in rows:
        uid: int = current_of.get(row["driver_user_id"], row["driver_user_id"])
        pts = (row["points_awarded"] or 0) + (row["fastest_lap_bonus"] or 0)
        total_points[uid] += pts
        race_participants.add(uid)

        session_type = SessionType(row["session_type"])
        if session_type is SessionType.FEATURE_RACE and row["outcome"] == "CLASSIFIED":
            pos: int = row["finishing_position"]
            round_num: int = row["round_number"]
            finish_counts[uid][pos] = finish_counts[uid].get(pos, 0) + 1
            existing = first_finish_rounds[uid].get(pos)
            if existing is None or round_num < existing:
                first_finish_rounds[uid][pos] = round_num

    all_drivers = set(total_points) | set(finish_counts)

    # Every seat in the division, reserves included. Two jobs, one query: the non-reserve
    # seats decide who joins the standings without having scored, and every seat supplies the
    # team the final tiebreak orders on — a reserve who raced is in the set already.
    from services.season_lifecycle_service import uncommitted_seat_excluded

    # A driver whose placement is not yet confirmed is not in the standings (issue #220).
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"""
            SELECT dp.discord_user_id, ti.name AS team_name, ti.is_reserve
            FROM team_seats ts
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            JOIN driver_profiles dp ON dp.id = ts.driver_profile_id
            WHERE ti.division_id = ?
              AND ts.driver_profile_id IS NOT NULL
              AND {uncommitted_seat_excluded("ts")}
            """,
            (division_id,),
        )
        seated_rows = await cursor.fetchall()
    # uid -> (team rank, team name). Rank 0 a named team, 1 the reserve team; a driver with
    # no seat at all gets 2 below, ranking after the reserves — their points stand but they
    # are no longer of a team (decided 2026-09-15).
    seats: dict[int, tuple[int, str]] = {}
    for r in seated_rows:
        uid = int(r["discord_user_id"])
        is_reserve = bool(r["is_reserve"])
        seats[uid] = (1 if is_reserve else 0, r["team_name"] or "")
        if is_reserve:
            continue
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
        # Tiebreaker: participated in any race (even DNF) ranks above never-participated
        not_participated = 0 if uid in race_participants else 1
        team_rank, team_name = seats.get(uid, (2, ""))
        return (
            -pts,
            count_vec,
            first_vec,
            not_participated,
            team_rank,
            team_name.casefold(),
            names.get(uid, str(uid)).casefold(),
            uid,
        )

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
                race_participant=uid in race_participants,
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
    """Aggregate team points for all sessions up to *up_to_round_id*.

    Sort order mirrors driver standings (FR-029): total_points DESC then finish-count
    tiebreaks; tiebreak uses Feature Race CLASSIFIED finishes only.

    The final tiebreak mirrors it too, and for the same reason (#143): alphabetically by
    team name, the reserve team after every named team, and ascending role id where two
    teams carry the same name. Without it two teams level on everything are ordered by set
    iteration over role snowflakes, which the addition of an unrelated team can silently
    reverse.

    The name ordered on is the one the database holds, as ``opening_team_standings`` already
    does — a constructor is drawn as a role mention, and no Discord lookup is needed to know
    what the league called the team.

    Returns snapshots with standing_position assigned from 1.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT team_role_id, finishing_position, points_awarded,
                   fastest_lap_bonus, outcome, session_type, round_number
            FROM (
                SELECT rsr.team_role_id, rsr.finishing_position,
                       rsr.points_awarded, rsr.fastest_lap_bonus, rsr.outcome,
                       sr.session_type, r.round_number
                FROM race_session_results rsr
                JOIN session_results sr ON sr.id = rsr.session_result_id
                JOIN rounds r ON r.id = sr.round_id
                WHERE r.division_id = ?
                  AND r.id <= ?
                  AND r.round_number <= (SELECT round_number FROM rounds WHERE id = ?)
                  AND sr.status = 'ACTIVE'
                UNION ALL
                SELECT qsr.team_role_id, qsr.finishing_position,
                       qsr.points_awarded, 0 AS fastest_lap_bonus, qsr.outcome,
                       sr.session_type, r.round_number
                FROM qualifying_session_results qsr
                JOIN session_results sr ON sr.id = qsr.session_result_id
                JOIN rounds r ON r.id = sr.round_id
                WHERE r.division_id = ?
                  AND r.id <= ?
                  AND r.round_number <= (SELECT round_number FROM rounds WHERE id = ?)
                  AND sr.status = 'ACTIVE'
            )
            ORDER BY round_number
            """,
            (
                division_id, up_to_round_id, up_to_round_id,
                division_id, up_to_round_id, up_to_round_id,
            ),
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
        if session_type is SessionType.FEATURE_RACE and row["outcome"] == "CLASSIFIED":
            pos: int = row["finishing_position"]
            rnum: int = row["round_number"]
            finish_counts[tid][pos] = finish_counts[tid].get(pos, 0) + 1
            existing = first_finish_rounds[tid].get(pos)
            if existing is None or rnum < existing:
                first_finish_rounds[tid][pos] = rnum

    all_teams = set(total_points) | set(finish_counts)

    # Every team instance of the division, reserves included. Two jobs, one query: the
    # non-reserve teams join the standings without having scored, and every team supplies the
    # name the final tiebreak orders on — a reserve team whose driver scored is in the set
    # already.
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT trc.role_id, ti.name AS team_name, ti.is_reserve
            FROM team_instances ti
            JOIN divisions d ON d.id = ti.division_id
            JOIN seasons s ON s.id = d.season_id
            JOIN team_role_configs trc
              ON trc.team_name = ti.name
            WHERE ti.division_id = ?
            """,
            (division_id,),
        )
        team_rows = await cursor.fetchall()
    # role id -> (team rank, team name). Rank 0 a named team, 1 the reserve team; a role the
    # division holds no instance for gets 2 below and ranks after both, ordered by id alone.
    team_meta: dict[int, tuple[int, str]] = {}
    for r in team_rows:
        tid = int(r["role_id"])
        is_reserve = bool(r["is_reserve"])
        team_meta[tid] = (1 if is_reserve else 0, r["team_name"] or "")
        if is_reserve:
            continue
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
        team_rank, team_name = team_meta.get(tid, (2, ""))
        return (-pts, count_vec, first_vec, team_rank, team_name.casefold(), tid)

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
# The opening classification
# ---------------------------------------------------------------------------
#
# Posted once, when the season is approved. Nobody has scored, so neither of the two
# functions above can supply the order: every component of their sort keys — points, the
# finish-count vectors, the first-finish rounds, participation — is identical when no round
# has been run, leaving `sorted()` to fall back on set iteration over Discord snowflakes.
# That is not an order, it just looks like one. Hence an explicit rule, and a test that pins
# it (decided 2026-09-08).
#
# Neither function persists anything. There is no round for a snapshot row to key to, and
# writing zero-point rows against round one would corrupt the very computation above.


async def opening_driver_standings(
    db_path: str,
    division_id: int,
    display_names: Mapping[int, str] | None = None,
) -> list[DriverStandingsSnapshot]:
    """The division's grid before a round has been run: every driver on zero.

    Ordered alphabetically by team name, then alphabetically by driver within the team, both
    case-insensitively.

    *display_names* are the names the graphic will actually draw, resolved from Discord by
    the caller. The order reads the same string the sheet does — the attendance sheet already
    orders on the resolved name for this reason, and a grid ordered on one name while
    displaying another would look simply wrong. A driver the caller could not resolve falls
    back to their id, as it does everywhere else.

    Selects the same drivers ``compute_driver_standings`` does — seated, non-reserve.
    """
    names = display_names or {}

    from services.season_lifecycle_service import uncommitted_seat_excluded

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"""
            SELECT dp.discord_user_id, ti.name AS team_name
            FROM team_seats ts
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            JOIN driver_profiles dp ON dp.id = ts.driver_profile_id
            WHERE ti.division_id = ?
              AND ti.is_reserve = 0
              AND ts.driver_profile_id IS NOT NULL
              AND {uncommitted_seat_excluded("ts")}
            """,
            (division_id,),
        )
        rows = await cursor.fetchall()

    seated: list[tuple[int, str]] = []
    for row in rows:
        try:
            seated.append((int(row["discord_user_id"]), row["team_name"] or ""))
        except (TypeError, ValueError):
            continue

    def _order(entry: tuple[int, str]) -> tuple[str, str]:
        user_id, team_name = entry
        return (team_name.casefold(), names.get(user_id, str(user_id)).casefold())

    return [
        DriverStandingsSnapshot(
            id=0,
            round_id=0,
            division_id=division_id,
            driver_user_id=user_id,
            standing_position=position,
            total_points=0,
            finish_counts={},
            first_finish_rounds={},
            race_participant=False,
        )
        for position, (user_id, _team) in enumerate(sorted(seated, key=_order), start=1)
    ]


async def opening_team_standings(
    db_path: str, division_id: int
) -> list[TeamStandingsSnapshot]:
    """The division's constructors before a round has been run: every team on zero.

    Ordered alphabetically by team name, case-insensitively, so the two opening sheets agree
    with one another about which team comes first.

    Keyed by role id, as a constructor's classification always is, and selecting the same
    teams ``compute_team_standings`` does. A team the server holds no role mapping for is
    absent here exactly as it would be after a round.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT trc.role_id, ti.name AS team_name
            FROM team_instances ti
            JOIN divisions d ON d.id = ti.division_id
            JOIN seasons s ON s.id = d.season_id
            JOIN team_role_configs trc
              ON trc.team_name = ti.name
            WHERE ti.division_id = ?
              AND ti.is_reserve = 0
            """,
            (division_id,),
        )
        rows = await cursor.fetchall()

    teams: list[tuple[int, str]] = []
    for row in rows:
        try:
            teams.append((int(row["role_id"]), row["team_name"] or ""))
        except (TypeError, ValueError):
            continue

    return [
        TeamStandingsSnapshot(
            id=0,
            round_id=0,
            division_id=division_id,
            team_role_id=role_id,
            standing_position=position,
            total_points=0,
            finish_counts={},
            first_finish_rounds={},
        )
        for position, (role_id, _name) in enumerate(
            sorted(teams, key=lambda entry: entry[1].casefold()), start=1
        )
    ]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

async def _drop_superseded_driver_rows(db, driver_snaps: list[DriverStandingsSnapshot]) -> None:
    """Delete a recomputed round's rows standing under an account the driver has since left.

    A round's standings are recomputed whenever a penalty, an appeal or an amendment lands in
    it or before it. A driver who changed account since the round was first computed now
    stands under their current account (issue #243), and the upsert below would leave their
    old row beside the new one: the same driver twice in one round. Only such rows go — a
    past account whose driver's current one the recomputation names. Any other row the
    recomputation leaves out is left as it was. Only a live season is ever recomputed, so
    this never touches a completed one.

    The message ids of the posted standings live on the round's top row. Where that row is
    one being dropped, they are carried onto the new top row.
    """
    by_round: dict[tuple[int, int], set[int]] = defaultdict(set)
    for snap in driver_snaps:
        by_round[(snap.round_id, snap.division_id)].add(int(snap.driver_user_id))
    for (round_id, division_id), kept in by_round.items():
        cursor = await db.execute(
            "SELECT driver_user_id, standings_message_id, constructor_standings_message_id "
            "FROM driver_standings_snapshots WHERE round_id = ? AND division_id = ?",
            (round_id, division_id),
        )
        existing = await cursor.fetchall()
        from services.driver_service import current_account_map_for_division

        current_of = await current_account_map_for_division(db, division_id)
        dropped = [
            r for r in existing
            if int(r["driver_user_id"]) in current_of
            and current_of[int(r["driver_user_id"])] in kept
        ]
        if not dropped:
            continue
        carried = next(
            (
                (r["standings_message_id"], r["constructor_standings_message_id"])
                for r in dropped
                if r["standings_message_id"] or r["constructor_standings_message_id"]
            ),
            None,
        )
        placeholders = ",".join("?" for _ in dropped)
        await db.execute(
            f"DELETE FROM driver_standings_snapshots WHERE round_id = ? AND division_id = ? "
            f"AND driver_user_id IN ({placeholders})",
            (round_id, division_id, *[r["driver_user_id"] for r in dropped]),
        )
        if carried is not None:
            top = min(
                (s for s in driver_snaps
                 if (s.round_id, s.division_id) == (round_id, division_id)),
                key=lambda s: s.standing_position,
            )
            await db.execute(
                "INSERT INTO driver_standings_snapshots "
                "(round_id, division_id, driver_user_id, standing_position, total_points) "
                "VALUES (?, ?, ?, ?, 0) "
                "ON CONFLICT(round_id, division_id, driver_user_id) DO NOTHING",
                (round_id, division_id, top.driver_user_id, top.standing_position),
            )
            await db.execute(
                "UPDATE driver_standings_snapshots SET "
                "standings_message_id = COALESCE(standings_message_id, ?), "
                "constructor_standings_message_id = COALESCE(constructor_standings_message_id, ?) "
                "WHERE round_id = ? AND division_id = ? AND driver_user_id = ?",
                (carried[0], carried[1], round_id, division_id, top.driver_user_id),
            )


async def persist_snapshots(
    db_path: str,
    driver_snaps: list[DriverStandingsSnapshot],
    team_snaps: list[TeamStandingsSnapshot],
) -> None:
    """INSERT OR REPLACE all snapshot rows into the database."""
    from services.driver_service import resolve_driver_profile_id

    async with get_connection(db_path) as db:
        await _drop_superseded_driver_rows(db, driver_snaps)

        # Whether each division exists, cached (all snaps in a batch are typically one division)
        _division_known: dict[int, bool] = {}

        for snap in driver_snaps:
            if snap.division_id not in _division_known:
                cursor = await db.execute(
                    "SELECT 1 FROM divisions WHERE id = ?", (snap.division_id,)
                )
                _division_known[snap.division_id] = await cursor.fetchone() is not None
            snap_profile_id: int | None = None
            if _division_known[snap.division_id]:
                snap_profile_id = await resolve_driver_profile_id(
                    snap.driver_user_id, db
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
    display_names: Mapping[int, str] | None = None,
) -> None:
    """Compute and persist driver + team standings snapshots for a round.

    *display_names* are the names the final tiebreak orders a full tie on, and they are
    passed here for the same reason the posting paths resolve them: the stored
    ``standing_position`` is the order a league was shown, so a recomputation that ordered
    two tied drivers by user id would leave the snapshot contradicting the sheet posted
    beside it — and the movement arrows of the next round read the stored order. Every
    caller holding a guild resolves them; one that does not falls back to the id
    (decided 2026-09-15).
    """
    driver_snaps = await compute_driver_standings(
        db_path, division_id, round_id, display_names
    )
    team_snaps = await compute_team_standings(db_path, division_id, round_id)
    await persist_snapshots(db_path, driver_snaps, team_snaps)


async def cascade_recompute_from_round(
    db_path: str,
    division_id: int,
    from_round_id: int,
    display_names: Mapping[int, str] | None = None,
) -> None:
    """Recompute and persist snapshots for all rounds from *from_round_id* onwards.

    Fetches all rounds >= the from_round's round_number, ordered ascending,
    and calls compute_and_persist_round for each.

    *display_names* are resolved once by the caller and used for every round of the cascade,
    rather than per round: the roster only grows as the season runs, so one resolution taken
    across the division covers them all, and a cascade over twenty rounds should not make
    twenty rounds of Discord lookups.
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
        await compute_and_persist_round(db_path, row["id"], division_id, display_names)


# ---------------------------------------------------------------------------
# Movement — the three columns a standings graphic draws and the table does not
#
# Constitution XIV.7 as amended at v4.5.0 admits these as a *derived presentation*:
# arithmetic over figures the textual standings already publish, deciding nothing and
# admitting no datum the bot did not already hold. Two conditions bind that admission, and
# both are why this code lives here rather than in the image module:
#
#   1. The derivation belongs with the data, so the textual path can adopt the columns by
#      calling this rather than growing a second implementation free to drift.
#   2. A value requiring a *rule* to reach stays forbidden. The countback separating two
#      entries level on points is already applied in the persisted ``standing_position``;
#      this reads that order and never re-establishes it.
#
# See specs/040-standings-image-generation/contracts/derived-columns.md.
# ---------------------------------------------------------------------------

#: The three directions of a change of standing position. Each is the datum an asset is
#: resolved from, so the module ships a file per direction (XIV.13, v4.5.0).
#:
#: Each names what it is a change of, because the marker directory holds three vocabularies
#: at once and a bare ``gained.svg`` beside ``race_p1.svg`` said nothing about which. The
#: value is an asset datum and nothing else -- it is never persisted, and the only reader
#: besides the resolver is the projection that hands it over.
MOVEMENT_GAINED = "position_change_gained"
MOVEMENT_LOST = "position_change_lost"
MOVEMENT_UNCHANGED = "position_change_none"


@dataclass(frozen=True)
class Movement:
    """One entry's change of position against the reference round.

    The gap to the leader is deliberately **not** here: it is arithmetic over the
    classification being drawn alone, so it exists even for the first round of a division
    where there is no reference round and no movement at all. Nesting it here once made it
    vanish with the movement record, which the graphic showed as a blank gap column on every
    entry the reference round did not hold. :func:`derive_gaps` owns it.

    ``change`` is unsigned; ``direction`` carries the sense.
    """

    previous_position: int
    change: int
    direction: str


def derive_gaps(current: list[tuple[int, int, int]]) -> dict[int, int]:
    """Key → the leader's points less this entry's.

    *current* is ``(key, standing_position, total_points)``. Nought for the leader itself —
    the *rendering* empties the leader's field, which is a presentation decision and not
    this one's.

    Separate from :func:`derive_movement` because it needs no reference round: a gap can
    always be drawn, including on the graphic of a division's first round.
    """
    if not current:
        return {}
    leader_points = max(points for _, _, points in current)
    return {key: leader_points - points for key, _, points in current}


async def reference_round_id(
    db_path: str, division_id: int, round_id: int
) -> int | None:
    """The round the movement of *round_id* is measured against, or None.

    The most recent round of the division that **holds standings**, strictly below the one
    drawn. A round recorded as cancelled and a round yet to be run hold none, so both are
    stepped over rather than emptying the column for every entry of the graphic drawn after
    them. Selecting on the presence of a snapshot rather than on the round's status is what
    makes that true of either kind without asking why the standings are absent.

    None where no earlier round of the division holds standings — the first round of a
    division, or one every earlier round of which was cancelled.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT r.id
            FROM rounds r
            WHERE r.division_id = ?
              AND r.round_number < (
                  SELECT round_number FROM rounds WHERE id = ?
              )
              AND EXISTS (
                  SELECT 1 FROM driver_standings_snapshots s
                  WHERE s.round_id = r.id AND s.division_id = r.division_id
              )
            ORDER BY r.round_number DESC
            LIMIT 1
            """,
            (division_id, round_id),
        )
        row = await cursor.fetchone()
    return row["id"] if row else None


async def previous_standing_positions(
    db_path: str,
    division_id: int,
    round_id: int,
    *,
    teams: bool = False,
) -> dict[int, int] | None:
    """Key → the standing position it held in the reference round, or None.

    The key is the driver's user id, or the team's Discord role id where *teams*. None —
    distinct from an empty mapping — where there is no reference round at all, which is
    what makes "the first round of a division" different from "a round nobody scored in".
    """
    reference = await reference_round_id(db_path, division_id, round_id)
    if reference is None:
        return None

    table = "team_standings_snapshots" if teams else "driver_standings_snapshots"
    key = "team_role_id" if teams else "driver_user_id"
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"""
            SELECT {key} AS entry_key, standing_position
            FROM {table}
            WHERE division_id = ? AND round_id = ?
            """,
            (division_id, reference),
        )
        rows = await cursor.fetchall()
        # The reference round may stand under an account the driver has since left; the
        # position is theirs all the same (issue #243).
        current_of: dict[int, int] = {}
        if not teams:
            from services.driver_service import current_account_map_for_division

            current_of = await current_account_map_for_division(db, division_id)
    return {
        current_of.get(row["entry_key"], row["entry_key"]): row["standing_position"]
        for row in rows
    }


def derive_movement(
    current: list[tuple[int, int, int]],
    previous: dict[int, int] | None,
) -> dict[int, Movement | None]:
    """Key → its :class:`Movement`, or None where the change cannot be determined.

    *current* is ``(key, standing_position, total_points)`` for every entry of the
    classification being drawn, in any order. *previous* is what
    :func:`previous_standing_positions` returned.

    The record is absent — never partly filled — in exactly two cases: no earlier round
    holds standings, and the reference round does not hold this entry. Both are values the
    data determine to be absent rather than values that could not be determined, so neither
    is a failure and neither raises a notice (XIV.3, XIV.4).

    The gap is never in that state and is not returned here: see :func:`derive_gaps`.
    """
    movements: dict[int, Movement | None] = {}
    for key, position, _points in current:
        if previous is None or key not in previous:
            movements[key] = None
            continue
        was = previous[key]
        if position < was:
            direction = MOVEMENT_GAINED
        elif position > was:
            direction = MOVEMENT_LOST
        else:
            direction = MOVEMENT_UNCHANGED
        movements[key] = Movement(
            previous_position=was,
            change=abs(was - position),
            direction=direction,
        )
    return movements
