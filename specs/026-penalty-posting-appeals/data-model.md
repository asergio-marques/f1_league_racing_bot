# Data Model: Penalty Posting, Appeals, and Result Lifecycle

**Feature**: 026-penalty-posting-appeals  
**Date**: 2026-03-30  
**Migration file**: `src/db/migrations/026_result_status_penalty_records.sql`

---

## Entity Changes

### Round (amended)

**Table**: `rounds`

| Change | Before | After |
|--------|--------|-------|
| `finalized` | `INTEGER NOT NULL DEFAULT 0` | Retained (inert after migration) |
| `result_status` | *(absent)* | `TEXT NOT NULL DEFAULT 'PROVISIONAL'` |

**Valid values for `result_status`**: `PROVISIONAL`, `POST_RACE_PENALTY`, `FINAL`

**State transitions**:

```
PROVISIONAL
    │
    ▼  (penalty review Approve clicked)
POST_RACE_PENALTY
    │
    ▼  (appeals review Approve clicked  OR  round results amend applied)
FINAL
```

**Migration data rule**: `UPDATE rounds SET result_status = 'FINAL' WHERE finalized = 1`  
All other rows default to `PROVISIONAL` via the column default.

**Model change** (`src/models/round.py`):  
`finalized: bool = False` → `result_status: str = "PROVISIONAL"`

---

### PenaltyRecord (new)

**Table**: `penalty_records`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| `driver_session_result_id` | INTEGER | NOT NULL, FK → `driver_session_results(id)` | The result row this penalty modifies |
| `penalty_type` | TEXT | NOT NULL | `TIME_PENALTY` or `DSQ` |
| `time_seconds` | INTEGER | NULL | Signed integer; positive = time removed, negative = time added; NULL for DSQ |
| `description` | TEXT | NOT NULL | Admin-entered description of the ruling |
| `justification` | TEXT | NOT NULL | Admin-entered justification |
| `applied_by` | TEXT | NOT NULL | Discord user ID of approving admin |
| `applied_at` | TEXT | NOT NULL | UTC ISO-8601 timestamp |
| `announcement_channel_id` | TEXT | NULL | Channel where announcement was posted (NULL if skipped) |

**Notes**:
- `penalty_type = 'TIME_PENALTY'` and `time_seconds` encodes sign: `+5` seconds = 5 (removes 5s from driver time), `-3` seconds = -3 (adds 3s back).
- `penalty_type = 'DSQ'` sets `time_seconds = NULL`.
- Replaces the loose `post_race_time_penalties` / `post_stewarding_total_time` text fields on `driver_session_results` for all new records. Existing rows retain their legacy columns (no migration required on existing data).

---

### AppealRecord (new)

**Table**: `appeal_records`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| `driver_session_result_id` | INTEGER | NOT NULL, FK → `driver_session_results(id)` | The result row this appeal correction modifies |
| `penalty_record_id` | INTEGER | NULL, FK → `penalty_records(id)` | Associated penalty, if correction targets a specific one |
| `status` | TEXT | NOT NULL DEFAULT 'UPHELD' | `UPHELD` (correction applied) or `OVERTURNED` (no change; reserved for future use) |
| `penalty_type` | TEXT | NOT NULL | `TIME_PENALTY` or `DSQ` — the corrected value |
| `time_seconds` | INTEGER | NULL | Corrected signed seconds; NULL for DSQ; same sign convention as PenaltyRecord |
| `description` | TEXT | NOT NULL | Admin-entered description of the appeal ruling |
| `justification` | TEXT | NOT NULL | Admin-entered justification |
| `submitted_by` | TEXT | NOT NULL | Discord user ID of approving admin |
| `submitted_at` | TEXT | NOT NULL | UTC ISO-8601 timestamp |
| `announcement_channel_id` | TEXT | NULL | Channel where announcement was posted |

**Notes**:
- For spec 026, the appeals wizard is admin-driven (not driver-initiated). The `status` column defaults to `UPHELD` for every staged correction applied. `OVERTURNED` is reserved for a future driver-initiated appeals flow where a driver appeals and the admin upholds the original decision (no result change).
- In this increment, only applied corrections are stored (one row per applied appeal correction). The admin stages corrections → approves → all are stored as `UPHELD`.

---

### DivisionResultsConfig (amended)

**Table**: `division_results_configs`

| Change | Before | After |
|--------|--------|-------|
| `penalty_channel_id` | *(absent)* | `TEXT NULL` — Discord channel ID for verdict announcements |

**Population**: Set via `/division verdicts-channel <division> <channel>`.  
**Fallback**: If `NULL`, the bot uses `results_channel_id` for all verdict announcements.

**Season review / approval impact**: `season_service.get_divisions_with_results_config()` must extend its SELECT and LEFT JOIN to include `drc.penalty_channel_id` so that `season_cog` can display it in `/season review` and enforce it as a blocker in `/season approve` Gate 2.

## StagedPenalty dataclass changes (in-memory only)

`StagedPenalty` in `penalty_service.py` gains two new fields:

```python
@dataclass
class StagedPenalty:
    driver_id: int
    penalty_str: str      # existing: raw modal input e.g. "+5s", "-3s", "DSQ"
    description: str      # NEW: verbatim from modal
    justification: str    # NEW: verbatim from modal
```

This is a transient in-memory object; no migration needed.

---

## Migration SQL

**File**: `src/db/migrations/026_result_status_penalty_records.sql`

```sql
-- Add result_status to rounds
ALTER TABLE rounds ADD COLUMN result_status TEXT NOT NULL DEFAULT 'PROVISIONAL';

-- Populate result_status from existing finalized flag
UPDATE rounds SET result_status = 'FINAL' WHERE finalized = 1;

-- Add penalty_channel_id to division_results_configs
ALTER TABLE division_results_configs ADD COLUMN penalty_channel_id TEXT;

-- penalty_records: stores each applied penalty from the wizard
CREATE TABLE IF NOT EXISTS penalty_records (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    driver_session_result_id INTEGER NOT NULL REFERENCES driver_session_results(id),
    penalty_type             TEXT    NOT NULL,
    time_seconds             INTEGER,
    description              TEXT    NOT NULL,
    justification            TEXT    NOT NULL,
    applied_by               TEXT    NOT NULL,
    applied_at               TEXT    NOT NULL,
    announcement_channel_id  TEXT
);

-- appeal_records: stores each applied appeal correction from the appeals wizard
CREATE TABLE IF NOT EXISTS appeal_records (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    driver_session_result_id INTEGER NOT NULL REFERENCES driver_session_results(id),
    penalty_record_id        INTEGER REFERENCES penalty_records(id),
    status                   TEXT    NOT NULL DEFAULT 'UPHELD',
    penalty_type             TEXT    NOT NULL,
    time_seconds             INTEGER,
    description              TEXT    NOT NULL,
    justification            TEXT    NOT NULL,
    submitted_by             TEXT    NOT NULL,
    submitted_at             TEXT    NOT NULL,
    announcement_channel_id  TEXT
);
```

---

## Validation Rules

| Field | Rule |
|-------|------|
| `result_status` | Must be one of exactly: `PROVISIONAL`, `POST_RACE_PENALTY`, `FINAL` (enforced in application layer, not DB constraint) |
| `penalty_type` | Must be one of exactly: `TIME_PENALTY`, `DSQ` |
| `time_seconds` for TIME_PENALTY | Non-zero signed integer (enforced by existing `validate_penalty_input`) |
| `time_seconds` for DSQ | Must be NULL |
| `description` | Non-empty string, max 200 characters (enforced by Discord modal's `max_length=200, required=True`) |
| `justification` | Non-empty string, max 200 characters (enforced by Discord modal's `max_length=200, required=True`) |
| `penalty_channel_id` | Valid Discord channel ID accessible by the bot (validated at command time via `bot.get_channel()`) |
