# Feature Specification: Message Retry Queue

**Feature Branch**: `017-msg-retry-queue`  
**Created**: 2026-03-12  
**Status**: Draft  
**Input**: User description: "If there is an error posting a message to a channel, it should be logged to the database. Every 5 minutes, the bot should attempt to post this message until it succeeds."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Failed Message Is Retried Automatically (Priority: P1)

When the bot fails to post a weather forecast or log message due to a transient error (e.g., a 503 upstream disconnect), the message is persisted immediately and automatically retried every 5 minutes until it posts successfully.

**Why this priority**: This is the core value of the feature. Without this, critical weather phase outputs can be silently dropped, violating Constitution Principle IV (Three-Phase Weather Pipeline) and Principle VII (Output Channel Discipline). The 2026-03-12 production incident — a 503 overflow error causing a missed forecast post — is the direct motivator.

**Independent Test**: Can be fully tested by simulating a channel send failure (e.g., Discord returning 503), verifying the failed message appears in the retry store, waiting one retry cycle, then restoring normal connectivity and confirming the message is posted and removed from the retry store.

**Acceptance Scenarios**:

1. **Given** the bot attempts to post a message to a forecast or log channel, **When** the send fails with any HTTP or connection error, **Then** the message content, target channel ID, and failure timestamp are persisted to the database before the current operation completes.
2. **Given** a pending message exists in the retry store, **When** the retry worker fires (every 5 minutes), **Then** the bot attempts to post each pending message in the order it was originally queued.
3. **Given** a pending message is successfully posted on a retry attempt, **When** the post succeeds, **Then** the entry is removed from the retry store and no further retry attempts are made for that message.
4. **Given** a pending message fails again on a retry attempt, **When** the send returns an error, **Then** the entry remains in the retry store, the failure count is incremented, and the next retry occurs after another 5-minute interval.

---

### User Story 2 - Retry Store Is Visible in Audit Log (Priority: P2)

Each retry attempt — successful or failed — is recorded in the existing audit trail so administrators can diagnose persistent delivery failures.

**Why this priority**: Constitution Principle V (Observability & Change Audit Trail) requires that state transitions are loggable. If a message has been retrying for an extended period, the admin needs visibility without having to inspect the database directly.

**Independent Test**: Can be tested independently by inspecting the calculation log channel after a retry cycle completes; a log entry indicating retry outcome (success or persistent failure) must appear.

**Acceptance Scenarios**:

1. **Given** a pending message is successfully delivered on a retry, **When** the post succeeds, **Then** a log entry noting the channel, original failure reason, number of retries taken, and delivery timestamp is posted to the calculation log channel.
2. **Given** a pending message has been retried and failed more than a configurable threshold (default: 12 attempts, i.e., ~1 hour), **When** the threshold is crossed, **Then** a warning entry is posted to the calculation log channel alerting that a message has been stuck for an extended period.

---

### Edge Cases

- What happens if the bot restarts while messages are pending in the retry store? Pending entries MUST survive a bot restart and be picked up by the retry worker on the next cycle after startup.
- What happens if the target channel is permanently deleted or the bot loses access permanently? The message remains in the retry store and continues to count retries; the extended-failure warning (User Story 2) surfaces this condition to administrators.
- What happens if the retry store itself cannot be written to (e.g., database error on initial failure)? The failed send is logged to the application log at ERROR level; the message is not silently swallowed, but it is also not retried (the retry mechanism depends on the store being writable).
- What happens if the same message content is queued multiple times for the same channel (e.g., duplicate phase triggers)? Each enqueued entry is treated independently; deduplication is not required.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: When any `OutputRouter` send attempt fails (HTTP error, connection reset, forbidden, channel not found), the system MUST persist a retry entry to the database containing: target channel ID, message content, original failure reason, enqueue timestamp, and retry count (initialized to 0).
- **FR-002**: The bot MUST run a background retry worker that fires every 5 minutes and processes all pending retry entries.
- **FR-003**: For each pending retry entry, the worker MUST attempt to post the message to the original target channel using the same chunking logic as the normal send path.
- **FR-004**: On successful delivery of a retried message, the system MUST delete the retry entry from the database.
- **FR-005**: On failed delivery during a retry attempt, the system MUST increment the retry count on the database entry and leave it pending for the next cycle.
- **FR-006**: Retry entries MUST persist across bot restarts; the retry worker MUST load and process all pending entries from the database on startup and then continue on its 5-minute schedule.
- **FR-007**: When a retried message is successfully delivered, the system MUST post a notification to the server's calculation log channel recording the channel ID, original failure reason, retry count at delivery, and delivery timestamp.
- **FR-008**: When a retry entry's count exceeds a configurable threshold (default: 12 retries), the system MUST post a warning to the calculation log channel identifying the stuck entry, target channel, and current retry count.
- **FR-009**: The retry worker MUST NOT itself enqueue new retry entries if it encounters a delivery failure during retry processing (to avoid infinite self-referential loops); it MUST only increment the existing entry's retry count.

### Key Entities

- **PendingMessage**: Represents a message queued for retry. Key attributes: unique ID, target channel ID (integer), message content (text), original failure reason (text), enqueue timestamp (UTC), retry count (integer), last attempted timestamp (UTC, nullable).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A message that fails to post due to a transient error is automatically delivered within 5 minutes of the error resolving, with no manual intervention.
- **SC-002**: Zero weather phase messages are permanently lost due to transient channel errors; every failed send either eventually delivers or surfaces a visible warning to administrators.
- **SC-003**: After a bot restart, all messages that were pending before the restart are retried within the first retry cycle (within 5 minutes of bot becoming ready).
- **SC-004**: Administrators can determine within one inspection of the calculation log channel whether any message has been retrying for more than approximately 1 hour.

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]
2. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

### User Story 2 - [Brief Title] (Priority: P2)

[Describe this user journey in plain language]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

### User Story 3 - [Brief Title] (Priority: P3)

[Describe this user journey in plain language]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

[Add more user stories as needed, each with an assigned priority]

### Edge Cases

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right edge cases.
-->

- What happens when [boundary condition]?
- How does system handle [error scenario]?

## Requirements *(mandatory)*

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right functional requirements.
-->

### Functional Requirements

- **FR-001**: System MUST [specific capability, e.g., "allow users to create accounts"]
- **FR-002**: System MUST [specific capability, e.g., "validate email addresses"]  
- **FR-003**: Users MUST be able to [key interaction, e.g., "reset their password"]
- **FR-004**: System MUST [data requirement, e.g., "persist user preferences"]
- **FR-005**: System MUST [behavior, e.g., "log all security events"]

*Example of marking unclear requirements:*

- **FR-006**: System MUST authenticate users via [NEEDS CLARIFICATION: auth method not specified - email/password, SSO, OAuth?]
- **FR-007**: System MUST retain user data for [NEEDS CLARIFICATION: retention period not specified]

### Key Entities *(include if feature involves data)*

- **[Entity 1]**: [What it represents, key attributes without implementation]
- **[Entity 2]**: [What it represents, relationships to other entities]

## Success Criteria *(mandatory)*

<!--
  ACTION REQUIRED: Define measurable success criteria.
  These must be technology-agnostic and measurable.
-->

### Measurable Outcomes

- **SC-001**: [Measurable metric, e.g., "Users can complete account creation in under 2 minutes"]
- **SC-002**: [Measurable metric, e.g., "System handles 1000 concurrent users without degradation"]
- **SC-003**: [User satisfaction metric, e.g., "90% of users successfully complete primary task on first attempt"]
- **SC-004**: [Business metric, e.g., "Reduce support tickets related to [X] by 50%"]
