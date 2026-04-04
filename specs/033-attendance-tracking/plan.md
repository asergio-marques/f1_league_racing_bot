# Implementation Plan: Attendance Tracking

**Branch**: `033-attendance-tracking` | **Date**: 2026-04-03 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/033-attendance-tracking/spec.md`

## Summary

This increment completes the Attendance module by implementing the core post-round
tracking pipeline: automatic attendance recording at penalty finalization, a pardon
workflow staged inside the penalty wizard, attendance point distribution, attendance
sheet posting with threshold footer, and automatic sanction enforcement
(autoreserve / autosack). Amendment recalculation (FR-028–FR-031) ensures the
pipeline re-runs whenever round results are amended.

**Technical approach**: A single new hook injected into the existing
`finalize_penalty_review` function (result_submission_service.py:402) orchestrates
all five pipeline steps in sequence. A second hook in `approve_amendment`
(amendment_service.py) handles recalculation. A new "Attendance Pardon" button and
`AddPardonModal` are added to `PenaltyReviewView` only (not `AppealsReviewView`).
Migration 032 adds two columns to existing tables and creates the `attendance_pardons`
table.

## Technical Context

**Language/Version**: Python 3.13.2  
**Primary Dependencies**: discord.py (Buttons, Views, Modals), aiosqlite  
**Storage**: SQLite (`bot.db`), auto-migrated from `src/db/migrations/`  
**Testing**: pytest — `python -m pytest tests/ -v` from repo root  
**Target Platform**: Raspberry Pi (Linux); Windows-compatible dev commands  
**Project Type**: Discord bot (slash commands + persistent Views)  
**Performance Goals**: All pipeline steps complete within the same interaction
response window (no deferred or background tasks required at small-league scale)  
**Constraints**: All Discord interactions must respond within 3 seconds (deferred
with `defer()` where needed); no new cog commands in this increment  
**Scale/Scope**: Single-server; up to ~40 drivers per division; all pipeline steps
run synchronously inside the finalization interaction

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Name | Status | Notes |
|-----------|------|--------|-------|
| I | Trusted Configuration Authority | ✅ Pass | Pardon button gated to tier-2 admin (interaction-role); sanction enforcement writes audit log |
| II | Deterministic & Reproducible Data | ✅ Pass | Point calculation is purely deterministic from stored rsvp_status, attended, and penalty config |
| III | Single Source of Truth | ✅ Pass | All attendance state lives in DB; no duplicate state kept in memory across restarts |
| IV | Mystery Round Isolation | ✅ Pass | Spec explicitly calls out Mystery rounds never reach session result submission; recording never triggered |
| V | Observability & Change Audit Trail | ✅ Pass | Every autosack and autoreserve action produces an audit log entry (FR-023, FR-024, SC-006) |
| VI | Incremental Scope Expansion | ✅ Pass | Attendance management is formally in-scope as Principle VI item 11 (added v2.10.0) |
| VII | Output Channel Discipline | ✅ Pass | Attendance sheet posts to the division's configured `attendance_channel_id`; pardon justification logged to calc-log only (FR-010) |
| VIII | Driver Profile Integrity | ✅ Pass | Autosack delegates to `PlacementService.sack_driver()`, which enforces `DriverService.transition_state()` internally — no direct state bypass |
| IX | Team & Division Structural Integrity | ✅ Pass | Autoreserve moves driver to Reserve team via seat mutation; autosack unassigns from all seats; division structure not violated |
| X | Modular Feature Architecture | ✅ Pass | Attendance module guard (is_enabled check) wraps all recording and distribution calls; module disabled = no-op (FR-004) |
| XI | Signup Wizard Integrity | ✅ Pass | No overlap with signup wizard; attendance operates entirely in the penalty wizard stage |
| XII | Race Results & Championship Integrity | ✅ Pass | Attendance recording uses `DriverSessionResult` rows as input; does not write to results tables; finalization hook is append-only |
| XIII | Attendance & Check-in Integrity | ✅ Pass | All FR-001–FR-031 implement the mechanics mandated by Principle XIII; no deviations from the ratified spec |

**Post-Phase-1 re-check**: ✅ No new violations introduced by data model design. Migration 032 schema additions (new columns + `attendance_pardons` table) are exactly those specified in constitution v2.10.0 Data & State Management.

## Project Structure

### Documentation (this feature)

```text
specs/033-attendance-tracking/
├── plan.md              # This file
├── research.md          # Phase 0 output — integration points, schema gaps, decisions
├── data-model.md        # Phase 1 output — migration 032, updated dataclasses
├── quickstart.md        # Phase 1 output — end-to-end test guide
└── tasks.md             # Phase 2 output (/speckit.tasks — NOT created by /speckit.plan)
```

### Source Code (modified / new files)

```text
src/
├── db/
│   └── migrations/
│       └── 032_attendance_tracking.sql          [NEW] migration — 2 ALTER TABLE + CREATE TABLE
├── models/
│   └── attendance.py                            [MODIFY] add fields to 3 dataclasses; add AttendancePardon
├── services/
│   ├── attendance_service.py                    [MODIFY] add 5 new async pipeline functions
│   ├── penalty_wizard.py                        [MODIFY] StagedPardon; staged_pardons field;
│   │                                                     _CID_PARDON button; AddPardonModal;
│   │                                                     _render_prompt_content extension
│   ├── result_submission_service.py             [MODIFY] finalize_penalty_review — inject
│   │                                                     attendance pipeline block
│   └── amendment_service.py                     [MODIFY] approve_amendment — inject
│                                                          recalculate_attendance_for_round call
│
│   # Read-only by attendance_service (no modifications needed):
│   ├── placement_service.py                     [READ] sack_driver + unassign_driver +
│   │                                                   assign_driver called by enforce_sanctions
│   └── driver_service.py                        [READ] resolve_driver_profile_id helper used
│                                                        during pardon staging

tests/
└── unit/
    └── test_attendance_tracking.py              [NEW] 15 unit tests covering FR-001–FR-031
```

**Structure Decision**: Single-project layout (Option 1). All changes are confined to
existing service/model/migration layers. No new cog commands are introduced in this
increment — all behaviour is triggered by existing penalty wizard interactions and the
existing amendment approval flow.

## Complexity Tracking

> No Constitution Check violations. This section is informational only.

No violations were identified. The design:
- Reuses existing service pattern (no new service files created).
- Hooks into two existing functions (`finalize_penalty_review`, `approve_amendment`).
- Adds one new modal and one new button to a single existing View.
- Uses a single new migration file following the established numbering convention.

---

## Implementation Guide

### Phase 0 Research Output

See [research.md](research.md) for full research findings. Key decisions:

1. **Primary hook**: `finalize_penalty_review` in `result_submission_service.py:402`
   — attendance pipeline runs here, after `result_status = 'POST_RACE_PENALTY'` is
   set, before the appeals prompt is posted.
2. **Pardon button**: `PenaltyReviewView` only; not `AppealsReviewView`.
3. **Amendment hook**: `approve_amendment` in `amendment_service.py` — call
   `recalculate_attendance_for_round` after standings are recomputed.
4. **Team sack implementation**: Reuse `PlacementService.sack_driver()` exactly as
   the `/driver sack` command does — it atomically nulls all `team_seats`, deletes
   `driver_season_assignments`, calls `DriverService.transition_state(NOT_SIGNED_UP)`,
   and revokes all placement + signed-up roles. Pass `bot.user.id` / `str(bot.user)`
   as the acting user. Catch `ValueError` for the already-sacked (NOT_SIGNED_UP) edge
   case and emit a no-op audit log entry instead of raising. Autoreserve uses
   `PlacementService.unassign_driver()` then `PlacementService.assign_driver()` to the
   Reserve team. Do not call `DriverService.transition_state()` or mutate seat rows
   directly.
5. **`AttendancePardon` rows staged in-memory** (`state.staged_pardons`) during the
   penalty review window; persisted to `attendance_pardons` table at finalization.

---

### Phase 1 Design Output

See [data-model.md](data-model.md) for full schema and dataclass changes. Summary:

**New migration**: `src/db/migrations/032_attendance_tracking.sql`
- `ALTER TABLE driver_round_attendance ADD COLUMN points_awarded INTEGER`
- `ALTER TABLE driver_round_attendance ADD COLUMN total_points_after INTEGER`
- `ALTER TABLE attendance_division_config ADD COLUMN attendance_message_id TEXT`
- `CREATE TABLE IF NOT EXISTS attendance_pardons (...)`

**Dataclass updates** (`src/models/attendance.py`):
- `DriverRoundAttendance`: add `points_awarded: int | None`, `total_points_after: int | None`
- `AttendanceDivisionConfig`: add `attendance_message_id: str | None`
- `AttendancePardon`: new dataclass

**Penalty wizard updates** (`src/services/penalty_wizard.py`):
- `StagedPardon`: new dataclass (in-memory staging)
- `PenaltyReviewState`: add `staged_pardons: list[StagedPardon] = field(default_factory=list)`
- `PenaltyReviewView`: add `_CID_PARDON = "att_pardon"` button; new `AddPardonModal` class
- `_render_prompt_content`: extend to show staged pardons subsection

---

### Attendance Pipeline (called from `finalize_penalty_review`)

```
finalize_penalty_review(interaction, state):
  ... [existing penalty apply + results repost + result_status update]

  # === NEW: Attendance pipeline ===
  if await attendance_module_enabled(db_path, division_id):
    await record_attendance_from_results(db_path, round_id, division_id)
    # INSERT state.staged_pardons into attendance_pardons table (inline block — T010)
    await distribute_attendance_points(db_path, round_id, division_id)
    await post_attendance_sheet(bot, guild, round_id, division_id)
    await enforce_attendance_sanctions(bot, guild, db_path, round_id, division_id, server_id, season_id)
  # === END Attendance pipeline ===

  ... [existing appeals prompt post]
```

### `record_attendance_from_results(db_path, round_id, division_id)`

1. Query all `driver_round_attendance` rows for the round where the driver is NOT
   in the Reserve team of the division (join `team_seats` / `driver_season_assignments`
   to identify reserve team seat).
2. Query all distinct `driver_profile_id` values from `driver_session_results` JOIN
   `session_results` WHERE `round_id = ?` — these are the "attended" drivers.
3. For each full-time driver row in `driver_round_attendance`:
   - If `driver_profile_id` in attended set: `UPDATE ... SET attended = 1`
     (FR-003: only upgrade, never downgrade).
   - Else if current `attended IS NULL`: `UPDATE ... SET attended = 0`.
   - Else if current `attended = 1`: skip (FR-003: no revert).
4. Module-disabled guard: call `attendance_module_enabled(db_path, division_id)` first; if False, return immediately.

### `distribute_attendance_points(db_path, round_id, division_id)`

1. Load `AttendanceConfig` for the division's server (penalty values).
2. Load all full-time `DriverRoundAttendance` rows for the round.
3. Load all `AttendancePardon` rows for those attendance IDs.
4. For each driver: compute `points_awarded` per the rule table in research.md.
5. Compute `total_points_after` = SUM of all `points_awarded` for the driver in
   this division across all finalized rounds (WHERE round's `result_status` IN
   ('POST_RACE_PENALTY', 'FINAL'), including the current round just updated).
6. `UPDATE driver_round_attendance SET points_awarded = ?, total_points_after = ?
   WHERE id = ?` for each driver.

### `post_attendance_sheet(bot, guild, round_id, division_id)`

1. Load `AttendanceDivisionConfig` for the division — get `attendance_channel_id`
   and `attendance_message_id`.
2. Load all full-time drivers' `(discord_user_id, total_points_after)` for this
   round's division, sorted: descending `total_points_after`, then lexicographic
   by guild display name.
3. Build sheet content: header + driver lines + footer (threshold lines conditional
   on non-zero/non-null values from `AttendanceConfig`).
4. If `attendance_message_id` is set, DELETE the prior message (catch `discord.NotFound`
   silently per FR-020).
5. POST the new sheet to `attendance_channel_id`. If channel not found, log and return.
6. `UPDATE attendance_division_config SET attendance_message_id = ? WHERE division_id = ?`
   with the new message ID.

### `enforce_attendance_sanctions(bot, guild, db_path, round_id, division_id, server_id, season_id)`

1. Load `AttendanceConfig` thresholds (`autoreserve_threshold`, `autosack_threshold`).
2. If both thresholds disabled, return immediately.
3. Load all full-time drivers with their `total_points_after` for this division's round.
4. For each driver:
   a. **Autosack check** (if threshold enabled and `total_points_after >= autosack_threshold`):
      - Call `PlacementService.sack_driver(server_id, driver_profile_id, season_id,
        bot.user.id, str(bot.user), guild, discord_user_id)`.
      - Catch `ValueError` (driver already NOT_SIGNED_UP) and emit a no-op audit log
        entry instead of raising.
      - Skip autoreserve check for this driver.
   b. **Autoreserve check** (if threshold enabled and `total_points_after >= autoreserve_threshold`
      and driver not already in Reserve team of this division):
      - Call `PlacementService.unassign_driver(server_id, driver_profile_id, division_id,
        season_id, ...)`.
      - Look up Reserve team name; call `PlacementService.assign_driver(server_id,
        driver_profile_id, division_id, reserve_team_name, season_id, ...)`.
      - Write audit log entry.

### `recalculate_attendance_for_round(bot, guild, db_path, round_id, division_id, server_id, season_id)`

*(Called from `approve_amendment` after standings recompute)*

1. Re-run `record_attendance_from_results(db_path, round_id, division_id)`.
   - Existing `attended` flags are overwritten by the recalculation (amendment
     replaces prior session results; FR-028).
2. Reload `AttendancePardon` rows — they are preserved (no DELETE; FR-029).
3. Recompute `points_awarded` and `total_points_after` for the round
   (same logic as `distribute_attendance_points`; FR-030).
4. Propagate `total_points_after` forward: for each subsequent finalized round in
   the same division (ordered by round number), re-run step 3 using the corrected
   cumulative from the preceding round.
5. Re-post attendance sheet (FR-031).
6. Re-evaluate sanctions (FR-031).

---

### AddPardonModal

```
class AddPardonModal(discord.ui.Modal, title="Attendance Pardon"):
  driver_id = discord.ui.TextInput(label="Driver Discord User ID", ...)
  pardon_type = discord.ui.TextInput(label="Pardon Type (NO_RSVP / NO_ATTEND / NO_SHOW)", ...)
  justification = discord.ui.TextInput(label="Justification", style=Paragraph, ...)

  async def on_submit(interaction):
    1. Parse and validate driver_id (must be integer; resolve to driver_profile_id)
    2. Validate pardon_type value (must be one of the three)
    3. Load DriverRoundAttendance row for (driver_profile_id, round_id, division_id)
    4. Validate pardon_type against rsvp_status / attended (reject with ephemeral error if invalid)
    5. Check for duplicate (same attendance_id + pardon_type already in state.staged_pardons)
    6. Check round result_status != 'POST_RACE_PENALTY' (reject if already finalized; FR-011)
    7. Append StagedPardon to state.staged_pardons
    8. Re-render prompt content (edit prompt message)
    9. Respond ephemeral "Pardon staged."
```

---

### `_render_prompt_content` Extension

After the existing "Staged Penalties" subsection, append:

```
if state.staged_pardons:
    lines.append("\n**Staged Attendance Pardons:**")
    for sp in state.staged_pardons:
        lines.append(f"  • <@{sp.driver_user_id}> — {sp.pardon_type} [justification logged]")
```

---

### Unit Test Plan (`tests/unit/test_attendance_tracking.py`)

| # | Test | FR |
|---|------|----|
| 1 | `test_record_attendance_sets_attended_flags` | FR-001 |
| 2 | `test_record_attendance_excludes_reserve_drivers` | FR-002 |
| 3 | `test_record_attendance_upgrades_absent_to_present` | FR-003 |
| 4 | `test_record_attendance_no_op_when_disabled` | FR-004 |
| 5 | `test_pardon_rejects_no_rsvp_for_accepted_driver` | FR-007 |
| 6 | `test_pardon_rejects_no_show_for_no_rsvp_driver` | FR-007 |
| 7 | `test_pardon_rejects_no_attend_for_attended_driver` | FR-007 |
| 8 | `test_point_distribution_no_rsvp_attended` | US3 row 1 |
| 9 | `test_point_distribution_no_rsvp_absent` | US3 row 2 |
| 10 | `test_point_distribution_accepted_absent` | US3 row 3 |
| 11 | `test_point_distribution_tentative_absent_zero` | US3 row 4 |
| 12 | `test_point_distribution_full_pardon_zero` | FR-013, FR-015 |
| 13 | `test_total_points_after_accumulates` | FR-014 |
| 14 | `test_sheet_ordering_descending_with_tiebreak` | FR-017, FR-018 |
| 15 | `test_sheet_footer_omits_disabled_thresholds` | FR-019 |
| 16 | `test_sheet_skips_delete_when_message_missing` | FR-020 |
| 17 | `test_autosack_supersedes_autoreserve` | FR-025 |
| 18 | `test_autoreserve_skips_already_reserved` | FR-026 |
| 19 | `test_sanctions_no_op_when_threshold_zero` | FR-027 |
| 20 | `test_amendment_recalculation_preserves_pardons` | FR-029 |

