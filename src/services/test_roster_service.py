"""test_roster_service.py — Fake driver management for test mode.

Provides helpers consumed by TestModeCog:
  - add_test_driver:        create a synthetic driver and seat them in a division team
  - list_test_drivers:      retrieve all fake drivers in a division (for cheat sheet)
  - clear_test_drivers:     remove all fake drivers from a named division
  - clear_all_test_drivers: remove all fake drivers from every division (toggle-off)
  - ensure_test_configs:    idempotently create "Standard" and "Half Points" season configs

Synthetic user IDs are guaranteed to be outside real Discord snowflake space by
using a base of 9_000_000_000_000_000_000, well above any real snowflake value
reachable in the foreseeable future (current IDs are ~1.3×10^18 as of 2026).
"""

from __future__ import annotations

import logging
from typing import TypedDict

from db.database import get_connection
from models.points_config import SessionType

log = logging.getLogger(__name__)

# Synthetic IDs start here — safely above all real Discord snowflakes.
_SYNTHETIC_ID_BASE = 9_000_000_000_000_000_000

# ─── Default test config definitions ────────────────────────────────────────

_STANDARD_NAME = "Standard"
_HALF_POINTS_NAME = "Half Points"

# Points per position (1-indexed) per session type.
_QUALIFYING_POINTS = {1: 3, 2: 2, 3: 1}

_SPRINT_RACE_STANDARD = {1: 10, 2: 9, 3: 8, 4: 7, 5: 6, 6: 5, 7: 4, 8: 3, 9: 2, 10: 1}
_SPRINT_RACE_HALF = {1: 5, 2: 4, 3: 3, 4: 2, 5: 1}

_FEATURE_RACE_STANDARD = {
    1: 30, 2: 27, 3: 24, 4: 21, 5: 18, 6: 15, 7: 13, 8: 11,
    9: 9, 10: 7, 11: 5, 12: 4, 13: 3, 14: 2, 15: 1,
}
_FEATURE_RACE_HALF = {1: 15, 2: 13, 3: 11, 4: 9, 5: 7, 6: 5, 7: 4, 8: 3, 9: 2, 10: 1}


# ─── Return types ────────────────────────────────────────────────────────────

class TestDriverInfo(TypedDict):
    profile_id: int
    discord_user_id: int
    display_name: str
    team_name: str


# ─── Internal helpers ────────────────────────────────────────────────────────

async def _next_synthetic_id(db_path: str) -> int:
    """Return the next available synthetic driver ID."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT MAX(CAST(discord_user_id AS INTEGER)) AS mx "
            "FROM driver_profiles "
            "WHERE is_test_driver = 1 "
            "  AND CAST(discord_user_id AS INTEGER) >= ?",
            (_SYNTHETIC_ID_BASE,),
        )
        row = await cursor.fetchone()
    current_max = row["mx"] if (row and row["mx"] is not None) else None
    return _SYNTHETIC_ID_BASE + 1 if current_max is None else current_max + 1


async def _get_active_season_id(server_id: int, db_path: str) -> int | None:
    """Return the active or setup season ID for a server, or None if none exists."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM seasons WHERE server_id = ? AND status IN ('ACTIVE', 'SETUP')",
            (server_id,),
        )
        row = await cursor.fetchone()
    return row["id"] if row else None


async def _get_division_id(
    server_id: int, season_id: int, division_name: str, db_path: str
) -> int | None:
    """Return the division ID for a named division in the active season, or None."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT d.id
            FROM divisions d
            JOIN seasons s ON s.id = d.season_id
            WHERE s.server_id = ?
              AND s.id = ?
              AND LOWER(d.name) = LOWER(?)
              AND d.status != 'CANCELLED'
            """,
            (server_id, season_id, division_name),
        )
        row = await cursor.fetchone()
    return row["id"] if row else None


# ─── Public API ──────────────────────────────────────────────────────────────

async def add_test_driver(
    server_id: int,
    driver_name: str,
    team_name: str,
    division_name: str,
    db_path: str,
) -> TestDriverInfo | str:
    """Create a fake driver profile and seat them in *team_name* in *division_name*.

    Returns a TestDriverInfo dict on success, or an error string on failure.
    """
    season_id = await _get_active_season_id(server_id, db_path)
    if season_id is None:
        return "No active or setup season found."

    division_id = await _get_division_id(server_id, season_id, division_name, db_path)
    if division_id is None:
        return f"Division '{division_name}' not found in the active season."

    async with get_connection(db_path) as db:
        # Find the team instance in this division (case-insensitive name match)
        cursor = await db.execute(
            "SELECT id, max_seats, is_reserve FROM team_instances "
            "WHERE division_id = ? AND LOWER(name) = LOWER(?)",
            (division_id, team_name),
        )
        team_row = await cursor.fetchone()
        if team_row is None:
            return f"Team '{team_name}' not found in division '{division_name}'."

        team_instance_id: int = team_row["id"]
        is_reserve: bool = bool(team_row["is_reserve"])

        # Find a free seat (driver_profile_id IS NULL)
        seat_cursor = await db.execute(
            "SELECT id, seat_number FROM team_seats "
            "WHERE team_instance_id = ? AND driver_profile_id IS NULL "
            "ORDER BY seat_number "
            "LIMIT 1",
            (team_instance_id,),
        )
        seat_row = await seat_cursor.fetchone()
        if seat_row is None:
            if not is_reserve:
                return f"No free seats available in team '{team_name}'."
            # Reserve team has unlimited seats — create a new one
            max_cursor = await db.execute(
                "SELECT MAX(seat_number) FROM team_seats WHERE team_instance_id = ?",
                (team_instance_id,),
            )
            max_row = await max_cursor.fetchone()
            next_seat_number = (max_row[0] or 0) + 1
            new_seat_cursor = await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                "VALUES (?, ?, NULL)",
                (team_instance_id, next_seat_number),
            )
            seat_id = new_seat_cursor.lastrowid  # type: ignore[assignment]
        else:
            seat_id: int = seat_row["id"]

        # Generate synthetic ID
        synthetic_uid = await _next_synthetic_id(db_path)
        uid_str = str(synthetic_uid)

        # Create driver profile
        try:
            profile_cursor = await db.execute(
                "INSERT INTO driver_profiles "
                "(server_id, discord_user_id, current_state, former_driver, is_test_driver, test_display_name) "
                "VALUES (?, ?, 'ASSIGNED', 0, 1, ?)",
                (server_id, uid_str, driver_name),
            )
            profile_id: int = profile_cursor.lastrowid  # type: ignore[assignment]
        except Exception as exc:
            return f"Failed to create driver profile: {exc}"

        # Occupy the seat
        await db.execute(
            "UPDATE team_seats SET driver_profile_id = ? WHERE id = ?",
            (profile_id, seat_id),
        )

        # Create season assignment
        await db.execute(
            "INSERT INTO driver_season_assignments "
            "(driver_profile_id, season_id, division_id, team_seat_id, "
            "current_position, current_points, points_gap_to_first) "
            "VALUES (?, ?, ?, ?, 0, 0, 0)",
            (profile_id, season_id, division_id, seat_id),
        )

        await db.commit()

    return TestDriverInfo(
        profile_id=profile_id,
        discord_user_id=synthetic_uid,
        display_name=driver_name,
        team_name=team_name,
    )


async def list_test_drivers(
    server_id: int,
    division_name: str,
    db_path: str,
) -> list[TestDriverInfo] | str:
    """Return all fake drivers in *division_name*, ordered by team then profile ID.

    Returns an error string if the division does not exist.
    """
    season_id = await _get_active_season_id(server_id, db_path)
    if season_id is None:
        return "No active or setup season found."

    division_id = await _get_division_id(server_id, season_id, division_name, db_path)
    if division_id is None:
        return f"Division '{division_name}' not found in the active season."

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT dp.id          AS profile_id,
                   dp.discord_user_id,
                   dp.test_display_name,
                   ti.name        AS team_name
            FROM driver_profiles dp
            JOIN team_seats ts     ON ts.driver_profile_id = dp.id
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            WHERE dp.is_test_driver = 1
              AND ti.division_id = ?
            ORDER BY ti.is_reserve ASC, ti.name, dp.id
            """,
            (division_id,),
        )
        rows = await cursor.fetchall()

    return [
        TestDriverInfo(
            profile_id=r["profile_id"],
            discord_user_id=int(r["discord_user_id"]),
            display_name=r["test_display_name"] or f"Driver {r['profile_id']}",
            team_name=r["team_name"],
        )
        for r in rows
    ]


async def clear_test_drivers(
    server_id: int,
    division_name: str,
    db_path: str,
) -> int | str:
    """Remove all fake drivers from *division_name* and return the count removed.

    Returns an error string if the division does not exist.
    """
    season_id = await _get_active_season_id(server_id, db_path)
    if season_id is None:
        return "No active or setup season found."

    division_id = await _get_division_id(server_id, season_id, division_name, db_path)
    if division_id is None:
        return f"Division '{division_name}' not found in the active season."

    return await _delete_test_drivers_in_division(division_id, db_path)


async def remove_test_driver(
    server_id: int,
    discord_user_id: int,
    db_path: str,
) -> str | dict:
    """Remove a single fake driver by their synthetic Discord user ID.

    Returns a dict with keys ``display_name`` and ``team_name`` on success,
    or an error string if the profile doesn't exist or is not a test driver.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT dp.id AS profile_id,
                   dp.test_display_name,
                   ti.name AS team_name
            FROM driver_profiles dp
            LEFT JOIN team_seats ts ON ts.driver_profile_id = dp.id
            LEFT JOIN team_instances ti ON ti.id = ts.team_instance_id
            WHERE dp.server_id = ?
              AND CAST(dp.discord_user_id AS INTEGER) = ?
              AND dp.is_test_driver = 1
            """,
            (server_id, discord_user_id),
        )
        row = await cursor.fetchone()

        if row is None:
            return "No test driver found with that user ID on this server."

        profile_id: int = row["profile_id"]
        display_name: str = row["test_display_name"] or f"Driver {profile_id}"
        team_name: str = row["team_name"] or "(unknown team)"

        # Vacate the seat
        await db.execute(
            "UPDATE team_seats SET driver_profile_id = NULL WHERE driver_profile_id = ?",
            (profile_id,),
        )
        # Remove season assignment
        await db.execute(
            "DELETE FROM driver_season_assignments WHERE driver_profile_id = ?",
            (profile_id,),
        )
        # Delete the profile
        await db.execute(
            "DELETE FROM driver_profiles WHERE id = ?",
            (profile_id,),
        )
        await db.commit()

    return {"display_name": display_name, "team_name": team_name}


async def clear_all_test_drivers(server_id: int, db_path: str) -> int:
    """Remove all fake drivers from all divisions in the active season.

    Returns the total count removed. Safe to call even if no active season exists.
    """
    season_id = await _get_active_season_id(server_id, db_path)
    if season_id is None:
        return 0

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM divisions WHERE season_id = ? AND status != 'CANCELLED'",
            (season_id,),
        )
        division_rows = await cursor.fetchall()

    total = 0
    for row in division_rows:
        total += await _delete_test_drivers_in_division(row["id"], db_path)
    return total


async def _delete_test_drivers_in_division(division_id: int, db_path: str) -> int:
    """Delete all fake driver profiles (and related rows) from *division_id*.

    Cascades handle season_assignments; we also vacate the occupied seats manually
    since team_seats.driver_profile_id is a nullable FK without ON DELETE SET NULL.
    """
    async with get_connection(db_path) as db:
        # Collect fake driver profile IDs in this division
        cursor = await db.execute(
            """
            SELECT dp.id AS profile_id
            FROM driver_profiles dp
            JOIN team_seats ts     ON ts.driver_profile_id = dp.id
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            WHERE dp.is_test_driver = 1
              AND ti.division_id = ?
            """,
            (division_id,),
        )
        profile_rows = await cursor.fetchall()

        if not profile_rows:
            return 0

        profile_ids = [r["profile_id"] for r in profile_rows]
        placeholders = ",".join("?" * len(profile_ids))

        # Vacate team seats (nullable FK — no cascade)
        await db.execute(
            f"UPDATE team_seats SET driver_profile_id = NULL "
            f"WHERE driver_profile_id IN ({placeholders})",
            profile_ids,
        )

        # Delete season assignments (FK cascade would handle this if defined,
        # but we do it explicitly for clarity)
        await db.execute(
            f"DELETE FROM driver_season_assignments "
            f"WHERE driver_profile_id IN ({placeholders})",
            profile_ids,
        )

        # Delete the profiles
        await db.execute(
            f"DELETE FROM driver_profiles WHERE id IN ({placeholders})",
            profile_ids,
        )

        await db.commit()

    return len(profile_ids)


# ─── Test config seeding ─────────────────────────────────────────────────────

async def ensure_test_configs(
    server_id: int,
    season_id: int,
    db_path: str,
) -> list[str]:
    """Idempotently create "Standard" and "Half Points" configs and attach to season.

    Returns a list of config names that were newly created (empty if both already existed).
    """
    created: list[str] = []

    configs: list[tuple[str, dict[SessionType, dict[int, int]], dict[SessionType, tuple[int, int]]]] = [
        (
            _STANDARD_NAME,
            {
                SessionType.SPRINT_QUALIFYING: _QUALIFYING_POINTS,
                SessionType.SPRINT_RACE: _SPRINT_RACE_STANDARD,
                SessionType.FEATURE_QUALIFYING: _QUALIFYING_POINTS,
                SessionType.FEATURE_RACE: _FEATURE_RACE_STANDARD,
            },
            {SessionType.FEATURE_RACE: (2, 15)},
        ),
        (
            _HALF_POINTS_NAME,
            {
                SessionType.SPRINT_QUALIFYING: _QUALIFYING_POINTS,
                SessionType.SPRINT_RACE: _SPRINT_RACE_HALF,
                SessionType.FEATURE_QUALIFYING: _QUALIFYING_POINTS,
                SessionType.FEATURE_RACE: _FEATURE_RACE_HALF,
            },
            {SessionType.FEATURE_RACE: (1, 10)},
        ),
    ]

    for config_name, session_entries, fl_configs in configs:
        newly_created = await _ensure_single_config(
            server_id=server_id,
            season_id=season_id,
            config_name=config_name,
            session_entries=session_entries,
            fl_configs=fl_configs,
            db_path=db_path,
        )
        if newly_created:
            created.append(config_name)

    return created


async def _ensure_single_config(
    server_id: int,
    season_id: int,
    config_name: str,
    session_entries: dict[SessionType, dict[int, int]],
    fl_configs: dict[SessionType, tuple[int, int]],
    db_path: str,
) -> bool:
    """Create and attach *config_name* if it does not yet exist in the season.

    *session_entries* maps each SessionType to its position→points dict.
    *fl_configs* maps each SessionType that has an FL bonus to (fl_points, fl_position_limit).
    Returns True if the config was newly created, False if it already existed.
    """
    async with get_connection(db_path) as db:
        # Check if already in season_points_links
        cursor = await db.execute(
            "SELECT 1 FROM season_points_links WHERE season_id = ? AND config_name = ?",
            (season_id, config_name),
        )
        already_linked = await cursor.fetchone() is not None

        if already_linked:
            return False

        # Create server-level config store entry (idempotent)
        await db.execute(
            "INSERT OR IGNORE INTO points_config_store (server_id, config_name) VALUES (?, ?)",
            (server_id, config_name),
        )

        # Link to season
        await db.execute(
            "INSERT OR IGNORE INTO season_points_links (season_id, config_name) VALUES (?, ?)",
            (season_id, config_name),
        )

        # Write season_points_entries per session type
        for session_type, pos_map in session_entries.items():
            for pos, points in pos_map.items():
                await db.execute(
                    "INSERT OR REPLACE INTO season_points_entries "
                    "(season_id, config_name, session_type, position, points) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (season_id, config_name, session_type.value, pos, points),
                )

        # Write season_points_fl entries
        for session_type, (fl_pts, fl_pos_limit) in fl_configs.items():
            await db.execute(
                "INSERT OR REPLACE INTO season_points_fl "
                "(season_id, config_name, session_type, fl_points, fl_position_limit) "
                "VALUES (?, ?, ?, ?, ?)",
                (season_id, config_name, session_type.value, fl_pts, fl_pos_limit),
            )

        await db.commit()

    return True
