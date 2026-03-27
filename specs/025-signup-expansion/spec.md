# Feature Specification: Signup Module Expansion

**Feature Branch**: `025-signup-expansion`
**Created**: 2026-03-27
**Status**: Draft
**Input**: User description: "Module configuration improvements: remove optional parameters from module enable for the signup module; add dedicated signup channel, base-role, and complete-role commands. Season validation must fail if any of the three are unset while the signup module is enabled. Season review must display these configurations. Module flow improvements: signup open gains an optional close timer parameter (precludes manual close while active); unassigned drivers are not dropped on auto-close, only non-approved signup wizard drivers are cancelled; signup open mentions all base-role holders; division lineup-channel command posts team lineups per division once all drivers are placed; lineup channel is optional for season approval. All new commands and interactions must be logged."

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Dedicated Signup Module Configuration Commands (Priority: P1)

A server administrator configures the signup module's channel and role requirements through three separate dedicated commands, independently of enabling the module. The general signup channel, the base role (who can see and use the signup area), and the complete role (granted when a signup is approved) are each set via their own command. Enabling the signup module no longer accepts these as parameters.

**Why this priority**: The entire configuration lifecycle of the signup module changes here. Every other story in this feature depends on the module being enabled without inline parameters; this is the root change that must land first.

**Independent Test**: Enable the signup module with no parameters; verify it succeeds. Run each of the three dedicated configuration commands and verify each persists independently. Verify that running `/module enable signup` with the old channel/role parameters is no longer accepted.

**Acceptance Scenarios**:

1. **Given** the signup module is disabled, **When** a server admin runs `/module enable signup` (no parameters), **Then** the module is marked active, a confirmation is shown ephemerally, and no channel or role configuration is demanded at this stage.
2. **Given** the signup module is enabled, **When** a server admin runs `/signup channel #general-signups`, **Then** the general signup channel is recorded, channel permissions are applied, and a confirmation is shown ephemerally.
3. **Given** the signup module is enabled, **When** a server admin runs `/signup base-role @Drivers`, **Then** the base role is recorded and a confirmation is shown ephemerally.
4. **Given** the signup module is enabled, **When** a server admin runs `/signup complete-role @SignedUp`, **Then** the complete role is recorded and a confirmation is shown ephemerally.
5. **Given** the signup module is enabled with no channel set, **When** any signup wizard action is attempted, **Then** the bot returns a clear error identifying the missing configuration.
6. **Given** the signup module is disabled, **When** any of the three configuration commands is run, **Then** the bot returns a clear error indicating the module is not active.

---

### User Story 2 — Season Validation and Review of Signup Module Configuration (Priority: P2)

When the signup module is enabled, a season in SETUP cannot be approved until the general signup channel, base role, and complete role are all configured. The season review command displays the current state of these three configurations alongside the rest of the season data.

**Why this priority**: Season approval is a hard gate in the existing workflow. Completing this story makes the new configuration contract visible and enforceable before any scheduling proceeds.

**Independent Test**: Enable the signup module, leave all three configurations unset, and attempt season approval. Verify approval is blocked with a diagnostic citing each missing item. Then set all three, and verify approval is no longer blocked by these items.

**Acceptance Scenarios**:

1. **Given** the signup module is enabled and none of the three configs are set, **When** a tier-2 admin attempts to approve the season, **Then** approval is blocked and the error message identifies all three missing configurations.
2. **Given** the signup module is enabled and only the channel is set, **When** season approval is attempted, **Then** approval is blocked and the error identifies the two still-missing configurations.
3. **Given** the signup module is enabled and all three configs are set, **When** season approval is attempted, **Then** the signup module configuration gate no longer blocks approval (other prerequisites still apply independently).
4. **Given** the signup module is enabled, **When** a tier-2 admin runs the season review command, **Then** the signup module section displays the configured channel, base role, and complete role (or an "unset" indicator for each that is missing).
5. **Given** the signup module is disabled, **When** a tier-2 admin runs the season review command, **Then** no signup module configuration section is shown.

---

### User Story 3 — Signup Open with Optional Automatic Close Timer (Priority: P3)

A tier-2 user opens signups and optionally specifies a future date/time at which signups will automatically close. When a timer is active, the manual `/signup close` command is blocked. On timer expiry, only drivers who have not yet been approved (Pending Signup Completion, Pending Driver Correction, Pending Admin Approval) are cancelled and transitioned to Not Signed Up; drivers already in Unassigned or Assigned state are unaffected. On bot restart, any active timer is re-armed. Signup open pings all members holding the base role.

**Why this priority**: The close timer is the primary new driver-facing behaviour. It must be implemented and testable before the manual-close interaction change (which gates on whether a timer is active).

**Independent Test**: Open signups with a close timer set 2 minutes in the future. Verify: base role is mentioned in the signup-open message; a timer job is scheduled. Wait for expiry. Verify: drivers in non-approved states are cancelled; Unassigned drivers remain; the signup button is removed; a "signups closed" notice is posted.

**Acceptance Scenarios**:

1. **Given** time slots exist and signups are closed, **When** a tier-2 user opens signups with no close time, **Then** the bot posts in the general signup channel mentioning all base-role holders, listing the tracks and image-proof requirement, with a signup button; no close timer is set.
2. **Given** time slots exist and signups are closed, **When** a tier-2 user opens signups with a close time of `2026-04-01 20:00 UTC`, **Then** the signup-open post mentions all base-role holders and states the auto-close time; a close timer job is scheduled.
3. **Given** a close timer is active, **When** a tier-2 user attempts to run `/signup close`, **Then** the command is blocked with a message indicating an auto-close is already scheduled.
4. **Given** a close timer fires and 2 drivers are in Pending Signup Completion and 1 is Unassigned, **Then** the 2 pending drivers are transitioned to Not Signed Up (channels frozen and deleted per existing rules), the Unassigned driver is untouched, and a "signups closed" notice is posted.
5. **Given** a close timer fires and 1 driver is in Pending Admin Approval, **Then** that driver is transitioned to Not Signed Up.
6. **Given** a close timer fires and 1 driver is in Assigned state, **Then** that driver's state and assignments are completely unaffected.
7. **Given** the bot restarts while a close timer is active, **When** the bot comes back online, **Then** the close timer is re-armed for the original close timestamp; if the close time has already passed, the close is executed immediately.
8. **Given** a close timer is active and signups are closed before it fires, **Then** the timer is cancelled (no double-close).

---

### User Story 4 — Division Lineup Announcement Channel (Priority: P4)

A server admin or tier-2 user configures an optional lineup announcement channel per division. Once all drivers in a given division are placed (no unassigned drivers remain for that division), the bot posts a formatted team lineup to that division's configured announcement channel, listing every team with their assigned drivers.

**Why this priority**: This is additive and self-contained. It requires the earlier configuration stories to be in place but has no effect on any blocking workflow — no approval gate depends on it.

**Independent Test**: Configure a lineup announcement channel for Division 1. Assign the last unassigned division driver. Verify a formatted lineup notice is posted to the configured channel listing all teams and drivers. Verify no post occurs if no lineup channel is configured for a division.

**Acceptance Scenarios**:

1. **Given** the signup module is enabled, **When** a tier-2 user runs `/division lineup-channel Division1 #div1-lineup`, **Then** the lineup channel for Division 1 is persisted and confirmed ephemerally.
2. **Given** a lineup channel is configured for Division 1, **When** there are still unassigned drivers for that division, **Then** no lineup post is made.
3. **Given** a lineup channel is configured for Division 1 and the last unassigned driver is assigned to a team in Division 1, **Then** the bot posts a formatted lineup to#div1-lineup listing all teams with their assigned drivers.
4. **Given** no lineup channel is configured for Division 1, **When** the last unassigned driver for Division 1 is placed, **Then** no lineup post is made and no error is raised.
5. **Given** a lineup channel is configured, **When** a driver is later unassigned (moving a division back to having unassigned drivers), **Then** no second lineup post is triggered until all drivers are placed again.
6. **Given** a lineup channel is configured for Division 1, **When** the lineup channel is set again with a different channel, **Then** the new channel replaces the old one.
7. **Given** the signup module is disabled, **When** the lineup channel command is run, **Then** the command is blocked with a clear error.

---

### Edge Cases

- What happens if the base role or complete role is deleted from the Discord server while the signup module is enabled? The bot should handle missing role gracefully (log an error, do not crash) and inform tier-2 admins when the role is first needed.
- What happens if the configured signup channel is deleted from Discord while signups are open? The bot must handle the missing channel gracefully and log the error; it must not crash or silently succeed.
- What if the close timer timestamp is in the past when signups are opened (e.g., a very short duration that resolves to a past time)? The bot must validate the close time is in the future and reject the command with a clear error.
- What if the last unassigned driver in a division has multiple divisions assigned — does the lineup fire per division? The lineup post for a given division fires only when that division has no more unassigned drivers, independently of other divisions.
- What happens if `/module disable signup` is run while a close timer is active? The disable operation must cancel the timer as part of its atomicity (consistent with Principle X rule 3).
- What if no drivers have been assigned to a division at all and the divison still has no unassigned drivers — does the lineup fire? If there are no assigned drivers and the division has no unassigned drivers (i.e., never had any since module enable), no lineup post should be triggered.

---

## Requirements *(mandatory)*

### Functional Requirements

**Module Configuration**

- **FR-001**: The `/module enable signup` command MUST accept no signup-specific parameters (channel, base role, complete role). Any previously accepted inline parameters MUST be removed.
- **FR-002**: A `/signup channel <channel>` command MUST be available to server admins to set the general signup channel for the signup module. Running it again MUST overwrite the existing value.
- **FR-003**: A `/signup base-role <role>` command MUST be available to server admins to set the base role. Running it again MUST overwrite the existing value.
- **FR-004**: A `/signup complete-role <role>` command MUST be available to server admins to set the complete role. Running it again MUST overwrite the existing value.
- **FR-005**: Each of FR-002, FR-003, and FR-004 MUST be blocked (with a clear error) when the signup module is disabled.
- **FR-006**: Setting the signup channel (FR-002) MUST atomically apply the correct Discord channel permission overwrites — visible to server admins, tier-2 admins, and base-role holders; base-role holders may only press buttons (no free message posting).

**Season Validation & Review**

- **FR-007**: Season approval MUST be blocked if the signup module is enabled and any of the three configurations (channel, base role, complete role) is unset. The error message MUST name every missing configuration item.
- **FR-008**: The season review command MUST display a signup module section showing the current channel, base role, and complete role when the signup module is enabled. Each unset item MUST display an explicit "not configured" indicator.
- **FR-009**: When the signup module is disabled, the signup module section MUST NOT appear in the season review output.

**Signup Open with Close Timer**

- **FR-010**: The `/signup open` command MUST accept an optional `close_at` parameter specifying a future date-time at which signups will auto-close.
- **FR-011**: The `close_at` value MUST be validated to be in the future at the time the command is run. Values in the past MUST be rejected with a descriptive error.
- **FR-012**: When signups are opened (with or without a close timer), the bot MUST send a message in the general signup channel that mentions all members holding the base role.
- **FR-013**: When a close timer is active, `/signup close` MUST be blocked with a message stating the auto-close time.
- **FR-014**: When the close timer fires, the bot MUST transition all drivers in Pending Signup Completion, Pending Driver Correction, and Pending Admin Approval to Not Signed Up, applying the same cancellation and channel-freeze semantics as a manually confirmed forced close.
- **FR-015**: When the close timer fires, drivers in Unassigned or Assigned state MUST NOT be affected.
- **FR-016**: The `close_at` timestamp MUST be persisted to durable storage (on `SignupConfiguration`) and re-armed on bot restart if non-null and the signup module is enabled.
- **FR-017**: If the persisted `close_at` has already elapsed when the bot restarts, the close-timer action MUST be executed immediately (not silently skipped).
- **FR-018**: When signups are closed (manually or by timer), the `close_at` field MUST be cleared from `SignupConfiguration`.
- **FR-019**: When signups are closed via timer and a close timer is concurrently active, the timer job MUST be cancelled to prevent double-execution.

**Division Lineup Announcement Channel**

- **FR-020**: A `/division lineup-channel <division> <channel>` command MUST be available to tier-2 admins to assign an optional lineup announcement channel per division. Running it again MUST overwrite.
- **FR-021**: Configuring a lineup announcement channel is NOT required for season approval and MUST NOT be included in any approval-gate check.
- **FR-022**: After every driver assignment change (assign, unassign, or sack) in a division that has a configured lineup channel, the bot MUST evaluate whether all previously unassigned drivers for that division are now placed (i.e., no drivers in Unassigned state have been waiting for placement in that division).
- **FR-023**: If the condition in FR-022 is met, the bot MUST post a formatted lineup notice to the configured channel, listing every team in the division with their assigned drivers.
- **FR-024**: If no lineup channel is configured for a division, no lineup post MUST be made and no error MUST be raised.
- **FR-025**: The `/division lineup-channel` command MUST be blocked (with a clear error) when the signup module is disabled.

**Logging**

- **FR-026**: Every new command (FR-002 through FR-004, FR-010, FR-020) and every automated action (close timer fire, lineup post) MUST produce a timestamped audit log entry to the server's log channel, recording the actor (or "system" for automated events), the action, and the result.

### Key Entities

- **SignupConfiguration** (amended, v2.6.0): Existing entity gains a `close_at` (TEXT, nullable) field holding the ISO 8601 UTC timestamp for the active auto-close timer. Null when no timer is active or signups are closed.
- **SignupDivisionConfig** (new, v2.6.0): Per-division config record owned by the signup module. Holds `lineup_channel_id` (TEXT, nullable). Keyed on `(server_id, division_id)`. Created lazily; absence means no lineup posting for that division.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A server admin can fully configure the signup module channel and roles via dedicated commands, without re-enabling the module, in a single interaction each.
- **SC-002**: Season approval rejects with an actionable, item-specific error message whenever any signup module configuration prerequisite is unset, with zero false negatives (items are missing) or false positives (items are set).
- **SC-003**: Signup open always mentions base-role members in the general signup channel; no signup open event occurs silently.
- **SC-004**: The close timer fires within a reasonable margin of the configured time (consistent with existing scheduled-job precision for weather phases); no auto-close is silently skipped.
- **SC-005**: Drivers in Unassigned or Assigned state are never dropped by an auto-close event; 100% of Pending Signup Completion / Pending Driver Correction / Pending Admin Approval drivers are transitioned to Not Signed Up on any close event (manual or automatic).
- **SC-006**: A configured lineup announcement channel receives exactly one lineup post per "all placed" event per division; duplicate or spurious posts do not occur.
- **SC-007**: Every command and automated event in this feature produces a log entry in the designated log channel; no action is silent.

---

## Assumptions

- "All drivers placed" for the lineup announcement trigger means: the division has had at least one driver assigned since the module was enabled, and the count of drivers in Unassigned state who have been placed in or are awaiting placement in that specific division is zero.
- The close-timer parameter format will follow the same input convention used elsewhere in the bot (e.g., date-time string); exact format to be specified in the plan/implementation.
- The lineup post content (formatting, team ordering, driver listing) follows existing team and division display conventions already established in the bot.
- The `/signup close` block-when-timer-active applies only to the interactive manual close command; the internal auto-close execution path is not subject to this gate.
- Season validation gate for signup module config (FR-007) is additive — it operates independently and in parallel with existing season approval prerequisites (weather channel, R&S module, etc.).
