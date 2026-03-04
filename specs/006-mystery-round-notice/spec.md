# Feature Specification: Mystery Round Notice at Phase 1 Horizon

**Feature Branch**: `006-mystery-round-notice`
**Created**: 2026-03-04
**Status**: Draft
**Input**: User description: "Rather than silently skipping Mystery rounds, the bot shall post a fixed notice to the division's forecast channel at T−5 days (Phase 1 horizon). The division role MUST NOT be mentioned. Nothing is posted at T−2 days (Phase 2 horizon) or T−2 hours (Phase 3 horizon)."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Forecast channel acknowledges the Mystery round at T−5 days (Priority: P1)

League drivers know a Phase 1 forecast is posted five days before every race. For Mystery
rounds, drivers currently see nothing — the absence of a forecast is surprising and suggests
the bot may have malfunctioned. With this change, a short acknowledgement message is posted
at the Phase 1 horizon, making it clear the silence is intentional.

**Why this priority**: This is the sole user-facing change and the entire scope of the
feature. There is no lower-priority work to defer.

**Independent Test**: Configure a round with format MYSTERY. Trigger or simulate the Phase 1
horizon. Verify the forecast channel receives the mystery notice message and no division
role is mentioned. Verify no message is posted at the Phase 2 or Phase 3 horizons.

**Acceptance Scenarios**:

1. **Given** a round with format MYSTERY is scheduled, **When** the Phase 1 horizon fires
   (T−5 days), **Then** the bot posts exactly one message to the division forecast channel
   with content matching the mystery notice template (see FR-003) and containing no
   `<@&...>` role mention.
2. **Given** a round with format MYSTERY, **When** the Phase 2 horizon (T−2 days) fires,
   **Then** no message is posted to any channel and no error is logged for this round.
3. **Given** a round with format MYSTERY, **When** the Phase 3 horizon (T−2 hours) fires,
   **Then** no message is posted to any channel and no error is logged for this round.
4. **Given** the mystery notice has been posted, **When** the round is subsequently amended
   to a non-MYSTERY format, **Then** the standard amendment-invalidation flow takes over
   (existing behaviour unchanged); the mystery-notice job is cancelled and the three standard
   phase jobs are rescheduled.
5. **Given** a non-MYSTERY round for which Phase 1 has already fired, **When** it is amended
   to MYSTERY format, **Then** existing phase results are invalidated per the existing
   amendment-invalidation logic; no retroactive mystery notice is posted.
   **Given** a non-MYSTERY round for which the Phase 1 horizon has NOT yet passed, **When**
   it is amended to MYSTERY format, **Then** existing phase jobs are cancelled, existing
   phase results (if any) are invalidated, and `mystery_r{round_id}` is scheduled at
   `scheduled_at − 5 days`.

---

### Edge Cases

- What if the mystery-notice job fires but the forecast channel is unconfigured or unreachable?
  → The existing `output_router` error-handling path applies; the error is logged; no retry
  is scheduled (consistent with phase 1/2/3 failure behaviour).
- Can a mystery notice fire more than once for the same round?
  → No. The job uses `replace_existing=True` and is keyed as `mystery_r{round_id}`, which
  is the same identifier `cancel_round` removes. Once the job fires it is consumed by
  APScheduler and cannot fire again unless explicitly rescheduled.
- Does the mystery notice produce a `phase_results` row in the database?
  → No. There are no random draws and no Rpc — there is nothing to audit. The posting is
  logged via the standard Python logger at INFO level only.
- What about the calculation log channel?
  → Nothing is posted to the log channel for a Mystery round notice. The notice is
  purely informational with no computation to record.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `SchedulerService.schedule_round` MUST schedule a mystery-notice job keyed
  `mystery_r{round_id}` at `scheduled_at − 5 days` (UTC) when `rnd.format == RoundFormat.MYSTERY`.
  It MUST NOT schedule Phase 1, Phase 2, or Phase 3 jobs.
- **FR-002**: `SchedulerService.cancel_round` MUST also attempt to remove the
  `mystery_r{round_id}` job (in addition to the existing `phase1_r`, `phase2_r`, `phase3_r`
  removals). Removal of a non-existent job MUST be silently ignored (consistent with existing
  phase cancellation behaviour).
- **FR-003**: The mystery-notice message MUST follow this exact structure:

  ```
  🏁 **Weather Forecast**
  **Track**: Mystery
  Conditions are unknown to all — weather will be determined by the game at race time.
  ```

  No `<@&...>` role mention MUST appear anywhere in the message.
- **FR-004**: A new `mystery_notice_message() -> str` function MUST be added to
  `src/utils/message_builder.py`. It MUST accept no parameters (the track is always
  rendered as the literal string `Mystery`). It MUST return exactly the string described
  in FR-003.
- **FR-005**: A new `run_mystery_notice(round_id: int, bot: Bot) -> None` coroutine MUST be
  added to a new module `src/services/mystery_notice_service.py`. It MUST:
  - Look up the round and its division's `forecast_channel_id`.
  - Verify the round format is still MYSTERY at execution time (guard against a format
    amendment firing just before the scheduled job).
  - Call `bot.output_router.post_forecast(_Div(), mystery_notice_message())`.
  - Log the posting at INFO level.
  - Perform NO random draws and NO `phase_results` database writes.
- **FR-006**: `SchedulerService` MUST expose a `register_mystery_notice_callback(cb: Callable)`
  method that stores the callback. A module-level `_mystery_notice_job(round_id: int)`
  callable MUST retrieve it via `_GLOBAL_SERVICE` and invoke it, following the same pattern
  as `_phase_job`.
- **FR-007**: `bot.py` MUST register the `run_mystery_notice` callback via
  `scheduler.register_mystery_notice_callback(...)` inside `on_ready`, alongside the
  existing phase callback registrations.
- **FR-008**: No `phase_results` row, no audit trail entry, and no log-channel message
  MUST be produced for a Mystery round notice.
- **FR-009**: When `amendment_service` changes a round's format TO MYSTERY, it MUST call
  `scheduler.cancel_round(round_id)` (removes all phase jobs) and then, if
  `round.scheduled_at − 5 days` is still in the future relative to the current UTC time,
  call `scheduler.schedule_round(round)` to register the `mystery_r{round_id}` job.
  If the T−5 horizon has already passed, no mystery-notice job is scheduled (the
  invalidation notice already informs the channel).

### Non-Functional Requirements

- **NFR-001**: The mystery-notice job MUST use the same `misfire_grace_time` (300 s), same
  UTC timezone, and same `replace_existing=True` semantics as the standard phase jobs.
- **NFR-002**: No new database migrations are required. No schema changes.
- **NFR-003**: All new code paths MUST be covered by unit tests:
  - `mystery_notice_message()` return value matches FR-003 exactly (character-for-character).
  - `schedule_round` for a MYSTERY round schedules exactly one job (`mystery_r{id}`) and
    zero phase jobs.
  - `cancel_round` removes the mystery-notice job ID (mocked scheduler call).
  - `run_mystery_notice` posts to the forecast channel and does NOT post to the log channel.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every MYSTERY round in a live season generates exactly one forecast-channel
  post at T−5 days and zero posts at T−2 days and T−2 hours.
- **SC-002**: The mystery-notice message contains zero `<@&...>` substrings.
- **SC-003**: Unit test suite passes with 100% of new test cases green and no regressions
  in existing phase-related tests.

## Clarifications

### Session 2026-03-04

- Q: Should the calculation log channel receive any entry for a Mystery round notice? → A: No — the notice is informational only; no computation log is produced.
- Q: Division role tagging for mystery notice? → A: No role tag; explicit departure from Phase 1/2/3 behaviour.
- Q: Should test mode distinguish "notice posted" vs "not yet posted" for Mystery rounds, requiring a schema change or phase_results row? → A: No — test mode always shows `*(Mystery Round — phases N/A)*`; NFR-002 (no schema changes) is upheld. AC-6 removed from scope.
- Q: Exact mystery-notice message copy (FR-003)? → A: "Conditions are unknown to all — weather will be determined by the game at race time." Intent: genuine universal unknowing (not secrecy); conditions are randomly set by the game, not pre-determined and hidden.
- Q: When a non-MYSTERY round is amended to MYSTERY and the T−5 horizon has not yet passed, should a mystery-notice job be scheduled? → A: Yes — schedule `mystery_r{round_id}` at T−5 (FR-009). If T−5 has already passed, no job is scheduled.
