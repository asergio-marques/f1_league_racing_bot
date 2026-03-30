# Tasks: Penalty Posting, Appeals, and Result Lifecycle

**Input**: Design documents from `specs/026-penalty-posting-appeals/`  
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅, quickstart.md ✅

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story label (US1–US5 maps to spec.md priorities P1–P5)
- Every task includes an exact file path

## Path Conventions

Single project — `src/`, `tests/` at repository root.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the DB migration that all user stories depend on.

- [ ] T001 Create src/db/migrations/026_result_status_penalty_records.sql — adds `result_status TEXT NOT NULL DEFAULT 'PROVISIONAL'` to `rounds`, populates it from `finalized`, adds `penalty_channel_id TEXT` to `division_results_configs`, creates `penalty_records` and `appeal_records` tables (full SQL in specs/026-penalty-posting-appeals/data-model.md)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Model and dataclass changes required before any user story can be implemented.

⚠️ **CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T002 Update Round dataclass in src/models/round.py — replace `finalized: bool = False` with `result_status: str = "PROVISIONAL"`; update all code in the same file that reads `round.finalized` to read `round.result_status == "FINAL"` instead
- [ ] T003 [P] Extend StagedPenalty dataclass in src/services/penalty_service.py with two new fields: `description: str = ""` and `justification: str = ""`

**Checkpoint**: Foundation ready — user story implementation can now begin.

---

## Phase 3: User Story 1 — Result Lifecycle Labeling and Heading Format (Priority: P1) 🎯 MVP

**Goal**: Three-state round lifecycle (`PROVISIONAL` → `POST_RACE_PENALTY` → `FINAL`), standard heading and lifecycle label on every results and standings post, submission channel stays open after penalty approval and transitions to a minimal appeals prompt, round results amend gated to `FINAL`.

**Independent Test**: Submit a round → approve penalty review with zero staged penalties → approve appeals review with zero staged corrections. Verify three distinct results/standings reposts each carry the correct heading format and lifecycle label (`Provisional Results`, `Post-Race Penalty Results`, `Final Results`). Verify `round results amend` is rejected on a `PROVISIONAL` round and accepted on a `FINAL` round. No verdicts channel configuration needed.

- [ ] T004 [US1] Add `heading: str` and `label: str` parameters to all results and standings post/repost functions in src/services/results_post_service.py — every function that produces a results or standings Discord message must accept and prepend the heading line and label line (see contract specs/026-penalty-posting-appeals/contracts/result-post-heading-format.md)
- [ ] T005 [US1] Update the initial submission results and standings post calls in src/services/result_submission_service.py to compute and pass the heading (`Season {N} {DivisionName} Round {X} — {SessionName}`) and label `"Provisional Results"` to the updated functions from T004
- [ ] T006 [US1] Extract `finalize_penalty_review(interaction, state)` from `finalize_round()` in src/services/result_submission_service.py — sets `rounds.result_status = 'POST_RACE_PENALTY'`, reposts results/standings with `"Post-Race Penalty Results"` label, keeps the submission channel open, and posts a minimal `AppealsReviewView` (Approve button only at this stage — full wizard expansion in T018) to the channel
- [ ] T007 [US1] Wire `ApprovalView.approve_btn` in src/services/penalty_wizard.py to call `finalize_penalty_review()` instead of `finalize_round()`; after re-wiring, confirm no other callers of `finalize_round()` remain (grep the codebase) and remove the function — it is dead code once this task is complete
- [ ] T008 [US1] Add `finalize_appeals_review(interaction, state)` in src/services/result_submission_service.py — sets `rounds.result_status = 'FINAL'`, reposts results/standings with `"Final Results"` label, closes the submission channel; add minimal `AppealsReviewView(discord.ui.View, timeout=None)` class with a single Approve button to src/services/penalty_wizard.py and wire its Approve button to call `finalize_appeals_review()`; also add `staged_appeals: list[StagedPenalty] = field(default_factory=list)` to `PenaltyReviewState` in the same file — this is the staging list for appeal corrections, mirroring `staged_penalties`, consumed by T018
- [ ] T009 [US1] Gate the `round results amend` command handler in src/cogs/results_cog.py — read `result_status` for the target round and reject with a clear error message if it is not `'FINAL'`; ensure the amend repost always calls the results post function with label `"Final Results"`
- [ ] T010 [US1] Update the `standings sync` and `rounds sync` forced-repost command handlers in src/cogs/results_cog.py to read the round's current `result_status` and pass the corresponding label to the results post functions

**Checkpoint**: Full three-state lifecycle is working end-to-end. US1 independently testable.

---

## Phase 4: User Story 2 — Division Verdicts Channel Configuration (Priority: P2)

**Goal**: `/division verdicts-channel` command stores a per-division announcements channel. `/season review` displays it. `/season approve` blocks if it is missing on any division.

**Independent Test**: Run `/division verdicts-channel`, verify channel stored in `division_results_configs.penalty_channel_id`. Run `/season review` and verify the verdicts channel appears in the division block. Attempt `/season approve` with a division missing the channel and verify it is rejected with the expected error.

- [ ] T011 [P] [US2] Extend `get_divisions_with_results_config()` in src/services/season_service.py — add `drc.penalty_channel_id` to the SELECT and populate `div.penalty_channel_id` on each returned Division object
- [ ] T012 [US2] Add `/division verdicts-channel <division> <channel>` subcommand to the `division` `app_commands.Group` in src/cogs/season_cog.py — validates the bot can access the channel (`channel.permissions_for(guild.me).send_messages`), UPSERTs `penalty_channel_id` on the matching `division_results_configs` row, writes a `VERDICTS_CHANNEL_SET` audit log entry; see contract specs/026-penalty-posting-appeals/contracts/division-verdicts-channel-command.md for exact responses
- [ ] T013 [US2] Update the `/season review` per-division block in src/cogs/season_cog.py to display a `Verdicts channel: <#id>` (or `*(not configured)*`) line after the standings channel line, using `div.penalty_channel_id` from T011
- [ ] T014 [US2] Extend the R&S prerequisites block in `/season approve` (Gate 2) in src/cogs/season_cog.py — add a check for missing `penalty_channel_id` per division; append an error entry per division and block approval with the same error format as the existing results/standings channel checks

**Checkpoint**: Verdicts channel command, review display, and approval gate all work. US2 independently testable.

---

## Phase 5: User Story 3 — Penalty Announcements (Priority: P3)

**Goal**: Each approved penalty produces one announcement post per penalty in the verdicts channel. Announcement contains all five required fields as specified in the contract. If the verdicts channel is inaccessible, the announcement is skipped without blocking finalization.

**Independent Test**: Stage a penalty (with description and justification populated via US5 tasks if done, or temporarily hardcoded strings if not), click Approve, verify one announcement post appears in the verdicts channel with heading, driver mention, penalty translation, description, and justification.

**Note**: Description and justification values in the staged penalty come from the modal fields added in US5 (T021–T022). In sequential implementation, complete T021–T022 before expecting populated announcement content. The service itself can be built against the `StagedPenalty.description` and `StagedPenalty.justification` fields already added in T003.

- [ ] T015 [US3] Create src/services/verdict_announcement_service.py with:
  - `translate_penalty(penalty_str: str) -> str` — converts `+Ns` → `"{N} seconds removed"`, `-Ns` → `"{N} seconds added"`, `"DSQ"` → `"Disqualified"`
  - `post_penalty_announcements(bot, state, applied_penalties)` — resolves target channel (`penalty_channel_id` only; skip if NULL or inaccessible), posts one message per penalty in the format defined in specs/026-penalty-posting-appeals/contracts/announcement-message-format.md
  - `post_appeal_announcements(bot, state, applied_corrections)` — identical contract, called from appeals approval (T019)
- [ ] T016 [US3] Extend `apply_penalties()` in src/services/penalty_service.py to INSERT one `penalty_records` row per applied penalty (columns: `driver_session_result_id`, `penalty_type`, `time_seconds`, `description`, `justification`, `applied_by`, `applied_at`, `announcement_channel_id`); `announcement_channel_id` is populated with the channel ID where the announcement was actually posted, or NULL if skipped; return the list of inserted records so the caller can pass them to the announcement service; also remove the existing writes to `driver_session_results.post_race_time_penalties` and `post_stewarding_total_time` from `apply_penalties()` — new records MUST only be stored in `penalty_records`
- [ ] T017 [US3] Call `verdict_announcement_service.post_penalty_announcements()` from `finalize_penalty_review()` in src/services/result_submission_service.py after `apply_penalties()` completes; pass only the non-empty applied list (no call when staged list was empty)

**Checkpoint**: Penalty announcements post correctly. US3 independently testable.

---

## Phase 6: User Story 4 — Appeals Review Stage (Priority: P4)

**Goal**: After penalty approval, the transient channel transitions to a full appeals wizard mirroring the penalty review wizard (Add Correction / No Changes / Approve). Approved corrections are applied, standings recomputed, `Final Results` posted, channel closed. Bot restart re-posts the appeals prompt.

**Independent Test**: Approve a penalty review → stage one appeal correction in the appeals prompt → click Approve → verify channel closes, round is `FINAL`, `Final Results` post appears, appeal announcement posted.

**Note**: The `AddPenaltyModal` is reused as-is in this phase (the modal already has description/justification fields after T021–T022 from US5). If implementing sequentially before US5, the modal will lack those fields temporarily — this is acceptable for structural testing of the appeals view itself.

- [ ] T018 [US4] Expand `AppealsReviewView` in src/services/penalty_wizard.py with Add Correction button (opens `_SessionSelectView` → `AddPenaltyModal`, staged to `state.staged_appeals`), No Changes/Confirm button, and Make Changes recovery button — mirror the `PenaltyReviewView` structure exactly; update `finalize_penalty_review()` to post the full `AppealsReviewView` instead of the minimal stub from T008
- [ ] T019 [US4] Extend `finalize_appeals_review()` in src/services/result_submission_service.py to: apply staged appeal corrections to `driver_session_results` rows (reusing the penalty application mechanics); INSERT one `appeal_records` row per correction (columns per data-model.md); cascade-recompute standings from the affected round; call `verdict_announcement_service.post_appeal_announcements()`; then close the channel
- [ ] T020 [US4] Register `AppealsReviewView` in the bot restart recovery handler in src/bot.py — query for rounds with `result_status = 'POST_RACE_PENALTY'` that still have an open submission channel and re-post the appeals review prompt, matching the restart recovery guarantee from spec 023 FR-014

**Checkpoint**: Full appeals wizard functional end-to-end. US4 independently testable.

---

## Phase 7: User Story 5 — Mandatory Description and Justification Fields on the Modal (Priority: P5)

**Goal**: `AddPenaltyModal` gains two new required `TextInput` fields (description, justification). Discord's built-in required-field validation blocks submission without them. Same modal reused for both wizards. Staged penalty and audit log carry the values.

**Independent Test**: Open the modal, leave description empty → Discord rejects submission. Leave justification empty → Discord rejects. Fill all four fields → staged entry carries values verbatim.

**Dependency note**: These tasks complete the data pipeline for US3 and US4. If implementing sequentially after US3/US4, completing T021–T022 will immediately populate announcement content and audit log entries that previously held empty strings.

- [ ] T021 [US5] Extend `AddPenaltyModal` in src/services/penalty_wizard.py with two new `discord.ui.TextInput` fields: `description_input` (label `"Penalty description"`, `required=True`, `max_length=200`, `style=discord.TextStyle.paragraph`) and `justification_input` (label `"Justification"`, `required=True`, `max_length=200`, `style=discord.TextStyle.paragraph`)
- [ ] T022 [US5] Update `AddPenaltyModal.on_submit()` in src/services/penalty_wizard.py to read `self.description_input.value` and `self.justification_input.value` and pass them to the `StagedPenalty` constructor
- [ ] T023 [P] [US5] Update the audit log entries written in `finalize_penalty_review()` and `finalize_appeals_review()` in src/services/result_submission_service.py to include the description and justification for each applied penalty and correction

**Checkpoint**: Full end-to-end pipeline complete — modal → staged penalty → announcement → audit log all carry description and justification.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Test coverage and quickstart validation.

- [ ] T024 [P] Run `python -m pytest tests/ -v` from repo root and confirm all pre-existing tests pass with the `result_status` / `finalized` change from T002
- [ ] T025 Update tests/unit/test_penalty_wizard.py — add tests for `AppealsReviewView` approve-only path, expanded `AppealsReviewView` with Add/Confirm, and `finalize_penalty_review()` wiring; update any existing tests that reference `finalize_round()` or `rounds.finalized`
- [ ] T026 [P] Update tests/unit/test_results_post_service.py — assert that every post function produces the standard heading format and includes the correct lifecycle label; cover all three label values
- [ ] T027 [P] Create tests/unit/test_verdict_announcement_service.py — `translate_penalty()` edge cases; `post_penalty_announcements()` posts to verdicts channel when configured and accessible; skips cleanly (no exception) when `penalty_channel_id` is None or channel is inaccessible; posts nothing and does not raise when staged list is empty
- [ ] T028 Create tests/integration/test_round_lifecycle.py — full `PROVISIONAL` → `POST_RACE_PENALTY` → `FINAL` lifecycle: three distinct post events, correct labels on each, channel-close only at FINAL, `round results amend` rejected at PROVISIONAL and POST_RACE_PENALTY, accepted at FINAL; also cover zero-staged-list transitions per FR-009 (zero penalties staged → still advances to `POST_RACE_PENALTY`) and FR-010 (zero corrections staged → still advances to `FINAL`)
- [ ] T029 Validate full manual flow per specs/026-penalty-posting-appeals/quickstart.md

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — blocks all user stories
- **US1 (Phase 3)**: Depends on Phase 2 — no dependency on US2–US5
- **US2 (Phase 4)**: Depends on Phase 2 — no dependency on US1 or US3–US5
- **US3 (Phase 5)**: Depends on Phase 2 — structurally independent; for fully populated announcement content, US5 T021–T022 should be done first
- **US4 (Phase 6)**: Depends on US1 (T008 — AppealsReviewView stub must exist); for fully populated appeal content, US5 T021–T022 should be done first
- **US5 (Phase 7)**: Depends on Phase 2 — provides the data pipeline that completes US3 and US4 content
- **Polish (Phase 8)**: Depends on all desired user stories being complete

### User Story Dependencies

| Story | Blocked by | Notes |
|-------|-----------|-------|
| US1 (P1) | Phase 2 | Fully independent of all other stories |
| US2 (P2) | Phase 2, T011 | T012–T014 all modify `season_cog.py` — do sequentially |
| US3 (P3) | T003, T016 | `StagedPenalty` fields (T003) and `apply_penalties` PenaltyRecord output (T016) must exist; announcement content populated by US5 |
| US4 (P4) | T008 (stub view), T019 | Corrections applied in `finalize_appeals_review()` from US1; full modal content populated by US5 |
| US5 (P5) | T003 | Completes the data pipeline for US3 and US4 |

### Parallel Opportunities Within Phases

- **Phase 2**: T002 and T003 touch different files — run in parallel
- **Phase 3**: T009 and T010 both modify `results_cog.py` — do sequentially; T004–T008 are sequential in `results_post_service.py` and `result_submission_service.py`; T011 (US2) can be started in parallel with Phase 3 work since it is in a separate file
- **Phase 5**: T015 (`verdict_announcement_service.py` — new file) can be started in parallel with T016 (`penalty_service.py`)
- **Phase 8**: T024, T026, T027 are all independent and can run in parallel

---

## Parallel Example: US1 + US2 early start

```bash
# Once Phase 2 is complete, these can proceed in parallel:
# Thread A — US1 lifecycle
Task T004: src/services/results_post_service.py heading + label
→ T005: src/services/result_submission_service.py initial post label
→ T006: finalize_penalty_review()
→ T007: wire ApprovalView.approve_btn
→ T008: finalize_appeals_review() + AppealsReviewView stub
→ T009: results_cog.py amend gate
→ T010: results_cog.py sync reposts

# Thread B — US2 season config (can start alongside Thread A)
Task T011: src/services/season_service.py query extension  [P]
→ T012: season_cog.py /division verdicts-channel command
→ T013: season_cog.py /season review display
→ T014: season_cog.py /season approve gate
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1: Setup (T001)
2. Complete Phase 2: Foundational (T002–T003)
3. Complete Phase 3: US1 (T004–T010)
4. **STOP and VALIDATE**: Submit a round, walk through zero-penalty + zero-correction lifecycle, verify all three labels and heading format, verify amend gate
5. Deploy if ready

### Recommended Sequential Order

Given that US5 (modal fields) populates the description/justification values consumed by US3 (announcements) and US4 (appeals), perform US5 before US3 and US4 when implementing solo:

1. Phase 1 → Phase 2 → **US1** → **US2** → **US5** → **US3** → **US4** → Polish

### Full Parallel Strategy

With two developers:
- **Developer A**: Phase 1 → Phase 2 → US1 → US4 → Polish (T025, T028)
- **Developer B**: T011 (Phase 2 parallel) → US2 → US5 → US3 → Polish (T026–T027)

---

## Notes

- `[P]` = different files, no incomplete dependencies
- Each user story has an **Independent Test** that can be run before the next story starts
- US5 is ordered last per the spec's priority, but its modal-field tasks (T021–T022) should be pulled forward in sequential solo implementation to populate US3/US4 content
- Commit after each phase checkpoint at minimum
- Run `python -m pytest tests/ -v` from repo root (not from `src/`)
