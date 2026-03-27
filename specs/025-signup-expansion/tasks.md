# Tasks: Signup Module Expansion

**Input**: Design documents from `/specs/025-signup-expansion/`
**Branch**: `025-signup-expansion`
**Prerequisites**: plan.md ✅ spec.md ✅ research.md ✅ data-model.md ✅ contracts/commands.md ✅

**Tests**: Not requested — no test tasks included.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US4)
- Exact file paths are included in all task descriptions

---

## Phase 1: Setup

**Purpose**: Schema migration and model changes that every user story depends on.

**⚠️ CRITICAL**: All user story work is blocked until these tasks are complete.

- [ ] T001 Write migration `src/db/migrations/024_signup_expansion.sql`: (a) `ALTER TABLE signup_module_config ADD COLUMN close_at TEXT`; (b) full table recreation to make `signup_channel_id`, `base_role_id`, `signed_up_role_id` nullable (RENAME → CREATE NEW → INSERT → DROP OLD); (c) `CREATE TABLE IF NOT EXISTS signup_division_config (id INTEGER PRIMARY KEY AUTOINCREMENT, server_id INTEGER NOT NULL, division_id INTEGER NOT NULL, lineup_channel_id INTEGER, UNIQUE(server_id, division_id), FOREIGN KEY (server_id) REFERENCES server_configs(server_id) ON DELETE CASCADE, FOREIGN KEY (division_id) REFERENCES divisions(id) ON DELETE CASCADE)`
- [ ] T002 Update `SignupModuleConfig` dataclass in `src/models/signup_module.py`: change `signup_channel_id`, `base_role_id`, `signed_up_role_id` from `int` to `int | None`; add `close_at: str | None = None`
- [ ] T003 Add `SignupDivisionConfig` dataclass to `src/models/signup_module.py`: fields `id: int`, `server_id: int`, `division_id: int`, `lineup_channel_id: int | None`

**Checkpoint**: Migration and models are ready — user story implementation can begin.

---

## Phase 2: Foundational (Blocking Service Layer)

**Purpose**: Service-layer CRUD that all user stories call. Must be complete before any cog changes.

**⚠️ CRITICAL**: Cog changes in Phases 3–6 depend on these service methods existing.

- [ ] T004 Add `get_division_config(server_id, division_id) -> SignupDivisionConfig | None` and `upsert_division_config(server_id, division_id, lineup_channel_id) -> None` to `src/services/signup_module_service.py` (reads/writes `signup_division_config` table)
- [ ] T005 Add `set_close_at(server_id, close_at_iso: str | None) -> None` to `src/services/signup_module_service.py` (updates `signup_module_config.close_at`)
- [ ] T006 Update `set_window_closed()` in `src/services/signup_module_service.py` to clear `close_at = NULL` in the same DB transaction as setting `signups_open = 0`
- [ ] T007 [P] Add `schedule_signup_close_timer(server_id: int, close_at_iso: str) -> None` to `src/services/scheduler_service.py`: creates APScheduler `DateTrigger` job with `job_id = f"signup_close_{server_id}"`, calling new module-level callable `_signup_close_timer_job(server_id)`
- [ ] T008 [P] Add `cancel_signup_close_timer(server_id: int) -> None` to `src/services/scheduler_service.py`: removes `signup_close_{server_id}` job if it exists (no-op if absent)
- [ ] T009 Add module-level picklable callable `_signup_close_timer_job(server_id: int)` in `src/services/scheduler_service.py` that calls `_GLOBAL_SERVICE.signup_module_service` to execute forced close (same path as `execute_forced_close()` in module_cog)

**Checkpoint**: All service methods exist — cog layers can now be implemented in parallel.

---

## Phase 3: User Story 1 — Dedicated Signup Module Configuration Commands (P1) 🎯 MVP

**Goal**: `/module enable signup` takes no parameters; three dedicated `/signup channel`, `/signup base-role`, `/signup complete-role` commands each persist one config value independently.

**Independent Test**: (1) Run `/module enable signup` with no args — succeeds. (2) Run each of the three config commands — each confirms and persists independently. (3) Verify `/module enable signup channel:#ch base_role:@r signed_up_role:@r` is no longer a valid invocation.

- [ ] T010 [US1] Remove `channel`, `base_role`, and `signed_up_role` parameters from `_enable_signup()` in `src/cogs/module_cog.py`; change body to upsert a bare `signup_module_config` row (all channel/role fields NULL) via `signup_module_service`; keep enable flag logic unchanged
- [ ] T011 [P] [US1] Add `/signup channel <channel: TextChannel>` subcommand to `src/cogs/signup_cog.py`: guard module-enabled; update `signup_channel_id` via service; apply Discord permission overwrites (`@everyone view=False`, `base_role view=True, send=False, use_app_cmds=True` if set, bot `send=True`, interaction role `send=True`); if a prior channel was set, revert its bot-applied overwrites first; log `SIGNUP_CHANNEL_SET`
- [ ] T012 [P] [US1] Add `/signup base-role <role: Role>` subcommand to `src/cogs/signup_cog.py`: guard module-enabled; update `base_role_id` via service; if `signup_channel_id` is set, re-apply channel permission overwrites for the new role and remove overwrite for the old role; log `SIGNUP_BASE_ROLE_SET`
- [ ] T013 [P] [US1] Add `/signup complete-role <role: Role>` subcommand to `src/cogs/signup_cog.py`: guard module-enabled; update `signed_up_role_id` via service; log `SIGNUP_COMPLETE_ROLE_SET`
- [ ] T014 [US1] Remove the old `/signup config channel` and `/signup config roles` subcommands (or their group) from `src/cogs/signup_cog.py`

**Checkpoint**: US1 is fully functional and independently testable.

---

## Phase 4: User Story 2 — Season Validation and Review (P2)

**Goal**: Season approval rejects with a per-item error if the signup module is enabled but any of the three config fields is NULL. Season review displays current signup module config.

**Independent Test**: Enable signup module, run season approval — blocked with all three items named. Set all three, run season approval — signup gate no longer blocks. Run season review — signup module section shows all three values (or "not configured" indicators).

- [ ] T015 [US2] Add signup module config gate to the season approval function (location: `src/services/` or whichever service handles season approval): if `signup_module_enabled` AND any of `signup_channel_id`, `base_role_id`, `signed_up_role_id` is NULL, block approval with a bulleted list of each unset item by name
- [ ] T016 [US2] Add signup module section to the season review embed: when signup module is enabled, display `signup_channel_id` (as channel mention or "not configured"), `base_role_id` (as role mention or "not configured"), `signed_up_role_id` (as role mention or "not configured"); when disabled, omit section

**Checkpoint**: US2 is fully functional and independently testable.

---

## Phase 5: User Story 3 — Signup Open with Optional Close Timer (P3)

**Goal**: `/signup open` gains an optional `close_time` parameter; base role is mentioned in the open post; a timer job fires auto-close; `/signup close` is blocked while timer is active; bot restores the timer on restart.

**Independent Test**: Open signups with a 2-minute close time; verify base-role mention is in the post; verify `/signup close` is blocked; let the timer fire; verify pending drivers are cancelled and unassigned drivers are unchanged.

- [ ] T017 [US3] Add `close_time: str | None = None` parameter to `/signup open` in `src/cogs/signup_cog.py`; validate that if provided it parses to a UTC datetime in the future (reject with descriptive error if not); after opening, call `signup_module_service.set_close_at(server_id, close_at_iso)` and `scheduler_service.schedule_signup_close_timer(server_id, close_at_iso)` when timer is provided; add `allowed_mentions=discord.AllowedMentions(roles=[base_role])` and `role.mention` to the signup-open message content
- [ ] T018 [US3] Update `/signup open` pre-condition guard in `src/cogs/signup_cog.py` to check that `signup_channel_id`, `base_role_id`, and `signed_up_role_id` are all non-null before proceeding (return ephemeral error naming any missing item)
- [ ] T019 [US3] Add guard to `/signup close` in `src/cogs/signup_cog.py`: if `config.close_at` is not NULL, return ephemeral error `"Signups will auto-close at {close_at}. Cancel the timer first if you need to close manually."` (do not proceed to the close confirmation view)
- [ ] T020 [US3] Extend `_disable_signup()` in `src/cogs/module_cog.py` to call `scheduler_service.cancel_signup_close_timer(server_id)` as part of the disable operation (prevents orphaned timer after module disable)
- [ ] T021 [US3] Add close-timer restart recovery to `on_ready()` in `src/bot.py`: after scheduler start, query `signup_module_config` for rows with non-null `close_at`; if `close_at` is in the past, execute `execute_forced_close()` immediately; if in the future and the APScheduler job is not already present in the store, call `schedule_signup_close_timer()` to re-arm it

**Checkpoint**: US3 is fully functional and independently testable.

---

## Phase 6: User Story 4 — Division Lineup Announcement Channel (P4)

**Goal**: `/division lineup-channel <division> <channel>` configures an optional per-division announcement channel; after every assign/unassign/sack, if zero unassigned drivers remain server-wide and at least one assigned driver exists in the division, post a formatted lineup to that channel.

**Independent Test**: Configure lineup channel for Division 1; assign the last unassigned driver; verify formatted lineup embed is posted. Verify no post when no lineup channel set and no error is raised.

- [ ] T022 [US4] Add `/division lineup-channel <division: str> <channel: TextChannel>` subcommand to `src/cogs/driver_cog.py` (or the appropriate division-owning cog): guard signup-module-enabled; resolve division by name/tier from active season; upsert `SignupDivisionConfig` via service; log `SIGNUP_LINEUP_CHANNEL_SET`; return ephemeral confirmation
- [ ] T023 [US4] Add `_maybe_post_lineup(server_id: int, division_id: int, guild: discord.Guild) -> None` async helper to `src/services/placement_service.py` (or a method on the signup module service): (a) fetch `SignupDivisionConfig` for `(server_id, division_id)` — return immediately if `lineup_channel_id` is None; (b) count drivers in `UNASSIGNED` state server-wide — if > 0, return; (c) count drivers in `ASSIGNED` state for this division — if == 0, return; (d) fetch all teams in division with their assigned drivers; (e) build and post a formatted lineup embed to `lineup_channel_id`; (f) handle `discord.NotFound` (channel deleted) gracefully: log error, do not raise
- [ ] T024 [US4] Call `_maybe_post_lineup(server_id, division_id, guild)` at the end of the assign, unassign, and sack operations in `src/services/placement_service.py` (after the DB mutation commits and role changes are applied)

**Checkpoint**: US4 is fully functional and independently testable.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Audit logging for all automated events not already covered inline, and final validation.

- [ ] T025 [P] Emit audit log entry (`SIGNUP_WINDOW_CLOSED` / system actor) from `_signup_close_timer_job` in `src/services/scheduler_service.py` when the auto-close fires, matching the format used for manual forced-close log entries
- [ ] T026 [P] Emit audit log entry (`SIGNUP_LINEUP_POSTED` / system actor) from `_maybe_post_lineup()` in `src/services/placement_service.py` when a lineup is successfully posted
- [ ] T027 Run through the quickstart guide in `specs/025-signup-expansion/quickstart.md` end-to-end in a test server and confirm all acceptance scenarios from spec.md pass

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Setup)         → no dependencies; start immediately
Phase 2 (Service layer) → depends on Phase 1 (models must exist)
Phase 3 (US1)           → depends on Phase 2
Phase 4 (US2)           → depends on Phase 2
Phase 5 (US3)           → depends on Phase 2 + Phase 3 (enable must work without params first)
Phase 6 (US4)           → depends on Phase 2
Phase 7 (Polish)        → depends on Phases 3–6
```

### User Story Dependencies

- **US1 (P1)**: Unblocked after Phase 2 — no dependency on other stories
- **US2 (P2)**: Unblocked after Phase 2 — independent of US1 at service level; season review display only reads config, does not require US1 commands to work
- **US3 (P3)**: Depends on US1 (T010) landing first — `/signup open` guard for nullable config fields requires the enable to create a bare row; close-timer job depends on `set_close_at` (T005) and scheduler methods (T007–T009)
- **US4 (P4)**: Unblocked after Phase 2 — fully independent of US1/US2/US3

### Within Each User Story

- Cog commands before any downstream integration
- Service methods before cog commands
- Model/migration before service methods

### Parallel Opportunities

Within Phase 2: T007 and T008 can run in parallel (different scheduler methods, no shared state).  
Within Phase 3: T011, T012, T013 can run in parallel (each is a separate cog subcommand in the same file — coordinate on imports/group registration to avoid conflicts).  
Within Phase 7: T025 and T026 can run in parallel (different files).

---

## Parallel Example: Phase 3 (US1 Configuration Commands)

```
T010  ──────────────────────────────────► (enable decoupling, sequential - modifies shared _enable_signup)
T011  ─────────────────────────────────►  (signup channel command)
T012  ─────────────────────────────────►  (base-role command)      } run in parallel after T010
T013  ─────────────────────────────────►  (complete-role command)
              │
T014  ◄───────┘  (remove old config subcommands — do last to avoid breaking the file mid-edit)
```

---

## Implementation Strategy

**MVP Scope**: Phase 1 + Phase 2 + Phase 3 (US1) — decoupled module enable is the root change and is required for everything else to proceed safely. This is the minimum shippable increment.

**Recommended delivery order**:
1. Phases 1–3 (schema + service layer + US1 config commands) — unblocks all remaining work
2. Phase 4 (US2 season validation) — highest risk of breaking an existing gate; validate early
3. Phase 5 (US3 close timer) — most complex; isolated to scheduler + open/close commands
4. Phase 6 (US4 lineup channel) — entirely additive; can be delivered last without blocking anyone
5. Phase 7 (Polish) — wire up any log entries not already emitted inline

---

## Summary

| Metric | Value |
|---|---|
| Total tasks | 27 |
| Phase 1 (Setup) | 3 tasks |
| Phase 2 (Foundational service layer) | 6 tasks |
| Phase 3 (US1 — config commands) | 5 tasks |
| Phase 4 (US2 — season validation) | 2 tasks |
| Phase 5 (US3 — close timer) | 5 tasks |
| Phase 6 (US4 — lineup channel) | 3 tasks |
| Phase 7 (Polish) | 3 tasks |
| Parallelizable tasks [P] | 10 tasks |
| MVP scope (Phases 1–3) | 14 tasks |
