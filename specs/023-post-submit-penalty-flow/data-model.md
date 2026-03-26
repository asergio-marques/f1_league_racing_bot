# Data Model: Inline Post-Submission Penalty Review

**Branch**: `023-post-submit-penalty-flow`  
**Phase**: 1 — Design  
**Date**: 2026-03-26

---

## Overview

This feature introduces one schema change (migration 019) and one model change to support the new round finalization state and signed penalty values. No new tables are added; existing tables gain one column each or have their model field semantics updated.

---

## Schema Changes

### Migration 019 — `rounds.finalized`

```sql
-- Migration 019: Add finalized flag to rounds for post-submission penalty gate
ALTER TABLE rounds ADD COLUMN finalized INTEGER NOT NULL DEFAULT 0;
```

**Purpose**: Tracks whether a round has completed the Post-Round Penalties state and reached the FINALIZED terminal state. Used by:
- `test_mode_service.get_next_pending_phase` — excludes finalized rounds from the phase queue
- `test_mode_cog.advance` — blocks advance when a round has results but `finalized = 0`
- `result_submission_service` — set to `1` when finalization completes and the channel is closed
- Bot `on_ready` recovery scan — determines which open submission channels are in penalty-pending state vs. mid-submission

**Default**: `0` (not finalized). All existing rounds have no finalization concept; defaulting to `0` means they will be treated as non-finalized and the penalty-state guard will not apply to them retroactively in test mode (only new rounds go through the penalty flow).

**Table**: `rounds`  
**Column**: `finalized INTEGER NOT NULL DEFAULT 0`

---

## Model Changes

### `Round` dataclass — add `finalized` field

**File**: `src/models/round.py`

```python
@dataclass
class Round:
    id: int
    division_id: int
    round_number: int
    format: RoundFormat
    track_name: str | None
    scheduled_at: datetime
    phase1_done: bool = False
    phase2_done: bool = False
    phase3_done: bool = False
    status: str = "ACTIVE"
    finalized: bool = False          # NEW — True after penalty review approved
```

All DB queries that SELECT from `rounds` and map to a `Round` object must include the `finalized` column. These are in `season_service.py` (get_division_rounds) and anywhere `Round` objects are constructed from DB rows.

---

### `StagedPenalty` dataclass — `penalty_seconds` becomes signed

**File**: `src/services/penalty_service.py`

```python
@dataclass
class StagedPenalty:
    driver_user_id: int
    session_type: SessionType
    penalty_type: Literal["TIME", "DSQ"]
    penalty_seconds: int | None     # Negative = time reduction. None for DSQ.
```

No schema change; this is an in-memory type used during the wizard session only.

---

## Entities

### Round (existing — amended)

| Field | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `division_id` | INTEGER FK | → divisions |
| `round_number` | INTEGER | |
| `format` | TEXT | NORMAL / SPRINT / MYSTERY / ENDURANCE |
| `track_name` | TEXT NULL | |
| `scheduled_at` | TEXT | ISO datetime |
| `phase1_done` | INTEGER | Weather phase 1 sent |
| `phase2_done` | INTEGER | Weather phase 2 sent |
| `phase3_done` | INTEGER | Weather phase 3 sent |
| `status` | TEXT | ACTIVE / CANCELLED |
| **`finalized`** | **INTEGER** | **NEW — 0 until penalty review approved** |

---

### StagedPenalty (in-memory, transient)

Not persisted. Lives in the penalty wizard state object for the duration of the penalty review session.

| Field | Type | Notes |
|---|---|---|
| `driver_user_id` | int | Discord user ID of the penalized driver |
| `session_type` | SessionType | Which session the penalty applies to |
| `penalty_type` | "TIME" \| "DSQ" | |
| `penalty_seconds` | int \| None | Signed seconds. Positive = add time. Negative = subtract time. None for DSQ. |

---

### Penalty Application Record (persisted post-finalization)

Stored in `driver_session_results` via existing columns:

| Column | Populated by penalty |
|---|---|
| `outcome` | Set to `"DSQ"` for DSQ penalties |
| `post_steward_total_time` | Updated total time after signed TIME penalty |
| `post_race_time_penalties` | Accumulated signed seconds (e.g. `-5` for a −5 s penalty) — cumulative if multiple penalties applied |
| `finishing_position` | Recomputed after penalty application |
| `points_awarded` | Recomputed; `0` for DSQ |
| `fastest_lap_bonus` | `0` for DSQ |

The approving actor identity and the full penalty list are recorded in `audit_entries` (existing table, existing INSERT pattern in `penalty_service.apply_penalties`).

---

## State Transition Diagram

```
Round scheduled
      │
      ▼
 Submission channel opens
 (round_submission_channels.closed = 0)
      │
      │ Admin submits/cancels all sessions
      ▼
POST-ROUND PENALTIES state
 (session_results rows exist, finalized = 0, channel still open)
      │
      │ Admin approves penalty review (empty or non-empty list)
      ▼
 FINALIZED
 (rounds.finalized = 1, round_submission_channels.closed = 1, channel deleted)
```

---

## No-Change Entities

The following entities are unchanged in schema; only their service-layer interactions are affected:

- `session_results` — `results_message_id` continues to track the latest results message (interim replaced by final on finalization)
- `driver_standings_snapshots` — `standings_message_id` continues to track the latest standings post; updated on finalization
- `audit_entries` — existing table; new `PENALTY_APPLIED` + `ROUND_FINALIZED` audit entries follow the existing pattern
- `round_submission_channels` — `closed` column is set to `1` on finalization as before
