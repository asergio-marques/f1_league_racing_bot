# Tasks: Attendance RSVP Check-in & Reserve Distribution

**Input**: Design documents from `specs/032-attendance-rsvp-checkin/`
**Prerequisites**: plan.md ✅ spec.md ✅ research.md ✅ data-model.md ✅ quickstart.md ✅

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no blocking dependency on concurrent task)
- **[Story]**: Which user story (US1–US5) — setup/foundational/polish phases have no story label
- Exact file paths included in all task descriptions

---

## Phase 1: Setup

**Purpose**: Database migration that all subsequent work depends on.

- [ ] T001 Create `src/db/migrations/031_attendance_rsvp.sql` with `driver_round_attendance` and `rsvp_embed_messages` tables per data-model.md

**Checkpoint**: Migration exists and is applied by the migration runner on next bot start.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: New dataclasses and CRUD methods needed by every user story.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T002 Add `DriverRoundAttendance` and `RsvpEmbedMessage` dataclasses to `src/models/attendance.py`
- [ ] T003 Add `driver_round_attendance` CRUD to `src/services/attendance_service.py`: `bulk_insert_attendance_rows`, `upsert_rsvp_status` (must set `accepted_at = utcnow()` when transitioning to ACCEPTED, reset on re-Accept after a non-Accept, set NULL when transitioning away from ACCEPTED — per FR-022), `get_attendance_rows`, `get_attendance_row_for_driver`
- [ ] T004 Add `rsvp_embed_messages` CRUD to `src/services/attendance_service.py`: `insert_embed_message`, `get_embed_message`, `get_all_embed_messages` (returns all rows unconditionally — locking is enforced at interaction time, not at view re-arm time)

**Checkpoint**: Foundation ready — all data access methods available; user story implementation can begin.

---

## Phase 3: User Story 1 — Automated RSVP Embed Posting (Priority: P1) 🎯 MVP

**Goal**: Season approval schedules the RSVP notice job per non-Mystery round. When the job
fires, the bot posts the RSVP embed to the division's RSVP channel with the full driver
roster and three action buttons, creates `DriverRoundAttendance` rows for all division
drivers, and stores the message ID for future edits.

**Independent Test**: Configure a round with a known `scheduled_at`, set `rsvp_notice_days`
so the notice threshold is imminent, confirm the embed appears in the RSVP channel with the
correct title, Discord timestamp, location, event type, full per-team roster with `()`
indicators for all drivers, and three action buttons. Confirm `driver_round_attendance` rows
exist with `NO_RSVP` status. Confirm `rsvp_embed_messages` has one row with the correct
`message_id`.

- [ ] T005 [P] [US1] Create `src/services/rsvp_service.py` with `build_rsvp_embed` function: title format `Season N Round N — <track>`, Discord timestamp field, location + event type fields, per-team roster with status indicators `()` / `(✅)` / `(❓)` / `(❌)` per FR-003–FR-005
- [ ] T006 [P] [US1] Add `_rsvp_notice_job(round_id)` module-level async callable and `register_rsvp_notice_callback` method to `src/services/scheduler_service.py` following the `_phase_job` / `_mystery_notice_job` pattern
- [ ] T007 [US1] Add `schedule_attendance_round(rnd, cfg)` method with `DateTrigger` for notice job and extend `cancel_round` to also remove `rsvp_notice_r{round_id}` in `src/services/scheduler_service.py`
- [ ] T008 [US1] Implement `run_rsvp_notice(round_id)` in `src/services/rsvp_service.py`: query full-time + reserve roster via `driver_season_assignments → team_seats → team_instances`, call `build_rsvp_embed`, post to RSVP channel, bulk-insert `driver_round_attendance` rows, store `message_id` + `channel_id` in `rsvp_embed_messages`; skip + audit log if no RSVP channel (FR-008); bypass if Mystery round (FR-002)
- [ ] T009 [US1] Extend `_do_approve` in `src/cogs/season_cog.py` to call `scheduler_service.schedule_attendance_round(rnd, att_cfg)` for each non-Mystery round when attendance module is enabled, following the existing `is_weather_enabled` / `is_results_enabled` guard pattern
- [ ] T010 [US1] Register `register_rsvp_notice_callback` and add `rsvp_embed_messages` view re-arm loop (`bot.add_view(RsvpView(), message_id=row.message_id)` for all rows from `get_all_embed_messages`) in `src/bot.py` startup block

**Checkpoint**: End-to-end US1 flow works — approval schedules job, job fires, embed posted, rows created.

---

## Phase 4: User Story 2 — Driver RSVP Button Interaction (Priority: P1)

**Goal**: Registered drivers (full-time or reserve) press a button on the RSVP embed, their
status is persisted in `driver_round_attendance`, and the embed updates in-place. Non-members
receive an ephemeral error. Pressing the same button twice is a no-op.

**Independent Test**: Have a registered full-time driver and a registered reserve driver each
press Accept, Tentative, and Decline. Verify the embed updates each time. Have an unregistered
user press a button and verify the ephemeral error. Press the same button twice and verify
the ephemeral acknowledgement and no DB change.

- [ ] T011 [US2] Implement `RsvpView` class (`timeout=None`) with three `discord.ui.Button` components, `custom_id` values `rsvp_accept_r{round_id}`, `rsvp_tentative_r{round_id}`, `rsvp_decline_r{round_id}` in `src/cogs/attendance_cog.py`; parse `round_id` from the `_r{round_id}` suffix of `custom_id` in each handler
- [ ] T012 [US2] Add interaction logic to each button handler in `src/cogs/attendance_cog.py`: look up driver by Discord user ID in `driver_season_assignments`, reject non-members with ephemeral error (FR-011); check `rsvp_status` for no-op and reply ephemerally (FR-013); call `attendance_service.upsert_rsvp_status`; fetch message from `rsvp_embed_messages`; rebuild embed with `build_rsvp_embed` and edit message in-place (FR-010, FR-012)
- [ ] T013 [US2] Add `bot.add_view(RsvpView())` to the persistent-views registration block in `src/bot.py`

**Checkpoint**: Buttons work for valid members; non-members and no-ops handled correctly.

---

## Phase 5: User Story 3 — Reserve RSVP Extended Window (Priority: P2)

**Goal**: Locking rules enforced at interaction time. Full-time drivers and accepted reserves
lock at `rsvp_deadline_hours` threshold. Non-accepted reserves lock only at round start time.

**Independent Test**: Simulate post-deadline interaction for a full-time driver (must be
rejected), a reserve with ACCEPTED status (must be rejected), and a reserve with TENTATIVE
status (must succeed until round start). Verify all three cases independently.

- [ ] T014 [US3] Add locking evaluation at the start of each button handler in `src/cogs/attendance_cog.py`: query `round.scheduled_at` + `attendance_config.rsvp_deadline_hours`; if full-time driver and `now ≥ scheduled_at − deadline_hours`, reject with ephemeral error (FR-014); if reserve and ACCEPTED and `now ≥ scheduled_at − deadline_hours`, reject (FR-015); if reserve and NOT ACCEPTED and `now ≥ scheduled_at`, reject (FR-016); if `deadline_hours == 0`, treat all locks as round start (FR-017)

**Checkpoint**: Locking rules enforced for all driver types; extended reserve window confirmed.

---

## Phase 6: User Story 4 — Reserve Distribution at RSVP Deadline (Priority: P2)

**Goal**: Deadline job fires, runs the distribution algorithm against all accepted reserves
and team RSVP states, writes `assigned_team_id` / `is_standby` to `driver_round_attendance`,
disables embed buttons, posts assignment announcement to RSVP channel.

**Independent Test**: Seed a round with some teams having NO_RSVP / DECLINED / TENTATIVE /
ACCEPTED full-timers and multiple accepted reserves. Verify distribution output matches the
priority order (NO_RSVP → DECLINED → TENTATIVE), tie-breaking by fewest accepted full-timers
then by standings position, and reserve ordering by `accepted_at` timestamp. Verify standby
classification when reserves exceed vacancies. Verify no announcement posted when no reserves
accepted.

- [ ] T015 [P] [US4] Implement `run_reserve_distribution(round_id, division_id)` in `src/services/rsvp_service.py`: query accepted reserves ordered by `accepted_at` ASC; rank candidate teams by priority tier (FR-020) then tie-break by accepted-full-timer count then by `team_standings_snapshots.standing_position` (most recent `round_id` in division, fallback to alphabetical by team name when no snapshot exists, FR-021); assign reserves to vacancies per FR-023; classify unplaced reserves as standby (FR-024); write `assigned_team_id` + `is_standby` to `driver_round_attendance`; note: only non-Reserve teams are distribution candidates — the Reserve team is the supply pool (`is_reserve = 1`) and MUST NOT appear in the candidate ranking
- [ ] T016 [P] [US4] Add `_rsvp_deadline_job(round_id)` module-level async callable and `register_rsvp_deadline_callback` method to `src/services/scheduler_service.py` following the same pattern as T006
- [ ] T017 [US4] Implement `run_rsvp_deadline(round_id)` in `src/services/rsvp_service.py`: call `run_reserve_distribution` per division; fetch `rsvp_embed_messages` rows and edit each embed to remove or disable buttons; post assignment announcement to RSVP channel if any reserves were eligible (FR-025, FR-026)
- [ ] T018 [US4] Extend `schedule_attendance_round` to add deadline `DateTrigger` job and extend `cancel_round` to also remove `rsvp_deadline_r{round_id}` in `src/services/scheduler_service.py`
- [ ] T019 [US4] Register `register_rsvp_deadline_callback` and add missed-deadline recovery on startup (run `run_rsvp_deadline` immediately for any round whose deadline has already passed while bot was offline, FR-027) in `src/bot.py`

**Checkpoint**: Deadline job fires and produces correct distribution assignment; announcement posted; no announcement when no reserves.

---

## Phase 7: User Story 5 — Last-Notice Ping (Priority: P2)

**Goal**: When `rsvp_last_notice_hours > 0`, a last-notice job fires and posts a single
mention in the RSVP channel naming only full-time drivers still at NO_RSVP. Reserve drivers
excluded. No message posted if all full-time drivers have responded. Job not created when
`rsvp_last_notice_hours == 0`. If threshold already passed on restart, job silently skipped.

**Independent Test**: Set `rsvp_last_notice_hours` to a non-zero value, have some full-time
drivers respond and some not, fire the job, confirm only the non-responding full-time drivers
are mentioned. Confirm no message when all have responded. Confirm no job created when value
is 0.

- [ ] T020 [P] [US5] Add `_rsvp_last_notice_job(round_id)` module-level async callable and `register_rsvp_last_notice_callback` method to `src/services/scheduler_service.py`
- [ ] T021 [P] [US5] Implement `run_rsvp_last_notice(round_id)` in `src/services/rsvp_service.py`: query `driver_round_attendance` WHERE `rsvp_status = 'NO_RSVP'` joined to `driver_season_assignments → team_seats → team_instances` WHERE `is_reserve = 0`; if none found skip silently (FR-029); otherwise build mention string and post to RSVP channel (FR-028)
- [ ] T022 [US5] Extend `schedule_attendance_round` to add last-notice `DateTrigger` job only when `rsvp_last_notice_hours > 0` (FR-030), and extend `cancel_round` to also remove `rsvp_last_notice_r{round_id}` in `src/services/scheduler_service.py`
- [ ] T023 [US5] Register `register_rsvp_last_notice_callback` and add skip-if-past re-arm logic on startup (do NOT fire retroactively if threshold already passed at restart, per FR-029 edge case) in `src/bot.py`

**Checkpoint**: Last-notice ping sent only to correct drivers; skipped when all responded; not scheduled when disabled.

---

## Phase 8: Test Mode Integration

**Purpose**: Wire RSVP jobs into `/test-mode advance` and add `/test-mode rsvp set-status`.

**Dependencies**: Depends on Phase 3 (US1) for T027/T028 (RSVP service callables must exist); depends on Phase 2 (Foundational) for T029 (`upsert_rsvp_status` must exist).

- [ ] T027 Extend `_PHASE_PREFIX_MAP` in `src/services/scheduler_service.py` `get_pending_advance_jobs` to include `rsvp_notice` → phase 5, `rsvp_last_notice` → phase 6, `rsvp_deadline` → phase 7 (per research decision #9)
- [ ] T028 Extend the `advance` dispatcher in `src/cogs/test_mode_cog.py` to handle phase numbers 5, 6, 7: call `run_rsvp_notice(round_id, bot)`, `run_rsvp_last_notice(round_id, bot)`, `run_rsvp_deadline(round_id, bot)` respectively
- [ ] T029 Implement `/test-mode rsvp set-status` command in `src/cogs/test_mode_cog.py` (gated on test mode active): parameters `driver_id` (Discord user ID, mandatory), `status` (accepted/tentative/declined, mandatory), `division` (mandatory); locate `DriverRoundAttendance` row for the active round in that division; call `upsert_rsvp_status` with same `accepted_at` rules as a real button press; rebuild and edit the RSVP embed in-place (FR-031)

**Checkpoint**: `/test-mode advance` fires RSVP jobs in correct scheduled order; `/test-mode rsvp set-status` allows fake-driver status management for test validation.

---

## Phase 9: Polish & Tests

**Purpose**: Unit tests and integration validation.

- [ ] T024 [P] Write `tests/unit/test_rsvp_service.py`: distribution algorithm unit tests (priority ordering, tie-breaking with standings, tie-breaking with fallback, accepted_at timestamp ordering, standby classification, no-announcement when no reserves accepted); service method CRUD round-trips
- [ ] T025 [P] Write `tests/unit/test_rsvp_embed_builder.py`: embed content unit tests (title format, Mystery bypass, status indicator strings for all four statuses, per-team roster grouping)
- [ ] T026 Run quickstart.md validation end-to-end using test mode: enable test mode, add fake roster via `/test-mode roster add`, approve season, use `/test-mode advance` to fire notice job (phase 5), use `/test-mode rsvp set-status` to set RSVP statuses for fake drivers, use `/test-mode advance` to fire last-notice job (phase 6) and deadline job (phase 7); confirm all outputs match quickstart expectations (FR-031, constitution §XIII test mode clause)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 (migration must exist to code against schema)
- **Phase 3 (US1)**: Depends on Phase 2 — RSVP embed service, scheduler job, and bot wiring
- **Phase 4 (US2)**: Depends on Phase 3 — buttons need embed message IDs and DRA rows from US1
- **Phase 5 (US3)**: Depends on Phase 4 — locking logic amends handlers created in US2
- **Phase 6 (US4)**: Depends on Phase 2 + Phase 3 (scheduler infrastructure from US1, DRA rows); independent of US2/US3
- **Phase 7 (US5)**: Depends on Phase 2 + Phase 3 (scheduler infrastructure from US1, DRA rows); independent of US2/US3/US4
- **Phase 8 (Test Mode Integration)**: Depends on Phase 3 (US1) for T027/T028; Phase 2 for T029
- **Phase 9 (Polish)**: Depends on all user story phases and Phase 8

### User Story Dependencies

- **US1 (P1)**: After Foundational — no other story dependency
- **US2 (P1)**: After US1 — needs embed message IDs and DRA rows
- **US3 (P2)**: After US2 — amends button handlers
- **US4 (P2)**: After US1 — independent of US2/US3
- **US5 (P2)**: After US1 — independent of US2/US3/US4

### Within Each Phase

- Tasks in the same file are sequential
- Tasks marked [P] are in different files and can proceed concurrently
- T005 (rsvp_service.py) and T006 (scheduler_service.py) can run in parallel
- T015 (rsvp_service.py) and T016 (scheduler_service.py) can run in parallel
- T020 (scheduler_service.py) and T021 (rsvp_service.py) can run in parallel
- T024 and T025 (different test files) can run in parallel
- T018 appends to `cancel_round` and `schedule_attendance_round` as established by T007 — amend those methods, do not rewrite them
- T022 likewise appends to both as extended by T018 — amend, do not rewrite

---

## Parallel Execution Examples

### Phase 3: US1 (after T002–T004 complete)

```
T005 build_rsvp_embed (rsvp_service.py)          ┐  parallel
T006 _rsvp_notice_job callable (scheduler.py)    ┘
    ↓
T007 schedule_attendance_round (scheduler.py)
T008 run_rsvp_notice (rsvp_service.py)
    ↓
T009 _do_approve extension (season_cog.py)
T010 bot.py wiring
```

### Phase 6: US4 (after T010 complete)

```
T015 distribution algorithm (rsvp_service.py)    ┐  parallel
T016 _rsvp_deadline_job callable (scheduler.py)  ┘
    ↓
T017 run_rsvp_deadline (rsvp_service.py)
T018 extend schedule_attendance_round (scheduler.py)
    ↓
T019 bot.py wiring
```

### Phase 7: US5 (after T010 complete, can run alongside Phase 6)

```
T020 _rsvp_last_notice_job callable (scheduler.py) ┐  parallel
T021 run_rsvp_last_notice (rsvp_service.py)        ┘
    ↓
T022 extend schedule_attendance_round (scheduler.py)
T023 bot.py wiring
```

---

## Implementation Strategy

**MVP scope (Phase 1 → Phase 4)**: The bot posts RSVP embeds and drivers can respond via
buttons. 18 tasks. Covers US1 + US2, the two P1 stories. Verifiable end-to-end without
distribution or pings. ⚠️ RSVP locking (FR-014–FR-017) is not enforced until Phase 5
(US3) — do not deploy against an active season until Phase 5 is complete.

**Full scope (Phase 1 → Phase 7)**: All 23 implementation tasks. US3 adds correct reserve
locking; US4 adds distribution; US5 adds last-notice pings. US4 and US5 can be developed
in parallel after US1 is complete.

**Suggested order for solo developer**: Phase 1 → Phase 2 → Phase 3 (US1) → Phase 4 (US2)
→ Phase 5 (US3) → Phase 6 (US4) → Phase 7 (US5) → Phase 8.

---

## Task Count Summary

| Phase | Story | Tasks | Notes |
|-------|-------|-------|-------|
| Phase 1 | Setup | 1 | T001 |
| Phase 2 | Foundational | 3 | T002–T004 |
| Phase 3 | US1 (P1) | 6 | T005–T010 |
| Phase 4 | US2 (P1) | 3 | T011–T013 |
| Phase 5 | US3 (P2) | 1 | T014 |
| Phase 6 | US4 (P2) | 5 | T015–T019 |
| Phase 7 | US5 (P2) | 4 | T020–T023 |
| Phase 8 | Test Mode Integration | 3 | T027–T029 |
| Phase 9 | Polish | 3 | T024–T026 |
| **Total** | | **29** | |

**Parallel opportunities identified**: 6 (T005/T006, T015/T016, T020/T021, T024/T025, Phase 6+Phase 7 concurrent after US1, T027+T028 concurrent with T029 within Phase 8)
