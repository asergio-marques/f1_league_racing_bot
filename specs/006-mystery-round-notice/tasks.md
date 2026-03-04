# Tasks: Mystery Round Notice at Phase 1 Horizon

**Input**: Design documents from `specs/006-mystery-round-notice/`
**Prerequisites**: plan.md, spec.md

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no incomplete dependencies)
- **[Story]**: Which user story this task belongs to (US1 = mystery round notice)

---

## Phase 1: Setup

**Purpose**: Confirm branch and scaffolding are in place.

- [X] T001 Confirm `specs/006-mystery-round-notice/` contains plan.md and spec.md on branch `006-mystery-round-notice`

---

## Phase 2: Foundational (Blocking Prerequisites)

No new infrastructure required. The existing `OutputRouter`, `SchedulerService`, `message_builder.py`,
and test suite are all in place. Skip directly to implementation.

**Checkpoint**: Branch `006-mystery-round-notice` is checked out — confirmed ✅

---

## Phase 3: User Story 1 — Mystery Round Notice at T−5 Days (Priority: P1) 🎯 MVP

**Goal**: At the Phase 1 horizon (T−5 days) a Mystery round posts a fixed informational
message to the division forecast channel — no role tag, no random draws, no log-channel
entry. Nothing is posted at T−2 days or T−2 hours.

**Independent Test**: Run `pytest tests/unit/test_mystery_notice.py` — all new tests must
pass. Run `pytest tests/unit/` — nothing must regress. Manually verify `schedule_round`
with a MYSTERY round creates exactly one APScheduler job keyed `mystery_r{id}`.

### Tests for User Story 1

- [X] T002 [P] [US1] Create `tests/unit/test_mystery_notice.py` with unit tests covering
  all NFR-003 cases (write first — they will fail until implementation is complete):
  - `mystery_notice_message()` return value matches FR-003 exactly
    (no `<@&` substring; contains `🏁`, `**Weather Forecast**`, `**Track**: Mystery`,
    `Conditions are unknown to all`)
  - `schedule_round` called with a MYSTERY `Round` object schedules exactly one job
    keyed `mystery_r{round_id}` and zero `phase1_r` / `phase2_r` / `phase3_r` jobs
    (mock `self._scheduler.add_job`; assert call_count == 1 and job id arg matches)
  - `cancel_round` attempts to remove `mystery_r{round_id}` in addition to the three
    phase job ids (mock `self._scheduler.remove_job`; assert four remove attempts)
  - `run_mystery_notice` calls `output_router.post_forecast` exactly once with the
    mystery-notice string and does NOT call `output_router.post_log`

### Implementation for User Story 1

- [X] T003 [P] [US1] Add `mystery_notice_message() -> str` to `src/utils/message_builder.py` (FR-004):
  - Returns exactly:
    ```
    🏁 **Weather Forecast**
    **Track**: Mystery
    Conditions are unknown to all — weather will be determined by the game at race time.
    ```
  - No parameters; no role mention
- [X] T004 [P] [US1] Add mystery-notice scheduler support to `src/services/scheduler_service.py` (FR-001, FR-002, FR-006):
  - Add module-level `async def _mystery_notice_job(round_id: int)` callable following
    the `_phase_job` pattern (look up `_GLOBAL_SERVICE._mystery_notice_callback`)
  - Add `_mystery_notice_callback: Callable | None = None` instance attribute in `__init__`
  - Add `register_mystery_notice_callback(self, cb: Callable) -> None` method
  - Update `schedule_round`: when `rnd.format == RoundFormat.MYSTERY`, schedule
    `mystery_r{rnd.id}` at `scheduled_at − 5 days` using the same `DateTrigger`,
    `replace_existing=True`, and `misfire_grace_time` as phase jobs; return immediately
    (no phase jobs scheduled)
  - Update `cancel_round`: add `mystery_r{round_id}` to the set of job ids to attempt
    removal (silently ignore `JobLookupError` / any exception, consistent with phase jobs)
- [X] T005 [US1] Create `src/services/mystery_notice_service.py` with `run_mystery_notice(round_id, bot)` (FR-005):
  - Look up round row + division `forecast_channel_id` via `get_connection`
  - Guard: if row is None or `row["format"] != "MYSTERY"`, log a warning and return
  - Build a `_Div` helper with `forecast_channel_id` and call
    `bot.output_router.post_forecast(_Div(), mystery_notice_message())`
  - Log at INFO: `"Mystery notice posted for round %s"` with `round_id`
  - No `phase_results` write; no `post_log` call
  (depends on T003 for the import of `mystery_notice_message`)
- [X] T006 [US1] Update `src/services/amendment_service.py` amendment-to-MYSTERY path (FR-009):
  - After calling `scheduler.cancel_round(round_id)`, check whether
    `round.scheduled_at − timedelta(days=5) > datetime.now(timezone.utc)`
  - If true: call `scheduler.schedule_round(updated_round)` to register the mystery-notice job
  - If false (T−5 already passed): do nothing — the invalidation notice already informs the channel
  (depends on T004 for `schedule_round` MYSTERY behaviour)
- [X] T007 [US1] Register mystery notice callback in `src/bot.py` inside `on_ready` (FR-007):
  - Import `run_mystery_notice` from `services.mystery_notice_service`
  - Call `self.scheduler.register_mystery_notice_callback(run_mystery_notice_cb)`
    alongside the existing `register_callbacks(phase1_cb, phase2_cb, phase3_cb)` call
  (depends on T004, T005)
- [X] T008 [US1] Run full unit suite: `pytest tests/unit/` — all new tests pass, zero
  regressions in existing phase, message_builder, and scheduler tests (depends on T002–T007)

**Checkpoint**: Mystery rounds now post a notice at T−5 days. The feature is fully
verifiable by running the unit suite without a running Discord bot.

---

## Final Phase: Polish & Cross-Cutting Concerns

- [X] T009 [P] Verify `mystery_notice_message()` output contains no `<@&` substring:
  `python -c "from utils.message_builder import mystery_notice_message; assert '<@&' not in mystery_notice_message()"`
- [X] T010 [P] Verify `schedule_round` for MYSTERY no longer logs the old
  `"no weather phases scheduled"` message (grep confirms updated log text in scheduler_service.py)

---

## Dependencies

```
T002 (tests) ──────────────────────────────► T008
T003 (message) ──► T005 ──► T007 ──────────► T008
T004 (scheduler) ─► T005 ──► T007 ──────────► T008
                 └─► T006 ──────────────────► T008
```

T001, T009, T010 are fully independent.
T002, T003, T004 have no dependencies on each other → all three can start in parallel.

## Parallel Execution

T002, T003, and T004 all touch different files and can be implemented simultaneously:

```
# Start all three in parallel:
Task T002: tests/unit/test_mystery_notice.py         (new file — tests first, will fail)
Task T003: src/utils/message_builder.py              (new function)
Task T004: src/services/scheduler_service.py         (new callable + method + schedule_round + cancel_round)

# Then, once T003 + T004 are done:
Task T005: src/services/mystery_notice_service.py    (new file — imports T003)
Task T006: src/services/amendment_service.py         (modifies T004 path)

# Then, once T005 + T004 are done:
Task T007: src/bot.py                               (wires T005 + T004 callback)

# Validate:
Task T008: pytest tests/unit/
```

## Implementation Strategy

MVP = T001 → (T002 ∥ T003 ∥ T004) → (T005 ∥ T006) → T007 → T008 → (T009 ∥ T010).
All tasks are required; the feature is small enough to deliver in one increment.
