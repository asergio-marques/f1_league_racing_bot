# Feature Specification: Forecast Channel Message Cleanup

**Feature Branch**: `001-forecast-msg-cleanup`  
**Created**: 2026-03-04  
**Status**: Draft  
**Input**: User description: "Before posting the Phase 2 output message for a given round and division, delete the Phase 1 output message for that same round and division. Before posting the Phase 3 output message, delete the Phase 2 output message. 24 hours after a round starts, delete the Phase 3 output message. This keeps forecast channels tidy and easy to read."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Phase Transition Replaces Previous Forecast (Priority: P1)

A league member opens the division's weather forecast channel before a race weekend.
Instead of seeing a growing stack of Phase 1, Phase 2, and Phase 3 messages from the same
round, they see only the most recent phase message. When Phase 2 is posted, Phase 1 is gone.
When Phase 3 is posted, Phase 2 is gone.

**Why this priority**: This is the core requirement. Without phase-to-phase cleanup the channel
becomes cluttered with superseded forecasts, which is the primary pain the feature addresses.

**Independent Test**: Can be fully tested by triggering Phase 1 posting for a round, then
triggering Phase 2 for the same round, and confirming that only the Phase 2 message exists
in the forecast channel for that round and division.

**Acceptance Scenarios**:

1. **Given** Phase 1 has been posted for Round R in Division D, **When** Phase 2 is posted
   for Round R / Division D, **Then** the Phase 1 message no longer exists in the Division D
   forecast channel and the Phase 2 message is present.
2. **Given** Phase 2 has been posted for Round R in Division D, **When** Phase 3 is posted
   for Round R / Division D, **Then** the Phase 2 message no longer exists in the Division D
   forecast channel and the Phase 3 message is present.
3. **Given** Phase 1 has been posted for Round R in Division D **and** Phase 1 has been
   posted for Round S in Division D, **When** Phase 2 is posted for Round R / Division D,
   **Then** only the Phase 1 message for Round R is deleted; Round S Phase 1 message is
   unaffected.
4. **Given** Phase 1 has been posted for Round R in Division A **and** Phase 1 has been
   posted for Round R in Division B, **When** Phase 2 is posted for Round R / Division A,
   **Then** only Division A's Phase 1 message is deleted; Division B's Phase 1 message is
   unaffected.

---

### User Story 2 - Post-Race Forecast Expiry (Priority: P2)

After a race weekend has concluded, a league member opens the forecast channel and sees a
clean state with no old Phase 3 message still pinned there. The Phase 3 message automatically
disappears 24 hours after the round's scheduled start time.

**Why this priority**: Completes the full cleanup lifecycle. Without this, Phase 3 messages
accumulate across rounds and the channel still becomes cluttered over a season.

**Independent Test**: Can be fully tested by verifying that a Phase 3 message previously
present in the channel is absent when checked 24 hours (± a short scheduling tolerance) after
the round's scheduled start time.

**Acceptance Scenarios**:

1. **Given** Phase 3 has been posted for Round R in Division D and the round's scheduled
   start time has passed, **When** exactly 24 hours after the scheduled start time elapses,
   **Then** the Phase 3 message for Round R / Division D no longer exists in the forecast
   channel.
2. **Given** Phase 3 has been posted for Round R in Division D, **When** only 23 hours have
   elapsed since the scheduled start time, **Then** the Phase 3 message is still present in
   the forecast channel.
3. **Given** Phase 3 messages exist for multiple rounds across multiple divisions,
   **When** the 24-hour expiry fires for one specific round/division combination, **Then**
   only that specific Phase 3 message is deleted; all others remain.

---

### User Story 3 - Resilient Deletion (Priority: P3)

The bot attempts to delete a forecast message that has already been manually removed by a
server administrator. The bot handles this gracefully: it logs the situation and continues
posting the next phase message without failing or generating a visible error.

**Why this priority**: Defensive behavior that prevents a missing message from breaking
the pipeline. Less critical than the happy path but necessary for production reliability.

**Independent Test**: Can be fully tested by manually deleting a Phase 1 message from the
channel, then triggering Phase 2, and confirming that Phase 2 posts successfully and the bot
does not error out.

**Acceptance Scenarios**:

1. **Given** the Phase 1 message for Round R / Division D has been manually deleted from the
   channel before Phase 2 fires, **When** Phase 2 runs for Round R / Division D, **Then**
   Phase 2 is posted successfully, the missing message is logged, and no error is surfaced
   to users.
2. **Given** the bot does not have permission to delete messages in the forecast channel,
   **When** a phase transition or 24-hour expiry fires, **Then** the deletion failure is
   logged, the next phase message is still posted (for phase transitions), and no error
   is surfaced to users.

---

### Edge Cases

- What happens when a round is amended and a phase is invalidated after its message was
  already posted? The stored message ID for the invalidated phase must be cleared and the
  message deleted (or recorded as already-gone) before the re-run phase posts a fresh
  message.
- What happens when the bot restarts between when a phase was posted and when the deletion
  should occur? Stored message IDs are persisted in durable storage, so the scheduled
  deletion must be able to recover on restart.
- What happens when a round is postponed after Phase 3 was already posted? If Phase 3 was
  invalidated by the postponement amendment, its cleanup follows amendment-invalidation
  rules. The 24-hour post-round expiry, if rescheduled, applies to the new start time.
- What happens when a round is cancelled after a phase message has been posted? The message
  should be deleted as part of the cancellation's amendment-invalidation flow, not left
  waiting for the 24-hour timer.
- What happens for Mystery Rounds? No phase messages are ever posted, so no cleanup actions
  are needed or triggered.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: For every non-Mystery round, the system MUST record the channel message ID of
  the Phase 1 forecast message immediately after it is successfully posted, keyed by round
  identifier and division identifier, and persist it to durable storage.
- **FR-002**: For every non-Mystery round, the system MUST record the channel message ID of
  the Phase 2 forecast message immediately after it is successfully posted, keyed by round
  identifier and division identifier, and persist it to durable storage.
- **FR-003**: For every non-Mystery round, the system MUST record the channel message ID of
  the Phase 3 forecast message immediately after it is successfully posted, keyed by round
  identifier and division identifier, and persist it to durable storage.
- **FR-004**: Before posting the Phase 2 forecast message for a given round and division,
  the system MUST attempt to delete the stored Phase 1 forecast message for that round and
  division from the division's weather forecast channel.
- **FR-005**: Before posting the Phase 3 forecast message for a given round and division,
  the system MUST attempt to delete the stored Phase 2 forecast message for that round and
  division from the division's weather forecast channel.
- **FR-006**: Exactly 24 hours after a round's scheduled start time, the system MUST
  attempt to delete the stored Phase 3 forecast message for that round and division from
  the division's weather forecast channel.
- **FR-007**: The 24-hour post-start deletion MUST be scheduled using the same scheduler
  mechanism used to trigger weather phases (i.e., as a persistent scheduled job tied to the
  round's start time), so that it survives bot restarts.
- **FR-008**: If the forecast message targeted for deletion no longer exists in the channel
  (e.g., it was manually deleted), the system MUST treat this as a non-error, log the
  situation, and continue without interrupting any pending pipeline operation.
- **FR-009**: If the bot lacks permission to delete a forecast message, the system MUST log
  the failure and continue without interrupting any pending pipeline operation.
- **FR-010**: After a deletion attempt (successful, missing, or permission-denied), the
  stored message ID for that phase/round/division MUST be cleared from durable storage.
- **FR-011**: When a round amendment triggers phase-output invalidation per the existing
  amendment rules, the stored message IDs for the invalidated phases MUST be cleared and
  the corresponding messages deleted (subject to FR-008 and FR-009) as part of the
  invalidation flow, before the re-run phase posts its new message.
- **FR-012**: Deletion actions MUST NOT be attempted for Mystery Rounds (which have no
  phase messages).
- **FR-013**: A deletion attempt for Round R / Division A MUST NOT affect any message
  belonging to a different round or a different division.

### Key Entities

- **Forecast Message Record**: Represents a posted phase forecast message. Attributes:
  round identifier, division identifier, phase number (1, 2, or 3), channel message ID,
  posted timestamp. One record per round/division/phase combination at any given time.

## Assumptions

- The "round's scheduled start time" used as the T=0 reference for the 24-hour expiry is
  the same timestamp already used as T=0 for phase-horizon scheduling (T−5d, T−2d, T−2h).
- If a round is cancelled, the amendment-invalidation flow (FR-011) handles message
  deletion; no separate 24-hour expiry job is fired for cancelled rounds.
- The bot has the necessary channel permissions to delete its own messages in the
  division forecast channel. Lack of permission is treated as a degraded-but-non-fatal
  condition (FR-009).
- Only the most recently posted message ID for a given round/division/phase is tracked at
  any one time. If a phase is invalidated and re-run (amendment flow), the new message ID
  replaces the old one in storage after FR-011 clears the prior record.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After Phase 2 is posted for any round, the Phase 1 forecast message for that
  round and division is absent from the division's weather forecast channel.
- **SC-002**: After Phase 3 is posted for any round, the Phase 2 forecast message for that
  round and division is absent from the division's weather forecast channel.
- **SC-003**: 24 hours (within a scheduling tolerance of ±5 minutes) after a round's
  scheduled start time, the Phase 3 forecast message for that round and division is absent
  from the division's weather forecast channel.
- **SC-004**: A forecast channel that has completed a full round lifecycle (all three phases
  posted and the 24-hour expiry fired) contains no messages from that round.
- **SC-005**: A failed deletion attempt (missing message or permission error) does not
  prevent the next phase message from being posted and is recorded in the calculation log
  channel.
