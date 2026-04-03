# Tasks: Attendance Module — Initial Setup & Configuration

**Branch**: `031-attendance-module`  
**Input**: `specs/031-attendance-module/` (plan.md, spec.md, research.md, data-model.md, quickstart.md)  
**Run tests**: `python -m pytest tests/ -v` from repo root

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the two new source files that all user stories depend on — the DB migration and model dataclasses. No existing code is touched. Both tasks are fully independent.

- [ ] T001 [P] Create migration `030_attendance_module.sql` with `attendance_config` and `attendance_division_config` tables in `src/db/migrations/030_attendance_module.sql`
- [ ] T002 [P] Create `AttendanceConfig` and `AttendanceDivisionConfig` dataclasses in `src/models/attendance.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core service layer that all user stories call into. MUST be complete before any cog work begins.

**⚠️ CRITICAL**: No user story implementation can begin until this phase is complete.

- [ ] T003 Implement `AttendanceService` with `get_config`, `get_or_create_config`, `delete_division_configs`, and module-level `validate_timing_invariant` helper in `src/services/attendance_service.py`
- [ ] T004 [P] Add `is_attendance_enabled` and `set_attendance_enabled` methods to `src/services/module_service.py`
- [ ] T005 Register `attendance_service = AttendanceService(db_path)` on the bot instance in `src/bot.py`

**Checkpoint**: Foundation ready — all service methods exist; user story cog work can begin.

> **FR-009 deferred**: FR-009 ("on bot restart, re-arm any RSVP notice and last-notice scheduled jobs") is deferred to the RSVP automation increment. No APScheduler jobs are created in this increment, so FR-009 is vacuously satisfied here.

---

## Phase 3: User Story 1 — Enable and Disable the Attendance Module (Priority: P1) 🎯 MVP

**Goal**: Full module lifecycle — enable (with R&S dependency + season-state gate), manual disable, cascading auto-disable when R&S is disabled, and attendance status in `/season review`.

**Independent Test**: Enable the module, confirm the `attendance_config` row exists with defaults, confirm `/season review` shows "Enabled". Disable, confirm flag cleared and division configs removed. Disable R&S while attendance is on; confirm both are off.

### Tests for User Story 1

- [ ] T006 [P] [US1] Write lifecycle unit tests (`test_is_attendance_enabled_false_by_default`, `test_enable_creates_config_with_defaults`, `test_enable_sets_flag_true`, `test_disable_sets_flag_false`, `test_disable_deletes_division_configs`, `test_reenable_resets_to_defaults`, `test_enable_rollback_on_db_failure`) in `tests/unit/test_attendance_service.py` — `test_enable_rollback_on_db_failure` simulates a DB error after `INSERT attendance_config` but before `set_attendance_enabled`; asserts no partial row is left and the enabled flag remains unset (FR-004)

### Implementation for User Story 1

- [ ] T007 [US1] Add `"attendance"` to `_MODULE_CHOICES` and add dispatch branches in `enable`/`disable` commands in `src/cogs/module_cog.py`
- [ ] T008 [US1] Implement `_enable_attendance` (R&S gate, ACTIVE-season gate, already-enabled guard, `INSERT OR REPLACE attendance_config` with defaults, audit entry, `post_log`, followup) in `src/cogs/module_cog.py`
- [ ] T009 [US1] Implement `_disable_attendance(self, interaction, *, cascade: bool = False)` (not-enabled guard, `UPDATE attendance_config SET module_enabled = 0`, `DELETE FROM attendance_division_config WHERE server_id = ?`, audit entry, `post_log`; when `cascade=False` also `defer()` and `followup.send("✅ Attendance module disabled.")`; when `cascade=True` skip defer/followup — parent owns the interaction) in `src/cogs/module_cog.py` — Note: FR-006 is vacuously satisfied here; `DriverRoundAttendance` is not introduced in this increment. Future disable logic MUST NOT add `DELETE` statements for that table.
- [ ] T010 [US1] Add cascade call `await self._disable_attendance(interaction, cascade=True)` inside `_disable_results` after R&S is disabled, guarded by `if attendance_enabled` check; uses `ATTENDANCE_MODULE_CASCADE_DISABLED` audit change type in `src/cogs/module_cog.py`
- [ ] T011 [US1] Add `attendance_on` lookup and `"  Attendance: {on/off}"` line to the modules block in `season_review` in `src/cogs/season_cog.py`

**Checkpoint**: `/module enable attendance`, `/module disable attendance`, cascading disable, and `/season review` module status all work independently.

---

## Phase 4: User Story 2 — Configure RSVP and Attendance Channels per Division (Priority: P2)

**Goal**: `/division rsvp-channel` and `/division attendance-channel` store channel IDs per division; both appear in `/season review`; both are required before a season can be approved.

**Independent Test**: Set RSVP and attendance channels for a division, confirm they appear in `/season review`. Attempt `/season approve` without channels set — confirm a clear error names the division and the missing channel type(s). Confirm both channels appear after setting.

### Tests for User Story 2

- [ ] T012 [P] [US2] Write division config unit tests (`test_get_config_none_before_create`, `test_set_rsvp_channel`, `test_set_attendance_channel`, `test_set_channel_preserves_other_channel`) in `tests/unit/test_attendance_service.py`

### Implementation for User Story 2

- [ ] T013 [US2] Add `get_division_config`, `set_rsvp_channel`, and `set_attendance_channel` methods (upsert preserving the other channel field) to `src/services/attendance_service.py`
- [ ] T014 [P] [US2] Implement `/division rsvp-channel <division> <channel>` command (module-enabled guard, season lookup, division lookup, INSERT OR REPLACE via `attendance_service.set_rsvp_channel`, audit entry, `post_log`) in `src/cogs/season_cog.py`
- [ ] T015 [P] [US2] Implement `/division attendance-channel <division> <channel>` command (same pattern as T014 but for `set_attendance_channel`) in `src/cogs/season_cog.py`
- [ ] T016 [US2] Add per-division RSVP and attendance channel rows to the division loop in `season_review` (gated on `attendance_on`; show `*(not set)*` if unset) in `src/cogs/season_cog.py`
- [ ] T017 [US2] Add attendance approval gate (Gate 4) to `_do_approve` — checks all divisions have both `rsvp_channel_id` and `attendance_channel_id`; blocks with named-division error if any missing — in `src/cogs/season_cog.py`

**Checkpoint**: Channel commands work; `/season review` shows channel assignments; approval is correctly gated.

---

## Phase 5: User Story 3 — Configure RSVP Timing Parameters (Priority: P3)

**Goal**: Three `/attendance config` commands (`rsvp-notice`, `rsvp-last-notice`, `rsvp-deadline`) update the timing fields on `attendance_config` with invariant enforcement and ACTIVE-season rejection.

**Independent Test**: Set `rsvp-notice 7` → accepted. Set `rsvp-deadline 100` (violates `7×24=168 > last_notice=1`, deadline=100 > last_notice=1) → rejected with clear message. Attempt any timing command during an ACTIVE season → rejected.

### Tests for User Story 3

- [ ] T018 [P] [US3] Write timing invariant unit tests (`test_timing_invariant_valid`, `test_timing_invariant_notice_too_small`, `test_timing_invariant_deadline_exceeds_last`, `test_timing_invariant_last_zero_sentinel_valid`, `test_timing_invariant_last_equals_deadline_rejected`) in `tests/unit/test_attendance_service.py`

### Implementation for User Story 3

- [ ] T019 [US3] Create `/attendance` command group with `config` subgroup scaffold in `src/cogs/attendance_cog.py`
- [ ] T020 [US3] Implement `/attendance config rsvp-notice <days>` (module-enabled guard, ACTIVE-season guard, `validate_timing_invariant` with new `notice_days`, UPDATE attendance_config, ephemeral confirm) in `src/cogs/attendance_cog.py`
- [ ] T021 [US3] Implement `/attendance config rsvp-last-notice <hours>` (same guards + invariant with new `last_notice_hours`) in `src/cogs/attendance_cog.py`
- [ ] T022 [US3] Implement `/attendance config rsvp-deadline <hours>` (same guards + invariant with new `deadline_hours`) in `src/cogs/attendance_cog.py`
- [ ] T023 [US3] Register `attendance_cog` in `src/bot.py`

**Checkpoint**: All three timing commands accept valid values, reject invariant violations, and reject ACTIVE-season attempts.

---

## Phase 6: User Story 4 — Configure Attendance Point Penalties and Sanction Thresholds (Priority: P4)

**Goal**: Five `/attendance config` commands update the five penalty/threshold fields. `autosack 0` and `autoreserve 0` store `NULL` (disabled).

**Independent Test**: Run `no-rsvp-penalty 2` → field updated. Run `autosack 0` → threshold stored as `NULL`. Run `autosack 5` → threshold stored as `5`. All commands rejected if module is not enabled.

### Tests for User Story 4

- [ ] T024 [P] [US4] Write penalty/threshold config unit tests (`test_config_penalty_fields_update`, `test_autosack_zero_stores_null`, `test_autoreserve_zero_stores_null`) in `tests/unit/test_attendance_service.py`

### Implementation for User Story 4

- [ ] T025 [P] [US4] Implement `/attendance config no-rsvp-penalty <points>` (module-enabled guard, non-negative int validation, UPDATE no_rsvp_penalty) in `src/cogs/attendance_cog.py`
- [ ] T026 [P] [US4] Implement `/attendance config no-attend-penalty <points>` (same pattern) in `src/cogs/attendance_cog.py`
- [ ] T027 [P] [US4] Implement `/attendance config no-show-penalty <points>` (same pattern) in `src/cogs/attendance_cog.py`
- [ ] T028 [P] [US4] Implement `/attendance config autosack <points>` (0 → store `NULL`, else store value) in `src/cogs/attendance_cog.py`
- [ ] T029 [P] [US4] Implement `/attendance config autoreserve <points>` (0 → store `NULL`, else store value) in `src/cogs/attendance_cog.py`

**Checkpoint**: All five penalty/threshold commands work with correct zero-to-null handling and module-guard rejection.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T030 Run full test suite and confirm all tests pass: `python -m pytest tests/ -v` from repo root — FR-011 (test-mode compatibility) is passively satisfied in this increment; no driver roster queries are performed. An explicit test will be required in the RSVP automation increment.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately. T001 and T002 are parallel.
- **Phase 2 (Foundational)**: Depends on Phase 1 complete. T004 can run in parallel with T003. T005 depends on both T003 and T004 completing first.
- **Phase 3 (US1)**: Depends on Phase 2 complete. T006 (tests) can be written in parallel with T007–T011.
- **Phase 4 (US2)**: Depends on Phase 2 complete. T013 must precede T014/T015 (both need service methods). T016 and T017 depend on T013. T016 also depends on T011 (reads the `attendance_on` variable added to `season_review` by T011).
- **Phase 5 (US3)**: Depends on Phase 2 complete. T019 (scaffold) must precede T020–T022.
- **Phase 6 (US4)**: Depends on T019 complete (relies on the cog scaffold).
- **Phase 7 (Polish)**: Depends on all phases complete.

### User Story Dependencies

- **US1 (P1)**: Depends on Phase 2 only — no dependencies on US2/US3/US4.
- **US2 (P2)**: Depends on Phase 2 and US1 complete (uses `is_attendance_enabled` guard). T013 service methods must precede T014/T015/T016/T017. T016 additionally depends on T011 (requires `attendance_on` in `season_review` scope).
- **US3 (P3)**: Depends on Phase 2 only (uses `validate_timing_invariant` from T003). US1 is a prerequisite at runtime (module-enabled guard) but not a build-time dependency.
- **US4 (P4)**: Depends on T019 (attendance_cog scaffold from US3). All five commands T025–T029 are parallel with each other.

### Parallel Opportunities

Within Phase 1: T001 ∥ T002  
Within Phase 2: T003 → (T004 ∥ T005)  
Within US1 (after Phase 2): T006 ∥ T007 → (T008 ∥ T009 ∥ T010) → T011  
Within US2 (after T013): T014 ∥ T015; then T016 → T017  
Within US2 tests: T012 ∥ T013  
Within US3 (after T019): T020 ∥ T021 ∥ T022  
Within US4 (after T019): T025 ∥ T026 ∥ T027 ∥ T028 ∥ T029  
US3 and US4 can overlap (T019 is their shared prerequisite)

---

## Summary

| Metric | Count |
|--------|-------|
| Total tasks | 30 |
| Phase 1 (Setup) | 2 |
| Phase 2 (Foundational) | 3 |
| US1 (Enable/Disable) | 6 |
| US2 (Channel Config) | 6 |
| US3 (Timing Config) | 5 |
| US4 (Penalty Config) | 6 |
| Polish | 1 |
| Test tasks | 4 (T006, T012, T018, T024) |
| Parallelisable [P] tasks | 17 |

**MVP scope**: Phase 1 + Phase 2 + Phase 3 (US1) — 11 tasks. Delivers working enable/disable lifecycle with cascading disable and season review integration.

**Suggested delivery order**: US1 → US2 → US3 + US4 in parallel → Polish.
