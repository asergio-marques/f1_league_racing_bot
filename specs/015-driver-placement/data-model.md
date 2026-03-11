# Data Model: Driver Placement and Team Role Configuration

**Feature**: `015-driver-placement` | **Phase**: 1

---

## Modified Entities

### `signup_records` (existing table — additive column)

**Change**: Add `total_lap_ms INTEGER` (nullable).  
**When set**: Computed and written atomically at signup approval (transition to Unassigned), in the same transaction that commits the driver's state change. Value = sum of each lap time in `lap_times_json` converted to milliseconds. `NULL` if `lap_times_json` is empty or absent (driver signed up with no configured tracks).  
**Why nullable**: Backfill for existing rows is not required; existing drivers without a value remain `NULL` and are sorted last in the seeding query.

```sql
ALTER TABLE signup_records ADD COLUMN total_lap_ms INTEGER;
```

**Python model addition** (`src/models/signup_module.py`, `SignupRecord`):
```python
total_lap_ms: int | None = None
```

---

### `driver_season_assignments` (existing table — additive column)

**Change**: Add `team_seat_id INTEGER REFERENCES team_seats(id)` (nullable at DB level; application enforces NOT NULL on new inserts).  
**Purpose**: Links a season assignment record directly to the exact seat occupied, enabling efficient seat-availability checks and clean seat-freeing on unassignment/sack.

```sql
ALTER TABLE driver_season_assignments ADD COLUMN team_seat_id INTEGER REFERENCES team_seats(id);
```

**Python model change** (`src/models/driver_profile.py`, `DriverSeasonAssignment`):
```python
@dataclass
class DriverSeasonAssignment:
    id: int
    driver_profile_id: int
    season_id: int
    division_id: int
    team_seat_id: int | None       # NEW — nullable for legacy rows
    current_position: int
    current_points: int
    points_gap_to_first: int
```

---

## New Entities

### `team_role_configs` (new table)

**Purpose**: Server-scoped map of team name → Discord role ID. Persists across seasons. One row per team per server. INSERT OR REPLACE semantics on overwrite.

```sql
CREATE TABLE IF NOT EXISTS team_role_configs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id   INTEGER NOT NULL
                    REFERENCES server_configs(server_id)
                    ON DELETE CASCADE,
    team_name   TEXT    NOT NULL,
    role_id     INTEGER NOT NULL,
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(server_id, team_name)
);
```

**Python model** (new dataclass in `src/models/team.py`):
```python
@dataclass
class TeamRoleConfig:
    id: int
    server_id: int
    team_name: str
    role_id: int
    updated_at: str
```

**Key operations**:
- `get_team_role_config(server_id, team_name) → TeamRoleConfig | None`
- `set_team_role_config(server_id, team_name, role_id) → None` — INSERT OR REPLACE; writes `updated_at = datetime('now')`.
- `get_all_team_role_configs(server_id) → list[TeamRoleConfig]` — for bulk role lookup during sack.

---

## Migration

**File**: `src/db/migrations/011_driver_placement.sql`

```sql
-- ==================================================================
-- 011_driver_placement.sql
-- Adds total_lap_ms to signup_records, team_seat_id to
-- driver_season_assignments, and creates team_role_configs.
-- ==================================================================

PRAGMA foreign_keys = OFF;

-- 1. Seeding value (computed at approval, stored for O(1) lookup)
ALTER TABLE signup_records
    ADD COLUMN total_lap_ms INTEGER;

-- 2. Direct seat FK on season assignments
ALTER TABLE driver_season_assignments
    ADD COLUMN team_seat_id INTEGER REFERENCES team_seats(id);

-- 3. Team → Discord role mapping (server-scoped)
CREATE TABLE IF NOT EXISTS team_role_configs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id   INTEGER NOT NULL
                    REFERENCES server_configs(server_id)
                    ON DELETE CASCADE,
    team_name   TEXT    NOT NULL,
    role_id     INTEGER NOT NULL,
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(server_id, team_name)
);

PRAGMA foreign_keys = ON;
```

---

## Entity Relationships

```
server_configs (1)
  ├── (∞) team_role_configs   [team_name → role_id, server-scoped]
  └── (∞) driver_profiles
              └── (∞) driver_season_assignments
                            ├── → seasons(id)
                            ├── → divisions(id)       [mention_role_id = division role]
                            └── → team_seats(id)      [NEW: team_seat_id]
                                        └── → team_instances(id)
                                                    └── name → team_role_configs.team_name

signup_records (1 per driver per server)
  ├── total_lap_ms (NEW: computed at approval)
  └── lap_times_json  (source for total_lap_ms computation)
```

---

## Validation Rules (from spec FR)

| Entity | Field | Rule |
|--------|-------|------|
| `team_role_configs` | `(server_id, team_name)` | UNIQUE — only one role per team per server |
| `team_role_configs` | mutation | Blocked while any season is ACTIVE (enforced at application layer) |
| `driver_season_assignments` | `(driver_profile_id, season_id, division_id)` | UNIQUE (existing constraint) — max 1 assignment per driver per division per season |
| `driver_season_assignments` | `team_seat_id` | NOT NULL on new inserts (application guard); nullable for legacy rows |
| `signup_records` | `total_lap_ms` | NULL = no lap times; non-null = sum in milliseconds ≥ 0 |
| `team_seats` | `driver_profile_id` | Set to driver's profile `id` on assignment; set to NULL on unassignment/sack |

---

## State Transitions Triggered by This Feature

| Command | Profile State Change | SeasonAssignment | TeamSeat | Roles |
|---------|---------------------|------------------|----------|-------|
| `/driver assign` (from Unassigned) | Unassigned → Assigned | CREATE row | SET `driver_profile_id` | GRANT division + team role |
| `/driver assign` (from Assigned) | Assigned → Assigned (no change) | CREATE row | SET `driver_profile_id` | GRANT division + team role |
| `/driver unassign` (last seat) | Assigned → Unassigned | DELETE row | CLEAR `driver_profile_id` | REVOKE division role; REVOKE team role if no other seat for that team |
| `/driver unassign` (not last seat) | Assigned → Assigned (no change) | DELETE row | CLEAR `driver_profile_id` | REVOKE division role; REVOKE team role if no other seat for that team |
| `/driver sack` | Unassigned/Assigned → Not Signed Up | DELETE all rows | CLEAR all | REVOKE all division + team roles |
