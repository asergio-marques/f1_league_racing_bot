# Tasks: Attendance Tracking

**Input**: Design documents from `specs/033-attendance-tracking/`
**Branch**: `033-attendance-tracking`

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel with other [P]-marked tasks (different files, no incomplete dependencies)
- **[US#]**: User story this task belongs to (maps to spec.md)
- Exact file paths included in all descriptions

---

## Phase 1: Setup

**Purpose**: Create the migration file that all service-layer work depends on.

- [ ] T001 Create `src/db/migrations/032_attendance_tracking.sql` — 2 `ALTER TABLE` statements (add `points_awarded` + `total_points_after` to `driver_round_attendance`; add `attendance_message_id` to `attendance_division_config`) and `CREATE TABLE attendance_pardons` with `UNIQUE(attendance_id, pardon_type)` constraint (see data-model.md §1 for full DDL)

---

## Phase 2: Foundational

**Purpose**: Update shared dataclasses. All user stories read these models; changes must land before any service-layer work begins.

**⚠️ CRITICAL**: No user story implementation can begin until T001 and T002 are complete.

- [ ] T002 Update `src/models/attendance.py` — (1) add `points_awarded: int | None` and `total_points_after: int | None` fields to `DriverRoundAttendance`; (2) add `attendance_message_id: str | None` field to `AttendanceDivisionConfig`; (3) add new `AttendancePardon` dataclass (id, attendance_id, pardon_type, justification, granted_by, granted_at) — see data-model.md §2

**Checkpoint**: Migration + models ready — user story work can begin.

---

## Phase 3: User Story 1 — Automatic Round Attendance Recording (Priority: P1) 🎯 MVP

**Goal**: On penalty-review approval, mark `attended` for every full-time driver in the division based on presence in `DriverSessionResult` rows; exclude Reserve-team drivers; no-op when module disabled.

**Independent Test**: Approve penalty review for a round with a known roster; query `driver_round_attendance` and verify `attended = 1/0` per driver, no row updated for the Reserve-team driver, and no change when module is disabled (`/module disable attendance` first).

- [ ] T003 [P] [US1] Implement `record_attendance_from_results(db_path, round_id, division_id)` in `src/services/attendance_service.py` — query `DriverSessionResult` for all sessions in the round; for each full-time driver in the division (excluding drivers whose `team_instances.is_reserve = 1` for that round), set `attended = 1` if any `DriverSessionResult` row exists for that driver in any session of the round (outcome modifier is irrelevant — DSQ/DNS counts as attended), `attended = 0` otherwise; honour upgrade-only rule (never revert `1 → 0`); no-op if attendance module is disabled (FR-001–FR-004)
- [ ] T004 [US1] Hook `record_attendance_from_results` into `finalize_penalty_review` in `src/services/result_submission_service.py` — add call immediately after the `UPDATE rounds SET result_status = 'POST_RACE_PENALTY'` block and before the appeals prompt is posted; guard with attendance module enabled check

**Checkpoint**: US1 independently testable — full-time drivers get `attended` flags at finalization.

---

## Phase 4: User Story 2 — Attendance Pardon in Penalty Wizard (Priority: P2)

**Goal**: Tier-2 admin can stage attendance pardons during penalty review via a new button + modal; staged pardons displayed in wizard summary; pardons persisted to DB at finalization; pardon button absent in appeals review stage.

**Independent Test**: Open penalty wizard for a round with a NO_RSVP driver. Click "Attendance Pardon", submit a valid NO_RSVP pardon — verify it appears in the wizard prompt. Attempt a duplicate or invalid pardon type and verify rejection. Approve penalties and verify `attendance_pardons` row exists in DB. Verify button is absent in the AppealsReviewView prompt.

- [ ] T005 [P] [US2] Add `StagedPardon` dataclass and `_CID_PARDON` constant to `src/services/penalty_wizard.py` — `StagedPardon` fields: `driver_user_id: int`, `driver_profile_id: int`, `attendance_id: int`, `pardon_type: str`, `justification: str`, `grantor_id: int` (see data-model.md §3)
- [ ] T006 [US2] Add `staged_pardons: list[StagedPardon]` field (default `field(default_factory=list)`) to `PenaltyReviewState` dataclass in `src/services/penalty_wizard.py` (depends T005)
- [ ] T007 [US2] Implement `AddPardonModal` class in `src/services/penalty_wizard.py` — 3-field discord.ui.Modal (Discord User ID, pardon type `NO_RSVP/NO_ATTEND/NO_SHOW`, justification); `on_submit`: resolve `driver_profile_id` via `resolve_driver_profile_id`; fetch `DriverRoundAttendance` row for this round; validate pardon type against `rsvp_status` + `attended` per FR-007; reject duplicate `(attendance_id, pardon_type)` already in `state.staged_pardons`; check `rounds.result_status != 'POST_RACE_PENALTY'` (FR-011); append `StagedPardon` to `state.staged_pardons`; log justification via `bot.output_router.post_log` (calc-log channel; FR-010) (depends T006)
- [ ] T008 [US2] Add "🏳️ Attendance Pardon" button with `custom_id = _CID_PARDON` to `PenaltyReviewView` in `src/services/penalty_wizard.py` — on click opens `AddPardonModal`; button must NOT be added to `AppealsReviewView` (FR-005) (depends T007)
- [ ] T009 [US2] Extend `_render_prompt_content` in `src/services/penalty_wizard.py` to append a "**Staged Attendance Pardons**" subsection when `state.staged_pardons` is non-empty — one line per pardon: `@mention — <pardon_type> [justification logged]` (depends T006)
- [ ] T010 [US2] Persist `state.staged_pardons` to `attendance_pardons` table inside `finalize_penalty_review` in `src/services/result_submission_service.py` — INSERT each `StagedPardon` row before the attendance pipeline runs; use `INSERT OR IGNORE` to tolerate idempotent re-runs (depends T007; runs before T012)

**Checkpoint**: US2 independently testable — pardon button works, validation rejects invalid types/duplicates, pardons survive to DB.

---

## Phase 5: User Story 3 — Attendance Point Distribution at Finalization (Priority: P2)

**Goal**: Immediately after penalty approval, compute and persist `points_awarded` and `total_points_after` for every full-time driver using the point rules table and any persisted pardons.

**Independent Test**: Finalize a round with drivers covering all 4 RSVP/attendance scenarios plus one with a NO_RSVP pardon staged (T010 persisted it); query `driver_round_attendance` and confirm each driver's `points_awarded` and `total_points_after` match the expected values from the US3 rules table.

- [ ] T011 [P] [US3] Implement `distribute_attendance_points(db_path, round_id, division_id)` in `src/services/attendance_service.py` — for every full-time driver with an `attended`-set row in this round: fetch `rsvp_status`, `attended`, and all `attendance_pardons` rows for that driver; apply the US3 point rules table (no_rsvp_penalty, no_attend_penalty, no_show_penalty, pardons waive the matching component); write `points_awarded`; compute `total_points_after` as `SUM(points_awarded)` over all finalized rounds for this driver in this division up to and including the current round (FR-012–FR-015)
- [ ] T012 [US3] Hook `distribute_attendance_points` into `finalize_penalty_review` in `src/services/result_submission_service.py` — call after `record_attendance_from_results` and T010 pardon persistence; guard with module enabled check (depends T004, T010)

**Checkpoint**: US3 independently testable — correct point values in DB after every finalization.

---

## Phase 6: User Story 4 — Attendance Sheet Posting (Priority: P3)

**Goal**: After point distribution, delete the prior sheet message (if any) and post a new one to the division's attendance channel, sorted descending by total points with a threshold footer; persist the new message ID.

**Independent Test**: Finalize a round; verify a message appears in the attendance channel listing all full-time drivers in descending point order with correct mentions and footer. Finalize a second round; verify the old message is deleted and new one posted. Manually delete the sheet message and finalize a third round; verify no error and a new sheet is posted.

- [ ] T013 [P] [US4] Implement `post_attendance_sheet(bot, guild, round_id, division_id)` in `src/services/attendance_service.py` — fetch `AttendanceDivisionConfig` for the division; if `attendance_message_id` set, delete the prior message via `guild.get_channel(attendance_channel_id).fetch_message(...)` and `delete()`, silently skip on `discord.NotFound`; build message: header line, one `@mention — X attendance points` line per full-time driver sorted by `total_points_after DESC` then display name alphabetically, threshold footer lines (omit lines where threshold is null/0); if no full-time drivers exist, post the header and footer lines only (do not skip the post entirely); post to `attendance_channel_id`; UPDATE `attendance_division_config.attendance_message_id` to new message ID (FR-016–FR-021)
- [ ] T014 [US4] Hook `post_attendance_sheet` into `finalize_penalty_review` in `src/services/result_submission_service.py` — call after `distribute_attendance_points`; pass `state.bot` and `interaction.guild`; wrap in try/except to log failures without blocking the appeals prompt (depends T012)

**Checkpoint**: US4 independently testable — sheet posted after every finalization, old one deleted first.

---

## Phase 7: User Story 5 — Automatic Sanction Enforcement (Priority: P3)

**Goal**: After the sheet is posted, evaluate every full-time driver's `total_points_after` in a single pass; apply autosack (via `placement_service.sack_driver`) or autoreserve (via `unassign_driver` then `assign_driver` to Reserve team) as appropriate; produce audit log entries; skip disabled thresholds and drivers already in Reserve.

**Independent Test**: Set `autoreserve_threshold = 1`, `autosack_threshold = 3`; finalize a round where Driver A accumulates 2 points and Driver B accumulates 4 points. Verify Driver A is moved to Reserve team (and not autosacked), Driver B is sacked from all divisions, one audit entry each; verify Driver A already in Reserve is skipped if they accumulate 2 points in a subsequent round.

- [ ] T015 [P] [US5] Implement `enforce_attendance_sanctions(bot, guild, db_path, round_id, division_id, server_id, season_id)` in `src/services/attendance_service.py` — fetch `AttendanceConfig` for `autoreserve_threshold` and `autosack_threshold`; for each full-time driver in the division, fetch their `total_points_after` for this round; single-pass evaluation: if `autosack_threshold` enabled and met → call `bot.placement_service.sack_driver(...)`, post audit log, skip autoreserve check for this driver; else if `autoreserve_threshold` enabled and met → check `team_instances.is_reserve` for current seat, skip if already in Reserve, else call `bot.placement_service.unassign_driver(...)` then `assign_driver(...)` for Reserve team name, post audit log; skip all action if `total_points_after` is null (not yet distributed) (FR-022–FR-027)
- [ ] T016 [US5] Hook `enforce_attendance_sanctions` into `finalize_penalty_review` in `src/services/result_submission_service.py` — call after `post_attendance_sheet`; fetch `server_id` and `season_id` from round context already loaded in the function; wrap in try/except to log failures without blocking remaining work (depends T014)

**Checkpoint**: US5 independently testable — autosack and autoreserve fire correctly at threshold crossings.

---

## Phase 8: User Story 6 — Attendance Recalculation on Round Amendment (Priority: P3)

**Goal**: When a round amendment is approved, re-run the full attendance pipeline (recording → distribution → sheet → sanctions) for the amended round using updated `DriverSessionResult` rows; preserve existing `AttendancePardon` rows; propagate changes to `total_points_after` for any subsequent rounds in the same division.

**Independent Test**: Finalize a round (sheet posted, Driver A absent=false). Amend results to add Driver A. Approve amendment. Verify Driver A's `attended` flips to 1, `points_awarded` recalculated (any prior pardon preserved), `total_points_after` updated, sheet reposted (old deleted), sanctions re-evaluated.

- [ ] T017 [P] [US6] Implement `recalculate_attendance_for_round(bot, guild, db_path, round_id, division_id, server_id, season_id)` in `src/services/attendance_service.py` — call `record_attendance_from_results` (full recompute from the updated result set; FR-003 upgrade-only rule does NOT apply here — amendment is a deliberate correction and may flip `attended` in either direction; do NOT delete existing pardons); call `distribute_attendance_points` (reads persisted `attendance_pardons` rows, FR-029 preserved); recompute `total_points_after` for all affected drivers across ALL subsequent finalized rounds in the same division (delta propagation, FR-030); call `post_attendance_sheet`; call `enforce_attendance_sanctions` (FR-031)
- [ ] T018 [US6] Hook `recalculate_attendance_for_round` into `approve_amendment` in `src/services/amendment_service.py` — after standings recomputation; fetch `guild` via `bot.get_guild(server_id)`; fetch `season_id` and `division_id` from the amended round; guard with attendance module enabled check; wrap in try/except to log failures without blocking amendment finalization (depends T017)

**Checkpoint**: US6 independently testable — full pipeline reruns correctly on amendment.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T019 [P] Write unit tests in `tests/unit/test_attendance_tracking.py` — 15 tests as enumerated in research.md §8, covering FR-001–FR-031: attendance recording, reserve exclusion, upgrade-only flag, module-disabled no-op, pardon validation rejection cases (3 pardon types), all 6 point distribution scenarios, points accumulation across rounds, sheet ordering + tiebreak, footer omission, silent skip on missing prior message, autosack-supersedes-autoreserve, skip-already-reserved, threshold-disabled no-op, amendment preserves pardons
- [X] T020 Run `python -m pytest tests/ -v` from repo root and confirm all existing tests plus T019 tests pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 — first story, no story-level dependencies
- **US2 (Phase 4)**: Depends on Phase 2 — independent of US1, but `finalize_penalty_review` pardon persistence (T010) must precede T012
- **US3 (Phase 5)**: Depends on T004 (US1 hook) and T010 (US2 pardon persistence) being complete
- **US4 (Phase 6)**: Depends on T012 (US3 hook)
- **US5 (Phase 7)**: Depends on T014 (US4 hook)
- **US6 (Phase 8)**: Depends on T016 (US5 hook) — needs full pipeline before recalculation is meaningful
- **Polish (Phase 9)**: Depends on T018

### User Story Dependencies

```
Phase 1 → Phase 2 → US1 (T003, T004)
                            ↗
Phase 2 → US2 (T005–T010) ↘
                             → US3 (T011, T012) → US4 (T013, T014) → US5 (T015, T016) → US6 (T017, T018) → Polish
```

- **US1 and US2 can be developed in parallel** (different files; both require only Phase 2)
- **US3 is gated on T004 (US1 hook) + T010 (US2 pardon persistence)**
- **US4, US5, US6 are sequential** — each builds on the previous pipeline step in the same function

### Parallel Opportunities Per Story

| Story | Parallelisable tasks | Sequential tasks |
|-------|---------------------|-----------------|
| US1 | T003 [P] (attendance_service.py) alongside T005–T006 [US2, penalty_wizard.py] | T004 after T003 |
| US2 | T005 [P] alongside T003 [US1] | T006 → T007 → T008/T009 → T010 |
| US3 | T011 [P] (attendance_service.py) alongside T013 or T015 if phase-gating is relaxed | T012 after T011 |
| US4 | T013 [P] can be coded while T011/T012 are reviewed | T014 after T013 |
| US5 | T015 [P] can be coded while T013/T014 are reviewed | T016 after T015 |
| US6 | T017 [P] can be coded while T015/T016 are reviewed | T018 after T017 |

---

## Implementation Strategy

**MVP**: Complete Phases 1–5 (US1 + US2 + US3). This delivers the core attendance data pipeline with full pardon support and correct points in the DB — the attendance sheet and sanctions can follow without blocking data integrity.

**Increment 2**: Add Phase 6 (US4 — sheet posting). Visible output for admins.

**Increment 3**: Add Phases 7–8 (US5 + US6 — sanctions and amendment recalculation). Consequential actions last, after the data pipeline is proven correct.

**Testing**: T019 can be written incrementally — write and run the tests relevant to each US as that phase completes, rather than waiting until the end.
