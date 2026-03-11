# Feature Specification: Signup Wizard and Flow

**Feature Branch**: `014-signup-wizard-flow`  
**Created**: 2026-03-10  
**Status**: Draft  
**Input**: User description: "Signup wizard and flow — module enable/disable, configuration, channel creation, sequential parameter collection, state machine transitions, inactivity timeouts, withdrawal, admin approval pipeline, and correction request cycle. Driver placement (assign/unassign/sack) is deferred to a subsequent increment."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Signup Module Activation (Priority: P1)

A server administrator enables the signup module by designating a general signup channel, a base role, and a signed-up role. The bot immediately restricts the general signup channel so that only server admins, tier-2 users, and base-role holders can see it, and base-role holders can only press buttons (no free typing). Disabling the module reverses all configuration and clears stored signup settings.

**Why this priority**: Every subsequent signup command, wizard, and channel is gated behind module activation. Nothing else in this feature functions without it.

**Independent Test**: Enable the module; verify channel permissions are set correctly. Disable the module; verify configuration is cleared and channel permissions reverted.

**Acceptance Scenarios**:

1. **Given** the signup module is disabled, **When** a server admin runs `/module enable signup` with a valid channel, base role, and signed-up role, **Then** the module is marked active, channel permissions are updated, and a confirmation is shown ephemerally.
2. **Given** the signup module is active, **When** a server admin runs `/module disable signup`, **Then** all signup configuration is cleared, and channel permission overrides applied by the bot are reverted.
3. **Given** the signup module is disabled, **When** any signup sub-command is executed, **Then** the bot returns a clear error indicating the module is not active.

---

### User Story 2 - Signup Configuration (Priority: P2)

A server administrator configures the signup module settings: whether nationality is collected, which lap time measurement label (Time Trial vs. Short Qualification) is used, and whether a proof image is required alongside each lap time. A tier-2 user manages the pool of time slots drivers may select for their availability.

**Why this priority**: Configuration must be established before signups can be opened; it governs how the wizard behaves for every driver.

**Independent Test**: Toggle each of the four configuration settings independently and verify the stored state changes correctly. Add and remove time slots and verify the ordered list updates correctly.

**Acceptance Scenarios**:

1. **Given** nationality collection is enabled, **When** a server admin toggles it off, **Then** the wizard will no longer ask for nationality in new sessions.
2. **Given** no time slots are configured, **When** a tier-2 user adds a slot (Monday, 20:00), **Then** a new slot with ID 1 is created and confirmed in the response.
3. **Given** one time slot exists (ID 1), **When** a tier-2 user removes it, **Then** the list is now empty.
4. **Given** no time slots exist, **When** a tier-2 user attempts to remove a slot, **Then** the command is blocked with a clear explanation.
5. **Given** time-image requirement is on, **When** a server admin toggles it off, **Then** subsequent wizard sessions will not require an image for lap time submission.

---

### User Story 3 - Opening and Closing Signups (Priority: P3)

A tier-2 user opens signups by selecting zero or more tracks, after which the bot posts a signup button in the general signup channel. A different tier-2 user can later close signups, with a safety confirmation if any drivers are mid-wizard.

**Why this priority**: The signup button is the entry point for the wizard; this story must be complete before any driver can begin.

**Independent Test**: Open signups and verify the button message appears in the general signup channel. Close signups with no in-progress drivers and verify the button is removed and a closed notice is posted.

**Acceptance Scenarios**:

1. **Given** no time slots are configured, **When** a tier-2 user runs `/signup open`, **Then** the command is blocked.
2. **Given** time slots exist and signups are closed, **When** a tier-2 user opens signups selecting 2 tracks, **Then** the bot posts a message in the general signup channel listing the tracks, whether image proof is required, and a signup button.
3. **Given** signups are open and no drivers are in progress, **When** a tier-2 user closes signups, **Then** the bot deletes the signup button message and posts "signups are closed" in the general signup channel.
4. **Given** signups are open and 2 drivers are in Pending Signup Completion, **When** a tier-2 user closes signups, **Then** the bot lists the in-progress drivers with their signup channel references and presents Confirm / Cancel buttons.
5. **Given** the tier-2 user confirms closure with in-progress drivers, **Then** all in-progress drivers are transitioned to Not Signed Up and the signup button is removed.

---

### User Story 4 - Driver Completes Signup Wizard (Priority: P4)

A driver with the base role presses the signup button in the general signup channel. The bot creates a private channel for them and walks them through collecting nationality, platform, platform ID, availability, driver type, preferred teams, preferred teammate, lap times per configured track, and extra notes. On completion, the data is committed and an admin review panel is posted.

**Why this priority**: This is the core value-delivery journey of the feature.

**Independent Test**: Complete the entire wizard flow as a driver and verify: channel created, all parameters collected in order, data committed at Pending Admin Approval, admin review panel visible to tier-2 users.

**Acceptance Scenarios**:

1. **Given** signups are open and a driver is Not Signed Up, **When** the driver presses the signup button, **Then** a private `<username>-signup` channel is created, the driver's state transitions to Pending Signup Completion, and the wizard begins.
2. **Given** a driver is in wizard collection, **When** they send nationality code `gb`, **Then** it is accepted and the wizard advances.
3. **Given** a driver submits an invalid nationality code, **Then** the bot rejects it with an explanation and re-prompts.
4. **Given** time-image is required and the driver sends a valid time string only (no image), **Then** the submission is rejected and the image requirement re-stated.
5. **Given** a driver submits lap time `1:23:567`, **Then** it is normalised and stored as `1:23.567`.
6. **Given** a driver submits lap time `1:23.5`, **Then** it is zero-padded and stored as `1:23.500`.
7. **Given** a driver completes the final wizard step, **Then** all data is atomically committed, the driver transitions to Pending Admin Approval, and the admin review panel is posted in the channel.
8. **Given** signups are open and a driver is already in Pending Admin Approval, **When** they press the signup button, **Then** they receive an ephemeral error indicating they are already in progress.

---

### User Story 5 - Admin Approves Signup (Priority: P5)

A tier-2 admin reviews the pending signup displayed in the driver's channel and clicks Approve. The driver transitions to Unassigned, the signed-up role is granted, and the channel enters a 24-hour read-only hold.

**Why this priority**: Approval is the primary successful outcome of the wizard; without it, drivers cannot reach Unassigned.

**Independent Test**: Approve a pending signup and verify: driver state is Unassigned, signed-up role is present on the Discord user, channel is read-only for the driver.

**Acceptance Scenarios**:

1. **Given** a driver is Pending Admin Approval, **When** a tier-2 admin presses Approve, **Then** the driver transitions to Unassigned, the signed-up role is granted, the wizard transitions to Unengaged, and the channel is made read-only with a 24-hour deletion timer.
2. **Given** a base-role user attempts to press Approve, **Then** the action is rejected with an ephemeral error.

---

### User Story 6 - Admin Requests Correction (Priority: P6)

A tier-2 admin flags an issue with a pending signup by pressing "Request Changes." The bot enters Awaiting Correction Parameter state and presents per-parameter selection buttons. The admin selects the parameter and the driver is re-prompted for that parameter only. On valid resubmission, the driver returns to Pending Admin Approval.

**Why this priority**: Admin oversight of signup data accuracy requires a structured correction cycle.

**Independent Test**: Request a correction for "Platform ID," verify wizard jumps to that step, driver resubmits valid text, driver returns to Pending Admin Approval with a refreshed review panel.

**Acceptance Scenarios**:

1. **Given** a driver is Pending Admin Approval, **When** a tier-2 admin presses "Request Changes," **Then** the driver transitions to Awaiting Correction Parameter and parameter selection buttons appear.
2. **Given** driver is Awaiting Correction Parameter and admin selects "Platform ID," **Then** the driver transitions to Pending Driver Correction and the wizard jumps to the Platform ID collection step.
3. **Given** driver is Pending Driver Correction for Platform ID and submits a non-empty string, **Then** the value is updated and the driver transitions to Pending Admin Approval; a refreshed review panel is posted.
4. **Given** driver is Awaiting Correction Parameter and the 5-minute timeout elapses, **Then** the driver transitions back to Pending Admin Approval and the original review panel is restored.
5. **Given** driver is Pending Driver Correction and presses the withdrawal button, **Then** the driver transitions to Not Signed Up.

---

### User Story 7 - Driver Withdrawal and System Cancellation (Priority: P7)

A driver may voluntarily withdraw during any in-wizard state. The system automatically cancels signups after 24 h of inactivity in Pending Signup Completion or Pending Driver Correction. An admin may also reject a signup outright. In every case the driver transitions to Not Signed Up and the channel enters a 24-hour read-only hold before deletion.

**Why this priority**: Every cancellation path is a required safety valve for correct state machine operation.

**Independent Test**: Press the withdrawal button during wizard collection; verify driver is Not Signed Up, channel is frozen, a cancellation notice is posted, and the channel is removed after 24 hours.

**Acceptance Scenarios**:

1. **Given** a driver is Pending Signup Completion and 24 hours pass without wizard progress, **Then** the driver transitions to Not Signed Up, the channel is frozen, a cancellation notice is posted, and the channel is deleted 24 hours later.
2. **Given** a driver is Pending Driver Correction and 24 hours pass without input, **Then** the same cancellation flow applies.
3. **Given** a driver is Pending Admin Approval and a tier-2 admin presses Reject, **Then** a rejection notice is posted, the driver transitions to Not Signed Up, and the channel enters the 24-hour hold.
4. **Given** a driver presses the withdrawal button during any in-wizard state, **Then** the driver transitions to Not Signed Up and the channel enters the 24-hour hold.

---

### User Story 8 - Server Leave and Re-engagement (Priority: P8)

If a driver leaves the server while in any in-wizard state, the wizard is cancelled and the channel is deleted immediately. If a driver re-presses the signup button while an existing channel is in any state, the existing channel is deleted immediately and a new one is created.

**Why this priority**: Prevents orphaned channels from accumulating in the server.

**Independent Test**: Simulate a server leave during Pending Signup Completion; verify driver is Not Signed Up and channel is immediately deleted without a hold period.

**Acceptance Scenarios**:

1. **Given** a driver is Pending Signup Completion and leaves the Discord server, **Then** the driver transitions to Not Signed Up and the channel is deleted immediately (no 24-hour hold).
2. **Given** a driver has any existing signup channel (including held/read-only) and presses the signup button again, **Then** the existing channel is deleted immediately and a new `<username>-signup` channel is created.

---

### Edge Cases

- What happens when a driver's Discord User ID is changed mid-wizard? The wizard state is transferred to the new User ID; the stored Discord username and server display name are overwritten by those of the new account.
- What happens when the bot restarts while a driver is mid-wizard? The wizard state and channel ID are read from the database on restart; the bot re-establishes message monitoring for each active wizard channel.
- What if a time slot referenced in an existing availability record is later removed by an admin? The historical record retains the deleted ID; it simply no longer appears in new wizard sessions.
- What if two admins simultaneously click Approve and Reject on the same review panel? The first action wins; the second receives an ephemeral error that the signup is no longer pending.
- What if no signup tracks are configured when signups are opened? The lap time collection step is skipped entirely for all wizard sessions in that open period.

## Requirements *(mandatory)*

### Functional Requirements

#### Module Lifecycle

- **FR-001**: A server administrator MUST be able to enable the signup module (`/module enable signup`) by providing a general signup channel, a base role, and a signed-up role.
- **FR-002**: On enablement, the bot MUST atomically update the general signup channel's permissions: base-role holders can see the channel and press buttons only; tier-2 users and server administrators can see and write freely; all other roles cannot see the channel.
- **FR-003**: A server administrator MUST be able to disable the signup module (`/module disable signup`), atomically clearing all signup configuration, cancelling any pending scheduled jobs, and reverting all channel permission overrides applied by the module.
- **FR-004**: Every command in the signup domain MUST be blocked with a clear, actionable error if the signup module is not enabled (Principle X).
- **FR-005**: All module enable/disable events MUST produce an audit log entry (Principle V).

#### Signup Configuration

- **FR-006**: A server administrator MUST be able to toggle nationality collection on or off (`/signup config nationality`). Default: on.
- **FR-007**: A server administrator MUST be able to set the lap time measurement label to "Time Trial" or "Short Qualification" (`/signup config time-type`). Default: Time Trial.
- **FR-008**: A server administrator MUST be able to toggle whether a proof image is required alongside each lap time submission (`/signup config time-image`). Default: required.
- **FR-009**: A tier-2 user MUST be able to add a time slot by specifying a day of the week and a time of day in HH:mm (24-hour) or h:mm AM/PM format (`/signup config time-slot add`).
- **FR-010**: Each time slot MUST receive a stable integer ID assigned in chronological order (day-of-week primary, time-of-day secondary). Removing a slot MUST NOT renumber remaining slots.
- **FR-011**: Duplicate day+time combinations MUST be rejected.
- **FR-012**: A tier-2 user MUST be able to remove a time slot by selecting from the current list (`/signup config time-slot remove`). If no slots exist, the command MUST be blocked with a clear message.
- **FR-013**: All configuration changes MUST produce an audit log entry (Principle V).

#### Opening and Closing Signups

- **FR-014**: Opening signups MUST be blocked if no time slots are configured.
- **FR-015**: A tier-2 user MUST be able to open signups (`/signup open`) by selecting zero or more tracks from those configured.
- **FR-016**: On opening, the bot MUST post a public message in the general signup channel listing: the selected signup tracks (or a note that none are required), whether image proof is required, and a button to initiate the signup wizard.
- **FR-017**: The selected track list and open/closed state MUST be persisted.
- **FR-018**: A tier-2 user MUST be able to close signups (`/signup close`). If no drivers are in Pending Signup Completion, Pending Admin Approval, or Pending Driver Correction, signups MUST close immediately.
- **FR-019**: If in-progress signups exist, the bot MUST list the in-progress drivers with their signup channel references and present Confirm / Cancel buttons to the invoking tier-2 user. On confirmation, all in-progress drivers MUST be transitioned to Not Signed Up following the standard cancellation flow.
- **FR-020**: On closing, the bot MUST delete the signup button message and post a "signups are closed" notice in the general signup channel.

#### Wizard Channel Management

- **FR-021**: When a Not Signed Up driver presses the signup button, the bot MUST create a private channel named `<discord_username>-signup`, accessible only to that driver, all tier-2 users, and server administrators.
- **FR-022**: Pressing the signup button while in any state other than Not Signed Up MUST return an ephemeral error to the driver (e.g., "already in progress" or "banned from signing up"). No channel is created.
- **FR-023**: Tier-2 users and server administrators MUST be able to post freely in any signup channel at all times.
- **FR-024**: While the driver's wizard is in Unengaged state, the driver MUST lose write permission in their signup channel. In any other wizard state, write permission MUST be restored.
- **FR-025**: If a driver with any existing signup channel (active or in hold) presses the signup button again, the existing channel MUST be deleted immediately and a new one created.
- **FR-026**: On any terminal event (approval, rejection, withdrawal, or timeout), the channel MUST be made read-only for the driver and scheduled for deletion 24 hours later. A bot message MUST be posted in the channel confirming the terminal event.
- **FR-027**: If a driver in any in-wizard state leaves the Discord server, the channel MUST be deleted immediately (no hold), and the driver transitioned to Not Signed Up.
- **FR-028**: The signup channel ID MUST be persisted in the driver's wizard record and retained until after the scheduled deletion executes (Principle XI).

#### Wizard Parameter Collection

- **FR-029**: At wizard start, the bot MUST record the driver's current Discord username and server display name.
- **FR-030**: A configuration snapshot (nationality flag, time type, image requirement, selected signup tracks, active time slots) MUST be captured at wizard start. Subsequent configuration changes MUST NOT alter an in-progress wizard.
- **FR-031**: The wizard MUST collect the following parameters sequentially, skipping steps disabled by configuration:
  1. **Nationality** *(skipped if nationality collection is off)*: accepts Discord regional indicator codes case-insensitively (e.g., `gb`, `US`) or the literal string `other`. Invalid inputs are rejected with a re-prompt.
  2. **Platform**: single choice — Steam, EA, Xbox, PlayStation.
  3. **Platform ID**: free-text string; no format validation.
  4. **Availability**: the bot displays all active time slots with their IDs; the driver types one or more slot IDs separated by spaces, commas, or comma+space. At least one valid slot ID is required.
  5. **Driver Type**: single choice — Full-Time Driver or Reserve Driver.
  6. **Preferred Teams**: ranked selection of up to 3 non-Reserve teams from those currently configured in the active season, or "No Preference." Choices are stored in selection order.
  7. **Preferred Teammate**: free-text string or "No Preference."
  8. **Lap Time per signup track** *(one step per track in configuration order; skipped entirely if no tracks are configured)*: accepted in `M:ss.mss` or `M:ss:mss` format; colon-separated milliseconds normalised to dot-separated; millisecond portion zero-padded to 3 digits if shorter, rounded to 3 digits if longer; leading/trailing whitespace stripped. If `time_image_required` is on, the message MUST include an image attachment. The prompt label MUST reflect the configured time type (Time Trial / Short Qualification).
  9. **Notes**: free-text up to 50 characters, or "No Notes."
- **FR-032**: The wizard MUST ignore all messages in the signup channel from any user other than the driver performing the signup.
- **FR-033**: A withdrawal button MUST remain visible in the driver's signup channel throughout their entire time in Pending Signup Completion, Pending Admin Approval, and Pending Driver Correction.

#### Driver and Wizard State Transitions

- **FR-034**: Pressing the signup button MUST transition the driver from Not Signed Up to Pending Signup Completion and advance the wizard from Unengaged to the first collection step.
- **FR-035**: Completing all collection steps MUST atomically commit all gathered parameters to the signup record and transition the driver to Pending Admin Approval. Lap time images MUST NOT be persisted; only normalised time strings are stored.
- **FR-036**: Any transition to Not Signed Up MUST set the wizard to Unengaged and apply the immutability rule: if `former_driver = true`, signup record fields are nulled (signup channel reference retained until pruned); if `former_driver = false`, the driver record is deleted in the same transaction.
- **FR-037**: Any transition to Unassigned MUST also set the wizard to Unengaged.
- **FR-038**: Every driver state change MUST be persisted and produce an audit log entry with actor, previous state, and new state (Principle V).

#### Admin Review Panel

- **FR-039**: On transition to Pending Admin Approval, the bot MUST post an admin review panel in the driver's signup channel displaying all committed signup fields and three action buttons: Approve, Request Changes, and Reject. These buttons MUST be restricted to tier-2 users and server administrators.
- **FR-040**: Pressing Approve MUST atomically: grant the signed-up role to the driver, transition the driver to Unassigned, set the wizard to Unengaged, and trigger the 24-hour channel hold.
- **FR-041**: Pressing Reject MUST: post a rejection notice in the channel, transition the driver to Not Signed Up, and trigger the 24-hour channel hold.

#### Correction Request Cycle

- **FR-042**: Pressing Request Changes MUST transition the driver to Awaiting Correction Parameter and display a set of parameter selection buttons — one per collectable wizard parameter. Only tier-2 users and server administrators may press these buttons.
- **FR-043**: The Awaiting Correction Parameter state has a 5-minute timeout. If no parameter is selected within 5 minutes, the driver MUST automatically transition back to Pending Admin Approval and the review panel MUST be restored.
- **FR-044**: When a parameter is selected, the driver MUST transition to Pending Driver Correction and the wizard MUST jump directly to that parameter's collection step, bypassing all others.
- **FR-045**: On valid input for the corrected parameter, the driver MUST transition to Pending Admin Approval and the wizard to Unengaged. A fresh admin review panel MUST be posted.
- **FR-046**: Acceptance and normalisation criteria for each parameter in the correction flow MUST be identical to those in the sequential collection flow.

#### Inactivity Timeouts

- **FR-047**: If a driver remains in Pending Signup Completion for 24 consecutive hours without wizard progress, the signup MUST be automatically cancelled: the driver transitions to Not Signed Up, the channel is frozen, a cancellation notice is posted, and the channel is deleted 24 hours later.
- **FR-048**: The same 24-hour inactivity timeout and cancellation sequence MUST apply to Pending Driver Correction.
- **FR-049**: There is no inactivity timeout for Pending Admin Approval; admin review may take as long as needed.

### Key Entities

- **SignupConfiguration** (per server): stores general signup channel ID, base role ID, signed-up role ID, nationality collection flag, time type (Time Trial / Short Qualification), image proof required flag, signups-open flag, and the ordered list of track IDs selected for the current open session.
- **TimeSlot** (per server): a day-of-week and time-of-day entry with a stable, non-reused sequential ID.
- **SignupRecord** (per driver per server): the committed submission — Discord username, server display name, nationality (nullable), platform, platform ID, availability slot IDs, driver type, ranked preferred teams list, preferred teammate, lap times mapped by track ID (normalised time strings), and notes. Nulled on re-entry to Not Signed Up when `former_driver = true`.
- **SignupWizardRecord** (per driver per server): current wizard step (Unengaged or a specific collection state), signup channel ID (retained until channel deletion), and in-progress draft answers not yet committed.
- **DriverProfile** (existing): driver lifecycle state and `former_driver` flag. The signed-up role is granted when the profile transitions to Unassigned.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A driver can progress through the complete signup wizard — from pressing the signup button to reaching Pending Admin Approval — in a single uninterrupted session, with no out-of-band communication required.
- **SC-002**: An admin can action any pending signup (approve, reject, or request correction) entirely within the driver's signup channel, without visiting any other interface.
- **SC-003**: No orphaned signup channels remain in the server more than 25 hours after any terminal event (approval, rejection, withdrawal, or timeout).
- **SC-004**: Concurrent wizards for different drivers operate in complete isolation — one driver's progress, state, or errors do not affect any other driver's wizard.
- **SC-005**: Every driver state transition produced by this feature is recoverable after a bot restart with zero data loss.
- **SC-006**: The inactivity cancellation timer fires within 5 minutes of the 24-hour deadline for both monitored states.

## Scope

### In Scope

- Signup module enable and disable (`/module enable signup`, `/module disable signup`).
- Signup configuration: nationality toggle, time-type toggle, time-image toggle, time-slot add/remove.
- Opening and closing signups, including the safety confirmation for in-progress drivers.
- Signup wizard: channel lifecycle, sequential parameter collection, driver state machine transitions (Not Signed Up → Pending Signup Completion → Pending Admin Approval → Unassigned or Not Signed Up), wizard state machine transitions (Unengaged ↔ collection states), correction cycle (Awaiting Correction Parameter, Pending Driver Correction), inactivity timeouts, withdrawal, server-leave handling, and re-engagement with existing channel.
- Admin review panel: Approve, Request Changes (correction cycle), and Reject actions.
- Signed-up role grant on approval.

### Out of Scope (Deferred to Next Increment)

- Driver assignment, unassignment, and sacking (`/driver assign`, `/driver unassign`, `/driver sack`).
- Seeded unassigned driver listing (`/signup list-unassigned`).
- Signed-up role removal on sacking or ban.
- Division role grant and revocation.
- Season ban and league ban issuance commands.
- Image persistence or archiving of lap time proof images (images are validated but not stored).

## Assumptions

- **A-001**: The signed-up role is granted when a driver's signup is approved (transition to Unassigned). It represents confirmed signup eligibility within the server.
- **A-002**: Time slot chronological ordering uses day-of-week (Monday = 1 … Sunday = 7) as primary sort key and time-of-day as secondary. Duplicate day+time combinations are disallowed.
- **A-003**: Removed time slot IDs may still appear in historical signup availability records; they are treated as unknown/unreferenced in any future display context.
- **A-004**: If multiple admins simultaneously action the same review panel, the first action wins; subsequent attempts receive an ephemeral error.
- **A-005**: The `<username>-signup` channel name uses the driver's Discord username at channel creation time and is not retroactively updated if the username changes.
- **A-006**: Lap time millisecond rounding (when > 3 digits) uses standard half-up rounding.
