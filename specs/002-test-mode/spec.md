# Feature Specification: Test Mode for System Verification

**Feature Branch**: `002-test-mode`  
**Created**: 2026-03-04  
**Status**: Draft  
**Input**: User description: "Add a test mode to allow system testing of the bot in Discord without waiting for phase triggers, with commands to enable test mode and advance through scheduled phases"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Toggle Test Mode (Priority: P1)

A user with the configured interaction role issues a command in the configured interaction channel to enable test mode. The bot confirms that test mode is now active. The user later issues the same command to disable it, and the bot confirms it has been disabled.

**Why this priority**: Without the ability to enter and exit test mode, no other test-mode functionality is accessible. This is the foundational capability gating everything else and is independently deployable as a toggle with clear on/off feedback.

**Independent Test**: Can be fully tested by issuing the toggle command and verifying the bot's confirmation response changes between "enabled" and "disabled" states accordingly.

**Acceptance Scenarios**:

1. **Given** the bot is configured (bot-init completed), **When** a user with the configured interaction role issues the test mode toggle command in the configured interaction channel, **Then** the bot responds confirming test mode is now active.
2. **Given** test mode is already active, **When** the same user issues the test mode toggle command again, **Then** the bot responds confirming test mode is now inactive.
3. **Given** test mode is active, **When** the bot is restarted, **Then** test mode remains active and phase advancement progress is preserved.
4. **Given** test mode toggle is attempted by a user without the configured interaction role, **When** they issue the command, **Then** the bot silently ignores the command as per the standard access control rules.
5. **Given** test mode toggle is attempted in a channel that is not the configured interaction channel, **When** issued, **Then** the bot silently ignores the command.

---

### User Story 2 - Advance to Next Phase (Priority: P2)

A user with the configured interaction role, while test mode is active, issues a command to advance the bot to the next pending scheduled phase. The bot immediately executes that phase — computing weather data, posting outputs to the relevant forecast and log channels — exactly as if the scheduler had triggered it at its real scheduled time.

**Why this priority**: This is the core test-mode action. It enables the user to verify that Phase 1, 2, and 3 outputs are correct across all rounds and divisions without waiting for scheduled times, making it the primary value delivered by this feature.

**Independent Test**: Can be fully tested by advancing through Phase 1, Phase 2, and Phase 3 of a single round for a single division and verifying that the bot posts appropriate messages to the forecast and log channels with correct content.

**Acceptance Scenarios**:

1. **Given** test mode is active and at least one phase has not yet been executed, **When** the user issues the advance-phase command, **Then** the bot identifies the chronologically earliest pending phase across all divisions and rounds and executes it immediately.
2. **Given** Phase 1 has been triggered for a round via advance-phase, **When** the user issues advance-phase again, **Then** the bot executes Phase 2 for the appropriate next pending phase, using the rain probability value calculated during Phase 1 of that round.
3. **Given** Phase 2 has been triggered for a round via advance-phase, **When** the user issues advance-phase again, **Then** the bot executes Phase 3, using the session weather slot types drawn during Phase 2 of that round.
4. **Given** all phases for all rounds of all divisions have been executed, **When** the user issues the advance-phase command, **Then** the bot responds informing the user that there are no remaining phases to advance.
5. **Given** test mode is active and a Mystery Round is the next pending round, **When** the user issues advance-phase, **Then** the bot skips that round's phases and advances to the next non-Mystery round's Phase 1.
6. **Given** test mode is not active, **When** a user issues the advance-phase command, **Then** the bot silently ignores it.

---

### User Story 3 - Review Season Configuration (Priority: P3)

A user with the configured interaction role, while test mode is active, issues a command to view a structured summary of the full season configuration. The summary shows all divisions, all rounds per division, the format and track for each round, the scheduled date, and which phases have been completed versus which remain pending.

**Why this priority**: This command supports verification of the season setup before or during phase advancement, helping users confirm that the bot has been configured correctly. It is supplementary to advancement but not required for testing phases.

**Independent Test**: Can be fully tested by configuring a season with at least two divisions and multiple rounds, then issuing the review command and verifying that all configured data appears correctly in the output.

**Acceptance Scenarios**:

1. **Given** test mode is active and a season has been configured and approved, **When** the user issues the season configuration review command, **Then** the bot responds with a structured summary listing each division, each round per division with its format, track name, scheduled date, and the completion status (done/pending) of each of the three phases.
2. **Given** some phases have already been advanced, **When** the user issues the review command, **Then** completed phases are clearly distinguished from pending ones in the summary.
3. **Given** a Mystery Round exists in the configured season, **When** the review command is issued, **Then** the Mystery Round appears in the summary with a clear indication that its phases are not applicable.
4. **Given** test mode is not active, **When** the review command is issued, **Then** the bot silently ignores it.

---

### Edge Cases

- What happens if test mode is enabled while the real scheduler has already triggered one or more phases for pending rounds?
- What happens if the advance-phase command is issued while a previous phase execution is still in progress?
- What if test mode is disabled before all phases are advanced — do already-executed test phases remain recorded, and do the remaining pending phases revert to waiting for their real scheduled times?
- What if no season has been configured and approved when the advance-phase or review command is issued in test mode?
- What if rounds across multiple divisions share the same scheduled date and time — in what order are their phases treated as "next"?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The bot MUST provide a command to toggle test mode on and off.
- **FR-002**: The test mode toggle command MUST be accessible only to users with the configured interaction role, issued in the configured interaction channel.
- **FR-003**: The bot MUST respond with a clear confirmation whenever test mode is enabled or disabled.
- **FR-004**: Test mode state MUST persist across bot restarts; the bot recovers into the same test mode state it was in before restart.
- **FR-005**: While test mode is active, the bot MUST provide an advance-phase command that triggers the next pending phase immediately.
- **FR-006**: The advance-phase command MUST determine the "next pending phase" as the chronologically earliest phase (by its normally-scheduled trigger time) that has not yet been executed across all divisions and all rounds.
- **FR-007**: When the advance-phase command is invoked, the bot MUST execute the target phase in full — performing all calculations and posting all outputs to the relevant forecast and log channels — identically to how it would behave during scheduled operation.
- **FR-008**: When no pending phases remain, the advance-phase command MUST respond to the user informing them that all phases for the season have been completed.
- **FR-009**: Mystery rounds MUST be skipped entirely by the advance-phase command; no phase is executed for them.
- **FR-010**: While test mode is active, the bot MUST provide a season configuration review command.
- **FR-011**: The season configuration review command MUST present a structured summary of all configured divisions and their rounds, including for each round: its format, track, scheduled date, and the completion status (executed/pending) of each phase.
- **FR-012**: Mystery rounds MUST be listed in the review summary with a clear indication that their phases do not apply.
- **FR-013**: Both the advance-phase and review commands MUST be silently ignored when test mode is not active.
- **FR-014**: The advance-phase and review commands MUST be subject to the same role and channel access restrictions as all other bot commands.

### Assumptions

- Test mode operates on the same season configuration used for normal production operation; it does not create or require a separate "test season."
- Disabling test mode while phases remain pending does not undo the phases already executed; those remain recorded. Pending phases revert to waiting for their real scheduled trigger times.
- When multiple divisions have a round scheduled at the exact same date and time, divisions are advanced in the order they were configured (first-configured first).
- Enabling test mode does not pause or cancel the real scheduler; if test mode is disabled, the scheduler continues normally and may still trigger real phases for rounds not yet executed.
- The advance-phase command triggers one phase per invocation.

### Key Entities

- **Test Mode State**: A server-scoped flag indicating whether test mode is currently active. Persists across restarts.
- **Phase Advancement Queue**: The ordered sequence of pending phases derived from the configured rounds and divisions, sorted by originally-scheduled trigger time. Each entry represents a specific phase (1, 2, or 3) for a specific round in a specific division.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can enable test mode, configure a season with multiple divisions and rounds, advance through every phase of every round of every division, and disable test mode — all without waiting for any scheduled times.
- **SC-002**: All phase outputs posted during test mode are indistinguishable in format and content from those produced during normal scheduled operation for equivalent inputs.
- **SC-003**: The season configuration review command presents a complete and accurate summary of all configured rounds and phases that allows a user to identify any missing or incorrect configuration values before advancing phases.
- **SC-004**: A full end-to-end test of a season with 2 divisions and 5 rounds each (30 total phases) can be completed within 10 minutes of user interaction time, excluding season configuration setup.
- **SC-005**: Test mode state and phase advancement progress survive a bot restart with zero data loss.
