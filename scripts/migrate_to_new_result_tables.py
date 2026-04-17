"""scripts/migrate_to_new_result_tables.py

One-shot migration that copies existing driver_session_results rows into the
new qualifying_session_results and race_session_results tables.

Assumptions for each row:
  - postrace_time_penalties_ms = 0  (no prior wizard penalties carried over)
  - appeal_time_penalties_ms   = 0  (no prior appeal penalties carried over)
  - ingame_time_penalties_ms   = parsed from the existing time_penalties column

For race rows:
  P1  base_time_ms = abs_total_time_ms - ingame_time_penalties_ms
  P2+ base_time_ms = (P1_abs_total_time_ms + interval_ms) - ingame_time_penalties_ms
        where interval_ms is parsed from total_time (delta gap string)
  Lapped ("+N Laps") → base_time_ms=NULL, laps_behind=N
  DNS/DNF/DSQ        → base_time_ms=NULL, laps_behind=NULL

For qualifying rows:
  P1  best_lap = existing best_lap as-is (already absolute)
  P2+ best_lap = computed as P1_best_lap_ms + gap_ms, formatted as string
      If gap is "N/A" or unparseable use the existing best_lap value directly.

Usage:
    python scripts/migrate_to_new_result_tables.py [db_path]

If db_path is not supplied the script uses the default path from src/db/database.py.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys

# Make src/ importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from db.database import get_connection, run_migrations


# ---------------------------------------------------------------------------
# Time helpers (duplicated here so the script is self-contained)
# ---------------------------------------------------------------------------

_LAP_TIME_RE = re.compile(
    r"^(?:(?P<h>\d+):)?(?P<m>\d+):(?P<s>\d+)(?:\.(?P<ms>\d+))?$"
)
_DELTA_GAP_RE = re.compile(
    r"^\+(?:(?:(?P<h>\d+):)?(?P<m>\d+):)?(?P<s>\d+)(?:\.(?P<ms>\d+))?$"
)
_ABS_TIME_RE = re.compile(
    r"^\d+:\d{2}\.\d{3}$"
    r"|^\d+\.\d{3}$"
    r"|^\d+:\d{2}:\d{2}\.\d{3}$"
)
_DELTA_TIME_RE = re.compile(
    r"^\+\d+:\d{2}\.\d{3}$"
    r"|^\+\d+\.\d{3}$"
    r"|^\+\d+:\d{2}:\d{2}\.\d{3}$"
)
_LAP_GAP_RE = re.compile(r"^\+?(\d+) Laps?$", re.IGNORECASE)
_OUTCOME_LITERALS = frozenset({"DNS", "DNF", "DSQ"})


def _time_to_ms(s: str) -> int | None:
    m = _LAP_TIME_RE.match((s or "").strip())
    if not m:
        return None
    h = int(m.group("h") or 0)
    mins = int(m.group("m") or 0)
    secs = int(m.group("s") or 0)
    ms_raw = m.group("ms") or "0"
    ms = int(ms_raw.ljust(3, "0")[:3])
    return (h * 3600 + mins * 60 + secs) * 1000 + ms


def _delta_to_ms(s: str) -> int | None:
    m = _DELTA_GAP_RE.match((s or "").strip())
    if not m:
        return None
    h = int(m.group("h") or 0)
    mins = int(m.group("m") or 0)
    secs = int(m.group("s") or 0)
    ms_raw = m.group("ms") or "0"
    ms = int(ms_raw.ljust(3, "0")[:3])
    return (h * 3600 + mins * 60 + secs) * 1000 + ms


def _ms_to_time(ms: int) -> str:
    total_s, ms_part = divmod(ms, 1000)
    total_m, secs = divmod(total_s, 60)
    hours, mins = divmod(total_m, 60)
    if hours:
        return f"{hours}:{mins:02d}:{secs:02d}.{ms_part:03d}"
    return f"{mins}:{secs:02d}.{ms_part:03d}"


def _penalty_str_to_ms(s: str | None) -> int:
    """Parse a time-penalty string ("5.000", "1:05.000") to ms. Returns 0 for N/A or None."""
    if not s or s.strip().upper() == "N/A":
        return 0
    result = _time_to_ms(s.strip())
    return result if result is not None else 0


# ---------------------------------------------------------------------------
# Migration helpers
# ---------------------------------------------------------------------------

def _is_race(session_type: str) -> bool:
    return "RACE" in session_type.upper()


def _migrate_qualifying_session(
    session_result_id: int,
    rows: list,
) -> list[dict]:
    """Convert qualifying driver_session_results rows to qualifying_session_results dicts."""
    # Find P1's best_lap_ms for gap computation
    p1_best_lap_ms: int | None = None
    for r in rows:
        if r["finishing_position"] == 1:
            p1_best_lap_ms = _time_to_ms(r["best_lap"] or "")
            break

    result = []
    for r in rows:
        outcome = (r["outcome"] or "CLASSIFIED").upper()
        pos = r["finishing_position"]
        raw_best_lap = r["best_lap"] or ""

        # Determine stored best_lap
        if outcome in _OUTCOME_LITERALS:
            # Store outcome literal as display value
            stored_best_lap = outcome
        elif pos == 1:
            stored_best_lap = raw_best_lap  # already absolute
        else:
            # Try to compute from gap
            raw_gap = r["gap"] or "N/A"
            gap_ms = _delta_to_ms(raw_gap) if raw_gap.upper() != "N/A" else None
            if gap_ms is not None and p1_best_lap_ms is not None:
                stored_best_lap = _ms_to_time(p1_best_lap_ms + gap_ms)
            elif _ABS_TIME_RE.match(raw_best_lap):
                stored_best_lap = raw_best_lap  # already absolute in old data
            else:
                stored_best_lap = raw_best_lap  # fallback: keep as-is

        result.append({
            "session_result_id": session_result_id,
            "driver_user_id": r["driver_user_id"],
            "team_role_id": r["team_role_id"],
            "finishing_position": pos,
            "outcome": outcome,
            "tyre": r["tyre"],
            "best_lap": stored_best_lap,
            "points_awarded": r["points_awarded"] or 0,
            "driver_profile_id": r.get("driver_profile_id"),
        })
    return result


def _migrate_race_session(
    session_result_id: int,
    rows: list,
) -> list[dict]:
    """Convert race driver_session_results rows to race_session_results dicts."""
    # Find P1's absolute total_time in ms
    p1_abs_ms: int | None = None
    for r in sorted(rows, key=lambda x: x["finishing_position"]):
        ms = _time_to_ms(r["total_time"] or "")
        if ms is not None:
            p1_abs_ms = ms
            break

    result = []
    for r in rows:
        outcome = (r["outcome"] or "CLASSIFIED").upper()
        pos = r["finishing_position"]
        total_time_raw = (r["total_time"] or "").strip()
        ingame_ms = _penalty_str_to_ms(r.get("time_penalties"))

        # Determine base_time_ms and laps_behind
        base_time_ms: int | None = None
        laps_behind: int | None = None

        if outcome in _OUTCOME_LITERALS:
            # DNF/DNS/DSQ: no base time
            base_time_ms = None
        else:
            lap_m = _LAP_GAP_RE.match(total_time_raw)
            if lap_m:
                # "+N Laps" driver
                laps_behind = int(lap_m.group(1))
            else:
                abs_ms = _time_to_ms(total_time_raw)
                delta_ms = _delta_to_ms(total_time_raw)
                if abs_ms is not None:
                    # P1 or any driver with absolute time
                    base_time_ms = abs_ms - ingame_ms
                elif delta_ms is not None and p1_abs_ms is not None:
                    # P2+ with gap string
                    base_time_ms = (p1_abs_ms + delta_ms) - ingame_ms
                # else: unparseable — leave base_time_ms as None

        fastest_lap = r.get("fastest_lap")
        if fastest_lap and fastest_lap.upper() in ("N/A", ""):
            fastest_lap = None

        result.append({
            "session_result_id": session_result_id,
            "driver_user_id": r["driver_user_id"],
            "team_role_id": r["team_role_id"],
            "finishing_position": pos,
            "outcome": outcome,
            "base_time_ms": base_time_ms,
            "laps_behind": laps_behind,
            "ingame_time_penalties_ms": ingame_ms,
            "postrace_time_penalties_ms": 0,
            "appeal_time_penalties_ms": 0,
            "fastest_lap": fastest_lap,
            "fastest_lap_bonus": r.get("fastest_lap_bonus") or 0,
            "points_awarded": r.get("points_awarded") or 0,
            "driver_profile_id": r.get("driver_profile_id"),
        })
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def migrate(db_path: str) -> None:
    # Ensure migration 035 has been applied
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        # Check whether new tables already have data
        cur = await db.execute(
            "SELECT COUNT(*) AS c FROM race_session_results"
        )
        row = await cur.fetchone()
        if row and row["c"] > 0:
            print(
                f"race_session_results already has {row['c']} rows — "
                "migration appears to have already run. Aborting."
            )
            return

        # Load all session_results
        cur = await db.execute(
            "SELECT id, session_type FROM session_results WHERE status = 'ACTIVE'"
        )
        sessions = await cur.fetchall()
        print(f"Found {len(sessions)} active session(s) to migrate.")

        qual_inserted = 0
        race_inserted = 0

        for sr in sessions:
            sr_id: int = sr["id"]
            st: str = sr["session_type"]

            # Load non-superseded driver rows
            cur = await db.execute(
                """
                SELECT driver_user_id, team_role_id, finishing_position,
                       outcome, tyre, best_lap, gap, total_time, fastest_lap,
                       time_penalties, points_awarded, fastest_lap_bonus,
                       driver_profile_id
                FROM driver_session_results
                WHERE session_result_id = ? AND is_superseded = 0
                ORDER BY finishing_position
                """,
                (sr_id,),
            )
            driver_rows = [dict(r) for r in await cur.fetchall()]

            if not driver_rows:
                continue

            if _is_race(st):
                new_rows = _migrate_race_session(sr_id, driver_rows)
                for nr in new_rows:
                    await db.execute(
                        """
                        INSERT INTO race_session_results (
                            session_result_id, driver_user_id, team_role_id,
                            finishing_position, outcome, base_time_ms, laps_behind,
                            ingame_time_penalties_ms, postrace_time_penalties_ms,
                            appeal_time_penalties_ms, fastest_lap, fastest_lap_bonus,
                            points_awarded, driver_profile_id
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            nr["session_result_id"],
                            nr["driver_user_id"],
                            nr["team_role_id"],
                            nr["finishing_position"],
                            nr["outcome"],
                            nr["base_time_ms"],
                            nr["laps_behind"],
                            nr["ingame_time_penalties_ms"],
                            nr["postrace_time_penalties_ms"],
                            nr["appeal_time_penalties_ms"],
                            nr["fastest_lap"],
                            nr["fastest_lap_bonus"],
                            nr["points_awarded"],
                            nr["driver_profile_id"],
                        ),
                    )
                    race_inserted += 1
            else:
                new_rows = _migrate_qualifying_session(sr_id, driver_rows)
                for nr in new_rows:
                    await db.execute(
                        """
                        INSERT INTO qualifying_session_results (
                            session_result_id, driver_user_id, team_role_id,
                            finishing_position, outcome, tyre, best_lap,
                            points_awarded, driver_profile_id
                        ) VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            nr["session_result_id"],
                            nr["driver_user_id"],
                            nr["team_role_id"],
                            nr["finishing_position"],
                            nr["outcome"],
                            nr["tyre"],
                            nr["best_lap"],
                            nr["points_awarded"],
                            nr["driver_profile_id"],
                        ),
                    )
                    qual_inserted += 1

        await db.commit()

    print(
        f"Migration complete: {qual_inserted} qualifying row(s), "
        f"{race_inserted} race row(s) inserted."
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Migrate driver_session_results to new tables.")
    parser.add_argument(
        "db_path",
        nargs="?",
        default=None,
        help="Path to the SQLite database (default: value from db/database.py)",
    )
    args = parser.parse_args()

    if args.db_path:
        target = args.db_path
    else:
        # Fall back to the bot's default path
        import importlib
        db_mod = importlib.import_module("db.database")
        target = getattr(db_mod, "DB_PATH", None)
        if target is None:
            print("ERROR: could not determine default DB_PATH. Pass it explicitly.")
            sys.exit(1)
        print(f"Using default DB_PATH: {target}")

    asyncio.run(migrate(target))
