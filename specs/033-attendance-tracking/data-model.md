# Data Model: Attendance Tracking — 033

**Branch**: `033-attendance-tracking`  
**Phase**: 1 — Design  
**Date**: 2026-04-03

---

## 1. Migration 032 — `032_attendance_tracking.sql`

**File**: `src/db/migrations/032_attendance_tracking.sql`

```sql
-- Migration 032: Attendance tracking — finalization columns, sheet message ID,
--                and attendance pardons table.

-- 1. Add finalization output columns to driver_round_attendance.
--    Both are nullable until the penalty-finalization pipeline runs.
ALTER TABLE driver_round_attendance ADD COLUMN points_awarded   INTEGER;
ALTER TABLE driver_round_attendance ADD COLUMN total_points_after INTEGER;

-- 2. Add attendance sheet message ID to attendance_division_config.
--    Nullable: not set until the first sheet is posted for a division.
ALTER TABLE attendance_division_config ADD COLUMN attendance_message_id TEXT;

-- 3. Create the attendance_pardons table.
--    One row per driver per round per pardon type.
--    Unique constraint prevents duplicate pardon types for the same attendance row.
CREATE TABLE IF NOT EXISTS attendance_pardons (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    attendance_id  INTEGER NOT NULL
                   REFERENCES driver_round_attendance(id) ON DELETE CASCADE,
    pardon_type    TEXT    NOT NULL CHECK (pardon_type IN ('NO_RSVP', 'NO_ATTEND', 'NO_SHOW')),
    justification  TEXT    NOT NULL,
    granted_by     INTEGER NOT NULL,
    granted_at     TEXT    NOT NULL,
    UNIQUE (attendance_id, pardon_type)
);
```

---

## 2. Updated Dataclasses (`src/models/attendance.py`)

### 2.1 `DriverRoundAttendance` — Add finalization fields

```python
@dataclass
class DriverRoundAttendance:
    id: int
    round_id: int
    division_id: int
    driver_profile_id: int
    rsvp_status: str
    accepted_at: str | None
    assigned_team_id: int | None
    is_standby: bool
    attended: bool | None
    points_awarded: int | None       # NEW — net points after pardons
    total_points_after: int | None   # NEW — cumulative total across all rounds
```

### 2.2 `AttendanceDivisionConfig` — Add sheet message ID

```python
@dataclass
class AttendanceDivisionConfig:
    division_id: int
    server_id: int
    rsvp_channel_id: int | None
    attendance_channel_id: int | None
    attendance_message_id: str | None   # NEW — Discord message ID of posted sheet
```

### 2.3 `AttendancePardon` — New dataclass

```python
@dataclass
class AttendancePardon:
    id: int
    attendance_id: int
    pardon_type: str            # 'NO_RSVP' | 'NO_ATTEND' | 'NO_SHOW'
    justification: str
    granted_by: int             # Discord user ID
    granted_at: str             # ISO-8601 UTC
```

---

## 3. New Dataclasses (`src/services/penalty_wizard.py`)

### 3.1 `StagedPardon` — In-memory staging during penalty review

```python
@dataclass
class StagedPardon:
    driver_user_id: int          # Discord user ID (display / audit)
    driver_profile_id: int       # FK — driver_profiles.id
    attendance_id: int           # FK — driver_round_attendance.id
    pardon_type: str             # 'NO_RSVP' | 'NO_ATTEND' | 'NO_SHOW'
    justification: str
    grantor_id: int              # Discord user ID of staging admin
```

---

## 4. Updated `PenaltyReviewState` (`src/services/penalty_wizard.py`)

```python
@dataclass
class PenaltyReviewState:
    round_id: int
    division_id: int
    submission_channel_id: int
    session_types_present: list[SessionType]
    db_path: str
    bot: Any
    staged: list[StagedPenalty] = field(default_factory=list)
    staged_appeals: list[StagedPenalty] = field(default_factory=list)
    staged_pardons: list[StagedPardon] = field(default_factory=list)   # NEW
    prompt_message_id: int | None = None
    appeals_prompt_message_id: int | None = None
    round_number: int = 0
    division_name: str = ""
```

---

## 5. Entity Relationships

```
divisions (1) ──< driver_round_attendance (N)
                          │
                          ├── points_awarded: int | None
                          ├── total_points_after: int | None
                          └──< attendance_pardons (N)
                                    ├── pardon_type: 'NO_RSVP'|'NO_ATTEND'|'NO_SHOW'
                                    └── (attendance_id, pardon_type) UNIQUE

attendance_division_config (1-per-division)
    └── attendance_message_id: str | None  (Discord msg ID of posted sheet)
```

---

## 6. State Transitions

### `DriverRoundAttendance.attended`

| From | To | Trigger |
|------|----|---------|
| `NULL` | `true` or `false` | `record_attendance_from_results` (first run) |
| `false` | `true` | `record_attendance_from_results` (upgrade-only; FR-003) |
| `true` | `true` | `record_attendance_from_results` (idempotent; FR-003) |
| any | recomputed | `recalculate_attendance_for_round` (amendment; FR-028) |

### `DriverRoundAttendance.points_awarded` / `total_points_after`

| From | To | Trigger |
|------|----|---------|
| `NULL` | computed value | `distribute_attendance_points` (post-race finalization) |
| computed | recomputed | `recalculate_attendance_for_round` (amendment; FR-030) |

### `AttendanceDivisionConfig.attendance_message_id`

| From | To | Trigger |
|------|----|---------|
| `NULL` | Discord msg ID string | `post_attendance_sheet` (first post) |
| prior msg ID | new msg ID | `post_attendance_sheet` (subsequent post; prior msg deleted) |

---

## 7. Validation Rules

| Rule | Where enforced |
|------|---------------|
| ONE pardon per `(attendance_id, pardon_type)` | DB UNIQUE constraint + UI reject |
| NO_RSVP pardon requires `rsvp_status = 'NO_RSVP'` | `AddPardonModal.on_submit` |
| NO_ATTEND pardon requires `attended = 0` | `AddPardonModal.on_submit` |
| NO_SHOW pardon requires `rsvp_status = 'ACCEPTED'` AND `attended = 0` | `AddPardonModal.on_submit` |
| No pardons after `result_status = 'POST_RACE_PENALTY'` | `AddPardonModal.on_submit` (check rounds.result_status) |
| `attend` flag for Reserve-team drivers never set | `record_attendance_from_results` (skip if Reserve team) |
