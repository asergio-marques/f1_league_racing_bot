"""scoring.py — Points applied to a session's rows: the round's points recomputed, and a
session scored from the division's points configuration."""
from __future__ import annotations

import aiosqlite

from leaguebot.results.models.points_config import PointsConfigEntry, PointsConfigFastestLap, SessionType
from leaguebot.results.models.session_result import DriverSessionResult, OutcomeModifier  # DriverSessionResult kept as DTO for compute_points_for_session


_ACTIVE_SESSIONS_OF_ROUND_SQL = """
    SELECT sr.id AS session_result_id, sr.config_name, sr.session_type,
           s.id AS season_id
    FROM session_results sr
    JOIN rounds r ON r.id = sr.round_id
    JOIN divisions d ON d.id = r.division_id
    JOIN seasons s ON s.id = d.season_id
    WHERE sr.round_id = ? AND sr.status = 'ACTIVE'
"""


async def _recompute_session_points_on(db: aiosqlite.Connection, round_id: int) -> None:
    """Re-score every ACTIVE session of *round_id* on the save *db* belongs to, committing nothing.

    Written on the connection it is handed through `_apply_points_in_tx` so that the points are
    part of the approval's one save, and **raising on the first session that cannot be scored**
    (issue #439, defect 3): a round must not be published as approved with its old points, so
    the failure rolls the whole save back and stops the change.

    A session with no points configuration is not a failure: it is skipped, its rows keeping
    the points they have.
    """
    cursor = await db.execute(_ACTIVE_SESSIONS_OF_ROUND_SQL, (round_id,))
    for row in await cursor.fetchall():
        if row["config_name"] is None:
            continue
        await _apply_points_in_tx(
            db,
            row["session_result_id"],
            row["season_id"],
            row["config_name"],
            SessionType(row["session_type"]),
        )


async def _apply_points_from_config_on(
    db: aiosqlite.Connection,
    session_result_id: int,
    season_id: int,
    config_name: str,
    session_type: SessionType,
) -> bool:
    """Load the season's config entries, compute points for each driver row and UPDATE them on the
    connection handed, committing nothing (stage one's save, #439). Gives whether anything was
    written."""
    return await _apply_points_in_tx(db, session_result_id, season_id, config_name, session_type)


async def _apply_points_in_tx(
    db,
    session_result_id: int,
    season_id: int,
    config_name: str,
    session_type: SessionType,
) -> bool:
    """Score a session on the caller's connection, without committing.

    Returns whether anything was written. A resubmission's swap scores the sessions it inserts in
    the same transaction, so a crash can never leave a round's new results in place with no
    points on them.
    """
    from leaguebot.results.services.standings.standings_service import compute_points_for_session  # lazy import

    # Load config entries for (season, config, session_type)
    entries_cursor = await db.execute(
        "SELECT position, points FROM season_points_entries "
        "WHERE season_id = ? AND config_name = ? AND session_type = ? "
        "ORDER BY position",
        (season_id, config_name, session_type.value),
    )
    entry_rows = await entries_cursor.fetchall()

    # Load FL config
    fl_cursor = await db.execute(
        "SELECT fl_points, fl_position_limit FROM season_points_fl "
        "WHERE season_id = ? AND config_name = ? AND session_type = ?",
        (season_id, config_name, session_type.value),
    )
    fl_row = await fl_cursor.fetchone()

    # Load driver result rows for this session
    if session_type.is_qualifying:
        dsr_cursor = await db.execute(
            "SELECT id, driver_user_id, team_instance_id, finishing_position, "
            "outcome, NULL AS fastest_lap "
            "FROM qualifying_session_results "
            "WHERE session_result_id = ?",
            (session_result_id,),
        )
    else:
        dsr_cursor = await db.execute(
            "SELECT id, driver_user_id, team_instance_id, finishing_position, "
            "outcome, fastest_lap "
            "FROM race_session_results "
            "WHERE session_result_id = ?",
            (session_result_id,),
        )
    dsr_rows = await dsr_cursor.fetchall()

    # Load FL driver override (if any) from the session header
    fl_override_cursor = await db.execute(
        "SELECT fl_driver_override FROM session_results WHERE id = ?",
        (session_result_id,),
    )
    fl_override_row = await fl_override_cursor.fetchone()
    fl_driver_override: int | None = (
        fl_override_row["fl_driver_override"] if fl_override_row else None
    )

    if not entry_rows and fl_row is None:
        # No config data — nothing to compute (0 points stays as-is)
        return False

    config_entries = [
        PointsConfigEntry(
            id=0,
            config_id=0,
            session_type=session_type,
            position=r["position"],
            points=r["points"],
        )
        for r in entry_rows
    ]

    fl_config: PointsConfigFastestLap | None = None
    if fl_row is not None:
        fl_config = PointsConfigFastestLap(
            id=0,
            config_id=0,
            session_type=session_type,
            fl_points=fl_row["fl_points"],
            fl_position_limit=fl_row["fl_position_limit"],
        )

    driver_rows = [
        DriverSessionResult(
            id=r["id"],
            session_result_id=session_result_id,
            driver_user_id=r["driver_user_id"],
            team_instance_id=r["team_instance_id"],
            finishing_position=r["finishing_position"],
            outcome=OutcomeModifier(r["outcome"]),
            tyre=None,
            best_lap=None,
            gap=None,
            total_time=None,
            fastest_lap=r["fastest_lap"],
            time_penalties=None,
            post_steward_total_time=None,
            post_race_time_penalties=None,
            points_awarded=0,
            fastest_lap_bonus=0,
            is_superseded=False,
        )
        for r in dsr_rows
    ]

    # Mutates driver_rows in-place with computed points_awarded / fastest_lap_bonus
    compute_points_for_session(driver_rows, config_entries, fl_config, session_type, fl_override=fl_driver_override)

    # Persist to new tables (keyed by session_result_id + driver_user_id)
    if session_type.is_qualifying:
        for row in driver_rows:
            await db.execute(
                "UPDATE qualifying_session_results "
                "SET points_awarded = ? "
                "WHERE session_result_id = ? AND driver_user_id = ?",
                (row.points_awarded, session_result_id, row.driver_user_id),
            )
    else:
        for row in driver_rows:
            await db.execute(
                "UPDATE race_session_results "
                "SET points_awarded = ?, fastest_lap_bonus = ? "
                "WHERE session_result_id = ? AND driver_user_id = ?",
                (row.points_awarded, row.fastest_lap_bonus, session_result_id, row.driver_user_id),
            )
    return True
