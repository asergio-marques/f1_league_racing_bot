---

description: "Task list for 001-fix-rpc-formula"
---

# Tasks: Rpc Formula Divisor, Phase 1 Label & Season-End Pickling

**Input**: `specs/001-fix-rpc-formula/plan.md`, `specs/001-fix-rpc-formula/spec.md`
**Branch**: `001-fix-rpc-formula`
**Organization**: Tasks grouped by user story; independent files are parallelizable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)

---

## Phase 1: Setup

*No setup required — branch already exists and project structure is unchanged.*

---

## Phase 2: Foundational (Blocking Prerequisites)

*No foundational changes — all three fixes are isolated to existing files with no shared infrastructure impact.*

---

## Phase 3: User Story 1 — Wrong Rpc Divisor (Priority: P1) 🎯 MVP

**Goal**: Correct `compute_rpc` so rain probability reflects `round(Btrack * R1 * R2 / 3025, 2)` instead of the inflated `/ 3.025` value that always clamps to 1.0.

**Independent Test**: Call `compute_rpc(0.25, 79, 4)` → `0.03`; call `compute_rpc(0.3, 98, 98)` → `0.95`; clamp test `compute_rpc(1.0, 98, 98)` → `1.0`.

- [X] T001 [P] [US1] Fix divisor literal and docstring in `src/utils/math_utils.py` (`3.025` → `3025` on lines 30 and 35)
- [X] T002 [P] [US1] Update test comment and expected-value assertion in `tests/unit/test_math_utils.py` (`/ 3.025` → `/ 3025` on lines 30 and 32)

**Checkpoint**: `compute_rpc(0.25, 79, 4)` returns `0.03`; `compute_rpc(0.3, 98, 98)` returns `0.95`.

---

## Phase 4: User Story 2 — Internal Label in Phase 1 Message (Priority: P2)

**Goal**: Remove the internal notation `(Rpc)` from the user-facing Phase 1 forecast message so the line reads `**Rain Probability**: X%`.

**Independent Test**: String returned by `phase1_message()` contains `**Rain Probability**:` and does not contain `(Rpc)`.

- [X] T003 [P] [US2] Remove `(Rpc)` from the Rain Probability label in `src/utils/message_builder.py` (line 16: `"Rain Probability (Rpc):"` → `"Rain Probability:"`)

**Checkpoint**: Phase 1 message contains `**Rain Probability**:` with no parenthetical; all other content unchanged.

---

## Phase 5: User Story 3 — Advance Command Hangs After Final Phase (Priority: P3)

**Goal**: Fix the APScheduler closure-pickling bug so that after advancing the last Phase 3, the `advance` command sends a completion response and the season is correctly cleaned up. Applies to both test-mode and scheduled operation.

**Root cause**: `check_and_schedule_season_end` passed an unpicklable closure over `bot` to `SQLAlchemyJobStore.add_job`, raising `PicklingError` mid-coroutine, silently killing `followup.send` and `execute_season_end`.

**Independent Test**: With test mode active, advance to the final Phase 3. Bot must reply with the season-complete message (not hang). `/season-setup` must succeed immediately after. No active season row must remain in the DB.

- [X] T004 [US3] Add module-level `_season_end_job(server_id, season_id)` function, `_season_end_callback` field, `register_season_end_callback()` method, and update `schedule_season_end` to accept `season_id: int` (passing kwargs to `_season_end_job`) in `src/services/scheduler_service.py`
- [X] T005 [P] [US3] Remove `_cb` closure from `check_and_schedule_season_end`; call `schedule_season_end(server_id, fire_at, season_id_captured)` in `src/services/season_end_service.py`
- [X] T006 [P] [US3] Register `_season_end_cb(server_id, season_id)` via `register_season_end_callback` after `register_callbacks` in `src/bot.py`
- [X] T007 [P] [US3] Wrap `await runner(entry["round_id"], self.bot)` in try/except; send error followup on exception in `src/cogs/test_mode_cog.py`
- [X] T008 [P] [US3] Update `_FakeScheduler.schedule_season_end` stub and test assertion from `callback` to `season_id` in `tests/unit/test_season_end_service.py`

**Checkpoint**: Advancing the final phase sends the season-complete message; DB has no active season; no `PicklingError`; any phase runner exception produces an error followup instead of a hanging interaction.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Verify no regressions across the full test suite after all three fixes.

- [X] T009 Run `python -m pytest tests/ -q` and confirm all 91 tests pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phases 1–2**: Empty — no blocking work.
- **Phase 3 (US1)**: Start immediately. T001 and T002 are fully parallel (different files).
- **Phase 4 (US2)**: Independent of US1. T003 can run in parallel with T001/T002.
- **Phase 5 (US3)**: T004 modifies `scheduler_service.py` and must complete before T005–T008 (which depend on the new signature/method). T005–T008 are parallel with each other.
- **Phase 6 (Polish)**: Depends on T001–T008 all being complete.

### User Story Dependencies

- **US1 (P1)**: No dependencies — start immediately.
- **US2 (P2)**: No dependencies — start immediately.
- **US3 (P3)**: T004 must precede T005–T008; T005–T008 are then parallel.

### Parallel Opportunities

| Task(s) | File(s) | Parallel with |
|---------|---------|---------------|
| T001 | `src/utils/math_utils.py` | T002, T003, T004 |
| T002 | `tests/unit/test_math_utils.py` | T001, T003, T004 |
| T003 | `src/utils/message_builder.py` | T001, T002, T004 |
| T004 | `src/services/scheduler_service.py` | T001, T002, T003 |
| T005 | `src/services/season_end_service.py` | T006, T007, T008 (after T004) |
| T006 | `src/bot.py` | T005, T007, T008 (after T004) |
| T007 | `src/cogs/test_mode_cog.py` | T005, T006, T008 (after T004) |
| T008 | `tests/unit/test_season_end_service.py` | T005, T006, T007 (after T004) |

Optimal execution: T001 + T002 + T003 + T004 in one multi-replace pass, then T005 + T006 + T007 + T008 in a second pass, then T009.

---

## Implementation Strategy

**MVP scope**: T001 alone restores correct Rpc values. T002–T003 complete the cosmetic/test cleanup. T004–T008 fix the session-end pickling bug that leaves the bot permanently broken. All work is on the same branch.

**Delivery order**:
1. T001, T002, T003, T004 — first multi-replace pass (parallel, different files)
2. T005, T006, T007, T008 — second multi-replace pass (after T004 lands, parallel)
3. T009 — full test run to confirm no regressions
