# Research: Attendance Tracking — 033

**Branch**: `033-attendance-tracking`  
**Phase**: 0 — Research  
**Date**: 2026-04-03

---

## 1. Integration Points

### 1.1 Primary Hook — `finalize_penalty_review`

- **File**: `src/services/result_submission_service.py`, line 402
- **Decision**: Attendance recording + pardon application + point distribution +
  sheet posting + sanction enforcement are inserted as a sequential block inside
  `finalize_penalty_review`, immediately after `result_status = 'POST_RACE_PENALTY'`
  is written to the DB and before the appeals review prompt is posted.
- **Rationale**: This is the single confirmed transition point from
  `PROVISIONAL` → `POST_RACE_PENALTY`; all downstream attendance steps are
  logically bound to penalty approval, not to initial result submission. The spec
  explicitly requires distribution to happen at finalization (FR-012), not at
  submission time.
- **Alternatives considered**:
  - `finalize_appeals_review`: Advances round to `FINAL`; attendance tracking
    would be applied too late — pardons cannot be staged after finalization
    (FR-011), and waiting until `FINAL` is inconsistent with the spec (US3).
  - `enter_penalty_state` (provisional results phase): Too early — pardon
    validation depends on attendance flags being set, but pardons must be
    staged _during_ the penalty review stage, not at entry into it.

### 1.2 Attendance Recording Trigger

- **File**: `src/services/result_submission_service.py` (session result
  acceptance path)
- **Decision**: `record_attendance_from_results(db_path, round_id, division_id)`
  is called from `finalize_penalty_review`. The attendance flags are set per
  FR-001–FR-003 using `DriverSessionResult` rows already committed by the time
  finalization runs.
- **Rationale**: All session results for a round are present by the time the
  penalty review is approved. Recording attendance at finalization (not at each
  individual session submission) is simpler and ensures the full picture is
  available before flags are set. FR-003 (later sessions can upgrade absent →
  present) is naturally satisfied since all sessions exist before the single
  recording pass.
- **Alternatives considered**:
  - Recording attendance on each individual session result acceptance: More
    complex, requires partial-flag logic and idempotent updates across multiple
    calls; no user-visible benefit since the sheet is only posted at finalization.

### 1.3 Attendance Pardon Button — `PenaltyReviewView`

- **File**: `src/services/penalty_wizard.py`
- **Decision**: Add a new "🏳️ Attendance Pardon" button (CID `_CID_PARDON`) to
  `PenaltyReviewView` row 0. `PenaltyReviewState` gains a new field
  `staged_pardons: list[StagedPardon]`. `_render_prompt_content` is extended to
  include a staged-pardons subsection. A new `AddPardonModal` class is added.
- **Rationale**: The spec requires the pardon button to be absent during the
  appeals review stage (FR-005, US2 AC6). `PenaltyReviewView` is exclusively
  used during the penalty review stage; `AppealsReviewView` is the separate class
  used during appeals. Adding the button only to `PenaltyReviewView` satisfies
  both the presence requirement and the absence requirement without conditional
  rendering logic.
- **Alternatives considered**:
  - Adding a conditionally shown button to `AppealsReviewView` controlled by a
    flag: More complex, fragile, and violates the principle that `AppealsReviewView`
    is exclusively for the appeals stage.

### 1.4 Amendment Recalculation Hook

- **File**: `src/services/amendment_service.py`, function `approve_amendment`
- **Decision**: After standings recomputation in `approve_amendment`, call
  `recalculate_attendance_for_round(db_path, round_id, division_id, bot)` which
  re-runs FR-001–FR-003 attendance recording, recomputes `points_awarded` and
  `total_points_after` while preserving existing `AttendancePardon` rows, reposts
  the attendance sheet, and re-evaluates sanctions (FR-028–FR-031).
- **Rationale**: `approve_amendment` is the single confirmed exit point of the
  amendment approval flow. All `DriverSessionResult` rows have been updated by the
  time this function runs. Hooking here ensures recalculation always follows
  approved amendments, never partial/in-progress ones.
- **Alternatives considered**:
  - Hooking into `modify_session_points`: Only deals with points-store rows, not
    session result rows; attendance attendance recording depends on
    `DriverSessionResult` presence/absence, so this is the wrong hook point.
  - A deferred background task: Adds complexity and race conditions with subsequent
    penalty wizard interactions; inline synchronous execution is sufficient for a
    small-league bot.

---

## 2. Schema Gaps Identified

### 2.1 `driver_round_attendance` — Missing Columns

Migration 031 created this table with `attended INTEGER` but without the two
finalization output columns. Both must be added in migration 032.

| Column | Type | Constraint | Purpose |
|--------|------|-----------|---------|
| `points_awarded` | `INTEGER` | nullable until finalization | Net points after pardons |
| `total_points_after` | `INTEGER` | nullable until finalization | Cumulative total across all rounds in division |

### 2.2 `attendance_division_config` — Missing Column

Migration 030 created this table without message-ID tracking. Must be added in
migration 032.

| Column | Type | Constraint | Purpose |
|--------|------|-----------|---------|
| `attendance_message_id` | `TEXT` | nullable | Discord message ID of most-recently posted attendance sheet; used by FR-020 |

### 2.3 Missing Table — `attendance_pardons`

Defined in constitution v2.10.0 (Principle XIII, Data & State Management) but not
yet created by any migration. Must be created in migration 032.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `INTEGER PRIMARY KEY` | auto-increment |
| `attendance_id` | `INTEGER NOT NULL` | FK → `driver_round_attendance(id)` |
| `pardon_type` | `TEXT NOT NULL` | `NO_RSVP`, `NO_ATTEND`, or `NO_SHOW` |
| `justification` | `TEXT NOT NULL` | Free-text reason logged to calc-log channel |
| `granted_by` | `INTEGER NOT NULL` | Discord user ID of tier-2 admin who granted |
| `granted_at` | `TEXT NOT NULL` | ISO-8601 UTC timestamp |
| UNIQUE | | `(attendance_id, pardon_type)` — one pardon per type per driver per round |

---

## 3. Existing Service Inventory

### 3.1 `AttendanceService` (existing in `attendance_service.py`)

Existing read/write methods cover config CRUD and RSVP state management. None of
the tracking-pipeline methods exist yet. All tracking logic will be added to this
service as new async functions.

Methods to add:
- `record_attendance_from_results(db_path, round_id, division_id)`: FR-001–FR-004
- `distribute_attendance_points(db_path, round_id, division_id, staged_pardons)`:
  FR-012–FR-015
- `post_attendance_sheet(bot, guild, round_id, division_id)`: FR-016–FR-021
- `enforce_attendance_sanctions(bot, guild, round_id, division_id)`: FR-022–FR-027
- `recalculate_attendance_for_round(db_path, bot, guild, round_id, division_id)`:
  FR-028–FR-031

### 3.2 `DriverService` (existing in `driver_service.py`)

- `ALLOWED_TRANSITIONS` confirms that `ASSIGNED → UNASSIGNED` and
  `ASSIGNED → NOT_SIGNED_UP` are valid autosack target states.
- `transition_state()` enforces the state machine and writes audit entries;
  autosack must call this method, not manipulate the DB directly.
- `resolve_driver_profile_id(server_id, discord_user_id, db)` retrieves profile
  IDs from Discord user IDs; used during sanction enforcement.

### 3.3 `PlacementService` (existing in `placement_service.py`)

The seat mutation service for both autosanctions is `PlacementService`, not `TeamService`.
`TeamService` handles team CRUD only (add/rename/remove/seed); it has no assign/sack methods.

**Autosack** → `placement_service.sack_driver(server_id, driver_profile_id, season_id, acting_user_id, acting_user_name, guild, discord_user_id)`
- Validates state is `UNASSIGNED` or `ASSIGNED`; raises `ValueError` otherwise.
- Atomically: NULLs all `team_seats` for this profile, deletes all
  `driver_season_assignments` for this season, transitions to `NOT_SIGNED_UP`.
- Revokes all division roles, team roles, and the `signed_up_role` (the "driver
  role" referenced in FR-023).
- Writes a `DRIVER_UNASSIGN` audit entry per division; also calls
  `bot.output_router.post_log()` from the cog, which the attendance pipeline must
  replicate.

**Autoreserve** — two-step using existing methods:
1. Query current assignment: `SELECT ti.is_reserve FROM driver_season_assignments dsa JOIN team_seats ts ON ts.id = dsa.team_seat_id JOIN team_instances ti ON ti.id = ts.team_instance_id WHERE dsa.driver_profile_id = ? AND dsa.season_id = ? AND dsa.division_id = ?`
2. If `is_reserve = 1`: skip (FR-026 — already in Reserve).
3. Otherwise: `placement_service.unassign_driver(server_id, driver_profile_id, division_id, season_id, ...)` — frees seat and deletes `driver_season_assignments` row; transitions to `UNASSIGNED` if no other division assignments remain.
4. Look up Reserve team name: `SELECT name FROM team_instances WHERE division_id = ? AND is_reserve = 1 LIMIT 1`
5. `placement_service.assign_driver(server_id, driver_profile_id, division_id, reserve_team_name, season_id, ...)` — assigns to Reserve seat (Reserve team has unlimited seats; new seats are auto-created).

**Guild context**: Both methods require a `discord.Guild` object for role
mutations. In `finalize_penalty_review`, `guild = interaction.guild` is available.
In `approve_amendment` (amendment recalculation), guild must be fetched from `bot`
using `server_id`: `guild = bot.get_guild(server_id)`.

---

## 4. Penalty Wizard Integration Decisions

### 4.1 `StagedPardon` Dataclass

A new `StagedPardon` dataclass (in `penalty_wizard.py`) captures:
- `driver_user_id: int` — Discord user ID
- `driver_profile_id: int` — resolved at pardon submission time
- `attendance_id: int` — FK to `driver_round_attendance`
- `pardon_type: str` — `"NO_RSVP"`, `"NO_ATTEND"`, or `"NO_SHOW"`
- `justification: str`
- `grantor_id: int` — Discord user ID of submitting admin

### 4.2 Pardon Validation Rules

All validation runs inside the `AddPardonModal.on_submit` callback before staging:

| Pardon Type | Required State |
|-------------|---------------|
| `NO_RSVP` | `rsvp_status = 'NO_RSVP'` |
| `NO_ATTEND` | `attended = false` (0) |
| `NO_SHOW` | `rsvp_status = 'ACCEPTED'` AND `attended = false` |

An attempt to stage a duplicate (same `attendance_id` + `pardon_type`) is rejected
with a clear ephemeral error (spec US2 AC5; spec note: DB-level unique constraint
also blocks silent duplicates).

### 4.3 Prompt Content Extension

`_render_prompt_content` in `penalty_wizard.py` currently renders a list of staged
penalties. It must be extended to render a second subsection "Staged Attendance
Pardons" when `state.staged_pardons` is non-empty, showing driver mention, pardon
type, and justification placeholder "[justification logged]".

---

## 5. Attendance Point Calculation Logic

The point calculation is stateless and deterministic given per-driver inputs:

```
points = 0
rsvp = driver_round_attendance.rsvp_status
attended = driver_round_attendance.attended
pardons = set of staged pardon types for this driver

if rsvp == 'NO_RSVP' and 'NO_RSVP' not in pardons:
    points += config.no_rsvp_penalty

if not attended:
    if rsvp == 'ACCEPTED':
        if 'NO_SHOW' not in pardons:
            points += config.no_show_penalty
    elif rsvp == 'NO_RSVP':
        if 'NO_ATTEND' not in pardons:
            points += config.no_attend_penalty
    # if TENTATIVE or DECLINED and absent: 0 (no penalty)

points_awarded = points
```

`total_points_after` is the SUM of `points_awarded` across all finalized rounds for
this driver in this division, including the current round.

---

## 6. Attendance Sheet Format

```
**Attendance — <division_name>**

@mention1 — 4 attendance points
@mention2 — 2 attendance points
@mention3 — 0 attendance points

Drivers who reach <autoreserve_threshold> points will be moved to reserve.
Drivers who reach <autosack_threshold> points will be removed from all driving roles in all divisions.
```

- Footer lines are omitted individually when their threshold is `null` or `0`.
- Driver list order: descending `total_points_after`; ties broken alphabetically
  by guild display name.
- Every driver is shown, including those with 0 points.

---

## 7. Sanction Enforcement Rules

| Condition | Action |
|-----------|--------|
| `total_points_after >= autosack_threshold` (enabled) | Unassign from all seats in all divisions; revoke driver role; one audit log per division |
| `autoreserve_threshold <= total_points_after < autosack_threshold` (both enabled) | Unassign from current seat in this division; assign to Reserve team of this division; one audit log per action |
| Both thresholds triggered (`>=` autosack) | Autosack supersedes; autoreserve NOT additionally applied |
| Driver already in Reserve team, autoreserve triggered | No action |
| Threshold disabled (null or 0) | Sanction does not fire |

Sanction evaluation runs in a single pass over all drivers. Autosack check runs
before autoreserve check so that a driver who triggers both only gets sacked.

---

## 8. Test Strategy

Unit tests (new file: `tests/unit/test_attendance_tracking.py`):

1. `test_record_attendance_sets_attended_flags` — verifies FR-001
2. `test_record_attendance_excludes_reserve_team_drivers` — verifies FR-002
3. `test_record_attendance_upgrades_absent_to_present` — verifies FR-003
4. `test_record_attendance_no_op_when_module_disabled` — verifies FR-004
5. `test_pardon_validation_rejects_invalid_rsvp_state` — verifies FR-007 cases
6. `test_point_distribution_all_scenarios` — verifies US3 point rules (all 6 rows)
7. `test_point_distribution_with_pardons` — verifies FR-013, FR-015
8. `test_total_points_after_accumulates_across_rounds` — verifies FR-014
9. `test_sheet_ordering_descending_with_tiebreak` — verifies FR-017, FR-018
10. `test_sheet_footer_omits_disabled_thresholds` — verifies FR-019
11. `test_sheet_skips_delete_when_message_missing` — verifies FR-020
12. `test_autosack_supersedes_autoreserve` — verifies FR-025
13. `test_autoreserve_skips_already_reserved_driver` — verifies FR-026
14. `test_sanctions_disabled_when_threshold_zero` — verifies FR-027
15. `test_amendment_recalculation_preserves_pardons` — verifies FR-029
