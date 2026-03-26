# Tasks: Season Archive & Driver Profile Identity

**Input**: Design documents from `/specs/024-season-archive/`
**Branch**: `024-season-archive`
**Generated**: 2026-03-26

**User Stories**:
- US1 (P1) — Season Data Preserved on Completion
- US2 (P2) — Season Setup Gating
- US3 (P3) — Game Edition Recorded at Season Setup
- US4 (P4) — Season Number Derived from Archive Count
- US5 (P5) — Driver Profile Internal Identity Decoupled from Discord

**Tests**: No new test files. One existing test file updated (T022).

---

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel with other [P]-marked tasks in the same phase (different files, no incomplete-task dependencies)
- **[Story]**: User story this task belongs to (US1–US5)
- Exact file paths are included in every description

---

## Phase 1: Setup (Schema Foundation)

**Purpose**: Create the migration file that underpins all model and service changes. Nothing else can be implemented without this schema in place.

- [X] T001 Create `src/db/migrations/020_season_archive.sql` — `ALTER TABLE seasons ADD COLUMN game_edition INTEGER NOT NULL DEFAULT 0`; `ALTER TABLE driver_session_results ADD COLUMN driver_profile_id INTEGER REFERENCES driver_profiles(id)`; `ALTER TABLE driver_standings_snapshots ADD COLUMN driver_profile_id INTEGER REFERENCES driver_profiles(id)`; backfill UPDATE for driver_session_results joining through session_results→rounds→divisions→seasons to match discord_user_id; backfill UPDATE for driver_standings_snapshots joining through rounds→divisions→seasons; `CREATE INDEX IF NOT EXISTS idx_dsr_driver_profile ON driver_session_results(session_result_id, driver_profile_id)`; `CREATE INDEX IF NOT EXISTS idx_dss_driver_profile ON driver_standings_snapshots(division_id, driver_profile_id)` — see data-model.md for exact SQL

**Checkpoint**: Migration applied on next bot startup (`run_migrations` in `src/db/database.py`). Verify with `sqlite3 /tmp/testbot.db ".schema seasons"` — `game_edition` column present.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Model and service additions required by every user-story phase. No user-story work can begin until this phase is complete.

**⚠️ CRITICAL**: All of Phase 3–7 depend on T005 being complete.

- [X] T002 [P] Add `game_edition: int = 0` field to `Season` dataclass and update `_row_to_season` to read column index 5 in `src/models/season.py`
- [X] T003 [P] Add `driver_profile_id: int | None = None` field to `DriverSessionResult` dataclass in `src/models/session_result.py`
- [X] T004 [P] Add `driver_profile_id: int | None = None` field to `DriverStandingsSnapshot` dataclass in `src/models/standings_snapshot.py`
- [X] T005 Add `class SeasonImmutableError(Exception): pass`; add `async def has_active_or_setup_season(server_id: int) -> bool` (SELECT status IN ('ACTIVE','SETUP')); add `async def count_completed_seasons(server_id: int) -> int` (SELECT COUNT WHERE status='COMPLETED'); add `async def complete_season(season_id: int) -> None` (UPDATE status='COMPLETED'); add `async def assert_season_mutable(season: Season) -> None` (raises SeasonImmutableError if status==COMPLETED); update all `SELECT … FROM seasons` queries to include `game_edition` column — all in `src/services/season_service.py`

**Checkpoint**: `python -c "from models.season import Season; s = Season(1,1,'2026-01-01','SETUP',1,25); print(s.game_edition)"` → `25`

---

## Phase 3: User Story 1 — Season Data Preserved on Completion (Priority: P1) 🎯 MVP

**Goal**: When a season ends, status becomes COMPLETED, all associated rows are retained, DriverHistoryEntry records are written for every assigned driver, and all mutation paths reject COMPLETED-season targets.

**Independent Test**: Trigger season completion on a server with one division, one round, and one result submitted. Assert season row `status = 'COMPLETED'`, all division/round/result rows still present with original values. Then attempt to amend a round → assert `SeasonImmutableError` raised and no data changed.

### Implementation for User Story 1

- [X] T006 [P] [US1] Rewrite `execute_season_end` in `src/services/season_end_service.py` — load active season (return early if None); cancel scheduler job; load all ASSIGNED `driver_season_assignments`; for each driver×division write a `DriverHistoryEntry` (season_number, division_name, division_tier, final_position/final_points from most-recent `driver_standings_snapshots` row, points_gap_to_winner); call `season_service.complete_season(season.id)`; post `🏁 **Season {season.season_number} Complete!** All data has been preserved in the archive.` to log channel; remove `reset_server_data` call and `increment_previous_season_number` call; remove import of `reset_service`
- [X] T007 [P] [US1] Add `assert_season_mutable` guard (catch `SeasonImmutableError`, respond ephemeral `❌ This season is archived (COMPLETED) and cannot be modified.`) to `/season cancel`, `/division cancel`, `/round cancel`, and `/round delete` handlers in `src/cogs/season_cog.py`
- [X] T008 [P] [US1] Add `assert_season_mutable` guard (load season via round→division→season chain, raise on COMPLETED) to `cancel_round`, `postpone_round`, and `amend_round` in `src/services/amendment_service.py`; callers in the cog already surface `ValueError`-family errors — ensure `SeasonImmutableError` propagates with the standard immutability message
- [X] T009 [P] [US1] Add `assert_season_mutable` guard to result submission and result amendment paths in `src/services/result_submission_service.py`; load season from round→division chain before any write; catch `SeasonImmutableError` and return the immutability error string
- [X] T010 [P] [US1] Add `assert_season_mutable` guard before any penalty write in `src/services/penalty_wizard.py`; resolve session→round→season chain; catch `SeasonImmutableError` and respond with `❌ This season is archived (COMPLETED) and cannot be modified.`
- [X] T011 [P] [US1] Add `assert_season_mutable` guard to `/driver assign`, `/driver unassign`, and `/driver sack` handlers in `src/cogs/driver_cog.py`; load active season to check mutability; catch `SeasonImmutableError` and respond ephemeral with the immutability message

**Checkpoint**: US1 fully functional. Season completes → all rows retained. Any mutation attempt on a COMPLETED season → rejected with the correct message.

---

## Phase 4: User Story 2 — Season Setup Gating (Priority: P2)

**Goal**: `/season setup` is rejected when an ACTIVE or SETUP season exists; succeeds when all seasons are COMPLETED or none exist.

**Independent Test**: Complete a full season; verify `/season setup` succeeds. Immediately call `/season setup` again while the new one is in SETUP state → rejected with SETUP message. With an ACTIVE season → rejected with ACTIVE message.

### Implementation for User Story 2

- [X] T012 [US2] In `src/cogs/season_cog.py` `season_setup` handler: replace `has_existing_season` call with `has_active_or_setup_season`; split the single rejection into two distinct error messages — `❌ A season is currently active for this server. Complete it before starting a new one.` when ACTIVE exists; `❌ A season setup is already in progress for this server. Use /season review to continue, or cancel it first.` when SETUP exists (detect which by calling `get_active_season` and `get_setup_season` as needed, or by returning a status enum from the guard)

**Checkpoint**: US2 fully functional. Old `has_existing_season` call removed. New gating logic tested end-to-end per quickstart step 6.

---

## Phase 5: User Story 3 — Game Edition Recorded at Season Setup (Priority: P3)

**Goal**: `/season setup` requires a mandatory `game_edition` integer (≥1); value stored on the season record and visible in `/season review`.

**Independent Test**: Run `/season setup` without `game_edition` → Discord rejects (required parameter). Run with `game_edition:25` → setup succeeds, `/season review` shows `Season #1 (F1 25)`. Complete season → `SELECT game_edition FROM seasons WHERE id = ?` returns `25`.

### Implementation for User Story 3

- [X] T013 [US3] Add `game_edition: int = 0` field to `PendingConfig` dataclass in `src/cogs/season_cog.py`
- [X] T014 [US3] Add `game_edition: app_commands.Range[int, 1, 9999]` parameter to `season_setup` command signature in `src/cogs/season_cog.py`; assign `cfg.game_edition = game_edition` when creating the `PendingConfig`; pass `game_edition` through to `_snapshot_pending` and into `save_pending_snapshot`
- [X] T015 [P] [US3] Update `save_pending_snapshot` INSERT statement in `src/services/season_service.py` to include `game_edition` in the column list and bind the value passed by the caller; also preserve `game_edition` when re-snapshotting (carry forward from existing SETUP row)
- [X] T016 [US3] Update `/season review` summary formatter in `src/cogs/season_cog.py` to display `Season #{cfg.season_number} (F1 {cfg.game_edition})` in the title line; also update the setup-started confirmation message in `season_setup` to show game edition

**Checkpoint**: US3 fully functional. `game_edition` stored on season at setup, shown in review, retained after completion.

---

## Phase 6: User Story 4 — Season Number Derived from Archive Count (Priority: P4)

**Goal**: A new season's `season_number` is automatically set to `COUNT(COMPLETED seasons for this server) + 1`; the legacy `previous_season_number` counter is superseded.

**Independent Test**: With zero COMPLETED seasons: setup succeeds with `season_number = 1`. With three COMPLETED seasons: setup assigns `season_number = 4`. Verify `previous_season_number` in `server_configs` is never incremented by the new code.

### Implementation for User Story 4

- [X] T017 [US4] In `save_pending_snapshot` in `src/services/season_service.py`, replace the `existing_season_id == 0` branch that reads `previous_season_number + 1` with `await self.count_completed_seasons(server_id) + 1`; remove the `SELECT previous_season_number FROM server_configs` query from this branch; the re-snapshot branch (existing_season_id != 0) remains unchanged — it already preserves the computed season_number

**Checkpoint**: US4 fully functional. `count_completed_seasons` drives season numbering for all new seasons. Legacy counter column remains in schema but is never written.

---

## Phase 7: User Story 5 — Driver Profile Internal Identity Decoupled from Discord (Priority: P5)

**Goal**: All new writes to `driver_session_results` and `driver_standings_snapshots` populate `driver_profile_id` (resolved from Discord user ID at the command boundary); existing rows backfilled by migration.

**Independent Test**: Submit results for a driver profile → `driver_session_results.driver_profile_id` is non-NULL and equals `driver_profiles.id`. Reassign that driver to a new Discord account → all prior result rows still carry the original `driver_profile_id`; queries by the new Discord account's profile resolve the same internal ID.

### Implementation for User Story 5

- [X] T018 [US5] Add `async def resolve_driver_profile_id(server_id: int, discord_user_id: int, db) -> int | None` to `src/services/driver_service.py` — executes `SELECT id FROM driver_profiles WHERE server_id = ? AND CAST(discord_user_id AS INTEGER) = ?` and returns the integer PK or None if no profile found
- [X] T019 [P] [US5] Update every INSERT into `driver_session_results` in `src/services/result_submission_service.py` to call `resolve_driver_profile_id(server_id, row.driver_user_id, db)` and include `driver_profile_id` in the INSERT column list; pass the resolved value (or None for unresolvable legacy rows)
- [X] T020 [P] [US5] Update every INSERT into `driver_standings_snapshots` in `src/services/standings_service.py` to call `resolve_driver_profile_id(server_id, driver_user_id, db)` and include `driver_profile_id` in the INSERT column list
- [X] T021 [P] [US5] Update penalty result writes in `src/services/penalty_wizard.py` that INSERT or UPDATE `driver_session_results` to call `resolve_driver_profile_id` and populate `driver_profile_id` on the affected rows

**Checkpoint**: US5 fully functional. All new result rows carry `driver_profile_id`. Reassigning a driver's Discord account leaves all existing result/standings rows correctly attributed to the internal profile ID.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Finalise test coverage for the changed service and run the full verification sequence.

- [X] T022 Update `tests/unit/test_season_end_service.py` — replace any assertions that verify row deletion (e.g. `assert seasons_deleted == 1`) with assertions that verify archive semantics: `season.status == 'COMPLETED'`, division/round/result rows still present, two `driver_history_entries` rows created; remove any mock setup for `reset_server_data`
- [X] T023 [P] Remove now-unused import of `reset_service` and any reference to `increment_previous_season_number` from `src/services/season_end_service.py` if not already handled by T006
- [X] T024 Run full test suite from repo root — `cd src && pytest ../tests/ -v` — and resolve any regressions introduced by migration 020 or service changes
- [X] T025 [P] Execute quickstart.md manual smoke-test sequence on a test server: `/season setup game_edition:25` → approve → activate → complete → verify DB → `/season setup game_edition:26` → verify season_number=2 → attempt second setup → rejected

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (T001)
  └── Phase 2 (T002–T005)                   # T001 must apply before models/services compile cleanly
        └── Phase 3 (T006–T011)             # T005 provides assert_season_mutable
        └── Phase 4 (T012)                  # T005 provides has_active_or_setup_season
        └── Phase 5 (T013–T016)             # T002 provides game_edition model field
        └── Phase 6 (T017)                  # T005 provides count_completed_seasons
        └── Phase 7 (T018–T021)             # T003/T004 provide driver_profile_id model fields
              └── Phase 8 (T022–T025)
```

### User Story Dependencies

| Story | Depends on | Independent of |
|-------|-----------|----------------|
| US1 (T006–T011) | Phase 2 complete | US2, US3, US4, US5 |
| US2 (T012) | Phase 2 complete | US1, US3, US4, US5 |
| US3 (T013–T016) | Phase 2 complete (T002 for model field, T015 waits on T005 for save_pending_snapshot sig) | US1, US2, US4, US5 |
| US4 (T017) | Phase 2 complete (T005 for count_completed_seasons) | US1, US2, US3, US5 |
| US5 (T018–T021) | Phase 2 complete (T003/T004 for model fields); T018 before T019–T021 | US1, US2, US3, US4 |

### Within Each Phase

- Phase 2: T002, T003, T004 can run in parallel (different files). T005 is independent of T002–T004 and can also run in parallel with them.
- Phase 3: All of T006–T011 operate on different files and can run in parallel once Phase 2 is complete.
- Phase 5: T013 → T014 → T016 are sequential edits to `season_cog.py`. T015 (`season_service.py`) runs in parallel with T013–T016.
- Phase 7: T018 first. Then T019, T020, T021 in parallel (different files).

---

## Parallel Execution Examples

### Phase 2 (all in parallel after T001)

```
Agent A: T002  src/models/season.py
Agent B: T003  src/models/session_result.py
Agent C: T004  src/models/standings_snapshot.py
Agent D: T005  src/services/season_service.py
```

### Phase 3 / US1 (all in parallel after Phase 2)

```
Agent A: T006  src/services/season_end_service.py   (core archive logic)
Agent B: T007  src/cogs/season_cog.py               (season/division/round cancel guards)
Agent C: T008  src/services/amendment_service.py    (round amendment guards)
Agent D: T009  src/services/result_submission_service.py  (results immutability guard)
Agent E: T010  src/services/penalty_wizard.py       (penalty immutability guard)
Agent F: T011  src/cogs/driver_cog.py               (driver assignment guards)
```

### Phase 7 / US5 (T019–T021 in parallel after T018)

```
Agent A: T018  src/services/driver_service.py             (resolve helper — must finish first)
  then:
Agent B: T019  src/services/result_submission_service.py  (driver_profile_id on results INSERT)
Agent C: T020  src/services/standings_service.py          (driver_profile_id on snapshots INSERT)
Agent D: T021  src/services/penalty_wizard.py             (driver_profile_id on penalty writes)
```

---

## Implementation Strategy

**MVP scope**: Phase 1 + Phase 2 + Phase 3 (US1) delivers the core archive behaviour (no data deleted, season immutable on completion, DriverHistoryEntry written). This is independently deployable and satisfies SC-001.

**Incremental delivery order**:
1. P1 (T001–T011): Foundation + archive/immutability — largest change, highest value
2. P2 (T012): Gating fix — essential for any server to run a second season
3. P3 (T013–T016): Game edition — data capture, one cog + one service change
4. P4 (T017): Season number derivation — single-line change in season_service.py
5. P5 (T018–T021): Driver identity decoupling — write-path migration across three services

**Total tasks**: 25
**Tasks per story**: US1 = 6, US2 = 1, US3 = 4, US4 = 1, US5 = 4
**Foundational tasks**: 5 (T001–T005)
**Polish tasks**: 4 (T022–T025)
**Parallel opportunities**: 14 tasks marked [P]
**New files**: 1 (`src/db/migrations/020_season_archive.sql`)
**Modified files**: 11 (season.py, session_result.py, standings_snapshot.py, season_service.py, season_end_service.py, season_cog.py, amendment_service.py, result_submission_service.py, penalty_wizard.py, driver_cog.py, driver_service.py, standings_service.py)
**Format validation**: All 25 tasks begin with `- [ ]`, carry a T0XX ID, include a file path, and carry [Story] labels only within Phase 3–7.
