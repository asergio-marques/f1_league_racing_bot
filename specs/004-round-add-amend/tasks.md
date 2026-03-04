# Tasks: 004 — Round-Add Duplicate Guard & Round-Amend During Setup

**Branch**: `004-round-add-amend`
**Created**: 2026-03-04
**Status**: Complete
**Source**: [plan.md](plan.md) · [spec.md](spec.md)

---

## Execution Rules

- Phases must be completed in order.
- Tasks marked **[P]** within a phase may be executed in parallel with other **[P]** tasks in the same phase, provided they touch different files.
- Tasks that touch the same file must be executed sequentially.
- Mark each task **[x]** as soon as it is done.

---

## Phase 1 — Foundational

**Purpose**: Shared helper required by US1 before `amendment_cog.py` can call into `season_cog.py`.

- [x] T001 Add `_get_pending_for_server(server_id: int) -> PendingConfig | None` to `src/cogs/season_cog.py`

---

## Phase 2 — User Story 1: `/round-amend` on Pending Configs (Priority: P1)

**Goal**: Any `@admin_only` server admin can correct a round's track, datetime, or format in a pending season config before it is committed.

**Independent Test**: Run `/season-setup` → `/division-add` → `/round-add` to create a pending config. Without running `/season-approve`, invoke `/round-amend` with a corrected track. Confirm the change is reflected in `/season-review` and persists into the approved season.

- [x] T002 [US1] Update `round_amend()` in `src/cogs/amendment_cog.py` to check pending config before DB (depends on T001)

---

## Phase 3 — User Story 2: Duplicate Round-Number Guard in `/round-add` (Priority: P1)

**Goal**: When `/round-add` detects a conflicting `round_number`, present an ephemeral 4-button prompt: Insert Before, Insert After, Replace, Cancel. 60-second timeout leaves the round list unchanged.

**Independent Test**: With a division containing round 3, call `/round-add round_number=3`. Verify the 4-button prompt appears. Select each option in separate runs and verify the resulting round list after each choice.

- [x] T003 [P] [US2] Add `_rounds_insert_before`, `_rounds_insert_after`, `_rounds_replace` to `src/cogs/season_cog.py`
- [x] T004 [US2] Add `DuplicateRoundView(discord.ui.View)` class to `src/cogs/season_cog.py` (depends on T003)
- [x] T005 [US2] Update `round_add()` in `src/cogs/season_cog.py` to detect conflict and present `DuplicateRoundView` (depends on T004)

---

## Phase 4 — Tests

- [x] T006 [P] [US1] Create `tests/unit/test_amendment_cog_pending.py` covering 9 scenarios (happy paths, field validation, error cases)
- [x] T007 [P] [US2] Create `tests/unit/test_season_cog_duplicate.py` covering mutation helpers, all 4 view branches, timeout, and round_add integration

---

## Phase 5 — Validation

- [x] T008 Run `python -m pytest tests/ -v` — all 91 tests pass

---

## Phase 6 — Reliability Infrastructure (FR-014, FR-016)

**Purpose**: Foundational services needed before any persistence wiring in Phase 7 can be written. All three tasks touch different files and can run in parallel.

- [x] T009 [P] Add `has_active_or_completed_season()`, `save_pending_snapshot()`, `load_all_setup_seasons()` to `src/services/season_service.py`
- [x] T010 [P] Fix APScheduler pickle error in `src/services/scheduler_service.py`: add `_GLOBAL_SERVICE` module sentinel and `_phase_job` module-level callable; update `start()` and `schedule_round()`
- [x] T011 [P] Add `season_id: int = 0` field to `PendingConfig`; add `_snapshot_pending()` and `recover_pending_setups()` methods to `SeasonCog` in `src/cogs/season_cog.py`

**Checkpoint**: Service layer and cog helpers ready — per-mutation wiring (Phase 7) can now proceed.

---

## Phase 7 — Per-Mutation Persistence Wiring (FR-014)

**Purpose**: Wire `_snapshot_pending()` into every command that mutates the pending config so each change is immediately crash-safe. T012–T014 touch different files and can run in parallel.

- [x] T012 [P] Update `season_setup()` in `src/cogs/season_cog.py`: replace old guards with `_get_pending_for_server` + `has_active_or_completed_season` checks; call `_snapshot_pending(cfg)` after creating the initial config (depends on T009, T011)
- [x] T013 [P] Update `division_add()` in `src/cogs/season_cog.py`: add fallback `_get_pending_for_server` lookup; call `_snapshot_pending(cfg)` after mutation (depends on T011)
- [x] T014 [P] Update `round_amend()` pending path in `src/cogs/amendment_cog.py`: call `save_pending_snapshot()` after applying in-memory changes; update `pending_cfg.season_id` (depends on T009)
- [x] T015 Add `_recover_pending_setups(bot)` function to `src/bot.py`; call it in `on_ready` after `_recover_season_end_jobs` (depends on T011)

**Checkpoint**: Every mutation is now crash-safe; startup recovery is wired.

---

## Phase 8 — Date Ordering, Approval Refactor & UX (FR-015, FR-016)

**Purpose**: Add date ordering validation to `/round-add`, refactor `_do_approve` for schedule-before-transition, wire snapshot callback into `DuplicateRoundView`.

- [x] T016 [US2] Update `round_add()` in `src/cogs/season_cog.py`: add fallback `_get_pending_for_server` lookup; add date ordering validation (FR-015); call `_snapshot_pending(cfg)` on no-conflict path; pass `post_mutation_cb=_snapshot_cb` to `DuplicateRoundView` (depends on T011)
- [x] T017 Update `DuplicateRoundView.__init__` to accept `post_mutation_cb=None`; call `post_mutation_cb()` in `insert_before_cb`, `insert_after_cb`, `replace_cb` in `src/cogs/season_cog.py`
- [x] T018 Refactor `_do_approve()` and add `_get_pending_for_server` fallback to `season_review()` in `src/cogs/season_cog.py`: guard on `cfg.season_id == 0`; load divisions/rounds from DB; call `schedule_all_rounds` before `transition_to_active`; clear all server pending entries on success (depends on T009, T011)

**Checkpoint**: Approve flow is resilient; date ordering is enforced; all FR-015/FR-016 criteria met.

---

## Phase 9 — Test Mock Updates & Final Validation

- [x] T019 [P] Add `bot.season_service.save_pending_snapshot = AsyncMock(return_value=42)` to `_make_cog()` in `tests/unit/test_amendment_cog_pending.py`
- [x] T020 [P] Add `bot.season_service.save_pending_snapshot = AsyncMock(return_value=42)` to `test_round_add_no_conflict_continues_normally` in `tests/unit/test_season_cog_duplicate.py`
- [x] T021 Run `python -m pytest tests/ -v` — all 91 tests pass (depends on T019, T020)

---

## Dependency Graph

```
T001
├── T002 [US1]
└── T003–T005 [US2]
    ├── T006 [P] [US1]
    └── T007 [P] [US2]
        └── T008

T009 [P] ──┐
T010 [P] ──┤ (independent, different files)
T011 [P] ──┘
           │
           ├── T012 [P] ─┐
           ├── T013 [P] ─┤ (different files, parallel)
           ├── T014 [P] ─┘
           └── T015
                    │
               T016, T017, T018
                    │
               T019 [P] ─┐
               T020 [P] ─┘
                    │
                  T021
```

**Parallel opportunities**:
- T002 ‖ T003 (different files, after T001)
- T006 ‖ T007 (new files, after T005)
- T009 ‖ T010 ‖ T011 (different files, independent)
- T012 ‖ T013 ‖ T014 (different files, after T009 + T011)
- T019 ‖ T020 (different files, after T018)

---

## Summary

| Phase | Tasks | User Story / FR | Status |
|-------|-------|-----------------|--------|
| 1 — Foundational | T001 | — | ✅ |
| 2 — US1 pending-amend | T002 | US1 (P1) | ✅ |
| 3 — US2 duplicate guard | T003–T005 | US2 (P1) | ✅ |
| 4 — Tests | T006–T007 | US1 + US2 | ✅ |
| 5 — Validation | T008 | — | ✅ |
| 6 — Reliability infra | T009–T011 | FR-014, FR-016 | ✅ |
| 7 — Persistence wiring | T012–T015 | FR-014 | ✅ |
| 8 — Date ordering + approve | T016–T018 | FR-015, FR-016 | ✅ |
| 9 — Mocks + validation | T019–T021 | — | ✅ |

**Total tasks**: 21
**Parallelisable groups**: T002‖T003 · T006‖T007 · T009‖T010‖T011 · T012‖T013‖T014 · T019‖T020
**MVP scope**: T001 + T002 (US1 fully deliverable before US2 work begins)
