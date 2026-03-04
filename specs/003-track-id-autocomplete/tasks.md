# Task Plan: 003 — Track ID Autocomplete & Division Command Cleanup

**Branch**: `003-track-id-autocomplete`  
**Created**: 2026-03-04  
**Status**: Complete  
**Source**: [plan.md](plan.md)

---

## Execution Rules

- Phases must be completed in order (Setup → Tests → Core → Integration → Polish).
- Tasks marked **[P]** within a phase may be executed in parallel.
- Tasks that touch the same file must be executed sequentially.
- Mark each task **[X]** as soon as it is done.

---

## Phase 1 — Setup

### T001 — Fix bot startup crash (`bot_init` rename) [P]
- **File**: `src/cogs/init_cog.py`
- **Action**: Rename method `bot_init` → `handle_bot_init`. The `@app_commands.command(name="bot-init")` decorator preserves the Discord-facing command name.
- **Why first**: The bot cannot start until this is resolved; all other work depends on a running bot.
- **Status**: [X]

### T002 — Add `TRACK_IDS` registry [P]
- **File**: `src/models/track.py`
- **Action**: Add `TRACK_IDS: Final[dict[str, str]]` constant — 27 circuits keyed `"01"`–`"27"` in alphabetical order by canonical name.
- **Status**: [X]

### T003 — Write DB migration 003 [P]
- **File**: `src/db/migrations/003_remove_division_race_fields.sql`
- **Action**: `ALTER TABLE divisions DROP COLUMN race_day;` and `ALTER TABLE divisions DROP COLUMN race_time;`
- **Status**: [X]

---

## Phase 2 — Core: Data model & service layer

### T004 — Remove `race_day`/`race_time` from `Division` model
- **File**: `src/models/division.py`
- **Action**: Delete `race_day: int` and `race_time: str` fields from the `Division` dataclass.
- **Depends on**: T003
- **Status**: [X]

### T005 — Remove `race_day`/`race_time` from `SeasonService`
- **File**: `src/services/season_service.py`
- **Action**: Remove parameters from `add_division()` signature; update INSERT, SELECT, and `_row_to_division` helper.
- **Depends on**: T004
- **Status**: [X]

---

## Phase 3 — Core: Discord command layer

### T006 — Update `/division-add` command [P]
- **File**: `src/cogs/season_cog.py`
- **Action**: Remove `race_day` and `race_time` from `PendingDivision`, `@app_commands.describe`, the function signature, validation block, and confirmation message. Update `_do_approve` call to `add_division()`.
- **Depends on**: T005
- **Status**: [X]

### T007 — Add track autocomplete to `/round-add` [P]
- **File**: `src/cogs/season_cog.py`
- **Action**: Import `TRACK_IDS`. Update `@app_commands.describe` for `track`. Add ID → name resolution before validation. Register `@round_add.autocomplete("track")` callback that filters `TRACK_IDS` entries by `current` (case-insensitive substring, max 25 results).
- **Depends on**: T002
- **Status**: [X]

### T008 — Add track autocomplete to `/round-amend` [P]
- **File**: `src/cogs/amendment_cog.py`
- **Action**: Import `TRACK_IDS`. Update `@app_commands.describe` for `track`. Add ID → name resolution before validation. Register `@round_amend.autocomplete("track")` callback identical to T007's.
- **Depends on**: T002
- **Status**: [X]

### T009 — Update `/season-review` division display
- **File**: `src/cogs/season_cog.py`
- **Action**: Remove `day_names[div.race_day]` and `div.race_time` from the review summary string in `season_review()`.
- **Depends on**: T006
- **Status**: [X]

---

## Phase 4 — Documentation & Polish

### T010 — Update README with per-command parameter tables [P]
- **File**: `README.md`
- **Action**: Replace the sparse commands table with per-command sections each containing a parameter table (name, type, required, description). Add track ID quick-reference table.
- **Status**: [X]

### T011 — Update test seed fixtures [P]
- **File**: `tests/unit/test_test_mode_service.py`
- **Action**: Remove `race_day` and `race_time` columns from all division `INSERT` statements in the `_seed()` helper.
- **Depends on**: T003
- **Status**: [X]

---

## Phase 5 — Validation

### T012 — Run full test suite
- **Action**: `python -m pytest tests/ -v` — all 45 tests must pass.
- **Depends on**: T001–T011
- **Status**: [X]

### T013 — Verify bot startup
- **Action**: `python src/bot.py` — must log "All cogs loaded. Starting bot..." without `TypeError`.
- **Depends on**: T001–T011
- **Status**: [X]

---

## Summary

| Phase | Tasks | Status |
|-------|-------|--------|
| 1 — Setup | T001, T002, T003 | ✅ Complete |
| 2 — Data model & service | T004, T005 | ✅ Complete |
| 3 — Command layer | T006, T007, T008, T009 | ✅ Complete |
| 4 — Documentation & polish | T010, T011 | ✅ Complete |
| 5 — Validation | T012, T013 | ✅ Complete |

**All 13 tasks complete. 45/45 tests passing.**

---

## Phase 6 — Bot Data Reset Command (Addendum)

### T014 — Create `src/services/reset_service.py`

**Goal**: Implement `reset_server_data(server_id, db_path, scheduler_service, full=False) -> dict`.

**Acceptance criteria**:
- [ ] Fetches season IDs, division IDs, round IDs for the given `server_id` before opening any transaction
- [ ] Calls `scheduler_service.cancel_round(rid)` for every round ID
- [ ] Executes all DELETEs in a single aiosqlite transaction (FK-safe order: sessions → phase_results → rounds → divisions → seasons → audit_entries → [server_configs if full])
- [ ] Returns `{"seasons_deleted": int, "divisions_deleted": int, "rounds_deleted": int}`
- [ ] Does NOT validate the `confirm` string (that is the cog's responsibility)
- [ ] Works correctly when server has zero seasons (returns all-zero counts without error)

**Status**: [X]

---

### T015 — Create `src/cogs/reset_cog.py`

**Goal**: Discord command layer for `/bot-reset`.

**Acceptance criteria**:
- [ ] `@app_commands.command(name="bot-reset")` with `confirm: str` and `full: bool = False` parameters
- [ ] Decorated with `@admin_only` only (no `@channel_guard`)
- [ ] Rejects immediately with ephemeral error if `confirm != "CONFIRM"` (case-sensitive) — no DB access
- [ ] Calls `reset_service.reset_server_data(...)` and responds ephemerally with counts
- [ ] Partial response: includes "Server config preserved" note
- [ ] Full response: includes "run /bot-init to re-configure" reminder

**Status**: [X]

---

### T016 — Register `ResetCog` in `src/bot.py`

**Goal**: Wire the new cog into the bot's startup sequence.

**Acceptance criteria**:
- [ ] `from cogs.reset_cog import ResetCog` import added
- [ ] `await bot.add_cog(ResetCog(bot))` present in the cog-loading block
- [ ] Bot starts without errors

**Status**: [X]

---

### T017 — Write `tests/unit/test_reset_service.py`

**Goal**: Unit test coverage for `reset_server_data`.

**Acceptance criteria**:
- [ ] Test: partial reset deletes seasons/divisions/rounds but preserves `server_configs`
- [ ] Test: full reset additionally deletes `server_configs`
- [ ] Test: empty server (no seasons) returns all-zero counts and no error
- [ ] Test: `cancel_round` called exactly once per round present
- [ ] Test: transaction rolls back entirely if a DELETE raises (mock DB error)
- [ ] All tests pass with `pytest`

**Status**: [X]

---

### T018 — Update `README.md`

**Goal**: Document `/bot-reset` in the public README.

**Acceptance criteria**:
- [ ] `/bot-reset` listed under admin commands section
- [ ] Parameter table: `confirm` (required, must be `"CONFIRM"`), `full` (optional bool, default `False`)
- [ ] Brief description of partial vs full reset behaviour

**Status**: [X]

---

### T019 — Run full test suite & validate

**Goal**: Confirm all existing and new tests pass after the addendum implementation.

**Acceptance criteria**:
- [ ] `pytest` exits 0 with all tests passing (minimum: previous 45 + new reset tests)
- [ ] No regressions in existing test suite
- [ ] Bot starts cleanly (`python src/bot.py --dry-run` or equivalent)

**Status**: [X]

---

## Progress Summary (updated)

| Phase | Tasks | Status |
|-------|-------|--------|
| 1 — Setup | T001–T003 | ✅ Complete |
| 2 — Data model & service | T004, T005 | ✅ Complete |
| 3 — Command layer | T006–T009 | ✅ Complete |
| 4 — Documentation & polish | T010, T011 | ✅ Complete |
| 5 — Validation | T012, T013 | ✅ Complete |
| 6 — Bot reset command | T014–T019 | ✅ Complete |

---

## Phase 7 — Correctness Fixes (Addendum 2026-03-04)

### T020 — Fix `/round-add`: require track for non-MYSTERY formats

**Goal**: Reject `/round-add` immediately if format is not MYSTERY and track is empty.

**Acceptance criteria**:
- [X] Guard added after `fmt` is parsed, before any DB/state write
- [X] Error message names the disallowed format and directs to MYSTERY for blank tracks
- [X] MYSTERY rounds with no track still accepted as before

**Status**: [X]

---

### T021 — Fix `/season-setup`: block duplicate seasons

**Goal**: Prevent starting a new setup when an active or pending season already exists.

**Acceptance criteria**:
- [X] DB guard: `SeasonService.has_existing_season(server_id)` returns True for any existing season row
- [X] In-memory guard: scan `_pending` for any config belonging to this server
- [X] Both guards checked in `season_setup` before creating a new `PendingConfig`
- [X] Descriptive error message returned ephemerally

**Status**: [X]

---

### T022 — Fix `/bot-reset`: clear in-memory pending setups + season-end job

**Goal**: Ensure a reset leaves no stale in-memory state or scheduled jobs.

**Acceptance criteria**:
- [X] `SeasonCog.clear_pending_for_server(server_id)` method added
- [X] `ResetCog.handle_bot_reset()` calls `clear_pending_for_server` after successful reset
- [X] `cancel_season_end(server_id)` called to remove any pending season-end APScheduler job

**Status**: [X]

---

### T023 — Add `schedule_season_end` / `cancel_season_end` to SchedulerService

**Goal**: Support one-shot season-end jobs in APScheduler.

**Acceptance criteria**:
- [X] `schedule_season_end(server_id, fire_at, callback)` adds job with `replace_existing=True`
- [X] Job ID pattern: `season_end_{server_id}`
- [X] `cancel_season_end(server_id)` silently swallows missing-job errors

**Status**: [X]

---

### T024 — Create `src/services/season_end_service.py`

**Goal**: Encapsulate all season-completion logic.

**Acceptance criteria**:
- [X] `check_and_schedule_season_end(server_id, bot)`: calls `all_phases_complete`, if True finds `get_last_scheduled_at + 7 days` and schedules job
- [X] `execute_season_end(server_id, season_id, bot)`: idempotent guard, cancels job, posts log message, calls `reset_server_data(full=False)`
- [X] Both functions are no-ops when no active season exists

**Status**: [X]

---

### T025 — Hook season-end check into `phase3_service.run_phase3`

**Goal**: Trigger auto-deletion scheduling after every real Phase 3 completion.

**Acceptance criteria**:
- [X] `check_and_schedule_season_end(server_id, bot)` called at end of `run_phase3`
- [X] `server_id` sourced from the existing row query (no extra DB call)

**Status**: [X]

---

### T026 — Test mode advance triggers immediate season end on last phase

**Goal**: When all phases are exhausted by test-mode advance, the season ends immediately.

**Acceptance criteria**:
- [X] After running the phase, `get_next_pending_phase()` called again
- [X] If result is None and `phase_number == 3`: cancel scheduled job, call `execute_season_end`, respond with "Season complete!" message
- [X] Normal `followup.send` still executes for non-final advances

**Status**: [X]

---

### T027 — Write `tests/unit/test_season_end_service.py`

**Goal**: Unit test coverage for all new service helpers and end-to-end completion flow.

**Acceptance criteria**:
- [X] `test_has_existing_season_true/false`
- [X] `test_all_phases_complete_false_when_pending / true_when_done`
- [X] `test_get_last_scheduled_at_returns_latest / returns_none`
- [X] `test_check_does_not_schedule_when_phases_incomplete`
- [X] `test_check_schedules_when_all_phases_complete` (verifies fire_at = last + 7d)
- [X] `test_check_is_noop_when_no_active_season`
- [X] `test_execute_season_end_deletes_season`
- [X] `test_execute_season_end_posts_log_message`
- [X] `test_execute_season_end_is_idempotent`
- [X] `test_execute_season_end_preserves_server_config`
- [X] `test_execute_season_end_cancels_season_end_job`
- [X] All 65 tests passing (51 existing + 14 new)

**Status**: [X]

---

## Progress Summary (updated)

| Phase | Tasks | Status |
|-------|-------|--------|
| 1 — Setup | T001–T003 | ✅ Complete |
| 2 — Data model & service | T004, T005 | ✅ Complete |
| 3 — Command layer | T006–T009 | ✅ Complete |
| 4 — Documentation & polish | T010, T011 | ✅ Complete |
| 5 — Validation | T012, T013 | ✅ Complete |
| 6 — Bot reset command | T014–T019 | ✅ Complete |
| 7 — Correctness fixes | T020–T027 | ✅ Complete |

---

## Phase 8 — Startup Season-End Recovery (FR-025)

**Goal**: On bot startup, re-register any season-end APScheduler jobs lost during a process restart; fire immediately if the due date is already past.

**Independent Test** (SC-015): Restart the bot after a season’s last Phase 3 has completed. The `season_end_{server_id}` APScheduler job must be present and scheduled; a bot that was down beyond the 7-day window must execute `execute_season_end` immediately on startup.

- [ ] T028 [P] Add `get_all_server_ids_with_active_season()` async method to `src/services/season_service.py` — SELECT DISTINCT server_id FROM seasons WHERE status = 'ACTIVE'
- [ ] T029 [P] Update `check_and_schedule_season_end()` in `src/services/season_end_service.py` to accept an optional `now` override and add the past-fire_at branch: if `now >= fire_at` call `execute_season_end` directly instead of scheduling
- [ ] T030 Add startup recovery loop to `on_ready` in `src/bot.py` — iterate `get_all_server_ids_with_active_season()` and call `check_and_schedule_season_end(server_id, bot)` for each (depends on T028, T029)
- [ ] T031 [P] Write 3 startup recovery tests in `tests/unit/test_season_end_service.py`: `test_startup_recovery_schedules_future_job`, `test_startup_recovery_fires_immediately_when_past`, `test_startup_recovery_noop_when_phases_incomplete` (depends on T028, T029)

---

## Progress Summary (updated)

| Phase | Tasks | Status |
|-------|-------|--------|
| 1 — Setup | T001–T003 | ✅ Complete |
| 2 — Data model & service | T004, T005 | ✅ Complete |
| 3 — Command layer | T006–T009 | ✅ Complete |
| 4 — Documentation & polish | T010, T011 | ✅ Complete |
| 5 — Validation | T012, T013 | ✅ Complete |
| 6 — Bot reset command | T014–T019 | ✅ Complete |
| 7 — Correctness fixes | T020–T027 | ✅ Complete |
| 8 — Startup recovery | T028–T031 | 🟢 Not started |
