---

description: "Task list for 001-fix-rpc-formula"
---

# Tasks: Rpc Formula Divisor & Phase 1 Message Label

**Input**: `specs/001-fix-rpc-formula/plan.md`, `specs/001-fix-rpc-formula/spec.md`
**Branch**: `001-fix-rpc-formula`
**Organization**: Tasks grouped by user story; all production-code tasks are parallelizable (different files).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2)

---

## Phase 1: Setup

*No setup required — branch already exists and project structure is unchanged.*

---

## Phase 2: Foundational (Blocking Prerequisites)

*No foundational changes — both fixes are isolated to `src/utils/`. No shared infrastructure is affected.*

---

## Phase 3: User Story 1 — Wrong Rpc Divisor (Priority: P1) 🎯 MVP

**Goal**: Correct `compute_rpc` so rain probability reflects the specification formula `round(Btrack * R1 * R2 / 3025, 2)` instead of the inflated `/ 3.025` value that always clamps to 1.0.

**Independent Test**: Call `compute_rpc(0.25, 79, 4)` and assert the result is `0.03`. Call `compute_rpc(0.3, 98, 98)` and assert the result is `0.95`. Both should now be less than 1.0.

- [X] T001 [P] [US1] Fix divisor literal and docstring in `src/utils/math_utils.py` (`3.025` → `3025` on lines 30 and 35)
- [X] T002 [P] [US1] Update test comment and expected-value assertion in `tests/unit/test_math_utils.py` (`/ 3.025` → `/ 3025` on lines 30 and 32)

**Checkpoint**: `compute_rpc(0.25, 79, 4)` returns `0.03`; `compute_rpc(0.3, 98, 98)` returns `0.95`; clamp test `compute_rpc(1.0, 98, 98)` still returns `1.0`.

---

## Phase 4: User Story 2 — Internal Label in Phase 1 Message (Priority: P2)

**Goal**: Remove the internal specification notation `(Rpc)` from the user-facing Phase 1 forecast message so the line reads `**Rain Probability**: X%`.

**Independent Test**: Inspect the string returned by `phase1_message()`. It must contain `**Rain Probability**:` and must not contain `(Rpc)`.

- [X] T003 [P] [US2] Remove `(Rpc)` from the Rain Probability label in `src/utils/message_builder.py` (line 16: `"Rain Probability (Rpc):"` → `"Rain Probability:"`)

**Checkpoint**: Phase 1 message contains `**Rain Probability**:` with no parenthetical notation; all other message content is unchanged.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Verify no regressions across the full test suite after both fixes.

- [X] T004 Run `python -m pytest tests/ -q` and confirm all 91 tests pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phases 1–2**: Trivially empty — no blocking work.
- **Phase 3 (US1)**: Can start immediately — T001 and T002 touch different files and are fully parallel.
- **Phase 4 (US2)**: Independent of Phase 3 — T003 can run in parallel with T001 and T002.
- **Phase 5 (Polish)**: Depends on T001, T002, T003 all being complete.

### User Story Dependencies

- **User Story 1 (P1)**: No dependencies — start immediately.
- **User Story 2 (P2)**: No dependencies on US1 — can also start immediately.

### Parallel Opportunities

All three implementation tasks (T001, T002, T003) touch independent files and carry no runtime dependencies on each other:

| Task | File | Parallel with |
|------|------|---------------|
| T001 | `src/utils/math_utils.py` | T002, T003 |
| T002 | `tests/unit/test_math_utils.py` | T001, T003 |
| T003 | `src/utils/message_builder.py` | T001, T002 |

Optimal execution: apply T001 + T002 + T003 in a single `multi_replace_string_in_file` call, then run T004.

---

## Implementation Strategy

**MVP scope**: T001 alone restores correct Rpc values (US1 is the only user-visible correctness issue). T002 + T003 complete the fix cleanly.

**Delivery order**:
1. T001, T002, T003 — all in one multi-replace pass (parallel, different files)
2. T004 — full test run to confirm no regressions
