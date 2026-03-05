# Tasks: Forecast Channel Message Cleanup

**Input**: Design documents from `specs/007-forecast-msg-cleanup/`
**Prerequisites**: plan.md ✅ spec.md ✅ research.md ✅ data-model.md ✅ quickstart.md ✅

**Organization**: Tasks are grouped by user story to enable independent implementation and
testing of each story.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no unmet dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US4)
- No story label on Setup, Foundational, and Polish phase tasks

---

## Phase 1: Setup

**Purpose**: Database schema — must land before any service code can be written or tested.

- [X] T001 Add migration `src/db/migrations/004_forecast_messages.sql` — `forecast_messages` table with `id`, `round_id` (FK), `division_id` (FK), `phase_number CHECK (1|2|3)`, `message_id INTEGER NOT NULL`, `posted_at TEXT NOT NULL`; unique index `uq_forecast_messages_round_div_phase` on `(round_id, division_id, phase_number)` — per data-model.md

**Checkpoint**: Migration file present — Foundational work can begin.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Two changes that every user-story phase depends on: the `OutputRouter` must
return a `Message` object, and `forecast_cleanup_service.py` must exist with its
storage helper.

**⚠️ CRITICAL**: No user-story phase work can begin until T002 and T003 are both complete.

- [X] T002 Widen `OutputRouter._send` to capture and return the last `discord.Message` sent (or `None` on failure) and update `post_forecast` signature to `-> discord.Message | None` in `src/utils/output_router.py` — per research.md R-001; all `post_log` call sites unchanged
- [X] T003 [P] Create `src/services/forecast_cleanup_service.py` with module docstring and `store_forecast_message(round_id, division_id, phase_number, message, db_path)` async helper using `INSERT OR REPLACE INTO forecast_messages` — per data-model.md

**Checkpoint**: Foundation ready — all user-story phases can now begin.

---

## Phase 3: User Story 1 — Phase Transition Replaces Previous Forecast (P1) 🎯 MVP

**Goal**: When Phase 2 posts, the Phase 1 forecast message is deleted first. When Phase 3
posts, the Phase 2 message is deleted first. Deletion failures (missing or forbidden) are
handled gracefully and logged without blocking phase posting. (User Story 3 resilience is
built into this phase's core function.)

**Independent Test**: Trigger Phase 1 for a round, then trigger Phase 2 for the same
round/division; confirm only the Phase 2 message exists in the forecast channel.

- [X] T004 [US1] Implement `delete_forecast_message(round_id, division_id, phase_number, bot)` in `src/services/forecast_cleanup_service.py` — load stored `message_id` + `forecast_channel_id` via DB join; call `channel.get_partial_message(id).delete()`; catch `discord.NotFound` (log, non-error), `discord.Forbidden` (log to calc-log channel, non-error), `discord.HTTPException` (log, non-error); `DELETE FROM forecast_messages` after attempt regardless of outcome — FR-008, FR-009, FR-010, FR-012, FR-013
- [X] T005 [P] [US1] Update `src/services/phase1_service.py` to capture the `discord.Message` returned by `bot.output_router.post_forecast` and call `store_forecast_message` immediately after — FR-001
- [X] T006 [P] [US1] Update `src/services/phase2_service.py` to call `delete_forecast_message(round_id, division_id, phase_number=1, bot)` before posting, then capture the returned `Message` and call `store_forecast_message` for phase 2 — FR-002, FR-004
- [X] T007 [P] [US1] Update `src/services/phase3_service.py` to call `delete_forecast_message(round_id, division_id, phase_number=2, bot)` before posting, then capture the returned `Message` and call `store_forecast_message` for phase 3 — FR-003, FR-005

---

## Phase 4: User Story 2 — Post-Race Forecast Expiry (P2)

**Goal**: 24 hours after a round's scheduled start time, the Phase 3 forecast message is
automatically deleted. The job is scheduled alongside phase jobs and survives bot restarts.

**Independent Test**: Confirm a `cleanup_r{id}` APScheduler job is present after a round
is scheduled; confirm that when fired it calls deletion for Phase 3 only.

- [X] T008 [US2] Add to `src/services/scheduler_service.py`: module-level `_forecast_cleanup_job(round_id: int)` async callable (mirrors `_mystery_notice_job` pattern), `_forecast_cleanup_callback` attribute on `SchedulerService`, and `register_forecast_cleanup_callback(callback)` method — per research.md R-003
- [X] T009 [US2] Update `SchedulerService.schedule_round` in `src/services/scheduler_service.py` to add a `cleanup_r{rnd.id}` `DateTrigger` job at `scheduled_at + timedelta(hours=24)` for non-Mystery rounds; update `cancel_round` to also remove `cleanup_r{round_id}` — FR-006, FR-007
- [X] T010 [US2] Implement `run_post_race_cleanup(round_id, bot)` coroutine in `src/services/forecast_cleanup_service.py` — calls `delete_forecast_message` for `phase_number=3`; used as the `_forecast_cleanup_callback` target — FR-006, FR-008, FR-009
- [X] T011 [US2] Register forecast cleanup callback in `src/bot.py` `on_ready` — call `bot.scheduler_service.register_forecast_cleanup_callback(lambda rid: run_post_race_cleanup(rid, bot))` alongside the existing phase and mystery notice registrations — FR-007

---

## Phase 5: User Story 4 — Test Mode Suppresses Deletions (P2)

**Goal**: While test mode is active, no deletion attempts run; records are preserved.
When test mode is disabled, all stored forecast messages for that server are flushed
immediately.

**Independent Test**: Enable test mode, advance Phase 1 and Phase 2 for a round; confirm
both messages remain in the forecast channel. Disable test mode; confirm both are deleted.

- [X] T012 [US4] Add test-mode guard to `delete_forecast_message` in `src/services/forecast_cleanup_service.py` — lookup `server_id` via `round_id → rounds → divisions → seasons`; call `bot.config_service.get_server_config(server_id)`; if `test_mode_active` is True, return immediately without deleting or clearing the record — FR-014
- [X] T013 [US4] Implement `flush_pending_deletions(server_id, bot)` in `src/services/forecast_cleanup_service.py` — execute the server-scoped JOIN query from data-model.md; for each row call `channel.get_partial_message(message_id).delete()` with FR-008/FR-009 handling; `DELETE FROM forecast_messages WHERE id = ?` after each attempt — FR-015
- [X] T014 [US4] Update `TestModeCog.toggle` in `src/cogs/test_mode_cog.py` to call `await flush_pending_deletions(interaction.guild_id, self.bot)` when `new_state is False`, before sending the confirmation message — FR-015

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Amendment integration (FR-011) and the unit test file. These have no story
label because they span multiple user stories or are infrastructure-level.

- [X] T015 Update `AmendmentService.amend_round` in `src/services/amendment_service.py` to call `delete_forecast_message` for `phase_number` 1, 2, and 3 within the existing invalidation step (after `UPDATE phase_results SET status = 'INVALIDATED'`, before re-running phases) — FR-011
- [X] T016 [P] Write `tests/unit/test_forecast_cleanup.py` covering: US1 phase-transition deletion (happy path and cross-round/cross-division isolation), US2 24h expiry invokes Phase 3 deletion only, US3 NotFound → non-error + log, Forbidden → non-error + log, US4 suppression when test_mode active + flush-on-disable clears all records

---

## Dependencies (story completion order)

```
T001 (migration)
  └─▶ T002 (OutputRouter) ──┐
  └─▶ T003 (service skeleton)┘
            │
            ▼ (both complete)
    T004 (delete_forecast_message)
      ├─▶ T005 [P] (phase1_service)   ┐  all independent of
      ├─▶ T006 [P] (phase2_service)   │  each other → US1 done
      └─▶ T007 [P] (phase3_service)   ┘
    T008 (scheduler callables)
      └─▶ T009 (schedule_round/cancel_round)
    T003 + T008 ──▶ T010 (run_post_race_cleanup)
    T008 + T010 ──▶ T011 (bot.py callback)  → US2 done
    T004 ──▶ T012 (test_mode guard)
    T003 ──▶ T013 (flush_pending_deletions)
    T013 ──▶ T014 (TestModeCog.toggle)       → US4 done
    T004 + T005–T007 ──▶ T015 (AmendmentService)
    T004–T014 ──▶ T016 [P] (unit tests)
```

## Parallel execution opportunities

| Story | Parallel group (after prerequisites met) |
|-------|------------------------------------------|
| US1 | T005, T006, T007 — three different files, no inter-dependencies |
| US2 | T008 and T003 can proceed in parallel with US1; T010 and T011 after T008 |
| US4 | T012 modifies forecast_cleanup_service.py; can be drafted while T013 is written |
| Polish | T015 and T016 can be done in parallel (different files) |

## Implementation strategy

**MVP = US1 only (T001 → T002 → T003 → T004 → T005 + T006 + T007)**. This delivers the
core channel-cleanup guarantee (phase transition replaces forecast) with full resilience.
US2, US4, and amendment integration can follow as independent increments.
