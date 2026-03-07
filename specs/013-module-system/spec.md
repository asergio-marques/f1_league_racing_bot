# Feature Specification: Module System — Weather & Signup Modules

**Feature Branch**: `013-module-system`  
**Created**: 2026-03-07  
**Status**: Draft  
**Input**: User description provided via `/speckit.specify`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Weather Module Lifecycle (Priority: P1)

A server administrator can enable and disable the weather generation capability on demand
without losing historical phase outputs or disrupting other bot functions. When the module is
enabled while a season is active, all overdue weather phase thresholds are executed
sequentially and all remaining future events are scheduled. When disabled, every pending
weather event for the server is cancelled immediately.

**Why this priority**: The weather pipeline is the original core function of the bot. Moving
it to a default-off module changes its lifecycle contract; this story must be correct before
any other modular story can be validated. It also directly tests Principle X's
enable/disable atomicity requirement.

**Independent Test**: Enable the weather module with an active season containing upcoming
rounds and at least one round whose Phase 1 horizon has already passed; verify that the
overdue phase executes, that future rounds receive scheduled jobs, and that disabling
removes all pending jobs without altering historical outputs.

**Acceptance Scenarios**:

1. **Given** no active season exists and weather module is disabled, **When** an admin enables
   the weather module, **Then** the module state is saved as enabled, no jobs are created
   (no rounds to schedule), and a confirmation is posted to the log channel.
2. **Given** an active season with upcoming rounds and weather module disabled, **When** an
   admin enables the weather module, **Then** scheduler jobs are created for all rounds whose
   phase horizons have not yet passed.
3. **Given** an active season with a round whose Phase 1 horizon has already passed but
   Phase 1 has not been executed, **When** the weather module is enabled, **Then** Phase 1 is
   executed immediately as part of the enable operation before any other pending jobs are armed.
4. **Given** a round where Phase 1 has completed but Phase 2's horizon has also passed,
   **When** the weather module is enabled, **Then** Phase 2 is executed immediately (Phase 1 is
   not re-executed) and Phase 3 is scheduled normally if its horizon has not yet passed.
5. **Given** a round where all three phases have already completed, **When** the weather
   module is re-enabled, **Then** no redundant phase executions occur for that round.
6. **Given** a Mystery-format round exists in the active season, **When** the weather module
   is enabled, **Then** no weather jobs are created for that round (Principle IV — Mystery
   rounds are exempt from all weather phases).
7. **Given** the weather module is enabled, **When** an admin disables it, **Then** all
   pending weather scheduler jobs for this server are cancelled and a confirmation is posted
   to the log channel; historical phase outputs are unchanged.
8. **Given** the weather module is already enabled, **When** an admin attempts to enable it
   again, **Then** the bot rejects the command with a clear error.
9. **Given** the weather module is already disabled, **When** an admin attempts to disable it
   again, **Then** the bot rejects the command with a clear error.
10. **Given** a user who does not hold the server admin (trusted config) role issues the
    enable or disable command, **Then** the bot rejects it with a permission error.
11. **Given** the weather module is enabled and a new season is subsequently approved,
    **Then** weather scheduler jobs are armed for all rounds of the new season automatically
    as they would be under the pre-modular behaviour.

---

### User Story 2 - Signup Module Registration (Priority: P2)

A server administrator installs the signup module for the server by specifying the general
signup channel, the base role (drivers eligible to sign up), and the signed-up role (the role
awarded upon approval, stored for future wizard use). The bot automatically manages the
signup channel's visibility permissions. The admin can later uninstall the module, clearing
all configuration and restoring channel permission state.

**Why this priority**: All signup-related functionality — configuration, time slots, opening
signups — depends on the module being installed first. Without this story, all subsequent
signup stories are inoperable.

**Independent Test**: Enable the module with a real channel and two real roles; inspect the
channel's permission overrides and verify only server administrators, trusted role holders,
and base-role holders can see it, with base-role holders unable to send messages; then
disable the module and verify the bot's permission overrides are removed.

**Acceptance Scenarios**:

1. **Given** the signup module is not installed, **When** an admin runs the enable command
   with a valid channel, base role, and signed-up role, **Then** the module state is saved,
   channel permission overwrites are applied (base role: view-only; trusted role and server
   administrators: full access; all other users: no access), and a confirmation is posted to
   the log channel.
2. **Given** the signup module is installed, **When** an admin runs the disable command,
   **Then** the module's permission overwrites are removed from the signup channel, all
   module configuration is cleared, and a confirmation is posted to the log channel.
3. **Given** the signup module is installed and signups are currently open, **When** an admin
   runs the disable command, **Then** signups are closed first (as if `/signup disable` was
   run, including any graceful handling of in-progress drivers per US5), and then the module
   configuration is cleared.
4. **Given** the signup module is already installed, **When** an admin attempts to enable it
   again, **Then** the bot rejects the command with a clear error.
5. **Given** the signup module is not installed, **When** an admin attempts to disable it,
   **Then** the bot rejects the command with a clear error.
6. **Given** the bot lacks the permission to manage channel overwrites for the specified
   signup channel, **When** the enable command is run, **Then** the operation fails with a
   clear error identifying the missing permission; no partial configuration is saved.
7. **Given** a user who does not hold the server admin (trusted config) role issues the
   enable or disable command, **Then** the bot rejects it with a permission error.
8. **Given** the bot is reset (partial reset: season data cleared), **When** the module is
   checked, **Then** module state and configuration are preserved (tied to server config, not
   season data).
9. **Given** the bot is fully reset (full reset: config wiped), **When** the module state is
   checked, **Then** the signup module is in a disabled, unconfigured state.

---

### User Story 3 - Signup Module Global Settings (Priority: P3)

A server administrator can adjust three server-wide settings that control how the signup
wizard collects information: whether nationality is requested, whether lap times are labelled
as Time Trial or Short Qualification, and whether image proof is required alongside a lap
time submission. All three settings are configurable independently, and each has a
well-defined default.

**Why this priority**: These settings govern the wizard's data-collection behaviour.
They must be defined and persisted before signups can meaningfully be opened, and
their values are surfaced in the signup-open message for drivers to read.

**Independent Test**: Toggle each setting independently; verify the persisted value in the
signup module's settings output; verify the signup-open confirmation message reflects the
current state of each setting.

**Acceptance Scenarios**:

1. **Given** the signup module is installed, **When** an admin runs the nationality toggle,
   **Then** the nationality setting flips (ON → OFF or OFF → ON) and a confirmation is
   returned showing the new value. Default is ON.
2. **Given** the signup module is installed, **When** an admin runs the time-type toggle,
   **Then** the bot presents "Time Trial" and "Short Qualification" as options via buttons,
   and the option selected is persisted. Default is "Time Trial".
3. **Given** the signup module is installed, **When** an admin runs the time-image toggle,
   **Then** the time-image-required setting flips (ON → OFF or OFF → ON) and a confirmation
   is returned showing the new value. Default is ON (image required).
4. **Given** any of these settings is changed, **When** signups are subsequently opened
   (US5), **Then** the signup-open message correctly reflects all current setting values
   (time type label, image requirement).
5. **Given** the signup module is **not** installed, **When** any of these toggle commands is
   attempted, **Then** the bot rejects the command with a clear error.
6. **Given** a user who does not hold the server admin (trusted config) role issues any of
   these commands, **Then** the bot rejects it with a permission error.

---

### User Story 4 - Availability Time Slot Management (Priority: P4)

A trusted admin (tier 2) can add weekly availability time slots that drivers will later
select from during the signup wizard. Each slot is a day-of-week and time-of-day pair.
Slot IDs are automatically assigned in chronological order across the full week. Slots can
be removed by selecting from the numbered list. All slot mutations are blocked while signups
are open.

**Why this priority**: Time slots must be configured before signups can be opened (gate in
US5). The ID-based ordering and the mutation-lock-during-open-signup constraint are
correctness concerns that affect the wizard's data integrity.

**Independent Test**: Add three slots from different days in non-chronological input order;
verify IDs are assigned chronologically; remove the middle slot; verify remaining slots are
renumbered to maintain a gapless sequence. Attempt to mutate while signups are open and
confirm the bot blocks the operation.

**Acceptance Scenarios**:

1. **Given** no slots exist, **When** a trusted admin adds "Monday" at "19:00", **Then** one
   slot exists with ID 1.
2. **Given** one slot (Monday 19:00, ID 1) exists, **When** "Thursday 20:00" is added,
   **Then** Monday 19:00 = ID 1 and Thursday 20:00 = ID 2.
3. **Given** two slots (Monday 19:00 = ID 1, Thursday 20:00 = ID 2) exist, **When** "Wednesday
   18:00" is added, **Then** Monday 19:00 = ID 1, Wednesday 18:00 = ID 2, Thursday 20:00 = ID 3
   (IDs recomputed for all slots to maintain chronological gapless order).
4. **Given** multiple slots exist, **When** the remove command is run, **Then** the bot
   presents a numbered list of all slots (ID, day, time) for the admin to select from.
5. **Given** a slot with a low ID is removed, **When** the removal succeeds, **Then** all
   remaining slots are renumbered from 1 in chronological order (no gaps).
6. **Given** no time slots are configured, **When** the remove command is attempted, **Then**
   the bot blocks the command with a clear error.
7. **Given** signups are currently open, **When** any slot add or remove is attempted, **Then**
   the bot blocks the operation with a clear error (mutations locked during active signups).
8. **Given** a time input of "7:00 PM", **When** the slot is accepted, **Then** it is stored
   and displayed equivalently to "19:00" (both 24-hour and 12-hour AM/PM input formats are
   accepted).
9. **Given** the signup module is not installed, **When** slot commands are attempted, **Then**
   the bot rejects the command with a clear error.
10. **Given** a user who does not hold the trusted role (tier 2) issues slot commands, **Then**
    the bot rejects it with a permission error.

---

### User Story 5 - Open and Close Signups (Priority: P5)

A trusted admin (tier 2) can open the signup window by selecting which configured tracks
drivers must submit times for. The bot posts a signup initiation button and an informational
message to the signup channel. The admin can later close the window; if any drivers are
mid-signup, the admin is informed and must confirm before the window closes.

**Why this priority**: Opening signups is the gate through which all driver onboarding flows.
The graceful-closure path prevents drivers being silently abandoned mid-wizard when admins
close signups. This story ties together all prior signup module setup (US2–US4).

**Independent Test**: With the module installed and at least one time slot configured, open
signups with one track selected; verify the button and open-message appear in the signup
channel; close signups with no drivers in progress; verify the button is removed and the
closed-message is posted. Separately, simulate a driver in the Pending Signup Completion
state and verify the confirmation prompt appears before closure.

**Acceptance Scenarios**:

1. **Given** the module is installed, time slots are configured, and signups are closed,
   **When** a trusted admin runs signup enable selecting one track, **Then** the signup
   state is set to open, the selected track(s) are persisted, an initiation button and
   informational message are posted to the signup channel.
2. **Given** the signup open message is posted, **Then** it lists all selected tracks (or
   "no tracks" if zero were selected), states the time type (Time Trial / Short
   Qualification), and states whether image proof is required.
3. **Given** time slots are configured and the module is installed, **When** signup enable is
   run with zero tracks selected, **Then** the signup window opens successfully with no
   tracks listed.
4. **Given** no time slots are configured, **When** signup enable is attempted, **Then** the
   bot blocks the command with a clear error.
5. **Given** signups are already open, **When** signup enable is attempted, **Then** the bot
   rejects with a clear error.
6. **Given** signups are open and no drivers are in a mid-signup state (Pending Signup
   Completion, Pending Admin Approval, Pending Driver Correction), **When** a trusted admin
   runs signup disable, **Then** signups close immediately, the initiation button is deleted,
   and a "signups closed" message is posted to the signup channel.
7. **Given** signups are open and at least one driver is in a mid-signup state, **When** a
   trusted admin runs signup disable, **Then** the bot responds (ephemerally) listing all
   in-progress drivers and their signup channel references, with Confirm and Cancel buttons.
   - **If Confirm is pressed**: signups close, all in-progress drivers are transitioned to
     *Not Signed Up* (applying former-driver deletion rules per Principle VIII), the
     initiation button is deleted, and a "signups closed" message is posted.
   - **If Cancel is pressed**: no state change; signups remain open.
8. **Given** the signup disable confirmation prompt is shown, **When** neither button is
   pressed within 5 minutes, **Then** the prompt expires and no state change occurs.
9. **Given** signups are not currently open, **When** signup disable is attempted, **Then**
   the bot rejects with a clear error.
10. **Given** the signup module is not installed, **When** signup enable or disable is
    attempted, **Then** the bot rejects with a clear error.
11. **Given** a user who does not hold the trusted role (tier 2) issues these commands,
    **Then** the bot rejects with a permission error.

---

### Edge Cases

- What happens when the weather module is enabled while a round's Phase 1 horizon is
  between T−5 days and T−2 days (Phase 1 already due, Phase 2 not yet due)? Phase 1 must
  fire immediately; Phase 2 and Phase 3 should be scheduled at their normal horizons.
- What happens if the weather module is disabled mid-execution of a phase (e.g., Phase 1
  is in progress)? Since phase execution is synchronous within the scheduling system, the
  in-flight phase completes; only future scheduled jobs are cancelled.
- What happens if the signed-up role or base role configured in the signup module is deleted
  from the Discord server after the module was enabled? The bot should handle this gracefully
  — the signup channel button and open wizard still operate, but any role-grant operation
  attempted when the role no longer exists should fail with a clear error, not a crash.
- What happens if the signup channel is deleted from Discord after the module was enabled?
  The bot should detect the missing channel on next use (e.g., attempting to post) and return
  a clear error, not crash. The module remains in "enabled" state; the admin must disable and
  re-enable with a valid channel.
- Can the same channel be configured for both the signup channel and the interaction channel?
  This should be blocked with a clear error at module enable time, as the two channels have
  conflicting permission models.
- What is the maximum number of availability time slots? No hard cap is defined; the list is
  expected to be short (practical league usage: 3–15 slots). No special handling required
  for large inputs.
- What happens if overdue weather phases, when executed sequentially on module enable,
  produce errors (e.g., forecast channel no longer exists)? Each phase failure should be
  logged and reported clearly; remaining phases should NOT be attempted after a failure, to
  preserve phase-sequencing integrity (Phase 2 must not fire without successful Phase 1 output).

## Requirements *(mandatory)*

### Functional Requirements

**Module System (server-wide)**

- **FR-001**: The bot MUST maintain a per-server record of which optional modules are
  enabled or disabled. This state MUST persist across bot restarts.
- **FR-002**: All optional module enable and disable commands MUST be accessible only to
  server administrators (trusted config tier). Any other user MUST receive a permission error.
- **FR-003**: A module enable operation MUST succeed atomically: all configuration is saved
  and all side effects (job scheduling, channel permission changes) completed before
  confirmation is returned. On failure, all partial state MUST be rolled back.
- **FR-004**: A module disable operation MUST succeed atomically: all scheduled jobs for
  the module are cancelled and all live configuration is cleared before confirmation is
  returned. Historical data (phase results, audit entries, signup records) MUST be retained.
- **FR-005**: Every module enable/disable operation MUST produce an audit log entry (Principle V)
  and post a confirmation to the server's log channel.

**Weather Module**

- **FR-006**: The `/module enable weather` command MUST enable the weather module for the
  server. It takes no configuration parameters; per-division forecast channels remain
  configured at the `division add` step.
- **FR-007**: When the weather module is enabled and an active season exists, the bot MUST
  immediately execute, in Phase 1 → Phase 2 → Phase 3 order, all weather phases whose time
  horizons have already passed and have not yet been executed. Phases that have already
  completed MUST NOT be re-executed.
- **FR-008**: After executing any overdue phases, the weather module enable MUST schedule
  future-horizon jobs for all remaining non-Mystery rounds in the active season.
- **FR-009**: The `/module disable weather` command MUST cancel all pending weather scheduler
  jobs for the server. It MUST NOT alter any historical phase outputs or audit entries.
- **FR-010**: When a new season is approved on a server with the weather module enabled, the
  bot MUST arm weather scheduler jobs for all rounds of the new season, as it would have
  under the pre-modular behaviour.
- **FR-011**: Weather scheduler jobs MUST NOT be created for Mystery-format rounds under any
  circumstance (Principle IV).
- **FR-012**: The `forecast_channel` parameter in `division add` and `division duplicate` is
  **optional**. The bot MUST enforce the following mutual-exclusivity rules at the point of
  each command:
  - If the weather module is **enabled** and no `forecast_channel` is supplied, the command
    MUST fail with a clear error stating that a forecast channel is required while the
    weather module is active.
  - If the weather module is **disabled** and a `forecast_channel` is supplied, the command
    MUST fail with a clear error stating that a forecast channel cannot be set while the
    weather module is inactive.
  - Consequently, enabling the weather module when one or more existing divisions have no
    forecast channel configured MUST fail, listing the affected divisions so the admin can
    amend them first.

**Signup Module — Registration**

- **FR-013**: The `/module enable signup` command MUST accept three parameters: a signup
  channel, a base role, and a signed-up role. All three MUST be valid, existing Discord
  entities at the time of the command.
- **FR-014**: On enable, the bot MUST modify the signup channel's permission overwrites so
  that: (a) the server and bot retain full access; (b) trusted role (tier 2) holders have
  full read/write access; (c) base role holders can view the channel but MUST NOT be able
  to send messages; (d) all other roles are denied access.
- **FR-015**: The `/module disable signup` command MUST remove the bot's applied permission
  overwrites from the signup channel and clear all stored signup module configuration
  (channel, roles, settings, time slots, and open/closed state — but not driver profile
  records or historical signup records).
- **FR-016**: If the signup module is disabled while signups are open, the disable operation
  MUST first close signups (following the same flow as `/signup disable`, including
  in-progress-driver handling), then clear the module configuration.
- **FR-017**: The signup channel MUST NOT be the same channel as the server's interaction
  channel. The bot MUST block the enable command with a clear error if the same channel
  is supplied for both.

**Signup Module — Configuration**

- **FR-018**: The `/signup nationality toggle` command MUST flip the nationality-required
  flag (default: true). The command MUST be blocked if the signup module is not installed.
- **FR-019**: The `/signup time-type toggle` command MUST present "Time Trial" and "Short
  Qualification" as selectable options and persist the selection (default: Time Trial). The
  command MUST be blocked if the signup module is not installed.
- **FR-020**: The `/signup time-image toggle` command MUST flip the time-image-required flag
  (default: true). The command MUST be blocked if the signup module is not installed.
- **FR-021**: All three toggle commands MUST be available only to server administrators
  (trusted config tier).

**Availability Time Slots**

- **FR-022**: The `/signup time-slot add` command MUST accept a day of the week and a time
  of day. Time input MUST be accepted in both 24-hour format (`HH:MM`, e.g., `19:00`) and
  12-hour format with AM/PM designation (e.g., `7:00 PM`). Both formats MUST be stored as
  a canonical 24-hour representation internally.
- **FR-023**: Availability time slots MUST be stored and displayed in UTC, consistent with
  all other time values managed by the bot. Admins and drivers are expected to convert to
  their local timezone independently. No server-level timezone configuration is introduced.
- **FR-024**: Slot IDs MUST be auto-assigned as the 1-based position of the slot in the
  chronologically sorted list of all configured slots, ordered by (day_of_week ascending,
  time_of_day ascending) where Monday = 1 and Sunday = 7. IDs MUST be recomputed for all
  slots whenever a slot is added or removed, ensuring the set is always gapless and
  sequential from 1.
- **FR-025**: The `/signup time-slot remove` command MUST display all configured slots with
  their IDs, days, and times for the admin to select from, and MUST be blocked with a clear
  error if no slots exist.
- **FR-026**: Both slot mutation commands MUST be blocked with a clear error if signups are
  currently open.
- **FR-027**: Slot commands MUST be blocked if the signup module is not installed.
- **FR-028**: Slot commands MUST be available only to trusted role (tier 2) holders or server
  administrators.

**Signup Open / Close**

- **FR-029**: The `/signup enable` command MUST accept zero or more tracks from the server's
  configured track list and persist the selection. It MUST be blocked if no time slots are
  configured.
- **FR-030**: On signup open, the bot MUST post an initiation button and an informational
  message to the signup channel listing: selected tracks (or a "no tracks" notice if zero
  were selected), the current time-type label ("Time Trial" or "Short Qualification"), and
  whether image proof is required.
- **FR-031**: The `/signup disable` command, when no drivers have an in-progress state
  (Pending Signup Completion, Pending Admin Approval, Pending Driver Correction), MUST
  immediately close the signup window, delete the initiation button message, and post a
  "signups closed" message to the signup channel.
- **FR-032**: The `/signup disable` command, when one or more drivers are in an in-progress
  state, MUST present the admin with a list of affected drivers (username and signup channel
  reference) and Confirm / Cancel buttons before taking any action.
- **FR-033**: If the admin confirms the forced close, the bot MUST transition all in-progress
  drivers to *Not Signed Up* in a single operation (applying the deletion rules of Principle
  VIII), close the signup window, remove the initiation button, and post "signups closed" to
  the signup channel.
- **FR-034**: The confirmation prompt from FR-032 MUST expire after 5 minutes with no state
  change if neither button is pressed.
- **FR-035**: Signup open/close commands MUST be available only to trusted role (tier 2)
  holders or server administrators.
- **FR-036**: Signup open/close commands MUST be blocked if the signup module is not
  installed.

**Driver State Machine Extension**

- **FR-037**: The driver state machine MUST be extended with the following new transitions,
  required by the signup module's close-with-in-progress flow (FR-033):
  - *Pending Signup Completion* → *Not Signed Up*
  - *Pending Driver Correction* → *Not Signed Up*
  (The transition *Pending Admin Approval* → *Not Signed Up* was already defined in
  feature 012-driver-profiles-teams.)

### Key Entities *(include if feature involves data)*

- **ServerModuleConfig** (extends per-server configuration): A per-server record tracking
  which optional modules are currently enabled. Each entry is a (server_id, module_name,
  enabled) triple. Persisted across restarts.
- **SignupModuleConfig** (per server, exists while signup module is enabled): Stores the
  general signup channel reference, base role reference, and signed-up role reference
  configured at module enable time.
- **SignupModuleSettings** (per server): Stores the three configurable flags —
  `nationality_required` (Boolean, default true), `time_type` (Enum: TIME_TRIAL |
  SHORT_QUALIFICATION, default TIME_TRIAL), `time_image_required` (Boolean, default true).
  These persist independently of module enable/disable cycles.
- **AvailabilityTimeSlot** (per server): Represents a weekly recurring availability window.
  Fields: auto-assigned `id` (integer, 1-based), `day_of_week` (Monday through Sunday),
  `time_of_day` (stored in 24-hour canonical form). IDs are recomputed on every mutation.
- **SignupWindowState** (per server): Tracks whether the signup window is currently open,
  and which tracks were selected at the last `/signup enable` invocation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A server administrator can complete weather module enable or disable in a
  single command interaction, with confirmation visible in the log channel within the bot's
  standard 3-second acknowledgement window (Principle — Bot Behavior Standards).
- **SC-002**: When the weather module is enabled mid-season with overdue phase horizons,
  all overdue phases are executed and all future jobs are armed without manual intervention,
  leaving no rounds in an un-phased gap state.
- **SC-003**: A server administrator can complete signup module installation (channel, roles,
  and permission application) in a single command interaction with no manual Discord
  permission changes required.
- **SC-004**: All signup module configuration changes (settings toggles, time slot mutations,
  signup open/close) are reversible in a single subsequent command interaction, with no
  residual state left behind after a disable operation.
- **SC-005**: A server running only foundational capabilities (no modules enabled) has no
  scheduled weather jobs and no signup-channel presence; re-enabling a module fully restores
  the expected behaviour without requiring re-initialisation of core server configuration.
- **SC-006**: All module enable, disable, and configuration change events appear as entries
  in the audit trail, enabling full reconstruction of the server's module history.

## Assumptions

- The bot already holds the necessary Discord permission scopes (`Manage Channels` or
  `Manage Permissions`) to apply channel overwrites. If not, this can be resolved by
  updating the bot's invite link with the required permissions; it is not a scope concern
  for this feature.
- The signed-up role stored by the signup module at enable time is for future use by the
  signup wizard (a subsequent feature); this feature only stores and validates its existence.
- Time slot day-of-week ordering follows ISO week convention: Monday = 1 through Sunday = 7.
- Slot ID recomputation on mutation does not affect previously persisted driver signup wizard
  state during an open session, because slot mutations are gated behind a "signups must be
  closed" guard (FR-026). There is therefore no in-flight wizard data to corrupt when IDs
  change.
- The `forecast_channel` parameter in `division add` / `division duplicate` is optional and
  mutually gated by the weather module state per FR-012: required when weather is enabled,
  forbidden when it is disabled. This means existing divisions created while weather was
  disabled will need to be amended with a channel before the weather module can be enabled.
- Time slot day/times are stored and displayed in UTC (FR-023), matching the bot's existing
  convention for all datetime values.
