---

description: "Task list for test mode bug fixes (branch 009-test-mode-bugfix)"
---

# Tasks: Test Mode Bug Fixes

**Input**: Design documents from `specs/009-test-mode-bugfix/`
**Prerequisites**: plan.md ✅ spec.md ✅

**Status**: All tasks complete — implementation committed on `009-test-mode-bugfix` (covers all 6 bugs).

**Total tasks**: 12 (T001–T012) across 8 phases  
**Parallel opportunities**: T002, T005, T008 (different files, no shared deps)  
**MVP scope**: Phase 3 (US1) alone is independently deployable

---

## Phase 1: Setup

**Purpose**: Create the feature specification directory and documents so the branch
has full SpecKit traceability.

- [x] T001 Create `specs/009-test-mode-bugfix/` with `plan.md`, `spec.md`, and `tasks.md`

**Checkpoint**: Feature directory present — bug-fix phases can begin.

---

## Phase 2: Foundational (Blocking Prerequisites)

**N/A** — No new infrastructure, no migrations, no new files required. All fixes are
targeted edits to existing source files. Bug-fix phases may proceed immediately.

---

## Phase 3: User Story 1 — Mystery Rounds Must Not Appear as "Next Round" (P1) 🎯

**Story goal**: `/season-status` reports "None remaining" for any division whose only
incomplete rounds are Mystery rounds. Non-Mystery rounds are still surfaced correctly.

**Independent test criteria**: Seed a season with one fully-phased Normal round and one
Mystery round; run `/season-status`; verify the bot reports "None remaining" for that
division.

- [x] T002 [P] [US1] Add `r.format != RoundFormat.MYSTERY` guard to the `next_round`
  generator expression inside `season_status` in `src/cogs/season_cog.py`

**Checkpoint**: Bug 1 resolved — `/season-status` no longer cites Mystery rounds as
pending.

---

## Phase 4: User Story 2 — Season Ends Correctly When Advance Queue Is Exhausted (P2)

**Story goal**: When `/test-mode advance` is called after all non-Mystery phases are done,
the season is ended immediately if still `ACTIVE` (safety net), or the user receives
"nothing to advance" if the season is already cleared.

**Independent test criteria**: Enable test mode; advance all phases; call advance one more
time; verify the season is cleared and `/season-status` reports "No active season found."

- [x] T003 [US2] Replace the bare `followup.send` early-return (when `entry is None`) with
  a safety-net block: call `get_active_season` — if a season is still live cancel the
  scheduled job and call `execute_season_end`; otherwise send "nothing to advance" — in
  `src/cogs/test_mode_cog.py`

**Checkpoint**: Bug 2 resolved — season cannot remain stuck `ACTIVE`.

---

## Phase 5: User Story 3 — Test-Mode Commands Respect Interaction Role (P2)

**Story goal**: All three `/test-mode` subcommands are accessible to holders of the
configured interaction role and rejected for users without it. Commands do not appear in
DMs.

**Independent test criteria**: A non-admin user with the interaction role can issue
`/test-mode toggle`; an admin without the role is rejected by `channel_guard`.

- [x] T004 [US3] Add `guild_only=True` and `default_permissions=None` to the
  `test_mode = app_commands.Group(...)` class attribute in `src/cogs/test_mode_cog.py`
  so Discord resets any cached platform-level restriction on the next tree sync

**Checkpoint**: Bug 3 resolved — `channel_guard` is the sole enforcement gate.

---

## Phase 6: User Story 4 — Mystery Round Notice Dispatched via Test-Mode Advance (P1)

**Story goal**: `/test-mode advance` detects Mystery rounds whose notices have not been
sent, fires them via `run_mystery_notice`, and marks the round done. Subsequent calls skip
noticed rounds.

**Independent test criteria**: Seed a Mystery round with `phase1_done=0`; call
`/test-mode advance`; confirm notice posted and `phase1_done=1`; call advance again and
confirm the Mystery round is not mentioned.

- [x] T005 [P] [US4] Add `round_number: int` to `PhaseEntry` TypedDict; widen
  `get_next_pending_phase` query to include all rounds (remove `format != 'MYSTERY'`
  filter); return `phase_number=0` entry for unnoticed Mystery rounds; skip noticed ones
  — in `src/services/test_mode_service.py`
- [x] T006 [US4] Add `phase_number == 0` dispatch block before `phase_runners` dict in
  the advance command: call `run_mystery_notice`, set `phase1_done=1` on success, reply
  with notice-sent ephemeral; on failure reply with error ephemeral without setting flag
  — in `src/cogs/test_mode_cog.py`
- [x] T007 [US4] Update `tests/unit/test_test_mode_service.py`: rename
  `test_mystery_rounds_excluded` → `test_mystery_round_notice_pending_returns_entry`
  (assert `phase_number==0`); add new `test_mystery_round_notice_done_excluded` (assert
  `result is None` when `phase1_done=1`)

**Checkpoint**: Bug 4 resolved — Mystery notices fire during test-mode advance.

---

## Phase 7: User Story 5 — Reset Clears `forecast_messages` Without FK Violation (P1)

**Story goal**: `/bot-reset` completes on any server that has had Phase 1 run. No FK
constraint error; `forecast_messages` is empty after reset.

**Independent test criteria**: Run Phase 1; seed a `forecast_messages` row; issue
`/bot-reset`; confirm command succeeds and `forecast_messages` is empty.

- [x] T008 [P] [US5] Add `DELETE FROM forecast_messages WHERE round_id IN ({ph})` after
  `phase_results` delete and before `rounds` delete in `src/services/reset_service.py`
- [x] T009 [US5] Add `test_reset_deletes_forecast_messages` regression test in
  `tests/unit/test_reset_service.py` — seeds a row, calls `reset_server_data`, asserts
  no FK error and zero rows remain

**Checkpoint**: Bug 5 resolved — reset transaction never raises FK constraint failed.

---

## Phase 8: User Story 6 — Advance Logs Show User-Visible Round Number (P3)

**Story goal**: Log lines from `/test-mode advance` include `round=<round_number>`
(user-visible) and `id=<round_id>` (DB primary key) for all dispatch paths.

**Independent test criteria**: Issue `/test-mode advance`; confirm log line contains
both `round=<N>` and `id=<M>`.

- [x] T010 [US6] Update log line in `src/cogs/test_mode_cog.py` advance command to emit
  `round=<entry["round_number"]>` alongside `id=<entry["round_id"]>` for both the mystery
  notice path and the normal phase-runner path

**Checkpoint**: Bug 6 resolved — logs are unambiguous to league managers.

---

## Final Phase: Polish & Cross-Cutting Concerns

**Purpose**: Confirm all fixes are consistent with the constitution and that the full
test suite is green.

- [x] T011 [P] Add Sync Impact Report entry to `.specify/memory/constitution.md`
  documenting all six bugs, their root causes, fixes, and which principles each restores
- [x] T012 [P] Run `pytest tests/ -q` and verify all 164 tests pass with no regressions

**Checkpoint**: Branch ready for PR — 164 tests passing, constitution updated.

---

## Dependencies Section

### Story completion order

```
T001 (Setup)
  ├─► T002 [US1] — independent (season_cog.py only)
  ├─► T003 [US2] — independent; same file as T004/T006/T010, apply sequentially
  ├─► T004 [US3] — independent; same file as T003/T006/T010, apply sequentially
  ├─► T005 [US4] → T006 [US4] → T007 [US4]   (T006 needs T005's PhaseEntry change)
  ├─► T008 [US5] → T009 [US5]
  └─► T005 → T010 [US6]                        (T010 needs round_number from T005)

T002–T010 all complete ──► T011, T012 (parallel)
```

### Parallel execution examples per story

```text
# US1, US4 service, US5 service — fully independent across files:
T002 (season_cog.py) ║ T005 (test_mode_service.py) ║ T008 (reset_service.py)

# US2/US3/US4-cog/US6 share test_mode_cog.py — sequential within that file:
T004 → T003 → T006 → T010

# Tests follow their subject:
T007 after T005  |  T009 after T008

# Quality gates in parallel once all implementation tasks done:
T011 ║ T012
```

### Implementation strategy

MVP is US1 alone (T002) — independently testable and deployable.
Recommended sequential order for single-developer work:

1. T001 — feature docs
2. T002 — `season_cog` mystery guard (simplest, fully isolated)
3. T004 — `test_mode` Group permissions (class attribute, top of file)
4. T003 — advance safety net (method body, same file)
5. T005 — `PhaseEntry` + widened query (service layer, no cog deps)
6. T006 — mystery dispatch in cog (depends on T005)
7. T010 — log line update (depends on T005's `round_number` field)
8. T008 — `reset_service` FK chain fix (fully isolated)
9. T007 — service tests (T005 must be complete)
10. T009 — reset regression test (T008 must be complete)
11. T011 + T012 — constitution sync and test verification
