# Data Model: Season Archive & Driver Profile Identity

**Branch**: `024-season-archive` | **Date**: 2026-03-26

---

## Schema Changes (Migration 020)

Migration file: `src/db/migrations/020_season_archive.sql`

### 1. `seasons` — Add `game_edition`

```sql
ALTER TABLE seasons ADD COLUMN game_edition INTEGER NOT NULL DEFAULT 0;
```

| Field | Type | Constraint | Notes |
|-------|------|------------|-------|
| `game_edition` | INTEGER | NOT NULL DEFAULT 0 | Positive integer (e.g. 25 for F1 25). 0 = legacy/unset. Validated ≥ 1 at command layer. |

**Existing rows**: default to 0 (legacy — no game edition recorded). All new seasons created after this migration will carry the value entered at `/season setup`.

**State transitions**: `game_edition` is set once at setup snapshot and is immutable thereafter. A COMPLETED season retains the value unchanged.

---

### 2. `driver_session_results` — Add `driver_profile_id`

```sql
ALTER TABLE driver_session_results
    ADD COLUMN driver_profile_id INTEGER REFERENCES driver_profiles(id);
```

| Field | Type | Constraint | Notes |
|-------|------|------------|-------|
| `driver_profile_id` | INTEGER | nullable FK → `driver_profiles(id)` | Internal stable driver identifier. NULL only for legacy rows created before this migration that could not be backfilled. |

**Backfill** (applied in migration 020 after the ALTER):
```sql
UPDATE driver_session_results AS dsr
SET driver_profile_id = (
    SELECT dp.id
    FROM driver_profiles dp
    WHERE CAST(dp.discord_user_id AS INTEGER) = dsr.driver_user_id
      AND dp.server_id = (
          SELECT s.server_id
          FROM session_results sr
          JOIN rounds r  ON r.id     = sr.round_id
          JOIN divisions d ON d.id   = r.division_id
          JOIN seasons s  ON s.id    = d.season_id
          WHERE sr.id = dsr.session_result_id
      )
    LIMIT 1
)
WHERE dsr.driver_profile_id IS NULL;
```

**Write behaviour going forward**: All new inserts to `driver_session_results` MUST set `driver_profile_id` (looked up at the command boundary before the INSERT). `driver_user_id` is retained as a denormalised Discord snowflake for mention rendering; it is never used as a data-lookup key after this migration.

---

### 3. `driver_standings_snapshots` — Add `driver_profile_id`

```sql
ALTER TABLE driver_standings_snapshots
    ADD COLUMN driver_profile_id INTEGER REFERENCES driver_profiles(id);
```

| Field | Type | Constraint | Notes |
|-------|------|------------|-------|
| `driver_profile_id` | INTEGER | nullable FK → `driver_profiles(id)` | Same semantics as `driver_session_results.driver_profile_id`. |

**Backfill**:
```sql
UPDATE driver_standings_snapshots AS dss
SET driver_profile_id = (
    SELECT dp.id
    FROM driver_profiles dp
    WHERE CAST(dp.discord_user_id AS INTEGER) = dss.driver_user_id
      AND dp.server_id = (
          SELECT s.server_id
          FROM rounds r
          JOIN divisions d ON d.id = r.division_id
          JOIN seasons s ON s.id = d.season_id
          WHERE r.id = dss.round_id
      )
    LIMIT 1
)
WHERE dss.driver_profile_id IS NULL;
```

**Index** (added in migration 020 for query performance):
```sql
CREATE INDEX IF NOT EXISTS idx_dss_driver_profile
    ON driver_standings_snapshots(division_id, driver_profile_id);
CREATE INDEX IF NOT EXISTS idx_dsr_driver_profile
    ON driver_session_results(session_result_id, driver_profile_id);
```

---

## Model Changes

### `src/models/season.py` — `Season`

Add field:

```python
@dataclass
class Season:
    id: int
    server_id: int
    start_date: date
    status: SeasonStatus
    season_number: int = 0
    game_edition: int = 0       # ← NEW: positive integer, 0 = legacy
```

Update `_row_to_season` helper to read column index 5 (`game_edition`).

---

### `src/models/session_result.py` — `DriverSessionResult`

Add field:

```python
@dataclass
class DriverSessionResult:
    ...
    driver_profile_id: int | None = None   # ← NEW: FK to driver_profiles.id
```

The existing `driver_user_id: int` field is retained for display-name resolution.

---

### `src/models/standings_snapshot.py` — `DriverStandingsSnapshot`

Add field:

```python
@dataclass
class DriverStandingsSnapshot:
    ...
    driver_profile_id: int | None = None   # ← NEW: FK to driver_profiles.id
```

The existing `driver_user_id: int` field is retained for Discord mention rendering.

---

## Entity Relationships (affected tables)

```
server_configs (server_id PK)
  └── seasons [game_edition NEW] (server_id FK)
        └── divisions (season_id FK)
              └── rounds (division_id FK)
                    ├── session_results (round_id FK)
                    │     └── driver_session_results [driver_profile_id NEW] (session_result_id FK)
                    └── driver_standings_snapshots [driver_profile_id NEW] (round_id FK)

driver_profiles (id PK, discord_user_id)
  └── driver_season_assignments (driver_profile_id FK)
  └── driver_session_results.driver_profile_id  ← NEW FK
  └── driver_standings_snapshots.driver_profile_id  ← NEW FK
  └── driver_history_entries (driver_profile_id FK)
```

---

## Entities — Unchanged Schema, Changed Semantics

### Season (status = COMPLETED)

Once a Season's `status` is set to `COMPLETED`, all of the following become **read-only** (enforced at service layer via `assert_season_mutable`):

- `seasons` row itself
- `divisions` rows for that season
- `rounds` rows under those divisions
- `sessions` rows under those rounds
- `session_results` rows under those rounds
- `driver_session_results` rows under those session results
- `driver_standings_snapshots` rows referencing those rounds
- `driver_season_assignments` rows for that season
- `team_instances` and `team_seats` for that season's divisions
- `season_points_links`, `season_points_entries`, `season_points_fl` for that season
- `season_amendment_state` and `season_modification_entries` for that season

No schema-level constraint enforces this; enforcement is at the application/service layer only.

### DriverHistoryEntry (existing — write semantics codified)

Written at season completion (before `status` flips to COMPLETED) for every ASSIGNED driver. Sources:
- `season_number`: from `seasons.season_number`
- `division_name`, `division_tier`: from the `divisions` row
- `final_position`, `final_points`: from the most recent `driver_standings_snapshots` for that driver × division
- `points_gap_to_winner`: derived from final points vs. the season winner's final points

---

## Validation Rules

| Entity | Rule | Enforcement Layer |
|--------|------|------------------|
| `seasons.game_edition` | Must be ≥ 1 for all seasons created after migration 020 | Command layer (`season_setup` param validation) |
| `seasons.status` | COMPLETED → no further transitions allowed | Service layer (`assert_season_mutable`) |
| `driver_session_results.driver_profile_id` | Must be non-NULL for all rows inserted after migration 020 | Service layer (resolve before INSERT) |
| `driver_standings_snapshots.driver_profile_id` | Must be non-NULL for all rows inserted after migration 020 | Service layer (resolve before INSERT) |
| Season setup | Must be rejected if any season for the server is in SETUP or ACTIVE state | Service layer (`has_active_or_setup_season`) |
| Season number | Derived as `COUNT(COMPLETED seasons) + 1` at first snapshot; never re-derived | Service layer (`save_pending_snapshot`) |

---

## State Transitions

### Season lifecycle (updated)

```
[no season] → SETUP → ACTIVE → COMPLETED
                ↑               (immutable, permanent)
         (re-setup allowed
          only if all existing
          seasons COMPLETED)
```

The COMPLETED state is **terminal**. There is no COMPLETED → anything transition.

### game_edition

Set once: `SETUP` snapshot. Carried unchanged through `ACTIVE` → `COMPLETED`. No transitions.
