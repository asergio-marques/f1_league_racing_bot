# Data Model: Results & Standings — Points Config, Submission, and Standings

**Feature**: `019-results-submission-standings`  
**Date**: 2026-03-18  
**Migration file**: `src/db/migrations/017_results_core.sql`

---

## Entities from v2.4.0 Constitution (full definitions)

The following entities were ratified in the constitution v2.4.0 and are introduced by this feature.

---

### PointsConfigStore

Server-scoped named configuration record. The `config_name` is the unique identifier within a server.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | INTEGER | PK AUTOINCREMENT | Internal ID |
| `server_id` | INTEGER | NOT NULL, FK → `server_configs(server_id)` ON DELETE CASCADE | Owning server |
| `config_name` | TEXT | NOT NULL | User-chosen name, e.g. `"100%"` |
| — | — | UNIQUE(`server_id`, `config_name`) | Names are unique per server |

---

### PointsConfigEntry

Points for one finishing position in one session type within one named server config. Unspecified positions default to 0.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | INTEGER | PK AUTOINCREMENT | |
| `config_id` | INTEGER | NOT NULL, FK → `points_config_store(id)` ON DELETE CASCADE | Parent config |
| `session_type` | TEXT | NOT NULL | One of: `SPRINT_QUALIFYING`, `SPRINT_RACE`, `FEATURE_QUALIFYING`, `FEATURE_RACE` |
| `position` | INTEGER | NOT NULL | 1-indexed finishing position |
| `points` | INTEGER | NOT NULL DEFAULT 0 | Points awarded |
| — | — | UNIQUE(`config_id`, `session_type`, `position`) | One row per position per session per config |

---

### PointsConfigFastestLap

Fastest-lap bonus for a race session type within one named server config. Only valid for `SPRINT_RACE` and `FEATURE_RACE`.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | INTEGER | PK AUTOINCREMENT | |
| `config_id` | INTEGER | NOT NULL, FK → `points_config_store(id)` ON DELETE CASCADE | Parent config |
| `session_type` | TEXT | NOT NULL | `SPRINT_RACE` or `FEATURE_RACE` only |
| `fl_points` | INTEGER | NOT NULL DEFAULT 0 | Bonus points for fastest lap |
| `fl_position_limit` | INTEGER | NULL | Max position eligible (e.g. 10 → positions 1–10 eligible); NULL means no limit |
| — | — | UNIQUE(`config_id`, `session_type`) | One row per race session per config |

---

### SeasonPointsLink *(already exists as `season_points_links` from migration 016)*

Weak-link table attaching server-level config names to a season in SETUP. Discarded conceptually on season approval (the `season_points_entries` snapshot is the source of truth post-approval); the rows may remain but are not used after approval.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | INTEGER | PK AUTOINCREMENT | |
| `season_id` | INTEGER | NOT NULL, FK → `seasons(id)` ON DELETE CASCADE | |
| `config_name` | TEXT | NOT NULL | Matches a `PointsConfigStore.config_name` for the same server |
| — | — | UNIQUE(`season_id`, `config_name`) | |

---

### SeasonPointsStore

Immutable snapshot of config entries scoped to an approved season. Written atomically at season approval time. Overwritten atomically on mid-season amendment approval.

**season_points_entries** (per-position breakdowns):

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | INTEGER | PK AUTOINCREMENT | |
| `season_id` | INTEGER | NOT NULL, FK → `seasons(id)` ON DELETE CASCADE | |
| `config_name` | TEXT | NOT NULL | |
| `session_type` | TEXT | NOT NULL | Same enum as PointsConfigEntry |
| `position` | INTEGER | NOT NULL | |
| `points` | INTEGER | NOT NULL DEFAULT 0 | |
| — | — | UNIQUE(`season_id`, `config_name`, `session_type`, `position`) | |

**season_points_fl** (fastest-lap overrides):

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | INTEGER | PK AUTOINCREMENT | |
| `season_id` | INTEGER | NOT NULL, FK → `seasons(id)` ON DELETE CASCADE | |
| `config_name` | TEXT | NOT NULL | |
| `session_type` | TEXT | NOT NULL | `SPRINT_RACE` or `FEATURE_RACE` only |
| `fl_points` | INTEGER | NOT NULL DEFAULT 0 | |
| `fl_position_limit` | INTEGER | NULL | |
| — | — | UNIQUE(`season_id`, `config_name`, `session_type`) | |

---

### SeasonAmendmentState

Per-season record tracking amendment mode and uncommitted changes.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `season_id` | INTEGER | PK, FK → `seasons(id)` ON DELETE CASCADE | One row per season |
| `amendment_active` | INTEGER | NOT NULL DEFAULT 0 | 1 = amendment mode on |
| `modified_flag` | INTEGER | NOT NULL DEFAULT 0 | 1 = modification store has uncommitted changes; toggle-off blocked while 1 |

---

### SeasonModificationStore

Working copy of the season points store, present only while amendment mode is active. Discarded on revert or approval.

**season_modification_entries**:

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | INTEGER | PK AUTOINCREMENT | |
| `season_id` | INTEGER | NOT NULL, FK → `seasons(id)` ON DELETE CASCADE | |
| `config_name` | TEXT | NOT NULL | |
| `session_type` | TEXT | NOT NULL | |
| `position` | INTEGER | NOT NULL | |
| `points` | INTEGER | NOT NULL DEFAULT 0 | |
| — | — | UNIQUE(`season_id`, `config_name`, `session_type`, `position`) | |

**season_modification_fl**:

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | INTEGER | PK AUTOINCREMENT | |
| `season_id` | INTEGER | NOT NULL, FK → `seasons(id)` ON DELETE CASCADE | |
| `config_name` | TEXT | NOT NULL | |
| `session_type` | TEXT | NOT NULL | |
| `fl_points` | INTEGER | NOT NULL DEFAULT 0 | |
| `fl_position_limit` | INTEGER | NULL | |
| — | — | UNIQUE(`season_id`, `config_name`, `session_type`) | |

---

### SessionResult

Top-level result record per session per round per division.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | INTEGER | PK AUTOINCREMENT | |
| `round_id` | INTEGER | NOT NULL, FK → `rounds(id)` ON DELETE CASCADE | |
| `division_id` | INTEGER | NOT NULL, FK → `divisions(id)` | Denormalised for query convenience |
| `session_type` | TEXT | NOT NULL | `SPRINT_QUALIFYING`, `SPRINT_RACE`, `FEATURE_QUALIFYING`, `FEATURE_RACE` |
| `status` | TEXT | NOT NULL DEFAULT `'ACTIVE'` | `ACTIVE` or `CANCELLED` |
| `config_name` | TEXT | NULL | Points config chosen; NULL for CANCELLED sessions |
| `submitted_by` | INTEGER | NULL | Discord user ID of submitting admin |
| `submitted_at` | TEXT | NULL | ISO-8601 timestamp |
| — | — | UNIQUE(`round_id`, `session_type`) | One session per type per round |

---

### DriverSessionResult

Per-driver row within a SessionResult.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | INTEGER | PK AUTOINCREMENT | |
| `session_result_id` | INTEGER | NOT NULL, FK → `session_results(id)` ON DELETE CASCADE | |
| `driver_user_id` | INTEGER | NOT NULL | Discord user ID |
| `team_role_id` | INTEGER | NOT NULL | Discord role ID of team driven for |
| `finishing_position` | INTEGER | NOT NULL | 1-indexed |
| `outcome` | TEXT | NOT NULL DEFAULT `'CLASSIFIED'` | `CLASSIFIED`, `DNF`, `DNS`, `DSQ` |
| `tyre` | TEXT | NULL | Qualifying sessions only |
| `best_lap` | TEXT | NULL | Qualifying: best lap time string; also used for fastest_lap in race when pre-DNF |
| `gap` | TEXT | NULL | Qualifying: gap to 1st |
| `total_time` | TEXT | NULL | Race sessions only |
| `fastest_lap` | TEXT | NULL | Race sessions only |
| `time_penalties` | TEXT | NULL | Race sessions only |
| `post_steward_total_time` | TEXT | NULL | Future use; NULL unless penalty applied |
| `post_race_time_penalties` | TEXT | NULL | Future use; NULL unless penalty applied |
| `points_awarded` | INTEGER | NOT NULL DEFAULT 0 | Computed finishing-position points |
| `fastest_lap_bonus` | INTEGER | NOT NULL DEFAULT 0 | 0 or fl_points value |
| `is_superseded` | INTEGER | NOT NULL DEFAULT 0 | 1 when this row is replaced by a full re-entry amendment |

---

### DriverStandingsSnapshot

Standings state per driver per round per division. One row per `(round_id, division_id, driver_user_id)`.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | INTEGER | PK AUTOINCREMENT | |
| `round_id` | INTEGER | NOT NULL, FK → `rounds(id)` ON DELETE CASCADE | |
| `division_id` | INTEGER | NOT NULL, FK → `divisions(id)` | |
| `driver_user_id` | INTEGER | NOT NULL | |
| `standing_position` | INTEGER | NOT NULL | Rank in division at this round |
| `total_points` | INTEGER | NOT NULL DEFAULT 0 | Cumulative to this round |
| `finish_counts` | TEXT | NOT NULL DEFAULT `'{}'` | JSON: `{"1": 2, "3": 1, …}` — Feature Race finish counts only |
| `first_finish_rounds` | TEXT | NOT NULL DEFAULT `'{}'` | JSON: `{"1": 3, "3": 7, …}` — earliest round where each position was first achieved |
| — | — | UNIQUE(`round_id`, `division_id`, `driver_user_id`) | |

---

### TeamStandingsSnapshot

Same structure as DriverStandingsSnapshot, keyed by team role instead of driver.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | INTEGER | PK AUTOINCREMENT | |
| `round_id` | INTEGER | NOT NULL, FK → `rounds(id)` ON DELETE CASCADE | |
| `division_id` | INTEGER | NOT NULL, FK → `divisions(id)` | |
| `team_role_id` | INTEGER | NOT NULL | Discord role ID |
| `standing_position` | INTEGER | NOT NULL | |
| `total_points` | INTEGER | NOT NULL DEFAULT 0 | |
| `finish_counts` | TEXT | NOT NULL DEFAULT `'{}'` | Feature Race finish counts JSON |
| `first_finish_rounds` | TEXT | NOT NULL DEFAULT `'{}'` | JSON |
| — | — | UNIQUE(`round_id`, `division_id`, `team_role_id`) | |

---

### RoundSubmissionChannel *(operational tracking only)*

Tracks the transient Discord channel created at each round's start.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | INTEGER | PK AUTOINCREMENT | |
| `round_id` | INTEGER | NOT NULL, FK → `rounds(id)` ON DELETE CASCADE | |
| `channel_id` | INTEGER | NOT NULL | Discord channel snowflake |
| `created_at` | TEXT | NOT NULL | ISO-8601 |
| `closed` | INTEGER | NOT NULL DEFAULT 0 | 1 when all sessions submitted/cancelled |
| — | — | UNIQUE(`round_id`) | One submission channel per round max |

---

## Entity Relationships

```text
server_configs
    ↓ (1:N)
points_config_store
    ↓ (1:N)
    ├── points_config_entries        (per position per session)
    └── points_config_fl             (per race session)

seasons
    ↓ (1:N — weak link, pre-approval)
season_points_links                  (config_name references)

seasons
    ↓ (1:N — snapshot, post-approval)
    ├── season_points_entries        (copied from server store on approval)
    ├── season_points_fl             (copied from server store on approval)
    ├── season_amendment_state       (1:1 per season)
    ├── season_modification_entries  (working copy during amendment mode)
    └── season_modification_fl

divisions
    ↓ (1:N)
rounds
    ↓ (1:N)
    ├── session_results              (one per session type per round)
    │       ↓ (1:N)
    │       driver_session_results   (one per driver per session)
    ├── driver_standings_snapshots   (one per driver per round per division)
    ├── team_standings_snapshots     (one per team per round per division)
    └── round_submission_channels    (1:1 operational record)
```

---

## Validation Rules

| Entity | Rule | Enforcement Point |
|--------|------|-------------------|
| `PointsConfigEntry` | Positions must be monotonically non-increasing in points within a config+session group | Season approval gate (`season_cog._do_approve`) |
| `PointsConfigFastestLap` | `session_type` must be `SPRINT_RACE` or `FEATURE_RACE` | Service-level rejection with error message |
| `DriverSessionResult.outcome` | `DNF`: eligible for FL bonus (if position limit met), not for position points | `standings_service.compute_points` |
| `DriverSessionResult.outcome` | `DNS`/`DSQ`: ineligible for both position points and FL bonus | `standings_service.compute_points` |
| `SessionResult` | CANCELLED sessions carry no `DriverSessionResult` rows and no `config_name` | `result_submission_service.accept_session` |
| `SeasonAmendmentState` | `amendment_active` may not be set to 0 while `modified_flag = 1` | `amendment_service.disable_amendment_mode` |
| `round_submission_channels` | `round cancel` is rejected while a row exists with `closed = 0` for that round | `season_service._cancel_round` guard |

---

## State Transitions

### SessionResult.status

```
(round start)
    → PENDING_INPUT   [in-memory / submission open]
    → ACTIVE          [results accepted, config chosen]
    → CANCELLED       [admin submitted "CANCELLED"]
```

### SeasonAmendmentState

```
amendment_active=0, modified_flag=0  (default)
    → [admin toggles on]
amendment_active=1, modified_flag=0
    → [trusted admin modifies store]
amendment_active=1, modified_flag=1
    → [admin reverts]       → amendment_active=1, modified_flag=0
    → [admin approves]      → amendment_active=0, modified_flag=0, store overwritten
    → [toggle-off attempt while modified_flag=1] → REJECTED
```
