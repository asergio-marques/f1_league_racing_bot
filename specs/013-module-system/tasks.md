# Tasks: Module System — Weather & Signup Modules

**Input**: Design documents from `specs/013-module-system/`  
**Branch**: `013-module-system`  
**Prerequisites**: plan.md ✅ spec.md ✅ research.md ✅ data-model.md ✅ contracts/ ✅

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel with other [P] tasks in the same phase (no shared file dependencies)
- **[Story]**: Which user story this task belongs to (US1–US5)
- All paths are relative to repository root

---

## Phase 1: Setup

**Purpose**: Database migration and shared model additions that every user story depends on.

- [X] T001 Write migration `src/db/migrations/009_module_system.sql` per data-model.md §1 (adds `weather_module_enabled` + `signup_module_enabled` columns to `server_configs`; creates `signup_module_config`, `signup_module_settings`, `signup_availability_slots` tables; recreates `divisions` table with nullable `forecast_channel_id`)
- [X] T002 [P] Add `weather_module_enabled: bool = False` and `signup_module_enabled: bool = False` fields to `ServerConfig` dataclass in `src/models/server_config.py`; update `get_server_config()` and `save_server_config()` SQL in `src/services/config_service.py` to read/write both new columns
- [X] T003 [P] Change `forecast_channel_id: int` to `forecast_channel_id: int | None` in `Division` dataclass in `src/models/division.py`
- [X] T004 [P] Create `src/models/signup_module.py` with `SignupModuleConfig`, `SignupModuleSettings`, and `AvailabilitySlot` dataclasses per data-model.md §4

**Checkpoint**: Migration and models ready — all subsequent phases can begin.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: `ModuleService` and `SignupModuleService` must exist before any cog or story task can call them. Driver state machine extensions must be in place before the signup-close flow is implemented.

- [X] T005 Create `src/services/module_service.py` with `ModuleService` class: methods `is_weather_enabled(server_id)`, `is_signup_enabled(server_id)`, `set_weather_enabled(server_id, value)`, `set_signup_enabled(server_id, value)` — all backed by the `server_configs` columns added in T002
- [X] T006 Create `src/services/signup_module_service.py` with `SignupModuleService` class: methods `get_config(server_id)`, `save_config(config)`, `delete_config(server_id)`, `get_settings(server_id)`, `save_settings(settings)`, `get_slots(server_id)` (returns ranked list), `add_slot(server_id, day_of_week, time_hhmm)`, `remove_slot_by_rank(server_id, slot_id)`, `get_window_state(server_id)`, `set_window_open(server_id, track_ids, button_message_id)`, `set_window_closed(server_id)`
- [X] T007 Add two new allowed state transitions to `DriverService` in `src/services/driver_service.py` per FR-037: `PENDING_SIGNUP_COMPLETION → NOT_SIGNED_UP` and `PENDING_DRIVER_CORRECTION → NOT_SIGNED_UP`
- [X] T008 Add `cancel_all_weather_for_server(server_id: int)` method to `SchedulerService` in `src/services/scheduler_service.py`: queries all rounds for active/setup seasons of `server_id`, calls `cancel_round(round_id)` for each
- [X] T009 Instantiate `ModuleService` and `SignupModuleService` in `src/bot.py` `main()` and attach to `bot` object; load new cogs `module_cog` and `signup_cog` in `setup_hook`

**Checkpoint**: Services wired to bot, driver SM extended — story implementation can begin.

---

## Phase 3: User Story 1 — Weather Module Lifecycle (Priority: P1) 🎯 MVP

**Goal**: Admins can enable and disable the weather module. Enable arms all overdue + future phase jobs; disable cancels all pending weather jobs. All gating on `division add` for `forecast_channel` conditionality.

**Independent Test**: Enable weather module with an active season having one past-horizon and one future-horizon round; verify overdue phase executes and future job is scheduled; then disable and verify all jobs are cancelled and historical outputs are untouched.

### Implementation for User Story 1

- [X] T010 [US1] Create `src/cogs/module_cog.py` with `ModuleCog` class; add `/module` app_commands group decorated with `@channel_guard @admin_only`; add `enable` and `disable` subcommands each accepting a `module_name: app_commands.Choice[str]` with values `"weather"` and `"signup"`; wire `bot.module_service`
- [X] T011 [US1] Implement weather-enable logic in `ModuleCog.enable` (weather branch): (1) guard already-enabled; (2) pre-validate all active-season divisions have non-null `forecast_channel_id` — list offenders and fail if any missing (FR-012); (3) defer interaction; (4) execute overdue phases sequentially via existing phase service callbacks (Phase 1 → 2 → 3 per round, skipping completed and Mystery rounds); (5) call `SchedulerService.schedule_all_rounds()` for future horizons; (6) within a single `aiosqlite` transaction: call `module_service.set_weather_enabled(server_id, True)` and write the audit entry (Principle X rule 2 — enable atomicity); (7) post log channel confirmation; on any failure: call `scheduler_service.cancel_all_weather_for_server(server_id)` to purge any partially-created jobs, then reset the enabled flag to False (full rollback — no partial job/flag state left)
- [X] T012 [US1] Implement weather-disable logic in `ModuleCog.disable` (weather branch): (1) guard already-disabled; (2) call `scheduler_service.cancel_all_weather_for_server(server_id)`; (3) call `module_service.set_weather_enabled(server_id, False)`; (4) emit audit entry + post log channel confirmation
- [X] T013 [US1] Gate `SchedulerService.schedule_round()` behind a weather-module check in `src/services/scheduler_service.py`: before calling `add_job`, check `module_service.is_weather_enabled(server_id)` derived from the round's season's server; skip silently if disabled
- [X] T014 [US1] Gate `on_ready` scheduler recovery in `src/bot.py` (`_recover_missed_phases`): wrap the phase-recovery loop with a check that `weather_module_enabled` is true for the server before arming jobs
- [X] T015 [US1] Update `division add` command in `src/cogs/season_cog.py`: make `forecast_channel` an optional parameter; add mutual-exclusivity guard per contracts/division-changes.md — call `module_service.is_weather_enabled(server_id)` and fail with appropriate error if (enabled + no channel) or (disabled + channel supplied)
- [X] T016 [P] [US1] Update `division duplicate` command in `src/cogs/season_cog.py` with the same mutual-exclusivity guard as T015

**Checkpoint**: US1 fully functional — weather module toggles correctly, scheduling is gated, division add enforces FR-012.

---

## Phase 4: User Story 2 — Signup Module Registration (Priority: P2)

**Goal**: Admins can enable the signup module (specifying channel + 2 roles); bot applies channel permission overwrites. Admins can disable it, clearing config and removing overwrites.

**Independent Test**: Enable module with a real channel and two roles; inspect channel `overwrites` and verify base-role = view-only, trusted-role = full access, everyone = no access; disable and verify bot overwrites are removed.

### Implementation for User Story 2

- [X] T017 [US2] Implement signup-enable logic in `ModuleCog.enable` (signup branch): (1) guard already-enabled; (2) accept + validate `channel`, `base_role`, `signed_up_role` parameters (add them to the `enable` command signature as optional discord-typed params, required when `module_name == "signup"`); (3) guard same-channel-as-interaction-channel (FR-017); (4) check bot has `manage_channels` on the channel; (5) apply `PermissionOverwrite`s per contracts/module.md §enable-signup; (6) upsert `SignupModuleConfig` via `signup_module_service.save_config()`; (7) call `module_service.set_signup_enabled(server_id, True)`; (8) emit audit entry + log channel confirmation; rollback on failure
- [X] T018 [US2] Implement signup-disable logic in `ModuleCog.disable` (signup branch): (1) guard not-enabled; (2) if `signups_open` → run forced-close sub-flow (same as `/signup disable` confirm path: transition in-progress drivers to NOT_SIGNED_UP, delete button message, post "signups closed"); (3) remove bot-applied `PermissionOverwrite`s from signup channel; (4) call `signup_module_service.delete_config(server_id)` (cascades to settings, slots); (5) call `module_service.set_signup_enabled(server_id, False)`; (6) emit `MODULE_DISABLE` + `SIGNUP_FORCE_CLOSE` audit entries; post log channel confirmation

**Checkpoint**: US2 complete — signup module installs/uninstalls cleanly with permission management.

---

## Phase 5: User Story 3 — Signup Module Global Settings (Priority: P3)

**Goal**: Admins can toggle three settings independently: nationality required, time type (Time Trial / Short Qualification), time image required. All gated behind signup-module-enabled check.

**Independent Test**: Toggle each setting from its default; verify persisted value via `/signup config view`; re-toggle back to default; verify restored.

### Implementation for User Story 3

- [X] T019 Create `src/cogs/signup_cog.py` with `SignupCog` class; add `/signup` app_commands group; add `interaction_check` that verifies signup module is enabled (uses `bot.module_service.is_signup_enabled(server_id)`) for all commands except `config channel`, `config roles`, and `config view` (these three operate on pre-enable configuration and must never be module-gated); wire `bot.signup_module_service`
- [X] T020 [P] [US3] Implement `/signup nationality toggle` in `SignupCog` (decorated `@channel_guard @admin_only`): fetch-or-create settings row, flip `nationality_required`, save, emit `SIGNUP_SETTINGS_CHANGE` audit entry (field: `nationality_required`, old/new value), reply ephemeral with new value
- [X] T021 [P] [US3] Implement `/signup time-type toggle` in `SignupCog` (decorated `@channel_guard @admin_only`): present "Time Trial" and "Short Qualification" as `discord.ui.View` buttons; on selection persist `time_type` in settings, emit `SIGNUP_SETTINGS_CHANGE` audit entry (field: `time_type`, old/new value), reply ephemeral with chosen value
- [X] T022 [P] [US3] Implement `/signup time-image toggle` in `SignupCog` (decorated `@channel_guard @admin_only`): fetch-or-create settings row, flip `time_image_required`, save, emit `SIGNUP_SETTINGS_CHANGE` audit entry (field: `time_image_required`, old/new value), reply ephemeral with new value
- [X] T023 [US3] Implement `/signup config view` in `SignupCog` (no module-enabled gate): return ephemeral embed showing channel, base role, signed-up role, all three settings, and signups-open state; if no `signup_module_config` row exists (module never enabled), display all fields as "Not set" and settings as their defaults — do not error

**Checkpoint**: US3 complete — all three settings toggleable and inspectable via config view.

---

## Phase 6: User Story 4 — Availability Time Slot Management (Priority: P4)

**Goal**: Trusted admins can add/remove weekly time slots. IDs are 1-based chronological ranks (recomputed on every mutation). Mutations are blocked while signups are open.

**Independent Test**: Add three slots in non-chronological order; verify IDs are assigned chronologically; remove middle slot; verify gapless renumbering. Attempt mutation while signups open; verify block.

### Implementation for User Story 4

- [X] T024 [US4] Implement `/signup time-slot add <day> <time>` in `SignupCog` (decorated `@channel_guard @admin_only`): (1) guard signups-open (FR-026); (2) parse `time` — accept `HH:MM` 24h and `h:mm AM/PM` 12h, normalise to `HH:MM`, error if unparseable; (3) translate `day` Choice to ISO integer (Mon=1…Sun=7); (4) call `signup_module_service.add_slot(server_id, day_of_week, time_hhmm)` — handles UNIQUE constraint → "already exists" error; (5) emit `SIGNUP_SLOT_ADD` audit entry (day_of_week, time_hhmm, resulting slot_id); (6) reply ephemeral with re-queried ranked slot list
- [X] T025 [US4] Implement `/signup time-slot remove <slot_id>` in `SignupCog` (decorated `@channel_guard @admin_only`): (1) guard signups-open; (2) guard no-slots-exist (FR-025); (3) call `signup_module_service.remove_slot_by_rank(server_id, slot_id)` — raises on out-of-range; (4) emit `SIGNUP_SLOT_REMOVE` audit entry (removed slot_id, day_of_week, time_hhmm); (5) reply ephemeral with updated ranked slot list (or "no slots configured" if empty)
- [X] T026 [P] [US4] Implement `/signup time-slot list` in `SignupCog`: call `signup_module_service.get_slots(server_id)`, reply ephemeral with formatted list (`#N — DayName HH:MM UTC`) or "no slots configured"

**Checkpoint**: US4 complete — slot add/remove/list works with correct chronological ranking and mutation guards.

---

## Phase 7: User Story 5 — Open and Close Signups (Priority: P5)

**Goal**: Trusted admins open the signup window (selecting 0..n tracks); bot posts the button + info message. Admins close it; if drivers are mid-signup, admin is prompted to confirm with a 5-minute expiry. Forced close transitions all in-progress drivers to NOT_SIGNED_UP.

**Independent Test**: Open signups with one track; verify button + open-message in signup channel; close with no in-progress drivers; verify button deleted and closed-message posted. Open again, simulate a driver in PENDING_SIGNUP_COMPLETION; close and verify confirmation prompt appears, and on confirm the driver is transitioned to NOT_SIGNED_UP.

### Implementation for User Story 5

- [X] T027 [US5] Implement `/signup enable [track_ids]` in `SignupCog` (decorated `@channel_guard @admin_only`): (1) guard signups-already-open; (2) guard no-slots-configured (FR-029); (3) parse `track_ids` optional `str` parameter by splitting on commas and/or spaces into individual ID strings — treat omitted or empty string as an empty list (zero tracks); (4) validate each ID against `TrackService` (or existing track DB), error on any unknown ID; (5) store `selected_tracks_json` and set `signups_open=1` via `signup_module_service.set_window_open()`; (6) post signup button (`discord.ui.Button`) + informational message to `signup_channel_id` per FR-030: list selected tracks by name (if zero tracks provided, display "No tracks specified" — not a blank/missing field), time type label, and whether image proof is required; (7) persist `signup_button_message_id`; (8) emit `SIGNUP_OPEN` audit entry; reply ephemeral confirmation
- [X] T028 [US5] Implement `/signup disable` in `SignupCog` (decorated `@channel_guard @admin_only`): (1) guard signups-not-open; (2) query active-season drivers in `PENDING_SIGNUP_COMPLETION`, `PENDING_ADMIN_APPROVAL`, `PENDING_DRIVER_CORRECTION` states for this server; (3) if none → immediate close (step 5); (4) if any → present ephemeral `discord.ui.View` with driver list + Confirm / Cancel buttons (5-minute timeout); on Confirm → execute forced-close sub-flow; on Cancel / timeout → no state change
- [X] T029 [US5] Implement the forced-close sub-flow (used by both `/signup disable` confirm-path and `module disable signup`): (a) bulk-transition all in-progress drivers to `NOT_SIGNED_UP` using `driver_service` (applies former-driver deletion rules); (b) fetch and delete `signup_button_message_id` from signup channel (graceful `NotFound`); (c) post "signups are closed" message to signup channel; (d) call `signup_module_service.set_window_closed(server_id)`; (e) emit `SIGNUP_CLOSE` or `SIGNUP_FORCE_CLOSE` audit entry

**Checkpoint**: US5 complete — full signup open/close lifecycle works including graceful forced-close with confirmation prompt.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T030 [P] Write unit tests in `tests/unit/test_module_service.py`: cover `is_weather_enabled`, `is_signup_enabled`, `set_weather_enabled` (enable/disable/idempotency), `set_signup_enabled` (enable/disable/idempotency)
- [X] T031 [P] Write unit tests in `tests/unit/test_signup_module_service.py`: cover slot add (happy path, duplicate, signups-open guard), slot remove (happy path, out-of-range, no-slots guard), chronological ranking after add/remove, open/close window state transitions
- [X] T032 [P] Write unit tests for new `DriverService` transitions in `tests/unit/test_driver_service.py` (or extend existing): `PENDING_SIGNUP_COMPLETION → NOT_SIGNED_UP` and `PENDING_DRIVER_CORRECTION → NOT_SIGNED_UP` both succeed; verify invalid transitions from those states still raise; additionally assert that `PENDING_ADMIN_APPROVAL → NOT_SIGNED_UP` (defined in feature 012) is present and passes — T029 depends on all three transitions existing
- [X] T033 Verify migration `009_module_system.sql` applies cleanly on top of `008_driver_profiles_teams.sql`: run full migration chain in a temp DB; assert all tables exist, `forecast_channel_id` is nullable, `server_configs` has both new boolean columns defaulting to 0
- [X] T034 [P] Update existing tests that create `Division` objects or stub `division add` with a `forecast_channel` argument — known affected files: `tests/unit/test_season_service.py`, `tests/unit/test_division_service.py` (if exists), and any integration fixtures in `tests/integration/` that seed division rows; make `forecast_channel` optional (pass `None` when weather module disabled in test context)
- [X] T035 [P] Write unit/integration test for FR-010 in `tests/unit/test_module_service.py` or `tests/integration/`: enable weather module, then approve the season; assert `SchedulerService.schedule_round()` is called for each non-Mystery round; confirms the season-approve → scheduling gate works correctly under the new module flag

---

## Dependencies

```
Phase 1 (T001–T004) → Phase 2 (T005–T009) → all story phases in parallel

Phase 3 (US1):  T010 → T011 → T012
                T013, T014, T015, T016  (can run in parallel with each other after T009)

Phase 4 (US2):  T017 → T018  (T017 requires T010 cog skeleton from Phase 3)

Phase 5 (US3):  T019 → T020, T021, T022, T023  (T020–T023 parallel after T019)

Phase 6 (US4):  T024, T025, T026  (all require T019 cog skeleton and T006 service)

Phase 7 (US5):  T027 → T028 → T029  (T029 is a shared sub-flow, implemented once and called by T028 and T018)

Phase 8:        T030–T035  (all after all story phases complete)
```

### User Story Completion Order

```
US1 (P1) → US2 (P2) → US3 (P3) → US4 (P4) → US5 (P5)
```

US3 and US4 have no direct dependency on each other and can be developed in parallel once
US2 is complete (both only require the `SignupCog` skeleton and `SignupModuleService`).

---

## Parallel Execution Examples

**After Phase 2 completes**, the following can begin simultaneously:
- T010–T016 (US1 weather module logic)
- T019 + T020–T022 (US3 settings toggles, once `SignupCog` skeleton exists)

**Within US3**, T020, T021, T022, T023 can all be worked in parallel (different commands, same cog file — coordinate on cog file ownership).

**Within Phase 8**, T030, T031, T032, T034, T035 can all be worked in parallel (different test files).

---

## Implementation Strategy

| Increment | Delivers | Validates |
|-----------|----------|-----------|
| MVP: Phases 1–3 | Weather module on/off, scheduling gated, `division add` conditionality | US1 fully functional; existing weather bot behaviour preserved under new flag |
| +Phase 4 | Signup module install/uninstall with channel permissions | US2; confirms modular permission management works |
| +Phase 5 | All three signup settings toggles | US3 |
| +Phase 6 | Time slot add/remove/list | US4 |
| +Phase 7 | Signup window open/close with graceful forced-close | US5; all signup stories integrated |
| +Phase 8 | Unit tests, migration test, regression fixes | Full test coverage |

**Suggested MVP scope**: Phases 1–3 (Tasks T001–T016). After this increment the weather
module is fully operational under modular control with no regressions to existing behaviour.

---

## Task Count Summary

| Phase | Tasks | User Story |
|-------|-------|-----------|
| Phase 1: Setup | T001–T004 | — |
| Phase 2: Foundational | T005–T009 | — |
| Phase 3 | T010–T016 | US1 (7 tasks) |
| Phase 4 | T017–T018 | US2 (2 tasks) |
| Phase 5 | T019–T023 | US3 (5 tasks) |
| Phase 6 | T024–T026 | US4 (3 tasks) |
| Phase 7 | T027–T029 | US5 (3 tasks) |
| Phase 8: Polish | T030–T035 | — |
| **Total** | **35 tasks** | **5 user stories** |
