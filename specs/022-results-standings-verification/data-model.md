# Data Model: Results & Standings — Standings Design, Sync Command, and Sort-Key Correction

**Feature branch**: `022-results-standings-verification`
**Date**: 2026-03-23

---

## Overview

No new database tables, columns, or migrations are required for this feature. All work is a correction to computation logic and the addition of one command. The entities listed below are the **existing** ones whose interaction is being corrected or newly invoked.

---

## Entities (existing, no schema change)

### DriverStandingsSnapshot

**Table**: `driver_standings_snapshots`

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | Auto-increment |
| `round_id` | INTEGER FK → `rounds.id` | Round this snapshot belongs to |
| `division_id` | INTEGER FK → `divisions.id` | Division scope |
| `driver_user_id` | INTEGER | Discord User ID |
| `standing_position` | INTEGER | 1-indexed rank |
| `total_points` | INTEGER | All sessions (race + qualifying) |
| `finish_counts` | TEXT (JSON) | `{"1": n, "3": m, ...}` — Feature Race finishes only |
| `first_finish_rounds` | TEXT (JSON) | `{"1": round_number, ...}` — Feature Race; earliest round per position |
| `standings_message_id` | INTEGER NULL | Discord message ID of the standings post; only stored on the P1 row |

**Invariant being corrected**: finish-count and first-finish-round vectors must be compared at a uniform length equal to the global maximum finishing position across all drivers in the snapshot computation, not per-driver.

---

### TeamStandingsSnapshot

**Table**: `team_standings_snapshots`

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | Auto-increment |
| `round_id` | INTEGER FK → `rounds.id` | |
| `division_id` | INTEGER FK → `divisions.id` | |
| `team_role_id` | INTEGER | Discord role ID of the team |
| `standing_position` | INTEGER | 1-indexed rank |
| `total_points` | INTEGER | Sprint Race + Feature Race sessions |
| `finish_counts` | TEXT (JSON) | Feature Race finishes only |
| `first_finish_rounds` | TEXT (JSON) | Feature Race earliest round per position |

**Same invariant applies**: vectors must use `global_max_pos` across all teams.

---

### DriverSessionResult

**Table**: `driver_session_results`

| Column | Type | Notes |
|--------|------|-------|
| `driver_user_id` | INTEGER | Stays unchanged on team re-assignment |
| `team_role_id` | INTEGER | Stamped at submission; never mutated retroactively |
| ... | ... | All other columns unchanged |

**Invariant**: `team_role_id` in historic session results is never modified by a driver's team re-assignment. Driver standings aggregate all rows for `driver_user_id` regardless of current team.

---

### DivisionResultsConfig

**Table**: `division_results_config`

| Column | Type | Notes |
|--------|------|-------|
| `division_id` | INTEGER PK FK → `divisions.id` | |
| `reserves_in_standings` | INTEGER (boolean) | Default 1 (visible) |

**Usage**: read by `_get_show_reserves()` to determine whether reserve drivers appear in posted standings output. Written by `/results reserves toggle <division>`.

---

## Sort-Key Logic Change (service-layer only)

### Before (defective)

```python
def _sort_key(uid):
    fc = finish_counts.get(uid, {})
    ffr = first_finish_rounds.get(uid, {})
    max_pos = max(fc.keys(), default=0)          # ← per-entity, causes wrong comparison
    count_vec = tuple(-fc.get(p, 0) for p in range(1, max_pos + 1))
    first_vec = tuple(ffr.get(p, 999999) for p in range(1, max_pos + 1))
    return (-total_points.get(uid, 0), count_vec, first_vec)
```

### After (correct)

```python
# Computed ONCE before any sort call:
global_max_pos = max(
    (max(fc.keys(), default=0) for fc in finish_counts.values()),
    default=0,
)

def _sort_key(uid):
    fc = finish_counts.get(uid, {})
    ffr = first_finish_rounds.get(uid, {})
    count_vec = tuple(-fc.get(p, 0) for p in range(1, global_max_pos + 1))
    first_vec = tuple(ffr.get(p, 999999) for p in range(1, global_max_pos + 1))
    return (-total_points.get(uid, 0), count_vec, first_vec)
```

This fix is applied identically to both `compute_driver_standings` and `compute_team_standings`.

---

## New Service Function

### `repost_standings_for_division(db_path, division_id, guild)` → `results_post_service.py`

**Purpose**: Recompute and repost the latest standings for a division without a new round submission. Used by `/results standings sync`.

**Logic**:
1. Query `rounds` for the most recent completed round in the division (`round_number` DESC, limit 1).
2. If none found, return `None` (caller signals "no data").
3. Call `compute_driver_standings(db_path, division_id, last_round_id)`.
4. Call `compute_team_standings(db_path, division_id, last_round_id)`.
5. Fetch `standings_channel_id` from `division_results_config` (or `divisions` table — confirm column location).
6. Fetch `show_reserves` via `_get_show_reserves(db_path, division_id)`.
7. Call `post_standings(db_path, division_id, last_round_id, channel, driver_snaps, team_snaps, guild, show_reserves)`.

**Returns**: `bool` — `True` if posted, `False` if no completed rounds exist.

---

## Validation Rules (unchanged)

- Finish counts and first-achieved-round maps only include Feature Race sessions (enforced by the `session_type IS 'FEATURE_RACE'` filter in the SQL query — no change).
- Total points include all non-superseded, ACTIVE session results across all session types (enforced by the existing query — no change).
- Sprint Race points contribute to totals but never to countback (enforced by the SQL `session_type IN ('FEATURE_RACE', 'SPRINT_RACE')` filter for team totals with Feature-Race-only countback inside the loop — no change).
