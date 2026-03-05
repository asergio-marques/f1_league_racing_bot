---

description: "Task list for test mode bug fixes (branch 009-test-mode-bugfix)"
---

# Tasks: Test Mode Bug Fixes

**Input**: Design documents from `specs/009-test-mode-bugfix/`
**Prerequisites**: plan.md ✅ spec.md ✅

**Status**: All tasks complete — implementation committed on `009-test-mode-bugfix`.

**Organization**: Tasks are grouped by user story (one bug per story) to enable
independent review and verification.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no unmet dependencies)
- **[Story]**: Which user story / bug this task addresses (US1–US3)
- No story label on Setup and Polish phase tasks

---

## Phase 1: Setup

**Purpose**: Create the feature specification directory and documents so the branch
has full SpecKit traceability.

- [X] T001 Create `specs/009-test-mode-bugfix/` with `plan.md` and `spec.md`

**Checkpoint**: Feature directory present — bug-fix tasks can begin.

---

## Phase 2: Foundational (Blocking Prerequisites)

**N/A** — No new infrastructure, no migrations, no new files required. All fixes are
targeted edits to two existing source files. Bug-fix phases may proceed immediately.

---

## Phase 3: User Story 1 — Mystery Rounds Must Not Appear as "Next Round" (P1) 🎯

**Goal**: `/season-status` reports "None remaining" for any division whose only
incomplete rounds are Mystery rounds. Non-Mystery rounds are still surfaced correctly.

**Independent Test**: Seed a season with one fully-phased Normal round and one Mystery
round; run `/season-status`; verify the bot reports "None remaining" for that division.

- [X] T002 [P] [US1] Add `r.format != RoundFormat.MYSTERY` guard to the `next_round`
  generator expression inside `season_status` in `src/cogs/season_cog.py`

**Checkpoint**: Bug 1 resolved — `/season-status` no longer cites Mystery rounds as pending.

---

## Phase 4: User Story 2 — Season Ends Correctly When Advance Queue Is Exhausted (P2)

**Goal**: When `/test-mode advance` is called after all non-Mystery phases are done,
the Season is ended immediately if it is still `ACTIVE` (safety net), or the user
receives a "nothing to advance" message if the season is already cleared.

**Independent Test**: Enable test mode; advance all phases; call advance one more time;
verify the season is cleared and `/season-status` reports "No active season found."

- [X] T003 [US2] Replace the bare `followup.send` early-return (when `entry is None`) in
  the `advance` command with a safety-net block that calls `get_active_season` — if a
  season is still live, cancel the pending scheduled job and call `execute_season_end`
  before responding; otherwise send the existing "nothing to advance" message — in
  `src/cogs/test_mode_cog.py`

**Checkpoint**: Bug 2 resolved — season cannot remain stuck `ACTIVE` after test-mode
advance exhausts all phases.

---

## Phase 5: User Story 3 — Test-Mode Commands Respect the Configured Interaction Role (P2)

**Goal**: All three `/test-mode` subcommands are accessible to holders of the configured
interaction role (set via `/bot-init`) and rejected for users without it — regardless of
admin/Manage Server permissions. The commands do not appear in DMs.

**Independent Test**: After adding fixes and re-syncing the command tree, verify that a
non-admin user with the interaction role can issue `/test-mode toggle` successfully, and
that an admin without the role is rejected by `channel_guard`.

- [X] T004 [US3] Add `guild_only=True` and `default_permissions=None` to the
  `test_mode = app_commands.Group(...)` class attribute in `src/cogs/test_mode_cog.py`
  so Discord resets any cached platform-level restriction on the next tree sync and
  `channel_guard` remains the sole enforcement gate

**Checkpoint**: Bug 3 resolved — Discord no longer applies admin-level restrictions to
`/test-mode` commands; a fresh `/tree sync` propagates the corrected permissions.

---

## Final Phase: Quality Gates

**Purpose**: Confirm all fixes are consistent with the constitution and that the full
test suite remains green.

- [X] T005 [P] Add Sync Impact Report entry to `.specify/memory/constitution.md`
  documenting all three bugs, their root causes, fixes, and which principles each
  correction restores
- [X] T006 [P] Run `pytest tests/ -q` and verify all 162 tests pass with no regressions

**Checkpoint**: Branch ready for PR.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately.
- **Foundational (Phase 2)**: N/A — skipped.
- **Bug Fixes (Phases 3–5)**: All depend only on Setup. Each targets a different file or
  a clearly separate section of the same file; they may be worked in parallel:
  - T002 is in `season_cog.py` — fully independent of T003/T004.
  - T003 and T004 are both in `test_mode_cog.py` but in non-overlapping sections
    (method body vs. class attribute). Sequential ordering is recommended to avoid
    merge friction on the same file.
- **Quality Gates (Final Phase)**: Depend on all bug-fix phases being complete.

### User Story Dependencies

- **US1 (T002)**: Independent of US2 and US3.
- **US2 (T003)**: Independent of US1 and US3.
- **US3 (T004)**: Independent of US1 and US2; same file as T003 — apply sequentially.
- **Quality Gates (T005, T006)**: Depend on T002, T003, T004 all being complete.

---

## Parallel Execution Examples

```text
# T002 (season_cog.py) and T004 (test_mode_cog.py Group attr) are safe to parallelize:
Task T002: next_round mystery guard in src/cogs/season_cog.py
Task T004: guild_only + default_permissions in src/cogs/test_mode_cog.py

# T003 (test_mode_cog.py advance body) must be sequential with T004 (same file):
Task T004 first → then Task T003

# T005 and T006 can run in parallel once T002/T003/T004 are done:
Task T005: constitution.md sync report
Task T006: pytest tests/ -q
```

---

## Implementation Strategy

All three bugs are isolated one-to-two-line edits in existing files. No new files, no
new dependencies, no migrations. The recommended order for sequential work is:

1. **T001** — create feature docs (this file + plan.md + spec.md).
2. **T002** — season_cog next_round fix (simplest, self-contained).
3. **T004** — test_mode_cog Group attribute fix (class-level, top of file).
4. **T003** — test_mode_cog advance safety net (method body, lower in file).
5. **T005 + T006** — constitution sync and test verification.

Each fix can be independently validated before moving to the next.
