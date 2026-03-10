# Tasks: Signup Wizard and Flow

**Input**: Design documents from `/specs/014-signup-wizard-flow/`
**Prerequisites**: plan.md ✅, spec.md ✅

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no blocking dependencies within the batch)
- **[Story]**: User story label (US1–US8) — required for all user story phase tasks
- Exact file paths in all descriptions

---

## Phase 1: Setup

**Purpose**: Database schema required by every subsequent phase

- [ ] T001 Create src/db/migrations/010_signup_wizard.sql — add `signup_records` table, `signup_wizard_records` table, `slot_sequence_id INTEGER` column to `signup_availability_slots` with backfill, `selected_track_ids TEXT` column to `signup_module_config`, and `ban_races_remaining INTEGER NOT NULL DEFAULT 0` column to `driver_profiles`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Model and service-layer additions that every user story depends on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T002 [P] Add `AWAITING_CORRECTION_PARAMETER = "AWAITING_CORRECTION_PARAMETER"` to `DriverState` enum and `ban_races_remaining: int = 0` field to `DriverProfile` dataclass in src/models/driver_profile.py
- [ ] T003 [P] Add `WizardState` enum (UNENGAGED + 9 collection states: COLLECTING_NATIONALITY, COLLECTING_PLATFORM, COLLECTING_PLATFORM_ID, COLLECTING_AVAILABILITY, COLLECTING_DRIVER_TYPE, COLLECTING_PREFERRED_TEAMS, COLLECTING_PREFERRED_TEAMMATE, COLLECTING_LAP_TIME, COLLECTING_NOTES), `ConfigSnapshot`, `SignupRecord`, and `SignupWizardRecord` dataclasses to src/models/signup_module.py; also rename `AvailabilitySlot.slot_id` → `slot_sequence_id` (remove computed rank property)
- [ ] T004 Add `AWAITING_CORRECTION_PARAMETER` to `ALLOWED_TRANSITIONS` dict (from PENDING_ADMIN_APPROVAL; to PENDING_DRIVER_CORRECTION and PENDING_ADMIN_APPROVAL) and add signup-data clearing logic on NOT_SIGNED_UP transition (`former_driver=True` → null all SignupRecord fields; `former_driver=False` → delete profile) in src/services/driver_service.py (depends on T002)
- [ ] T005 [P] Update `add_slot()` in src/services/signup_module_service.py to assign `slot_sequence_id` as `MAX(slot_sequence_id) + 1` per server on insert; update `get_slots()` to SELECT `slot_sequence_id` from DB; update `remove_slot()` to delete by `slot_sequence_id` (depends on T001, T003)
- [ ] T006 Add `SignupRecord` CRUD (`get_record`, `save_record`, `clear_record`) and `SignupWizardRecord` CRUD (`get_wizard`, `save_wizard`, `get_wizard_by_channel`, `delete_wizard`) and `capture_config_snapshot()` methods to src/services/signup_module_service.py (depends on T001, T003, T005)
- [ ] T007 Create src/services/wizard_service.py with `WizardService` class shell — `__init__` accepting `db_path`, `scheduler_service`, `output_router`; channel create/delete/hold helper stubs; `start_wizard`, `handle_message`, `_arm_inactivity_job`, `_cancel_inactivity_job` method stubs (depends on T003, T004, T006)
- [ ] T008 Set `intents.message_content = True` in src/bot.py; instantiate and attach `wizard_service = WizardService(db_path, scheduler_service, output_router)` to bot; add wizard startup-recovery coroutine call in `on_ready` to re-arm APScheduler jobs for non-UNENGAGED wizard records (depends on T007)

**Checkpoint**: Schema, models, service stubs, and bot wiring are in place — user story implementation can begin

---

## Phase 3: User Story 1 — Signup Module Activation (Priority: P1) 🎯 MVP

**Goal**: Server admins can enable and disable the signup module with correct Discord channel permission management

**Independent Test**: Enable module → verify `signup_channel` has overwrites: `base_role` = view + button-press only, `@everyone` = deny view, tier-2 role + bot = view + send freely. Disable module → all config rows deleted, all overwrites reverted, no active wizard jobs.

- [ ] T009 [US1] Extend `_enable_signup()` in src/cogs/module_cog.py to apply `channel.set_permissions()` overwrites on `signup_channel`: `base_role` → `PermissionOverwrite(view_channel=True, send_messages=False, use_application_commands=True)`; `@everyone` → `PermissionOverwrite(view_channel=False)`; `tier2_role` and `guild.me` → `PermissionOverwrite(view_channel=True, send_messages=True)` (FR-002)
- [ ] T010 [US1] Extend `_disable_signup()` in src/cogs/module_cog.py to fetch stored role IDs from config before deletion, call `channel.set_permissions(target, overwrite=None)` for each overwrite applied by `_enable_signup()`, and cancel all `wizard_inactivity_*` and `wizard_channel_delete_*` APScheduler jobs for the server (FR-003)
- [ ] T011 [US1] Add `MODULE_ENABLE` and `MODULE_DISABLE` audit log entries in src/cogs/module_cog.py for signup module enable and disable events (actor Discord user ID, display name, change type, UTC timestamp) (FR-005)

**Checkpoint**: US1 independently functional — module enable/disable correctly sets and reverts channel permissions

---

## Phase 4: User Story 2 — Signup Configuration (Priority: P2)

**Goal**: Server admins and tier-2 users manage all signup configuration with stable, non-reusable time slot IDs

**Independent Test**: Add a slot (Monday, 20:00) → confirm its `slot_sequence_id`. Remove it. Add a new slot (Tuesday, 19:00) → confirm it receives the next sequence ID, not the removed one. Toggle each of the four config settings and verify stored state changes.

- [ ] T012 [US2] Update `time_slot_add` command in src/cogs/signup_cog.py to read `AvailabilitySlot.slot_sequence_id` from the service response and display it in the confirmation message (FR-010)
- [ ] T013 [US2] Update `time_slot_remove` command in src/cogs/signup_cog.py to remove slots by `slot_sequence_id` (passed as user selection); update `_format_slots()` to display `slot_sequence_id` rather than positional rank (FR-010, FR-012)
- [ ] T014 [US2] Add audit log entries for nationality toggle, time-type toggle, time-image toggle, and time-slot add/remove config mutations in src/cogs/signup_cog.py where currently absent (FR-013)

**Checkpoint**: US2 independently functional — all config settings persist correctly; slot IDs are stable across add/remove cycles

---

## Phase 5: User Story 3 — Opening and Closing Signups (Priority: P3)

**Goal**: Tier-2 users can open and close signups; signup button appears/disappears in the general signup channel; in-progress driver safety confirmation works

**Independent Test**: Open signups (selecting 2 tracks) → verify button message posted in signup channel. Close signups with no in-progress drivers → button deleted, closed notice posted. Close with in-progress drivers → confirmation dialog lists them with channel references.

- [ ] T015 [P] [US3] Add `set_signups_open()`, `set_signups_closed()`, `save_selected_tracks()`, and `get_selected_tracks()` to src/services/signup_module_service.py (FR-017)
- [ ] T016 [P] [US3] Add `SignupButtonView` class (discord.ui.View with a single "Sign Up" button) as a top-level class in src/cogs/signup_cog.py (FR-016)
- [ ] T017 [US3] Add `/signup open` slash command to src/cogs/signup_cog.py — block if no slots are configured, accept optional track `app_commands.Choice` selections, call `save_selected_tracks()` + `set_signups_open()`, post signup button message in `signup_channel` listing selected tracks and image-requirement status (FR-014, FR-015, FR-016, FR-017)
- [ ] T018 [P] [US3] Add `ConfirmCloseView` class (Confirm / Cancel buttons) as a top-level class in src/cogs/signup_cog.py (FR-019)
- [ ] T019 [US3] Add `/signup close` slash command to src/cogs/signup_cog.py — if no in-progress drivers (PENDING_SIGNUP_COMPLETION, PENDING_ADMIN_APPROVAL, PENDING_DRIVER_CORRECTION) close immediately; otherwise present `ConfirmCloseView` with driver list and channel mention references; on confirm call forced-close flow and delete signup button message (FR-018, FR-019, FR-020)

**Checkpoint**: US3 independently functional — signup button lifecycle works end-to-end

---

## Phase 6: User Story 4 — Driver Completes Signup Wizard (Priority: P4)

**Goal**: A driver presses the signup button, receives a private channel, answers all configured parameter steps in sequence, and the admin review panel is posted on completion

**Independent Test**: Press signup button as Not Signed Up driver → private `<username>-signup` channel created, driver transitions to Pending Signup Completion. Complete all steps → `SignupRecord` written, driver transitions to Pending Admin Approval, admin review panel visible in channel.

- [ ] T020 [P] [US4] Implement `_normalise_lap_time(raw: str) -> str | None` in src/services/wizard_service.py — accept `M:ss.mss` and `M:ss:mss`; normalise colon-separated ms to dot-separated; zero-pad ms portion if < 3 digits; half-up round to 3 digits if > 3; strip leading/trailing whitespace; return `None` for invalid format (FR-031 step 8, A-006)
- [ ] T021 [P] [US4] Implement `_validate_nationality(raw: str) -> str | None` in src/services/wizard_service.py — accept any 2-letter ASCII string case-insensitively; accept the literal `"other"` (case-insensitive); return normalised lowercase value or `None` for invalid input (FR-031 step 1)
- [ ] T022 [US4] Implement `WizardService.start_wizard()` in src/services/wizard_service.py — create private `<username>-signup` channel with overwrites (driver: view + send; tier-2 + admin + bot: view + send; @everyone: deny view), record `discord_username` and `server_display_name`, call `capture_config_snapshot()`, persist `SignupWizardRecord` with initial state, transition driver to `PENDING_SIGNUP_COMPLETION`, post first collection prompt (depends on T020, T021)
- [ ] T023 [US4] Implement `WizardService.handle_message()` dispatcher in src/services/wizard_service.py — load `SignupWizardRecord` by channel ID, check `wizard_state`, route to the correct per-step handler; ignore messages from any user other than the active driver (FR-032)
- [ ] T024 [US4] Implement per-step collection handlers for steps 2–7 (platform single-choice, platform ID free-text, availability slot IDs with comma/space parsing and validation against snapshot slots, driver type single-choice, preferred teams ranked up to 3, preferred teammate free-text/"No Preference") in src/services/wizard_service.py — validate, update draft answer in `SignupWizardRecord`, advance `WizardState`, post next prompt (FR-031 steps 2–7)
- [ ] T025 [US4] Implement collection handler for step 1 (nationality, using `_validate_nationality`) and steps 8–9 (lap time per configured track using `_normalise_lap_time` with image attachment enforcement when `time_image_required`; notes free-text up to 50 chars/"No Notes") in src/services/wizard_service.py — skip lap-time steps entirely when no tracks in snapshot (FR-031 steps 1, 8–9)
- [ ] T026 [US4] Implement `WizardService.commit_wizard()` in src/services/wizard_service.py — atomically write all draft answers from `SignupWizardRecord` to `SignupRecord`, advance driver to `PENDING_ADMIN_APPROVAL`, set `WizardState` to UNENGAGED (note: channel write revoke handled by `_trigger_channel_hold`, not here), post admin review panel message in signup channel (FR-035, FR-039)
- [ ] T027 [US4] Add `WithdrawButtonView` class (discord.ui.View with a "Withdraw" button) as a top-level class in src/cogs/signup_cog.py; button is visible and active throughout all in-wizard driver states (FR-033)
- [ ] T028 [US4] Add signup button callback handler to `SignupButtonView` in src/cogs/signup_cog.py — load driver state; if not `NOT_SIGNED_UP` respond ephemerally with appropriate error (in-progress, banned); otherwise call `bot.wizard_service.start_wizard()` and post `WithdrawButtonView` in the new channel (FR-022, FR-034)
- [ ] T029 [US4] Add `on_message` listener to src/cogs/signup_cog.py — skip bot messages; call `bot.wizard_service.get_wizard_by_channel(guild.id, channel.id)`; if record is None or `record.discord_user_id != message.author.id` return; otherwise call `bot.wizard_service.handle_message(record, message)` (FR-032)
- [ ] T030 [US4] Add wizard startup-recovery body to `on_ready` in src/bot.py — query all `SignupWizardRecords` with `wizard_state != 'UNENGAGED'`; for each, compare `last_activity_at + 24h` against `datetime.now(UTC)`; re-arm inactivity APScheduler job (with adjusted fire time) if deadline is in the future, else call `wizard_service.handle_inactivity_timeout()` immediately (SC-005)

**Checkpoint**: US4 independently functional — full wizard from button-press to admin review panel posted

---

## Phase 7: User Story 5 — Admin Approves Signup (Priority: P5)

**Goal**: A tier-2 admin approves a pending signup; driver reaches Unassigned with the signed-up role granted, channel enters 24-hour read-only hold before deletion

**Independent Test**: With driver in Pending Admin Approval, press Approve → driver state is Unassigned, signed-up role present on Discord user, channel is read-only for driver, channel deletion APScheduler job scheduled.

- [ ] T031 [US5] Create src/cogs/admin_review_cog.py with `AdminReviewView` (Approve, Request Changes, Reject buttons) — restrict callbacks to tier-2 role or `Manage Guild` permission; load driver state inside each callback and guard race conditions (first action wins; subsequent interactions receive ephemeral "already actioned" error) (FR-039, A-004)
- [ ] T032 [US5] Implement `WizardService.approve_signup()` in src/services/wizard_service.py — call `guild.get_member(discord_user_id).add_roles(signed_up_role)`, transition driver to `UNASSIGNED` via `driver_service.transition()`, call `_trigger_channel_hold()` (FR-040)
- [ ] T033 [US5] Implement `WizardService._trigger_channel_hold()` in src/services/wizard_service.py — revoke driver write permission on signup channel (`channel.set_permissions(member, send_messages=False)`), post terminal event confirmation message in channel, schedule `wizard_channel_delete_{server_id}_{discord_user_id}` APScheduler job (DateTrigger +24 h) (FR-026, SC-003)
- [ ] T034 [US5] Add module-level callable `_wizard_channel_delete_job(server_id, discord_user_id)` and its scheduler registration helper to src/services/scheduler_service.py following the existing `_GLOBAL_SERVICE` + `DateTrigger` pattern (job ID: `wizard_channel_delete_{server_id}_{discord_user_id}`) (FR-026)

**Checkpoint**: US5 independently functional — approval grants role, channel enters 24h hold with deletion scheduled

---

## Phase 8: User Story 6 — Admin Requests Correction (Priority: P6)

**Goal**: A tier-2 admin can flag a specific parameter for re-collection; driver resubmits only that parameter and returns to Pending Admin Approval with a fresh review panel

**Independent Test**: With driver in Pending Admin Approval, press Request Changes → driver in Awaiting Correction Parameter. Select "Platform ID" → driver in Pending Driver Correction, wizard at Platform ID step. Submit valid string → driver back at Pending Admin Approval with fresh review panel posted.

- [ ] T035 [US6] Add `CorrectionParameterView` to src/cogs/admin_review_cog.py — one button per collectable wizard parameter (nationality, platform, platform ID, availability, driver type, preferred teams, preferred teammate, lap times, notes); callbacks restricted to tier-2/admin; each callback calls `bot.wizard_service.select_correction_parameter()` with the parameter label (FR-042)
- [ ] T036 [US6] Implement `WizardService.request_changes()` in src/services/wizard_service.py — transition driver to `AWAITING_CORRECTION_PARAMETER`, post `CorrectionParameterView` in signup channel, store 5-minute `asyncio.create_task()` timeout reference in `_correction_tasks: dict[tuple, asyncio.Task]` keyed by `(server_id, discord_user_id)` (FR-042, FR-043)
- [ ] T037 [US6] Implement `WizardService.select_correction_parameter()` in src/services/wizard_service.py — cancel the stored asyncio timeout task; transition driver to `PENDING_DRIVER_CORRECTION`; set `WizardState` to the target parameter's collection state; post re-collection prompt for that single step only (FR-044)
- [ ] T038 [US6] Implement `WizardService._correction_timeout_callback()` in src/services/wizard_service.py — auto-transition driver from `AWAITING_CORRECTION_PARAMETER` back to `PENDING_ADMIN_APPROVAL`, remove task from `_correction_tasks`, re-post `AdminReviewView` in signup channel (FR-043)
- [ ] T039 [US6] Route correction resubmission in `WizardService.handle_message()` in src/services/wizard_service.py — when `WizardState` is a correction step, validate input using the same criteria as the sequential collection handlers (T024–T025); on valid input update the single field in `SignupRecord`, transition driver to `PENDING_ADMIN_APPROVAL`, post fresh `AdminReviewView` (FR-045, FR-046)

**Checkpoint**: US6 independently functional — full correction cycle completes and returns to admin review

---

## Phase 9: User Story 7 — Driver Withdrawal and System Cancellation (Priority: P7)

**Goal**: Voluntary withdrawal, 24-hour inactivity auto-cancellation, and admin rejection all correctly transition the driver to Not Signed Up and apply the channel hold

**Independent Test**: Press withdrawal button during wizard collection → driver Not Signed Up, channel frozen, cancellation notice posted. Simulate 24-hour inactivity deadline for Pending Signup Completion → same outcome.

- [ ] T040 [US7] Implement `WizardService.withdraw()` in src/services/wizard_service.py — accept driver in any in-wizard state; cancel any stored asyncio correction task and inactivity APScheduler job; transition driver to `NOT_SIGNED_UP` via `driver_service.transition()`; post cancellation notice in channel; call `_trigger_channel_hold()` (FR-033, FR-036)
- [ ] T041 [US7] Add withdrawal button callback to `WithdrawButtonView` in src/cogs/signup_cog.py — verify `interaction.user.id` matches driver's `discord_user_id`; call `bot.wizard_service.withdraw()`; guard race conditions with ephemeral error if driver already Not Signed Up (FR-033)
- [ ] T042 [US7] Implement `WizardService.reject_signup()` in src/services/wizard_service.py — post rejection notice in signup channel, transition driver to `NOT_SIGNED_UP`, call `_trigger_channel_hold()` (FR-041)
- [ ] T043 [US7] Implement 24-hour inactivity job arming for `PENDING_SIGNUP_COMPLETION` in src/services/wizard_service.py — arm `wizard_inactivity_{server_id}_{discord_user_id}` job (DateTrigger, `start_time + 24h`) on `start_wizard()`; reset arm (cancel + re-schedule as `last_activity_at + 24h`) on each valid message handled; cancel on state change out of PSC (FR-047)
- [ ] T044 [US7] Add module-level callable `_wizard_inactivity_job(server_id, discord_user_id)` and its scheduler registration helper to src/services/scheduler_service.py following the existing `_GLOBAL_SERVICE` + `DateTrigger` pattern; callback calls `wizard_service.handle_inactivity_timeout()` (job ID: `wizard_inactivity_{server_id}_{discord_user_id}`) (FR-047, FR-048)
- [ ] T045 [US7] Implement 24-hour inactivity job arming for `PENDING_DRIVER_CORRECTION` in src/services/wizard_service.py — arm (using T044 pattern) when entering PDC state via `select_correction_parameter()`; reset arm on each valid correction message; cancel on successful resubmission or withdrawal (FR-048)
- [ ] T046 [US7] Extend `execute_forced_close()` in src/cogs/module_cog.py to call `scheduler_service.cancel_job()` for `wizard_inactivity_*` and `wizard_channel_delete_*` for each driver being force-transitioned, before initiating their NOT_SIGNED_UP transition (FR-019 forced-close path)

**Checkpoint**: US7 independently functional — all cancellation paths tested and produce correct state + channel hold

---

## Phase 10: User Story 8 — Server Leave and Re-engagement (Priority: P8)

**Goal**: A driver leaving the server while in any in-wizard state triggers immediate channel deletion (no hold); pressing the signup button while a channel exists in any state immediately deletes the old channel before creating a new one

**Independent Test**: Fire `on_member_remove` for a driver in Pending Signup Completion → driver Not Signed Up, channel deleted immediately (no 24h hold). Press signup button while driver has an existing held channel → existing channel deleted immediately, new `<username>-signup` channel created.

- [ ] T047 [US8] Implement `WizardService.handle_member_remove()` in src/services/wizard_service.py — if removed member has a `SignupWizardRecord` with non-UNENGAGED state: cancel all asyncio tasks and APScheduler jobs for this driver, transition driver to `NOT_SIGNED_UP`, delete signup channel immediately via `guild.get_channel(channel_id).delete()` (no hold scheduled) (FR-027)
- [ ] T048 [US8] Add `on_member_remove` listener in src/cogs/signup_cog.py — call `bot.wizard_service.handle_member_remove(member.guild.id, member.id, member.guild)` for the leaving member (FR-027)
- [ ] T049 [US8] Extend `WizardService.start_wizard()` in src/services/wizard_service.py to check for an existing `SignupWizardRecord` with a non-null `signup_channel_id` before creating a new channel — if found: cancel all associated jobs and asyncio tasks, delete the existing channel immediately, then continue with new channel creation (FR-025)

**Checkpoint**: US8 independently functional — no orphaned channels accumulate from server leaves or re-engagements

---

## Phase 11: Polish & Cross-Cutting Concerns

**Purpose**: Test coverage, edge-case validation, and forced-close completeness

- [ ] T050 [P] Write unit tests for `WizardService` — all `WizardState` transitions, valid/invalid inputs per step, `_normalise_lap_time` variants, `_validate_nationality` variants, config-snapshot isolation (changes after snapshot do not affect in-progress wizard), inactivity-job arm/reset/cancel — in tests/unit/test_wizard_service.py
- [ ] T051 [P] Write unit tests for lap-time normalisation edge cases — `M:ss.mss` canonical, `M:ss:mss` colon-ms, zero-pad 1-digit and 2-digit ms, half-up round 4-digit ms, strip whitespace, reject no-minutes, reject letters — in tests/unit/test_lap_time.py
- [ ] T052 [P] Extend tests/unit/test_driver_state_machine.py with `AWAITING_CORRECTION_PARAMETER` transition paths (PENDING_ADMIN_APPROVAL → ACP, ACP → PENDING_DRIVER_CORRECTION, ACP → PENDING_ADMIN_APPROVAL timeout, PDC → PENDING_ADMIN_APPROVAL) and NOT_SIGNED_UP signup-data clearing (former_driver=True nulls fields, former_driver=False deletes record)
- [ ] T053 [P] Extend tests/unit/test_signup_module_service.py — stable `slot_sequence_id` assignment on add, removed-slot ID not reused on next add, `SignupRecord` get/save/clear, `SignupWizardRecord` get/save/delete/get-by-channel, config snapshot returns copy isolated from subsequent config changes
- [ ] T054 Run manual end-to-end validation against quickstart.md — happy path (enable → configure slots → open signups → complete wizard → approve), rejection path (admin presses Reject), correction path (Request Changes → select parameter → resubmit → approve), 24h inactivity simulation

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — **BLOCKS all user stories**
- **US1 (Phase 3)**: Depends on Phase 2
- **US2 (Phase 4)**: Depends on Phase 2 (T005 for stable slot IDs)
- **US3 (Phase 5)**: Depends on Phase 2; logically requires US1 (module must be enabled for signups to be openable) and US2 (slots must exist to open signups)
- **US4 (Phase 6)**: Depends on Phase 2; requires US3 (signup button must be postable)
- **US5 (Phase 7)**: Depends on US4 (T026 posts admin review panel that Approve button appears on)
- **US6 (Phase 8)**: Depends on US5 (T031 houses the Request Changes callback)
- **US7 (Phase 9)**: Depends on US4 (wizard channels and states), partial US5 (reject path in T042), partial US6 (correction inactivity in T045)
- **US8 (Phase 10)**: Depends on US4 (wizard channels and SignupWizardRecord)
- **Polish (Phase 11)**: Depends on all desired user stories being complete

### User Story Dependency Chain

```
US1 (Module Activation) ────────────────────────────┐
US2 (Configuration)     ────────────────────────────┤ both gate US3
US3 (Open/Close)   ← US1 + US2 ─────────────────── US4
US4 (Wizard)       ← US3 ───────────────────────── US5 · US7 partial · US8
US5 (Approval)     ← US4 ───────────────────────── US6
US6 (Correction)   ← US5 ───────────────────────── US7 partial
US7 (Withdrawal)   ← US4 + US5 + US6
US8 (Leave/Re-eng) ← US4
```

### Parallel Opportunities

**Phase 2 (Foundational)**
```
T002  src/models/driver_profile.py            ─┐ run in parallel
T003  src/models/signup_module.py             ─┘ (different files)

After T002 and T001 complete:
T004  src/services/driver_service.py          ─┐ run in parallel
T005  src/services/signup_module_service.py   ─┘ (different files)
```

**Phase 5 (US3)**
```
T015  signup_module_service.py (persistence)  ─┐
T016  signup_cog.py (SignupButtonView class)  ─┤ run in parallel
T018  signup_cog.py (ConfirmCloseView class)  ─┘ (independent definitions)
```

**Phase 6 (US4)**
```
T020  wizard_service.py (_normalise_lap_time) ─┐ run in parallel
T021  wizard_service.py (_validate_nationality)┘ (independent utilities)
```

**Phase 11 (Polish)**
```
T050  tests/unit/test_wizard_service.py       ─┐
T051  tests/unit/test_lap_time.py             ─┤ run in parallel
T052  tests/unit/test_driver_state_machine.py ─┤ (different files)
T053  tests/unit/test_signup_module_service.py─┘
```

---

## Implementation Strategy

### MVP First (US1 + US2 + US3)

1. Complete Phase 1: Setup (migration)
2. Complete Phase 2: Foundational (models, services, bot wiring)
3. Complete Phase 3: US1 → module enable/disable with channel permissions
4. Complete Phase 4: US2 → configuration with stable slot IDs
5. Complete Phase 5: US3 → signup button appears and is removable
6. **STOP and VALIDATE** — module lifecycle, configuration, and signup open/close work end-to-end

### Incremental Delivery

| Step | Stories | What Becomes Available |
|------|---------|------------------------|
| Setup + Foundational | — | Schema, models, service stubs |
| + US1 | P1 | Module enable/disable with channel permissions |
| + US2 | P2 | Full configuration with stable slot IDs |
| + US3 | P3 | Signup button posted and closeable |
| + US4 | P4 | **Core value: full wizard flow to admin review panel** |
| + US5 | P5 | Approval pipeline: driver reaches Unassigned |
| + US6 | P6 | Correction cycle: admin can request re-collection |
| + US7 | P7 | All cancellation paths and inactivity timeouts |
| + US8 + Polish | P8 | Server-leave handling; no orphaned channels |

---

## Summary

| Metric | Value |
|--------|-------|
| Total tasks | 54 (T001–T054) |
| Phase 1 (Setup) | 1 |
| Phase 2 (Foundational) | 7 |
| US1 | 3 |
| US2 | 3 |
| US3 | 5 |
| US4 | 11 |
| US5 | 4 |
| US6 | 5 |
| US7 | 7 |
| US8 | 3 |
| Polish | 5 |
| Parallelizable tasks ([P]) | 14 |
| New files | 3 (`010_signup_wizard.sql`, `wizard_service.py`, `admin_review_cog.py`) |
| Modified files | 7 (`driver_profile.py`, `signup_module.py`, `driver_service.py`, `signup_module_service.py`, `bot.py`, `module_cog.py`, `signup_cog.py`) |
| All 49 FRs covered | ✅ |
