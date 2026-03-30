# Feature Specification: Signup Module Modifications and Enhancements

**Feature Branch**: `027-signup-modifications`
**Created**: 2026-03-30
**Status**: Draft
**Input**: User description: "Modify existing signup module functionality and append minor features: server-leave logging for all active driver states; signup close time included in open announcement; nationality/country validation against a named list; admin review waiting message; bulk points configuration editing; rename signup unassigned command; CSV export of unassigned drivers."

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Nationality and Country Name Validation (Priority: P1)

A driver signing up types their nationality or country name in full (e.g., "British" or "United Kingdom") rather than a two-letter abbreviation. The bot validates the input against a bundled reference list of accepted nationalities and country names (plus "other" as a case-insensitive bypass). Invalid inputs are rejected with a prompt to try again. The wizard advances only when a valid entry is accepted.

**Why this priority**: This changes the wizard's primary collection step used by every driver completing a signup. All other wizard behaviour depends on a valid entry being collected at this step; it is the highest-impact change in this feature.

**Independent Test**: Open signups, begin the wizard, and reach the nationality step. Submit a valid full nationality ("German"), verify it is accepted and stored. Submit a valid full country name ("Germany"), verify it is accepted and stored. Submit a two-letter code ("DE"), verify it is rejected with a helpful error. Submit "other", verify it is accepted and stored. Verify the accepted value is reflected in the admin review panel.

**Acceptance Scenarios**:

1. **Given** a driver reaches the nationality step in the wizard, **When** they type a recognised nationality (e.g., "French"), **Then** the bot accepts the input, stores the canonical representation, and advances the wizard.
2. **Given** a driver reaches the nationality step, **When** they type a recognised country name (e.g., "France"), **Then** the bot accepts the input, stores the canonical representation, and advances the wizard.
3. **Given** a driver reaches the nationality step, **When** they type "other" (case-insensitive), **Then** the bot accepts it, stores "Other", and advances the wizard.
4. **Given** a driver reaches the nationality step, **When** they type a two-letter code (e.g., "FR"), **Then** the bot rejects the input with a message explaining the accepted formats and re-prompts.
5. **Given** a driver reaches the nationality step, **When** they type a free-form unrecognised string (e.g., "Martian"), **Then** the bot rejects the input and re-prompts.
6. **Given** nationality collection is toggled off in the module config, **When** a driver progresses through the wizard, **Then** the nationality step is skipped entirely and no validation occurs.
7. **Given** a driver is in the correction flow and the nationality field is flagged for correction, **When** the driver re-submits their nationality, **Then** the same validation rules apply and the wizard advances only on a valid entry.

---

### User Story 2 — Server-Leave Notification for Active Drivers (Priority: P2)

When a driver with an active status (signing up, approved/unassigned, or currently assigned to a team) leaves the Discord server, the bot posts a notification to the server's calculation log channel identifying the driver, their Discord User ID, and their state at the time of leaving. Drivers already in signing-up states continue to have their wizard cleaned up as before; the new requirement is that this cleanup — and any leave event for Unassigned or Assigned drivers — is made visible to administrators by posting to the log channel.

**Why this priority**: Administrator awareness of driver departures is critical for roster management and audit compliance (Principle V). Unassigned and Assigned driver leave events are currently completely silent.

**Independent Test**: Configure the log channel. Set a driver to Unassigned state. Remove the driver from the server. Verify a notification is posted to the log channel identifying the driver (display name and Discord User ID) and state. Repeat with a driver in Pending Signup Completion and verify the notification is also posted alongside the existing wizard cleanup behaviour.

**Acceptance Scenarios**:

1. **Given** a driver in Pending Signup Completion leaves the server, **When** the leave event fires, **Then** the wizard is cleaned up as per existing behaviour AND a notification is posted to the log channel with the driver's display name, Discord User ID, and state "Pending Signup Completion".
2. **Given** a driver in Pending Admin Approval leaves the server, **When** the leave event fires, **Then** the wizard is cleaned up AND a notification is posted to the log channel with the driver's state "Pending Admin Approval".
3. **Given** a driver in Pending Driver Correction leaves the server, **When** the leave event fires, **Then** the wizard is cleaned up AND a notification is posted to the log channel with state "Pending Driver Correction".
4. **Given** a driver in Awaiting Correction Parameter leaves the server, **When** the leave event fires, **Then** the wizard is cleaned up AND a notification is posted to the log channel with state "Awaiting Correction Parameter".
5. **Given** a driver in Unassigned state leaves the server, **When** the leave event fires, **Then** a notification is posted to the log channel with the driver's display name, Discord User ID, and state "Unassigned". The driver's profile is retained in the database as per Principle VIII (server-leave rule).
6. **Given** a driver in Assigned state leaves the server, **When** the leave event fires, **Then** a notification is posted to the log channel with the driver's display name, Discord User ID, and state "Assigned". The driver's profile and all team assignments are retained unchanged.
7. **Given** a driver with no profile or in Not Signed Up state leaves the server, **When** the leave event fires, **Then** no notification is posted and no state change occurs.
8. **Given** no log channel has been configured for the server, **When** an active driver leaves, **Then** the leave event is handled silently (no crash), and any applicable wizard cleanup still proceeds.

---

### User Story 3 — Signup Open Announcement Includes Close Time (Priority: P3)

When a tier-2 user opens signups with an optional auto-close time, the public announcement posted in the general signup channel includes the scheduled close time, formatted in a human-readable UTC representation, so that drivers know in advance when the signup window ends.

**Why this priority**: This is a minor additive change to an existing message. It requires no new data — the close time is already persisted — and delivers direct communication value to drivers without any structural changes.

**Independent Test**: Open signups specifying a close time. Observe the posted signup announcement embed in the general signup channel. Verify the close time is displayed in the embed body. Open signups without specifying a close time and verify no close time is shown in the embed.

**Acceptance Scenarios**:

1. **Given** a tier-2 admin opens signups with a close time of `2026-04-10T20:00:00Z`, **When** the announcement is posted in the signup channel, **Then** the embed includes a clearly labelled line (e.g., "Auto-closes at: 2026-04-10 20:00 UTC") visible to all channel members.
2. **Given** a tier-2 admin opens signups without specifying a close time, **When** the announcement is posted, **Then** no close-time line appears in the embed.
3. **Given** a close time line is present in the embed, **When** the auto-close fires and signups are closed, **Then** the behaviour of the close flow is unchanged from the existing implementation.

---

### User Story 4 — Admin Review Panel Waiting Message (Priority: P4)

After a driver completes the signup wizard and their submission enters Pending Admin Approval, the admin review panel posted in the driver's private channel includes a "Please wait for an admin to validate your signup." notice placed after the signup information summary and before the action buttons, with a single blank-line buffer separating it from the signup info block.

**Why this priority**: This is a cosmetic UX change to a single message. It has no data or state implications, and is independent of all other stories.

**Independent Test**: Complete a full wizard run and transition to Pending Admin Approval. Observe the admin review panel message. Verify the text "Please wait for an admin to validate your signup." appears after the notes line and before the Approve / Request Changes / Reject buttons, with a blank line above it.

**Acceptance Scenarios**:

1. **Given** a driver completes the signup wizard, **When** the admin review panel is posted, **Then** the message body contains, in order: (a) the full signup summary ending with the notes field, (b) one blank line, (c) the text "Please wait for an admin to validate your signup.", followed by (d) the Approve, Request Changes, and Reject action buttons.
2. **Given** a driver is in the correction flow and resubmits a corrected parameter, **When** the admin review panel is re-posted, **Then** the same waiting notice appears in the same position.

---

### User Story 5 — Bulk Points Configuration Editing (Priority: P5)

A tier-2 admin may edit the points allocation for multiple finishing positions in a named points configuration (or in the mid-season modification store) in a single interaction, by entering a multi-line block in the format `<position>, <points>` (one pair per line). Two new commands are added alongside the existing single-entry `results config session` and `results amend session` commands:

- `results bulk-config session` — bulk-sets position points on a named server-level config.
- `results bulk-amend session` — bulk-sets position points in the active modification store.

Both commands share the same bulk-entry mechanics. Invalid lines in the submission are reported clearly; valid lines are processed and committed.

**Why this priority**: The existing single-entry commands require repetitive individual interactions to configure a full points table (up to 20 positions). This is the most workflow-impacting change, reducing a 20-step configuration process to a single submission.

**Independent Test**: Create a named config "100%". Use `results bulk-config session` with a block of 5 position-points pairs for the Feature Race session and verify all 5 are stored. Submit a block with one invalid line and verify the valid lines are applied while the invalid line is reported with a clear error. In amendment mode, use `results bulk-amend session` with a multi-entry block and verify the modification store is updated accordingly.

**Acceptance Scenarios**:

1. **Given** a named config "100%" exists, **When** a tier-2 admin runs `results bulk-config session` for Feature Race and submits the block `1, 25\n2, 18\n3, 15\n4, 12`, **Then** positions 1–4 are updated in that session's config and a confirmation listing each applied change is shown.
2. **Given** the bulk submission contains a line `0, 10` (invalid position) or `1, -5` (invalid points), **When** submitted, **Then** the bot reports the offending line(s) with a clear explanation; any valid lines in the same block are still applied.
3. **Given** the named config does not exist, **When** `results bulk-config session` is run for it, **Then** the command is blocked with a "config not found" error and no changes are applied.
4. **Given** amendment mode is active, **When** a tier-2 admin runs `results bulk-amend session` with a valid block, **Then** the modification store is updated for all specified positions and a confirmation is shown.
5. **Given** amendment mode is not active, **When** `results bulk-amend session` is run, **Then** the command is blocked with a clear error indicating amendment mode must be enabled first.
6. **Given** the bulk submission is entirely empty or contains no parseable lines, **When** submitted, **Then** no changes are made and the user is informed.
7. **Given** a bulk submission contains duplicate position entries (e.g., position 1 appears twice), **When** submitted, **Then** the last valid value for that position is applied and the duplication is noted in the confirmation output.

---

### User Story 6 — Unassigned Drivers Command Restructure (Priority: P6)

The existing `/signup unassigned` command (which lists Unassigned drivers seeded by lap time) is renamed to `/signup unassigned list`. A new `/signup unassigned export` command is added under the same `unassigned` subgroup that produces a CSV file containing detailed driver data for all Unassigned drivers and delivers the file as an attachment in an ephemeral response.

**Why this priority**: The rename is a prerequisite for the export subcommand; both together constitute a coherent administrative reporting surface for driver placement.

**Independent Test**: Verify that `/signup unassigned` no longer resolves and `/signup unassigned list` returns the same seeded list as before. Run `/signup unassigned export` and verify an attached `.csv` file is returned with the correct headers and one row per Unassigned driver.

**Acceptance Scenarios**:

1. **Given** Unassigned drivers exist, **When** a tier-2 admin runs `/signup unassigned list`, **Then** the seeded Unassigned driver list is returned ephemerally, identical in content and format to what `/signup unassigned` previously returned.
2. **Given** no Unassigned drivers exist, **When** `/signup unassigned list` is run, **Then** the command returns an appropriate "no Unassigned drivers found" message.
3. **Given** Unassigned drivers exist, **When** a tier-2 admin runs `/signup unassigned export`, **Then** a CSV file is returned as an attachment in an ephemeral response.
4. **Given** the CSV file is opened, **Then** it contains the following headers in order: Seed, Display Name, Discord User ID, Driver Type, Lap Total, one column per configured time slot with the slot's day/time label as the header, Preferred Team 1, Preferred Team 2, Preferred Team 3, Platform, Platform ID.
5. **Given** a driver has selected a given time slot, **When** the CSV is generated, **Then** the cell under that slot's column contains "X"; if the driver did not select that slot, the cell is empty.
6. **Given** a driver has no preferred team entries, **When** the CSV is generated, **Then** the three preferred team columns are empty for that driver's row.
7. **Given** no Unassigned drivers exist, **When** `/signup unassigned export` is run, **Then** the command returns an appropriate message with no file attachment.
8. **Given** the set of configured time slots changes between exports, **When** a new CSV is generated, **Then** the column headers reflect the current slot configuration at time of export.

---

### Edge Cases

- What if the nationality validation list is very long and the error message listing valid options would exceed message length limits? The bot should provide a concise rejection message (e.g., "Please enter your full nationality (e.g., 'British') or country name (e.g., 'United Kingdom'), or type 'other'") without enumerating the full list.
- What if a driver in Assigned state leaves the server and their configured team seat becomes empty — does the seat get freed? No: per Principle VIII (server-leave rule), the profile and all assignments are retained; the seat assignment persists. The log notification simply records the departure.
- What if the log channel is deleted or inaccessible at the moment a leave event fires? The leave event must be handled gracefully; the departure is logged to the bot's application log and no crash occurs. Any applicable wizard cleanup still proceeds.
- What if the bulk-config submission is conducted via a mechanism with a character limit shorter than a full 20-position table? The feature must accommodate at minimum 20 `<position>, <points>` lines worth of input. If a submission is truncated mid-line, the incomplete line is treated as invalid and reported.
- What if `/signup unassigned export` is called while there are Unassigned drivers but some have incomplete signup records (null lap times, null platform)? The CSV should still be generated; missing values are represented as empty cells.

---

## Requirements *(mandatory)*

### Functional Requirements

**Nationality Validation (FR-N)**

- **FR-N001**: The nationality wizard step MUST accept a driver's input if and only if the value matches a recognised nationality adjective, a recognised country name, or the value "other" (case-insensitive). All other inputs MUST be rejected with a re-prompt.
- **FR-N002**: The validation reference MUST include at least all sovereign, widely-recognised states plus "other" as a universal fallback, in both country-name and nationality-adjective forms.
- **FR-N003**: The accepted value MUST be stored as the canonical form defined in the reference (e.g., the nationality adjective as the primary form, with country name as an alias that resolves to the same canonical value).
- **FR-N004**: Two-letter country codes (e.g., "GB", "FR") MUST be rejected; the rejection message MUST direct the driver to use the full form.
- **FR-N005**: When `nationality_required` is `false`, the validation step MUST be skipped entirely; no validation logic runs for that wizard session.

**Server-Leave Logging (FR-L)**

- **FR-L001**: On every member-remove event, the bot MUST check whether the departing member has an active driver profile with a state in {Pending Signup Completion, Pending Admin Approval, Awaiting Correction Parameter, Pending Driver Correction, Unassigned, Assigned}.
- **FR-L002**: For each matching driver, a notification MUST be posted to the server's configured calculation log channel, including: the driver's server display name (or Discord username if no display name is stored), the driver's Discord User ID, and their driver state at the time of departure.
- **FR-L003**: The existing wizard cleanup behaviour (state transition to Not Signed Up, channel deletion, job cancellation) for wizard-states MUST continue to execute as before; the log notification is additive, not a replacement.
- **FR-L004**: For Unassigned and Assigned drivers, no state changes or data deletions MUST occur; only the log notification is posted. The driver's profile and assignments are retained per Principle VIII.
- **FR-L005**: If the log channel is not configured or is inaccessible at the time of the event, the notification MUST be silently skipped; no crash or partial state change must result.

**Signup Open Announcement (FR-A)**

- **FR-A001**: When signups are opened with an auto-close time, the public signup announcement embed MUST include the scheduled close time in the `YYYY-MM-DD HH:MM UTC` display format.
- **FR-A002**: When signups are opened without an auto-close time, the embed MUST NOT include a close-time line.

**Admin Review Waiting Message (FR-W)**

- **FR-W001**: The admin review panel message MUST include the text "Please wait for an admin to validate your signup." immediately after the signup information block, separated from the notes field by exactly one blank line, and appearing before the action buttons.
- **FR-W002**: This text MUST appear both on the initial Pending Admin Approval post and on any re-post of the review panel (e.g., after a correction flow completes).

**Bulk Points Configuration (FR-B)**

- **FR-B001**: A new `results bulk-config session` command MUST be available to tier-2 admins that accepts a config name, session type, and a multi-line body of `<position>, <points>` pairs.
- **FR-B002**: A new `results bulk-amend session` command MUST be available to tier-2 admins that accepts a session type and a multi-line body of `<position>, <points>` pairs, operating against the active modification store.
- **FR-B003**: Each line in the bulk body MUST be parsed independently. Position MUST be a positive integer ≥ 1. Points MUST be a non-negative integer ≥ 0. Lines that do not conform MUST be reported to the user without blocking application of valid lines.
- **FR-B004**: Duplicate positions within a single submission MUST apply the last occurrence and note the duplication in the confirmation.
- **FR-B005**: `results bulk-amend session` MUST be gated behind amendment mode being active; if amendment mode is off, the command MUST return a clear error.
- **FR-B006**: If the named config does not exist for `results bulk-config session`, the command MUST fail with a "config not found" error before applying any changes.
- **FR-B007**: Both commands MUST produce an audit log entry per Principle V upon successful application, listing the session type and all position-points pairs that were applied.

**Unassigned Command Restructure (FR-U)**

- **FR-U001**: The existing `/signup unassigned` command MUST be renamed to `/signup unassigned list`. Its behaviour, output format, and access controls MUST remain identical to the current implementation.
- **FR-U002**: A new `/signup unassigned export` command MUST be added under the same subgroup, accessible to tier-2 admins, that generates and delivers a CSV file as an ephemeral file attachment.
- **FR-U003**: The CSV file MUST contain the following columns in this order: Seed, Display Name, Discord User ID, Driver Type, Lap Total, one column per currently-configured time slot (header = the slot's day/time description), Preferred Team 1, Preferred Team 2, Preferred Team 3, Platform, Platform ID.
- **FR-U004**: Under each time slot column, the cell for a given driver MUST contain "X" if the driver selected that slot, or be empty if not.
- **FR-U005**: The time slot columns MUST reflect the server's currently-active slot configuration at the time the export command is run.
- **FR-U006**: If no Unassigned drivers exist, the export command MUST return a "no Unassigned drivers" message with no file attachment.

### Key Entities

- **SignupRecord** (existing): The `nationality` field transitions from storing a two-letter ISO code to storing the canonical nationality/country name as resolved by the validation reference.
- **Nationality validation reference** (new, bundled): A static lookup structure mapping country names and nationality adjectives to their canonical stored form. Includes "other" as an always-valid entry.
- **TimeSlot** (existing): Used as the source of CSV column headers in the unassigned export; no schema changes required.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A driver completing the nationality step can successfully submit by typing a full nationality or country name; all attempts to submit a two-letter code are rejected within the same interaction.
- **SC-002**: Every departure of an active driver (in any wizard state, Unassigned, or Assigned) from the server produces a visible notification in the log channel within the same event cycle.
- **SC-003**: When signups are opened with a close time, the close time is visible in the public announcement in the signup channel so that drivers can read it without any admin intervention.
- **SC-004**: The admin review panel displays the waiting notice in every Pending Admin Approval state, on both initial submission and after correction resubmissions.
- **SC-005**: A tier-2 admin can configure a full 20-position points table for a session type in a single bulk-config interaction, reducing the minimum command count from 20 to 1.
- **SC-006**: The `results bulk-amend session` command applies all valid lines from a multi-position submission atomically; no partial write leaves the modification store in an inconsistent state.
- **SC-007**: `/signup unassigned list` returns the same results as the prior `/signup unassigned` command with no regression in content or format.
- **SC-008**: The CSV export produced by `/signup unassigned export` contains all Unassigned drivers, one per row, with all required columns including per-slot availability, and is immediately importable into a standard spreadsheet tool.

---

## Assumptions

- The nationality validation reference is implemented as a bundled static list embedded in the project. No external API or remote lookup is required.
- Both nationality adjective and country name for a given entry resolve to a single stored canonical value (the nationality adjective form, e.g., "British" rather than "United Kingdom"), consistent with how nationality fields are conventionally presented on official documents.
- The log channel referenced for server-leave notifications is the same calculation log channel already configured via `/bot-init` (Principle VII / V). No new channel type is introduced.
- The bulk-entry interface for the new `results bulk-config session` and `results bulk-amend session` commands uses a mechanism that supports multi-line text input. The exact interaction pattern (e.g., modal, multi-step prompt) is an implementation detail deferred to the plan.
- Time slot column headers in the CSV use the same `display_label` format already produced by the existing slot management commands (e.g., "Monday 20:00").
- "Lap Total" in the CSV is the formatted total lap time string already computed by the placement service (identical to what is shown in `/signup unassigned list`).
- The CSV file encoding is UTF-8.
