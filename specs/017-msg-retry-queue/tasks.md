# Tasks: Message Retry Queue (017)

**Input**: Design documents from `specs/017-msg-retry-queue/`
**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | quickstart.md ✅

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to ([US1], [US2])

---

## Phase 1: Setup

**Purpose**: Migration, model, and shared infrastructure — prerequisites for all implementation work.

- [ ] T001 Create migration `src/db/migrations/015_pending_messages.sql` with `pending_messages` table schema per data-model.md
- [ ] T002 Create dataclass `src/models/pending_message.py` with `PendingMessage` (id, server_id, channel_id, content, failure_reason, enqueued_at, retry_count, last_attempted_at)

**Checkpoint**: Schema and model in place — service and cog work can begin.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The `RetryService` must be complete before `OutputRouter` can be wired up and before `RetryCog` can call delivery.

**⚠️ CRITICAL**: Both user stories depend on this service layer being complete.

- [ ] T003 Create `src/services/retry_service.py` with `RETRY_WARN_THRESHOLD = 12` constant and `enqueue(db_path, server_id, channel_id, content, failure_reason) -> None` — INSERT a row into `pending_messages`
- [ ] T004 Add `get_all_pending(db_path) -> list[PendingMessage]` to `src/services/retry_service.py` — SELECT all rows ordered by `enqueued_at ASC`
- [ ] T005 Add `mark_delivered(db_path, entry_id) -> None` to `src/services/retry_service.py` — DELETE the row
- [ ] T006 Add `mark_failed(db_path, entry_id) -> None` to `src/services/retry_service.py` — increment `retry_count` and set `last_attempted_at = now(UTC)`

**Checkpoint**: Service CRUD layer complete — `attempt_delivery` and `OutputRouter` wiring can now proceed.

---

## Phase 3: User Story 1 — Failed Message Is Retried Automatically (Priority: P1) 🎯 MVP

**Goal**: Any `OutputRouter._send` failure persists the message, and a background worker retries it every 5 minutes until it is delivered.

**Independent Test**: Trigger a channel send failure (e.g., set forecast channel ID to a non-existent snowflake), verify a row appears in `pending_messages`, restore the channel, wait up to 5 minutes, confirm the row is deleted and the message was posted.

### Implementation for User Story 1

- [ ] T007 [US1] Add `attempt_delivery(entry: PendingMessage, bot: Bot) -> bool` to `src/services/retry_service.py`:
  - Resolve channel via `bot.get_channel` → `bot.fetch_channel`
  - Re-chunk content via `_chunk_message` from `utils.output_router`
  - Call `channel.send()` for each chunk
  - On success: call `mark_delivered`; post a log notification via `bot.output_router.post_log(entry.server_id, ...)` (best-effort; swallow failures at WARNING level)
  - On failure: if `entry.retry_count >= RETRY_WARN_THRESHOLD`, post a warning to `bot.output_router.post_log` first; then call `mark_failed`
  - Never raises; never calls `enqueue()` (FR-009)
- [ ] T008 [US1] Modify `src/utils/output_router.py`:
  - Add `retry_db_path: str | None = None` to `__init__`; store as `self._retry_db_path`
  - Add `server_id: int` parameter to `_send`
  - In `_send`, after `discord.HTTPException` or `discord.Forbidden` is caught: if `self._retry_db_path` is set, `await enqueue(self._retry_db_path, server_id, channel_id, content, str(exc))`
  - `post_forecast` gains `server_id: int` parameter and forwards it to `_send`
  - `post_log` already has `server_id`; update its `_send` call to pass it through
- [ ] T009 [US1] Update `src/services/phase1_service.py`: pass `server_id=row["server_id"]` to `bot.output_router.post_forecast(...)`
- [ ] T010 [P] [US1] Update `src/services/phase2_service.py`: pass `server_id=row["server_id"]` to `bot.output_router.post_forecast(...)`
- [ ] T011 [P] [US1] Update `src/services/phase3_service.py`: pass `server_id=row["server_id"]` to `bot.output_router.post_forecast(...)`
- [ ] T012 [P] [US1] Update `src/services/mystery_notice_service.py`: pass `server_id=row["server_id"]` to `bot.output_router.post_forecast(...)`
- [ ] T013 [P] [US1] Update `src/services/amendment_service.py`: pass `server_id` to `bot.output_router.post_forecast(...)` (already in scope as parameter)
- [ ] T014 [US1] Create `src/cogs/retry_cog.py` with `RetryCog`:
  - `@tasks.loop(minutes=5)` async method `retry_loop`: calls `get_all_pending`, iterates entries, calls `attempt_delivery` for each
  - `@retry_loop.before_loop` hook calls `await self._bot.wait_until_ready()`
  - `cog_unload` cancels the loop
- [ ] T015 [US1] Modify `src/bot.py`:
  - Construct `OutputRouter` with `retry_db_path=DB_PATH`
  - Import and `await bot.add_cog(RetryCog(bot))` alongside other cogs

**Checkpoint**: User Story 1 fully functional. Failed sends are persisted and retried every 5 minutes without manual intervention.

---

## Phase 4: User Story 2 — Retry Store Is Visible in Audit Log (Priority: P2)

**Goal**: Retry outcomes surface in the calculation log channel so administrators can diagnose persistent delivery failures.

**Independent Test**: After a successful retry delivery, a log-channel message confirms the channel ID, failure reason, retry count, and timestamp. After 12 failed retries, a warning appears in the log channel each subsequent cycle.

> **Note**: The core logging hooks are already embedded in `attempt_delivery` (T007). This phase validates and hardens the observable behaviour.

### Implementation for User Story 2

- [ ] T016 [US2] Verify delivery notification format in `src/services/retry_service.py` `attempt_delivery` (success branch):
  - Message MUST include: target channel ID, original `failure_reason`, `retry_count` at delivery, and UTC delivery timestamp
  - Log format should match existing log-channel message conventions (plain text, clear labelling)
- [ ] T017 [US2] Verify warning notification format in `src/services/retry_service.py` `attempt_delivery` (failure branch, threshold crossed):
  - Message MUST include: entry ID, target channel ID, current `retry_count`, `enqueued_at`
  - Warning fires every cycle once `retry_count >= RETRY_WARN_THRESHOLD` (not just once)
- [ ] T018 [US2] Add application-level logging (`log.warning(...)`) to `attempt_delivery` for the case where the log-channel notification itself fails (best-effort; body of work is a single log call in the swallowed-exception handler)

**Checkpoint**: Administrators can monitor retry state and detect stuck messages solely through the calculation log channel.

---

## Phase 5: Polish & Cross-Cutting Concerns

- [X] T019 [P] Write unit tests in `tests/unit/test_retry_service.py`:
  - `enqueue` inserts a row with correct fields
  - `get_all_pending` returns rows ordered by `enqueued_at`
  - `mark_delivered` deletes the row
  - `mark_failed` increments `retry_count` and sets `last_attempted_at`
  - Use in-memory aiosqlite DB (same pattern as existing unit tests)
- [X] T020 [P] Write integration test in `tests/integration/test_retry_worker.py`:
  - Simulate a send failure → row appears in `pending_messages`
  - Simulate recovery → `attempt_delivery` returns `True`, row is deleted
  - Simulate persistent failure (mock channel raises on every call) → `retry_count` increments, warning fires at threshold
- [ ] T021 Run quickstart.md manual verification steps to confirm end-to-end behaviour in a live bot environment

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 (model must exist before service is written)
- **Phase 3 (US1)**: Depends on Phase 2 — T007 through T015 all require the service layer
- **Phase 4 (US2)**: Depends on T007 (attempt_delivery) being written in Phase 3 — hardens existing notification code
- **Phase 5 (Polish)**: Depends on Phases 3 and 4 being complete

### Task-level dependencies within Phase 3

```
T001 → T002 → T003 → T004 → T005 → T006   (sequential setup + service CRUD)
                                   ↓
                                  T007      (attempt_delivery — needs mark_delivered, mark_failed)
                                   ↓
                                  T008      (OutputRouter wiring — needs enqueue)
                                  ↓ ↓ ↓ ↓ ↓
                     T009  T010  T011  T012  T013   (call-site updates — parallel, each 1 file)
                                   ↓
                                  T014      (RetryCog — needs attempt_delivery, get_all_pending)
                                   ↓
                                  T015      (bot.py wiring — needs OutputRouter + RetryCog)
```

### Parallel opportunities per phase

**Phase 3 call-site updates (T009–T013)**: All five update the single `post_forecast` call in independent service files. Can be worked simultaneously.

**Phase 5 tests (T019–T020)**: Unit and integration test files have no dependency on each other.

---

## Implementation Strategy

**MVP = Phase 1 + Phase 2 + Phase 3 (US1 only)**

This delivers the full retry queue: failed messages are persisted and automatically retried. Phase 4 (US2) adds the operational visibility layer on top. Phase 5 adds test coverage.

The incident that motivated this feature (503 forecast message drop on 2026-03-12) is fully addressed by Phase 3 alone.
