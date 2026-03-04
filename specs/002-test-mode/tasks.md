# Tasks: Test Mode for System Verification

**Input**: Design documents from `specs/002-test-mode/`
**Prerequisites used**: plan.md, spec.md, research.md, data-model.md, quickstart.md

**Tests**: Not requested in the specification. Unit tests for queue ordering logic are included in Polish as a robustness measure.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to
- Exact file paths are included in every description

---

## Phase 1: Setup

**Purpose**: Create the DB migration that is a prerequisite for everything else.

- [ ] T001 Create DB migration file `src/db/migrations/002_test_mode.sql` that adds `test_mode_active INTEGER NOT NULL DEFAULT 0` to `server_configs`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Update the ServerConfig model and config service to read/write the new column. All three user stories depend on these changes.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T002 [P] Add `test_mode_active: bool = False` field to `ServerConfig` dataclass in `src/models/server_config.py`
- [ ] T003 [P] Update `get_server_config` and `save_server_config` in `src/services/config_service.py` to read and write `test_mode_active`

**Checkpoint**: Foundation ready — user story implementation can now begin.

---

## Phase 3: User Story 1 — Toggle Test Mode (Priority: P1) 🎯 MVP

**Goal**: Users can enable and disable test mode. The state persists across restarts. All other test mode commands are gated behind this flag.

**Independent Test**: Issue `/test-mode toggle` → bot confirms enabled. Issue it again → bot confirms disabled. Restart the bot → run `/test-mode review` and verify it is still in the same state as before restart.

- [ ] T004 [P] [US1] Create `src/services/test_mode_service.py` with `toggle_test_mode(server_id, db_path) -> bool` async function that flips `test_mode_active` in `server_configs` and returns the new value
- [ ] T005 [US1] Create `src/cogs/test_mode_cog.py` with a `/test-mode` command group and a `toggle` subcommand decorated with `@channel_guard`; respond ephemerally with enabled/disabled confirmation
- [ ] T006 [US1] Load `TestModeCog` in `src/bot.py` alongside the existing cogs

**Checkpoint**: User Story 1 is fully functional and independently testable.

---

## Phase 4: User Story 2 — Advance to Next Phase (Priority: P2)

**Goal**: Users can advance through every phase of every round and division in priority order, one phase per command invocation, with full weather output posted to forecast and log channels.

**Independent Test**: With test mode active and a season configured, issue `/test-mode advance` repeatedly. Verify Phase 1 → Phase 2 → Phase 3 outputs are posted per round per division in `scheduled_at` order. Verify Mystery rounds are skipped. Verify a "nothing left" message appears after all phases are exhausted.

- [ ] T007 [US2] Add `get_next_pending_phase(server_id, db_path) -> dict | None` async function to `src/services/test_mode_service.py`; query returns the earliest pending `(round_id, phase_number, division_id, track_name, division_name)` tuple by sorting on `rounds.scheduled_at ASC, divisions.id ASC`; excludes Mystery rounds and already-done phases; returns `None` when all phases are complete
- [ ] T008 [US2] Add `advance` subcommand to `src/cogs/test_mode_cog.py`; guard silently if test mode inactive; call `get_next_pending_phase`, then dispatch to `run_phase1/2/3` from the appropriate service, then respond ephemerally confirming which phase/round/division was advanced (or "all phases complete" if `None`)

**Checkpoint**: User Stories 1 and 2 are both fully functional and independently testable.

---

## Phase 5: User Story 3 — Review Season Configuration (Priority: P3)

**Goal**: Users can inspect the full season configuration — all divisions, all rounds, format, track, scheduled date, and per-phase completion status — as a single ephemeral message, at any point during or before phase advancement.

**Independent Test**: With test mode active, after advancing some phases, issue `/test-mode review`. Verify all configured rounds appear, completed phases show ✅, pending phases show ⏳, and Mystery rounds show N/A.

- [ ] T009 [US3] Add `build_review_summary(server_id, db_path) -> str` async function to `src/services/test_mode_service.py`; query all divisions and rounds for the active season on this server; return a formatted string grouped by division, each round showing format, track, `scheduled_at` date, and `P1/P2/P3` completion indicators; Mystery rounds display `Phases N/A`
- [ ] T010 [US3] Add `review` subcommand to `src/cogs/test_mode_cog.py`; guard silently if test mode inactive; call `build_review_summary` and post the result as an ephemeral response

**Checkpoint**: All three user stories are fully functional and independently testable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Robustness, edge case coverage, and end-to-end validation.

- [ ] T011 [P] Add unit tests for `get_next_pending_phase` ordering logic in `tests/unit/test_test_mode_service.py`; cover: empty queue returns `None`, Mystery rounds excluded, cross-division `scheduled_at` tie-break by `division.id`, phase-number ordering within a round
- [ ] T012 Add edge case guards to `src/services/test_mode_service.py`: no active season → `advance`/`review` return an informative `None`/empty result; concurrent advance safety via `phase_done` idempotency already present in phase services (document this behaviour with a comment)
- [ ] T013 Validate the complete end-to-end `quickstart.md` walkthrough (enable test mode, 2 divisions × 2 rounds, advance all 12 phases, review, disable)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 — **blocks all user stories**
- **Phase 3 (US1)**: Depends on Phase 2 only
- **Phase 4 (US2)**: Depends on Phase 2; integrates with Phase 3 (`toggle` must work to gate `advance`)
- **Phase 5 (US3)**: Depends on Phase 2; integrates with Phase 3 (`toggle` must work to gate `review`)
- **Phase 6 (Polish)**: Depends on all user story phases

### User Story Dependencies

- **US1 (P1)**: Can start after Phase 2 — no dependency on US2 or US3
- **US2 (P2)**: Can start after Phase 2 — gated by US1's `test_mode_active` flag at runtime, but the `advance` subcommand can be written without US1 complete (flag read from DB)
- **US3 (P3)**: Can start after Phase 2 — same pattern as US2

### Parallel Opportunities

- T002 and T003 (Foundational) operate on different files — run in parallel
- T004 (create service file) and T005 (create cog file) operate on different files — run in parallel within US1
- Once Phase 2 is done, US1/US2/US3 implementation tasks can be distributed across developers
- T011 (unit tests) is independent of T012/T013 — run in parallel

---

## Parallel Example: Phase 2

```
# Both foundational tasks target different files:
T002 → src/models/server_config.py
T003 → src/services/config_service.py
```

## Parallel Example: User Story 1

```
# T004 and T005 target different files:
T004 → src/services/test_mode_service.py  (create)
T005 → src/cogs/test_mode_cog.py          (create)
# T006 depends on T005 being complete:
T006 → src/bot.py                         (register cog)
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001)
2. Complete Phase 2: Foundational (T002, T003)
3. Complete Phase 3: User Story 1 (T004, T005, T006)
4. **STOP and VALIDATE**: Toggle test mode on/off, confirm restart persistence
5. Proceed to US2 only after US1 is verified

### Incremental Delivery

1. Setup + Foundational → model and service layer ready
2. US1 → test mode toggle working → independently testable (MVP)
3. US2 → advance command working → independently testable
4. US3 → review command working → independently testable
5. Polish → tests, edge cases, quickstart validation

---

## Summary

| Phase | Tasks | User Story | Parallelisable |
|-------|-------|-----------|----------------|
| 1 — Setup | T001 | — | No |
| 2 — Foundational | T002, T003 | — | T002 ∥ T003 |
| 3 — Toggle Test Mode | T004, T005, T006 | US1 | T004 ∥ T005 |
| 4 — Advance Phase | T007, T008 | US2 | Sequential |
| 5 — Review Config | T009, T010 | US3 | Sequential |
| 6 — Polish | T011, T012, T013 | — | T011 ∥ T012 |

**Total tasks**: 13  
**Tasks per user story**: US1 — 3, US2 — 2, US3 — 2  
**Parallel opportunities**: 3 (Phase 2, Phase 3 service+cog, Polish test+edge)  
**Suggested MVP scope**: Phase 1 + Phase 2 + Phase 3 (T001–T006)
