# Tasks: Results & Standings — Inline Post-Submission Penalty Review

**Input**: Design documents from `specs/023-post-submit-penalty-flow/`
**Branch**: `023-post-submit-penalty-flow`
**Date**: 2026-03-26

**Prerequisites used**: plan.md, spec.md, data-model.md, research.md, quickstart.md, contracts/penalty-review-wizard.md

**Tests**: Not generated (spec does not request TDD).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to ([US1]–[US5] per spec.md)
- Exact file paths included in all descriptions

---

## Phase 1: Setup (Schema Foundation)

**Purpose**: Land the DB migration that all subsequent phases depend on.

- [ ] T001 Create `src/db/migrations/019_round_finalized.sql` with: `ALTER TABLE rounds ADD COLUMN finalized INTEGER NOT NULL DEFAULT 0;`
- [ ] T002 Register migration 019 in the migrations runner in `src/db/migrations/__init__.py` (follow the pattern used by migrations 018 and earlier)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Update the Round model, signed penalty support, and the shared wizard state type. All user stories depend on this phase.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T003 Add `finalized: bool = False` field to the `Round` dataclass in `src/models/round.py`
- [ ] T004 Update all DB `SELECT` queries that construct `Round` objects from rows to include the `finalized` column in `src/services/season_service.py` (specifically `get_division_rounds` and any other method that maps a DB row to `Round`)
- [ ] T005 [P] Update `_TIME_PENALTY_RE` in `src/services/penalty_service.py` from `r"^\+?(\d+)s?$"` to `r"^([+-]?\d+)s?$"` so the leading sign (positive or negative) is captured
- [ ] T006 Update `validate_penalty_input` in `src/services/penalty_service.py`: replace the `seconds <= 0` guard with `seconds == 0` (reject zero only); pass the signed captured integer directly to `StagedPenalty.penalty_seconds`
- [ ] T007 Update `_apply_time_penalty` in `src/services/penalty_service.py` to clamp the result: `ms = max(0, ms + penalty_seconds * 1000)` — document the clamp in the docstring
- [ ] T008 Verify `apply_penalties` in `src/services/penalty_service.py` accumulates signed `post_race_time_penalties` without a cast error (the existing `existing_pen + penalty_s` already works for negative values; confirm no `abs()` or unsigned cast is present and remove any such guard)
- [ ] T009 Create new file `src/services/penalty_wizard.py` and define `PenaltyReviewState` dataclass there with fields: `round_id: int`, `division_id: int`, `submission_channel_id: int`, `session_types_present: list[SessionType]` (non-cancelled sessions only), `staged: list[StagedPenalty]`, `db_path: str`, `bot: Any`

**Checkpoint**: Foundation ready — Round model updated, signed penalty intake works, wizard state type defined. User story implementation can begin.

---

## Phase 3: User Story 1 — Channel Transitions to Post-Round Penalties State (Priority: P1) 🎯 MVP Entry Point

**Goal**: After the final session is submitted or cancelled, the submission channel stays open and a penalty review prompt appears instead of closing.

**Independent Test**: Submit all sessions in a round. Confirm: interim results are posted to the results channel, interim standings are posted to the standings channel, the submission channel remains open, and a penalty review prompt with Add Penalty / No Penalties / Approve buttons appears in the submission channel.

- [ ] T010 [US1] Create `PenaltyReviewView(discord.ui.View, timeout=None)` in `src/services/penalty_wizard.py` with three module-level buttons as stubs: `Add Penalty` (disabled when `state.session_types_present` is empty), `No Penalties / Confirm`, and `Approve` (visible only when `state.staged` is non-empty); store a `PenaltyReviewState` reference on the view instance
- [ ] T011 [US1] Add a trusted-admin interaction guard to every button callback in `PenaltyReviewView` (and later `ApprovalView`) in `src/services/penalty_wizard.py`: query the actor's guild roles; reject non-trusted-admin interactions with an ephemeral permissions error and return early
- [ ] T012 [US1] Modify the final-session completion handler in `src/services/result_submission_service.py` (`run_result_submission_job` or equivalent) to call `enter_penalty_state(...)` instead of `close_submission_channel(...)` once all sessions for the round are submitted or cancelled
- [ ] T013 [US1] Implement `enter_penalty_state(bot, guild, round_id, division_id, submission_channel)` in `src/services/result_submission_service.py`: post interim results per non-cancelled session to the division's results channel (reuse existing format), post interim standings to the standings channel, instantiate `PenaltyReviewState`, instantiate `PenaltyReviewView(state)`, post the penalty review prompt (driver roster by session, empty staged list) to the submission channel with the view attached, and call `bot.add_view(view)`
- [ ] T014 [US1] Add an `on_message` guard in the submission channel handler in `src/services/result_submission_service.py`: if `rounds.finalized = 0` and all expected `session_results` rows exist for this round (penalty review is active), reject any plain-text session result input with a clear message stating the round is in penalty review state

**Checkpoint**: Submission channel remains open after last session. Penalty prompt appears. Non-admins are rejected. New session text is blocked.

---

## Phase 4: User Story 2 — Trusted Admin Enters, Reviews, and Adjusts Staged Penalties (Priority: P1)

**Goal**: Admins can stage signed time penalties and DSQs, remove individual staged entries, and advance to the approval step.

**Independent Test**: With a round in penalty state, stage a +5 s penalty on the race session leader, a −3 s penalty on another race driver, and a DSQ on a qualifying driver. Verify all three appear in the staged list. Remove the DSQ. Advance to the approval step and confirm only the two time penalties remain.

- [ ] T015 [US2] Create `AddPenaltyModal(discord.ui.Modal)` in `src/services/penalty_wizard.py` with two `TextInput` fields: driver (accepts Discord @mention or raw user ID) and penalty value (accepts `+Ns`, `-Ns`, bare `N`, or `DSQ`)
- [ ] T016 [US2] Implement `Add Penalty` button callback in `src/services/penalty_wizard.py`: present session selection buttons (one per entry in `state.session_types_present`); on session chosen, open `AddPenaltyModal` with the selected session passed as context
- [ ] T017 [US2] Implement `AddPenaltyModal.on_submit` in `src/services/penalty_wizard.py`: call `penalty_service.validate_penalty_input` on the value field; enforce: (a) zero-second penalty rejected, (b) time penalty on a qualifying session rejected (only DSQ accepted), (c) driver not found in the selected session's results rejected; on valid input, instantiate `StagedPenalty` and append to `state.staged`; edit the penalty prompt message to display the updated staged list with Remove buttons; send an ephemeral error for invalid inputs
- [ ] T018 [US2] Render a per-entry Remove button alongside each staged penalty in the prompt message component in `src/services/penalty_wizard.py`; implement the Remove callback to delete only that specific entry by its list index from `state.staged` and refresh the prompt message; all other staged entries must remain unchanged
- [ ] T019 [US2] Implement `No Penalties / Confirm` button handler in `src/services/penalty_wizard.py`: if `state.staged` is empty → advance directly to the approval step; if `state.staged` is non-empty → send a confirmation prompt asking the admin to confirm clearing the list; on confirm, clear `state.staged` and advance to the approval step; on cancel, return to the penalty entry state with the list intact
- [ ] T020 [US2] Create `ApprovalView(discord.ui.View, timeout=None)` in `src/services/penalty_wizard.py` with `Make Changes` and `Approve` buttons; `Make Changes` callback re-posts (or edits) the penalty prompt with `state.staged` intact and re-attaches `PenaltyReviewView`

**Checkpoint**: All penalty staging, validation, removal, and approval-step navigation works end-to-end.

---

## Phase 5: User Story 3 — Trusted Admin Approves; Round Finalized, Final Posts Replace Interim Posts (Priority: P1)

**Goal**: Approval applies all staged penalties, replaces interim posts with final posts, marks the round FINALIZED, and closes the submission channel.

**Independent Test**: Submit a round, stage a +5 s penalty on 1st place, and approve. Confirm: the interim results post is deleted and replaced by a corrected final table, the standings post is updated, `rounds.finalized = 1`, and the submission channel is closed.

- [ ] T021 [US3] Add `delete_and_repost_final_results(db_path, round_id, division_id, guild)` to `src/services/results_post_service.py`: for each non-cancelled session in the round, fetch `session_results.results_message_id`, delete that Discord message if it exists, post the corrected final results table, and overwrite `results_message_id` in DB; then delete the current `driver_standings_snapshots.standings_message_id` message, post fresh final standings, and update `standings_message_id`
- [ ] T022 [US3] Implement `finalize_round(interaction, state)` in `src/services/result_submission_service.py` (step 1 of 3): call `penalty_service.apply_penalties(db_path, round_id, division_id, staged, actor_id, bot)` for every `StagedPenalty` in `state.staged`; recompute final positions and points for all affected sessions (reuse existing position/points recalculation path); do nothing to results rows when `state.staged` is empty
- [ ] T023 [US3] In `finalize_round` (step 2 of 3) in `src/services/result_submission_service.py`, call `results_post_service.delete_and_repost_final_results(db_path, round_id, division_id, guild)` to replace interim results and standings posts with final posts
- [ ] T024 [US3] In `finalize_round` (step 3 of 3) in `src/services/result_submission_service.py`: `UPDATE rounds SET finalized = 1 WHERE id = ?`; write a `ROUND_FINALIZED` audit log entry to `audit_entries` recording actor, division, round, and the full serialized penalty list (include empty-list case); call `close_submission_channel(channel_id, round_id, guild, db_path)`; send an ephemeral confirmation to `interaction`
- [ ] T025 [US3] Wire the `Approve` button callback on `ApprovalView` in `src/services/penalty_wizard.py` to call `result_submission_service.finalize_round(interaction, state)`

**Checkpoint**: After Approve, interim posts are gone, final posts are present, `rounds.finalized = 1`, channel is closed, audit log entry exists.

---

## Phase 6: User Story 4 — Test Mode Blocks Advancing Until Current Round Is Finalized (Priority: P2)

**Goal**: In test mode, the advance command is blocked per division if any round has submitted sessions but `finalized = 0`.

**Independent Test**: In test mode, submit a round but do not approve the penalty state. Attempt `/test-mode advance`. Confirm the bot refuses with an error naming the pending round. Approve the penalty state. Retry the advance command. Confirm it succeeds.

- [ ] T026 [P] [US4] Add `is_round_finalized(db_path: str, round_id: int) -> bool` helper in `src/services/test_mode_service.py`; update `get_next_pending_phase` in the same file to add `AND r.finalized = 0` to the results-module phase 4 subquery so finalized rounds are no longer re-surfaced in the phase queue
- [ ] T027 [US4] Add a FINALIZED gate in `src/cogs/test_mode_cog.py` advance command (phase 4 path): after the existing `is_submission_open` check, also call `is_round_finalized`; if the round has `session_results` rows but `finalized = 0`, respond with an ephemeral error identifying the blocking division name and round number, and return without advancing

**Checkpoint**: Test mode advance is blocked with a named error when penalty state is pending; it proceeds once the round is finalized. Live mode is unaffected.

---

## Phase 7: User Story 5 — Remove Standalone `round results penalize` Command (Priority: P2)

**Goal**: Deregister the old standalone penalize command so it no longer appears in the slash command registry.

**Independent Test**: After this change, attempt to invoke the old penalize command from Discord. Confirm it is absent from the slash command menu.

- [ ] T028 [P] [US5] Remove the `round_results_penalize` decorated method (~lines 1348–1600) and its local `_SessionView`, `_UserIdView`, `_PenaltyView`, and `_ReviewView` classes from `src/cogs/season_cog.py`; remove the `@round_results_group.command(name="penalize", ...)` registration; confirm the cog loads without error

**Checkpoint**: Bot starts cleanly. The `/round results penalize` command is absent from Discord.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Persistent view registration and bot-restart recovery (FR-014).

- [ ] T029 Import `PenaltyReviewView` and `ApprovalView` from `src/services/penalty_wizard.py` in `src/bot.py` and add `bot.add_view(PenaltyReviewView(state=None))` and `bot.add_view(ApprovalView(state=None))` to the persistent view setup block (alongside `SignupButtonView`, `AdminReviewView`, etc.) so Discord button interactions survive bot restarts
- [ ] T030 Implement `_recover_submission_channels(bot)` coroutine in `src/bot.py` and call it from `on_ready`: query `round_submission_channels WHERE closed = 0`; for each row, load `rounds.finalized` and check if all expected `session_results` rows exist; if `finalized = 1` mark `closed = 1` and attempt channel deletion (orphaned cleanup); if all sessions submitted and `finalized = 0` re-post the penalty review prompt into the existing channel and re-attach a new `PenaltyReviewView` instance loaded from DB state

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 — BLOCKS all user stories
- **Phase 3 (US1)**: Depends on Phase 2 — entry point for the entire flow
- **Phase 4 (US2)**: Depends on Phase 3 (reuses `PenaltyReviewView` stubs and `PenaltyReviewState`)
- **Phase 5 (US3)**: Depends on Phase 4 (consumes `state.staged` from US2)
- **Phase 6 (US4)**: Depends on Phase 2 only — can proceed in parallel with Phases 3–5
- **Phase 7 (US5)**: No dependencies — can proceed in parallel with all other phases after Phase 1
- **Phase 8 (Polish)**: Depends on Phases 3–5 (needs `PenaltyReviewView` and `ApprovalView` to be fully defined)

### User Story Dependencies

- **US1 (P1)**: Requires Phase 2 complete. No dependency on US2–US5.
- **US2 (P1)**: Requires US1 complete (builds on `PenaltyReviewView` stubs and `PenaltyReviewState`).
- **US3 (P1)**: Requires US2 complete (finalizes `state.staged` populated by US2).
- **US4 (P2)**: Requires Phase 2 only. Can begin immediately after Foundational phase.
- **US5 (P2)**: Requires nothing. Pure deletion. Can begin immediately.

### Parallel Opportunities

Within Phase 2:
- T005–T008 (penalty_service.py) can run in parallel with T003–T004 (models/ and season_service.py) — different files

Across phases (after Phase 2):
- T026–T027 (US4, test_mode_service + test_mode_cog) run in parallel with US1–US3 work
- T028 (US5, season_cog deletion) runs in parallel with any phase

---

## Parallel Example: US4 alongside US1–US3

```bash
# After Phase 2 completes, these can proceed in parallel:

# Stream A: US1 → US2 → US3 (the main wizard flow)
T010 → T011 → T012 → T013 → T014   # US1
T015 → T016 → T017 → T018 → T019 → T020   # US2
T021 → T022 → T023 → T024 → T025   # US3

# Stream B: US4 (test mode gate — independent of wizard UI)
T026 → T027

# Stream C: US5 (command removal — fully isolated)
T028
```

---

## Implementation Strategy

### MVP First (US1 + US2 + US3 — the core round lifecycle)

1. Complete Phase 1 (Setup)
2. Complete Phase 2 (Foundational) — CRITICAL gate
3. Complete Phase 3 (US1) — channel stays open, prompt appears
4. Complete Phase 4 (US2) — penalties can be staged and adjusted
5. Complete Phase 5 (US3) — finalization works end-to-end
6. **STOP and VALIDATE** using quickstart.md scenarios
7. Add Phase 6 (US4) and Phase 7 (US5) before merging

### Incremental Delivery

- After Phase 3: US1 is independently testable (channel stays open, interim posts appear)
- After Phase 4: US2 is independently testable (full staging cycle without finalization)
- After Phase 5: US3 is independently testable (full round lifecycle complete)
- After Phase 6: Test mode gate is independently testable
- After Phase 7: Old command is gone — verify via Discord slash command menu
- After Phase 8: Restart recovery is testable by simulating a bot restart mid-penalty-state

---

## Summary

| Metric | Value |
|---|---|
| Total tasks | 30 |
| Phase 1 (Setup) | 2 |
| Phase 2 (Foundational) | 7 |
| US1 tasks (Phase 3) | 5 |
| US2 tasks (Phase 4) | 6 |
| US3 tasks (Phase 5) | 5 |
| US4 tasks (Phase 6) | 2 |
| US5 tasks (Phase 7) | 1 |
| Polish tasks (Phase 8) | 2 |
| Tasks marked [P] | 4 (T005, T026, T028, T029) |
| New files | 1 (`src/services/penalty_wizard.py`) |
| Modified files | 8 (`round.py`, `season_service.py`, `penalty_service.py`, `result_submission_service.py`, `results_post_service.py`, `test_mode_service.py`, `test_mode_cog.py`, `season_cog.py`, `bot.py`) |
| Suggested MVP scope | US1 + US2 + US3 (Phases 1–5) — complete round finalization cycle |
