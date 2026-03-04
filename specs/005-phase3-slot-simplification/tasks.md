# Tasks: Phase 3 Slot Simplification

**Input**: Design documents from `specs/005-phase3-slot-simplification/`  
**Prerequisites**: plan.md (above), spec amendment in `specs/001-league-weather-bot/spec.md` (FR-024, Clarifications 2026-03-04)

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1 = slot simplification)

---

## Phase 1: Setup

**Purpose**: Create feature directory structure (spec + plan already exist).

- [X] T001 Confirm `specs/005-phase3-slot-simplification/` directory and plan.md are present on branch `005-phase3-slot-simplification`

---

## Phase 2: Foundational (Blocking Prerequisites)

No new infrastructure required. The existing `message_builder.py`, `phase3_service.py`,
and test suite are already in place. Skip directly to implementation.

**Checkpoint**: Branch `005-phase3-slot-simplification` is checked out — confirmed ✅

---

## Phase 3: User Story 1 — Phase 3 Slot Simplification Output (Priority: P1) 🎯 MVP

**Goal**: When all drawn slots for a session are identical, the forecast channel post renders
the session as a single type label; the calculation log post renders the label plus the raw
draw sequence in parentheses. Sessions with a single drawn slot are exempt.

**Independent Test**: Run `pytest tests/unit/test_message_builder.py` — new tests for both
format helpers must pass. Run the existing `pytest tests/unit/` suite — nothing must regress.

### Implementation for User Story 1

- [X] T002 [P] [US1] Add `format_slots_for_forecast(slots: list[str]) -> str` helper to `src/utils/message_builder.py`
  - If `len(slots) > 1` and all entries are identical: return the single type label (e.g., `Clear`)
  - Otherwise: return the existing `" → ".join(f"*{s}*" for s in slots)` format
  - Single-slot case (`len(slots) == 1`): return the single label with no arrow, no simplification marker
- [X] T003 [P] [US1] Add `format_slots_for_log(slots: list[str]) -> str` helper to `src/utils/message_builder.py`
  - If `len(slots) > 1` and all entries are identical: return `"<type> (draws: <slot>, <slot>, ...)"`, e.g. `"Clear (draws: Clear, Clear, Clear)"`
  - Otherwise: return the existing `" → ".join(slots)` format (no italics needed for log)
  - Single-slot case (`len(slots) == 1`): return the single label verbatim — no parenthetical
- [X] T004 [US1] Update `phase3_message` in `src/utils/message_builder.py` to call `format_slots_for_forecast(slots)` per session instead of the inline `" → ".join(...)` expression (depends on T002)
- [X] T005 [US1] Update `phase3_service.py`: add a `"slots_display"` key to each `session_draws` entry, set to `format_slots_for_log(slots)`, before the `payload` dict is serialised and passed to `phase_log_message` (depends on T003)
  - Import `format_slots_for_log` from `utils.message_builder`
  - Add `"slots_display": format_slots_for_log(slots)` in the loop that builds `session_draws`

### Tests for User Story 1

- [X] T006 [P] [US1] Add unit tests for `format_slots_for_forecast` in `tests/unit/test_message_builder.py`
  - All-same multi-slot: `["Clear", "Clear", "Clear"]` → `"Clear"`
  - Mixed types: `["Clear", "Wet", "Clear"]` → `"*Clear* → *Wet* → *Clear*"`
  - Single slot: `["Wet"]` → `"Wet"` (no simplification marker, no arrow)
  - All five canonical types covered in at least one all-same test: Clear, Light Cloud, Overcast, Wet, Very Wet
- [X] T007 [P] [US1] Add unit tests for `format_slots_for_log` in `tests/unit/test_message_builder.py`
  - All-same multi-slot: `["Clear", "Clear", "Clear"]` → `"Clear (draws: Clear, Clear, Clear)"`
  - Mixed types: `["Clear", "Wet"]` → `"Clear → Wet"`
  - Single slot: `["Overcast"]` → `"Overcast"`
- [X] T008 [US1] Verify the full unit suite passes: `pytest tests/unit/` — no regressions in `test_phase3.py`, `test_math_utils.py`, or other existing tests (depends on T004, T005, T006, T007)

**Checkpoint**: At this point the slot-simplification rule is fully implemented and all unit
tests pass. The feature is independently verifiable without a running Discord bot.

---

## Final Phase: Polish & Cross-Cutting Concerns

- [X] T009 [P] Update `specs/001-league-weather-bot/tasks.md` — add a note in the Phase 3 section that FR-024 was amended on 2026-03-04; reference `specs/005-phase3-slot-simplification/tasks.md`
- [X] T010 [P] Confirm no stale `" → ".join(...)` pattern remains in `phase3_message` after T004 (`grep -n "join" src/utils/message_builder.py`)

---

## Dependencies

```
T002 ──► T004 ──► T008
T003 ──► T005 ──► T008
T006 ──────────► T008
T007 ──────────► T008
```

T001, T009, T010 are fully independent of all other tasks.

## Parallel Execution

All of T002, T003, T006, T007 operate on different functions/sections of the same file and
can be implemented in a single pass (no merge conflicts if done in order within the file):
1. Write T006 + T007 tests first (they will fail — good)
2. Implement T002 + T003 helpers (tests now pass)
3. Apply T004 + T005 wiring changes
4. Run T008 full suite validation
5. Apply T009 + T010 polish

## Implementation Strategy

MVP = T001 → T002 → T003 → T004 → T005 → T006 → T007 → T008 (all tasks — feature is small enough to deliver in one increment).
