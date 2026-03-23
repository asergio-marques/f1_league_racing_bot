---
description: "Task list for 022-results-standings-verification"
---

# Tasks: Results & Standings — Standings Design, Sync Command, and Sort-Key Correction

**Input**: Design documents from `specs/022-results-standings-verification/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/commands.md ✅, quickstart.md ✅

---

## Phase 1: Setup

**Purpose**: No scaffolding needed — all affected files already exist. This phase confirms the working baseline passes current tests before any changes are introduced.

- [ ] T001 Verify existing test suite passes on the feature branch: run `pytest tests/unit/test_standings_service.py tests/unit/test_results_formatter.py -v` and confirm 0 failures

---

## Phase 2: Foundational

**Purpose**: Apply the sort-key defect fix (C1) in `standings_service.py`. This must be complete before US1 tests can be written and before US3 (sync command) can call the corrected service.

**⚠️ CRITICAL**: US1 tests depend on the corrected sort-key logic; US3 depends on correct standings output. Both block on this phase.

- [ ] T002 Fix `compute_driver_standings` sort-key in `src/services/standings_service.py`: replace per-entity `max_pos = max(fc.keys(), default=0)` inside `_sort_key` with a single `global_max_pos` computed before the sort over all `finish_counts` values, then pad both `count_vec` and `first_vec` to `range(1, global_max_pos + 1)`
- [ ] T003 Apply the identical `global_max_pos` fix to `compute_team_standings` sort-key in `src/services/standings_service.py`

**Checkpoint**: Both compute functions now produce correctly ordered standings for all tiebreak scenarios. Run `pytest tests/unit/test_standings_service.py -v` — existing test must still pass.

---

## Phase 3: User Story 1 — Standings correctly rank drivers and teams by the specified tiebreak hierarchy (Priority: P1) 🎯 MVP

**Goal**: Ensure the sort-key fix is verified by a comprehensive set of automated tiebreak unit tests covering every acceptance scenario.

**Independent Test**: Run `pytest tests/unit/test_standings_service.py -v -k "tiebreak"` — all 5 new tests pass; existing tests still pass.

### Implementation for User Story 1

- [ ] T004 [P] [US1] Add `test_tiebreak_p2_count` to `tests/unit/test_standings_service.py`: two drivers equal on total points and wins, A has 2 Feature Race P2 finishes, B has 1 → A ranks above B (covers acceptance scenario 1)
- [ ] T005 [P] [US1] Add `test_tiebreak_p3_vs_no_p3` to `tests/unit/test_standings_service.py`: two drivers equal on points/wins/P2s, A has 1 P3, B has 0 P3 (no P3 key in dict) → A ranks above B (covers acceptance scenario 2; validates global-pad fix)
- [ ] T006 [P] [US1] Add `test_tiebreak_first_achieved_round` to `tests/unit/test_standings_service.py`: two drivers identical finish counts, A first achieved P2 in Round 1, B in Round 3 → A ranks above B (covers acceptance scenario 3)
- [ ] T007 [P] [US1] Add `test_tiebreak_teams_same_hierarchy` to `tests/unit/test_standings_service.py`: two teams equal on total points, one has Feature Race P1 finish the other doesn't → team with P1 ranks above (covers acceptance scenario 4; uses `compute_team_standings`)
- [ ] T008 [P] [US1] Add `test_tiebreak_cross_position_set` to `tests/unit/test_standings_service.py`: A has 1 P2 finish only (max_pos=2), B has 1 P3 finish only (max_pos=3) → A ranks above B; verifies pre-fix defect does not regress (covers acceptance scenario 5)

**Checkpoint**: `pytest tests/unit/test_standings_service.py -v` — all existing + 5 new tests pass.

---

## Phase 4: User Story 2 — Reserve driver visibility toggle controls standings output (Priority: P1)

**Goal**: Verify the existing `/results reserves toggle` implementation satisfies every acceptance scenario; fix any gaps found.

**Independent Test**: With an existing unit test or manual inspection, confirm the toggle flips `reserves_in_standings`, returns the correct ephemeral message, and that `_get_show_reserves` reads and returns it correctly. The formatter correctly omits/includes reserve drivers.

### Implementation for User Story 2

- [ ] T009 [US2] Audit `reserves_toggle` handler in `src/cogs/results_cog.py` against acceptance scenarios 1–5: confirm division-not-found returns `❌ Division '{division}' not found.` (scenario 5), confirm module gate is called before any DB access, confirm ephemeral messages match contracts/commands.md wording — fix any discrepancies found
- [ ] T010 [US2] Audit `_get_show_reserves` in `src/services/results_post_service.py`: confirm it defaults to `True` when no row exists (scenario 1 default-on behaviour) and returns `False` when `reserves_in_standings = 0` — fix if incorrect
- [ ] T011 [US2] Audit `format_driver_standings` call path in `src/utils/results_formatter.py`: confirm reserve drivers with `driver_user_id in reserve_user_ids` are filtered when `show_reserves=False`, included when `True` — fix if incorrect

**Checkpoint**: All acceptance scenarios for US2 can be manually verified; no code paths skip the module gate or return wrong messages.

---

## Phase 5: User Story 3 — Trusted admin forces a standings repost via the sync command (Priority: P2)

**Goal**: Implement and expose `/results standings sync <division>`.

**Independent Test**: With an active season and at least one completed round, run `/results standings sync <division-name>`. Confirm the standings channel receives a new post. With no completed rounds, confirm the ephemeral informational message is returned. With an invalid division name, confirm the error response.

### Implementation for User Story 3

- [ ] T012 [US3] Add `repost_standings_for_division(db_path: str, division_id: int, guild: discord.Guild) -> bool` to `src/services/results_post_service.py`: query the most recent completed `round_id` for the division; if none, return `False`; else compute driver + team standings, fetch standings channel from `division_results_config`, call `_get_show_reserves`, call `post_standings`, return `True`
- [ ] T013 [US3] Add `standings_group = app_commands.Group(name="standings", description="Standings commands", parent=results_group)` and `sync` subcommand to `src/cogs/results_cog.py`: call `_module_gate`, defer ephemerally, resolve division by name, call `repost_standings_for_division`, send appropriate success/no-data/error message per contracts/commands.md

**Checkpoint**: `/results standings sync` is registered as a slash command; manually verify all three response paths (success, no data, division not found).

---

## Phase 6: User Story 4 — Reserve driver point continuity on team re-assignment (Priority: P2)

**Goal**: Audit the team re-assignment mutation path and confirm that no code path modifies historic `driver_session_results.team_role_id`. No implementation change is expected; if a defect is found it must be fixed here.

**Independent Test**: Confirm `driver_session_results.team_role_id` is not touched by any code path in `driver_service.py` or `team_service.py`. Run existing standings tests referencing reserve/team behaviour.

### Implementation for User Story 4

- [ ] T014 [US4] Audit `src/services/driver_service.py` and `src/services/team_service.py` for any statement that writes to `driver_session_results` outside of result submission — confirm no retroactive `team_role_id` mutation exists; document finding as a comment in the relevant service or fix if a defect is found

**Checkpoint**: Confirmed (or fixed) that team re-assignment never mutates historic session results. US4 acceptance scenarios hold by data model invariant.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T015 [P] Run full unit test suite `pytest tests/unit/ -v` and confirm 0 failures
- [ ] T016 [P] Run full integration test suite `pytest tests/integration/ -v` and confirm 0 failures
- [ ] T017 Verify `/results standings sync` appears correctly in Discord command autocomplete (slash command tree sync)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — run immediately.
- **Phase 2 (Foundational — C1 fix)**: Depends on Phase 1. Blocks all remaining phases.
- **Phase 3 (US1 — tiebreak tests)**: Depends on Phase 2 (corrected sort-key must be in place before tests are meaningful).
- **Phase 4 (US2 — reserves toggle audit)**: Depends on Phase 2; independent of Phase 3.
- **Phase 5 (US3 — sync command)**: Depends on Phase 2 (calls corrected standing functions); independent of Phase 3 and 4.
- **Phase 6 (US4 — continuity audit)**: Depends only on Phase 2 conceptually; fully independent of Phases 3–5.
- **Phase 7 (Polish)**: Depends on all prior phases complete.

### User Story Dependencies

| Story | Depends on | Can start after |
|-------|-----------|-----------------|
| US1 (tiebreak tests) | Phase 2 (C1 fix) | Phase 2 done |
| US2 (reserves toggle audit) | Phase 2 | Phase 2 done |
| US3 (sync command) | Phase 2 + `repost_standings_for_division` | Phase 2 done |
| US4 (continuity audit) | Phase 2 conceptually | Phase 2 done |

### Parallel Opportunities

- T004–T008 (US1 tiebreak tests): all 5 are independent DB-fixture tests — write in parallel.
- T009–T011 (US2 audit): each touches a different file — can be read/audited in parallel.
- T015–T016 (final test runs): parallel execution.

---

## Parallel Execution Example: User Story 1 Tiebreak Tests

```
Phase 2 complete ──┬── T004 (P2 count test)          ──┐
                   ├── T005 (P3 vs no-P3 test)         │
                   ├── T006 (first-achieved-round test) ├── all pass → Phase 3 done
                   ├── T007 (team hierarchy test)       │
                   └── T008 (cross-position-set test)  ──┘
```

---

## Implementation Strategy

**MVP** (Phase 2 + Phase 3): The sort-key fix plus its 5 tiebreak tests constitute the competitive integrity correction and should be delivered first.

**Full delivery** (Phases 4–6): Existing command audit, sync command, and continuity check complete the specification ratification.

**No schema migrations required.** All changes are service-layer computation and a new cog command.
