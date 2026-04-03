# Feature Specification: Attendance Module — Initial Setup & Configuration

**Feature Branch**: `031-attendance-module`  
**Created**: 2026-04-03  
**Status**: Draft  
**Input**: User description: attendance module initial configuration (module lifecycle, channel setup, RSVP timing, attendance point settings)

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Enable and Disable the Attendance Module (Priority: P1)

A server administrator wants to activate attendance tracking for the server. They enable the Attendance module using the standard module command. They later disable it when attendance tracking is no longer needed. The module's enabled/disabled status is reflected in the season review.

**Why this priority**: The enable/disable lifecycle is the foundational gate for the entire module. Without it, no other attendance configuration or behaviour is accessible. It also enforces the dependency on the Results & Standings module, protecting data integrity.

**Independent Test**: Can be fully tested by enabling and disabling the module and confirming the gate logic (season state, R&S dependency) without any RSVP or attendance data existing.

**Acceptance Scenarios**:

1. **Given** the Results & Standings module is enabled and no active season exists, **When** a server admin runs `/module enable attendance`, **Then** the module is enabled, the server's attendance config row is initialised with defaults, and the log channel receives a confirmation notice.
2. **Given** the Results & Standings module is disabled, **When** a server admin attempts `/module enable attendance`, **Then** the command is rejected with a clear error stating that Results & Standings must be enabled first.
3. **Given** a season is in the `ACTIVE` state, **When** a server admin attempts `/module enable attendance`, **Then** the command is rejected with a clear error stating the module cannot be enabled during an active season.
4. **Given** the Attendance module is enabled, **When** a server admin runs `/module disable attendance`, **Then** the module is disabled atomically: the enabled flag is cleared, all associated scheduled jobs (rsvp/last-notice timers) are cancelled, division config is cleared, and the log channel receives a notice.
5. **Given** the Attendance module is enabled and the Results & Standings module is subsequently disabled, **When** the R&S module disable completes, **Then** the Attendance module is automatically disabled (same atomicity rules as manual disable).
6. **Given** no active season, **When** a server admin runs `/season review`, **Then** the review output includes the Attendance module's enabled/disabled status.

---

### User Story 2 — Configure RSVP and Attendance Channels per Division (Priority: P2)

A league manager wants to point the bot to the correct Discord channels for RSVP polls and attendance sheets for each division. They use the `division rsvp-channel` and `division attendance-channel` commands. Both channels appear in the season review alongside the other per-division channels. If either is missing when the season is approved, the approval is blocked.

**Why this priority**: Channel configuration is a prerequisite for season approval with the module enabled, and it needs to be in place before any RSVP or attendance automation can fire.

**Independent Test**: Can be fully tested by setting channels for a division and attempting a season approval in `SETUP` with and without the channels configured, confirming approval gating behaviour.

**Acceptance Scenarios**:

1. **Given** the Attendance module is enabled and a division exists, **When** a league manager runs `/division rsvp-channel <division> <channel>`, **Then** the RSVP channel is stored in `AttendanceDivisionConfig` for that division, and the season review reflects the new channel.
2. **Given** the Attendance module is enabled and a division exists, **When** a league manager runs `/division attendance-channel <division> <channel>`, **Then** the attendance channel is stored in `AttendanceDivisionConfig` for that division, and the season review reflects the new channel.
3. **Given** the Attendance module is enabled and a division is missing its RSVP channel, **When** a league manager attempts to approve the season, **Then** approval is blocked with a diagnostic naming the division and the missing channel.
4. **Given** the Attendance module is enabled and a division is missing its attendance channel, **When** a league manager attempts to approve the season, **Then** approval is blocked with a diagnostic naming the division and the missing channel.
5. **Given** both channels are set for all configured divisions, **When** a league manager approves the season, **Then** the attendance channel gate passes (other gates may still block approval independently).
6. **Given** the Attendance module is disabled, **When** a league manager runs either channel command, **Then** the command is rejected with a clear module-not-enabled error.

---

### User Story 3 — Configure RSVP Timing Parameters (Priority: P3)

A league manager wants to control how far in advance the RSVP notice is sent, whether a last-notice ping fires for un-RSVP'd drivers, and when the RSVP window closes. They use the three `attendance config` timing commands. All three values must satisfy the ordering invariant at all times and cannot be changed mid-season.

**Why this priority**: Timing configuration must be locked down before a season starts and validated for correctness before being persisted.

**Independent Test**: Can be fully tested by setting each timing parameter in isolation and in combination, verifying invariant enforcement and mid-season rejection, without any RSVP data existing.

**Acceptance Scenarios**:

1. **Given** the module is enabled and there is no active season, **When** a league manager runs `/attendance config rsvp-notice 7`, **Then** `rsvp_notice_days` is updated to 7 and validated against the current `rsvp_last_notice_hours` and `rsvp_deadline_hours` (7 × 24 = 168 > current last-notice hours > current deadline hours).
2. **Given** the module is enabled and there is no active season, **When** a league manager runs `/attendance config rsvp-last-notice 0`, **Then** `rsvp_last_notice_hours` is set to 0 (last-notice ping disabled), provided the invariant is not violated (notice × 24 > 0 is trivially true; 0 must still be > deadline only if deadline = 0 as well — see edge cases).
3. **Given** the module is enabled and there is no active season, **When** a league manager runs `/attendance config rsvp-deadline 3`, **Then** `rsvp_deadline_hours` is updated to 3, validated that the invariant notice × 24 > last-notice > deadline holds with the current saved values.
4. **Given** the module is enabled, **When** a league manager submits a timing value that would violate the invariant `notice_days × 24 > last_notice_hours > deadline_hours`, **Then** the command is rejected with a clear error describing which constraint is violated and what the current conflicting values are.
5. **Given** a season is in the `ACTIVE` state, **When** a league manager attempts any of the three timing commands, **Then** the command is rejected with a clear error stating configuration cannot be changed during an active season.

---

### User Story 4 — Configure Attendance Point Penalties and Sanction Thresholds (Priority: P4)

A league manager wants to define how many attendance points are awarded for each type of infraction and at what point a driver is automatically moved to reserve or sacked. They use the five `attendance config` penalty commands. Autoreserve and autosack are disabled by default (value 0).

**Why this priority**: Penalty configuration is independent of timing and channel configuration and can be set up in isolation, but is required for the attendance points logic to produce meaningful results.

**Independent Test**: Can be fully tested by configuring each penalty value and threshold and reading them back via a config view or the season review, without any attendance data existing.

**Acceptance Scenarios**:

1. **Given** the module is enabled, **When** a league manager runs `/attendance config no-rsvp-penalty 2`, **Then** `no_rsvp_penalty` is updated to 2.
2. **Given** the module is enabled, **When** a league manager runs `/attendance config no-attend-penalty 2`, **Then** `no_attend_penalty` is updated to 2.
3. **Given** the module is enabled, **When** a league manager runs `/attendance config no-show-penalty 3`, **Then** `no_show_penalty` is updated to 3.
4. **Given** the module is enabled, **When** a league manager runs `/attendance config autosack 5`, **Then** `autosack_threshold` is set to 5 (enabled).
5. **Given** the module is enabled, **When** a league manager runs `/attendance config autosack 0`, **Then** `autosack_threshold` is set to null (disabled).
6. **Given** the module is enabled, **When** a league manager runs `/attendance config autoreserve 3`, **Then** `autoreserve_threshold` is set to 3 (enabled).
7. **Given** the module is enabled, **When** a league manager runs `/attendance config autoreserve 0`, **Then** `autoreserve_threshold` is set to null (disabled).
8. **Given** the Attendance module is disabled, **When** a league manager runs any penalty configuration command, **Then** the command is rejected with a clear module-not-enabled error.

---

### Edge Cases

- What happens when `/module enable attendance` is run and no `AttendanceConfig` row exists yet for the server? The row must be created atomically with defaults during the enable operation.
- What happens when the Attendance module is disabled and re-enabled? Config is cleared on disable (per Principle X rule 6); re-enabling starts fresh with defaults.
- What is the timing invariant behaviour when `rsvp_last_notice_hours = 0` (last-notice disabled) and `rsvp_deadline_hours = 0`? Both zero means no last-notice ping and RSVP locks at round start time. The invariant `notice × 24 > last_notice > deadline` resolves to `notice × 24 > 0 > 0` which is `> 0 > 0` — a tie between last-notice and deadline at zero. The rule **must therefore be interpreted as**: `rsvp_deadline_hours ≤ rsvp_last_notice_hours < rsvp_notice_days × 24`, with 0 being valid for both `last_notice` and `deadline` simultaneously, but only valid for `last_notice` if `deadline = 0` as well.
- What happens if the channel passed to `division rsvp-channel` or `division attendance-channel` is the same as an already-registered module channel (e.g., the results channel)? This is not blocked at the configuration stage; cross-channel registration is the server admin's responsibility. Validation only enforces that a channel is set.
- What happens when the Attendance module is enabled via test mode? The module must respect test-mode fake driver rosters; enabling and configuring it while test mode is active must be permitted.
- What happens when `/module disable attendance` is issued while RSVP timer jobs are armed? All scheduled jobs for RSVP notices and last-notice pings must be cancelled atomically.

## Requirements *(mandatory)*

### Functional Requirements

#### Module Lifecycle

- **FR-001**: The Attendance module MUST be disabled by default for all servers.
- **FR-002**: A server administrator MUST be able to enable the Attendance module via `/module enable attendance`, subject to: (a) the Results & Standings module being enabled; (b) no season being in the `ACTIVE` lifecycle state.
- **FR-003**: Enabling the module MUST atomically create an `AttendanceConfig` row for the server with default values if one does not exist, set `module_enabled = true`, and post a confirmation to the server's log channel.
- **FR-004**: If any step of the enable operation fails, it MUST be rolled back and no partial state left.
- **FR-005**: A server administrator MUST be able to disable the Attendance module via `/module disable attendance`. Disabling MUST atomically: set `module_enabled = false`, cancel all RSVP-related scheduled jobs for the server, clear `AttendanceDivisionConfig` rows for the server, and post a notice to the log channel.
- **FR-006**: Historical data generated by the module (attendance records, pardon logs) MUST be retained on disable; only live/scheduled artifacts are removed.
- **FR-007**: If the Results & Standings module is disabled while the Attendance module is enabled, the Attendance module MUST be automatically disabled using the same atomicity rules as a manual disable.
- **FR-008**: The Attendance module MUST NOT be enabled when a season is in the `ACTIVE` state. The command MUST be rejected with a clear, actionable error.
- **FR-009**: On bot restart, if the Attendance module is enabled, the bot MUST re-arm any RSVP notice and last-notice scheduled jobs that have not yet fired for rounds in the current `ACTIVE` season.
- **FR-010**: The Attendance module's enabled/disabled status MUST appear in the `/season review` output alongside other module statuses.
- **FR-011**: The Attendance module MUST function correctly when test-mode fake driver rosters are in use.

#### Channel Configuration

- **FR-012**: League managers MUST be able to set a per-division RSVP channel via `/division rsvp-channel <division> <channel>`. The channel ID MUST be stored in `AttendanceDivisionConfig` for that division.
- **FR-013**: League managers MUST be able to set a per-division attendance channel via `/division attendance-channel <division> <channel>`. The channel ID MUST be stored in `AttendanceDivisionConfig` for that division.
- **FR-014**: Both channel commands MUST be rejected if the Attendance module is not enabled, with a clear error.
- **FR-015**: Both channels for every configured division MUST be present before a season may be approved when the Attendance module is enabled. Missing either channel for any division MUST block approval and identify the affected division(s) and missing channel(s) in the error message.
- **FR-016**: Both per-division channels MUST appear in the `/season review` output alongside other division channels (results, standings, weather forecast, etc.).

#### RSVP Timing Configuration

- **FR-017**: League managers MUST be able to configure the number of days before a round at which RSVP notices are sent via `/attendance config rsvp-notice <days>`. Default value: 5.
- **FR-018**: League managers MUST be able to configure the number of hours before a round at which un-RSVP'd drivers are pinged via `/attendance config rsvp-last-notice <hours>`. Default value: 1. A value of 0 disables the last-notice ping.
- **FR-019**: League managers MUST be able to configure the RSVP deadline in hours before a round via `/attendance config rsvp-deadline <hours>`. Default value: 2. A value of 0 means RSVP locks at the scheduled round start time.
- **FR-020**: All three timing parameters MUST satisfy the invariant `rsvp_notice_days × 24 > rsvp_last_notice_hours` and `rsvp_last_notice_hours ≥ rsvp_deadline_hours` at all times. Any command that would violate these constraints MUST be rejected with a clear error identifying the conflicting values.
- **FR-021**: All three timing commands MUST be rejected if a season is currently in the `ACTIVE` state, with a clear error.

#### Attendance Point Configuration

- **FR-022**: League managers MUST be able to configure the no-RSVP attendance point penalty via `/attendance config no-rsvp-penalty <points>`. Default value: 1. Must be a non-negative integer.
- **FR-023**: League managers MUST be able to configure the no-attend attendance point penalty via `/attendance config no-attend-penalty <points>`. Default value: 1. Must be a non-negative integer.
- **FR-024**: League managers MUST be able to configure the no-show attendance point penalty via `/attendance config no-show-penalty <points>`. Default value: 1. Must be a non-negative integer.
- **FR-025**: League managers MUST be able to configure the autosack threshold via `/attendance config autosack <points>`. Default: disabled. A value of 0 disables the feature (stored as null). Must be a non-negative integer.
- **FR-026**: League managers MUST be able to configure the autoreserve threshold via `/attendance config autoreserve <points>`. Default: disabled. A value of 0 disables the feature (stored as null). Must be a non-negative integer. This threshold only applies to drivers not already in the Reserve team.
- **FR-027**: All five penalty configuration commands MUST be rejected if the Attendance module is not enabled, with a clear error.

### Key Entities

- **AttendanceConfig** (per server): `module_enabled`, `rsvp_notice_days` (default 5), `rsvp_last_notice_hours` (default 1), `rsvp_deadline_hours` (default 2), `no_rsvp_penalty` (default 1), `no_attend_penalty` (default 1), `no_show_penalty` (default 1), `autoreserve_threshold` (nullable, default null), `autosack_threshold` (nullable, default null). Owned by the Attendance module. Created on first enable; cleared on disable.

- **AttendanceDivisionConfig** (per server, per division): `rsvp_channel_id` (nullable), `attendance_channel_id` (nullable). Keyed on `(server_id, division_id)`. Created lazily on first channel command for a division; cleared when the module is disabled.

## Assumptions

- The "league manager" role referenced in command access is the tier-2 admin (season/config authority) as defined in Principle I. Server administrators hold tier-2 authority by default on Discord.
- The `division rsvp-channel` and `division attendance-channel` commands are added to the existing `/division` command group, consistent with the pattern already established for `/division results-channel`, `/division standings-channel`, etc.
- The `attendance config` commands are grouped under a new `/attendance` top-level command group as subcommands of an `attendance config` subgroup, consistent with the Bot Behavior Standards subcommand-group convention.
- The invariant edge case where both `rsvp_last_notice_hours` and `rsvp_deadline_hours` are 0 is permitted (last-notice ping is disabled; RSVP locks at round start). The ordering constraint is satisfied as long as `rsvp_notice_days × 24 > 0`.
- "Ongoing season" in the context of timing command rejection means a season in the `ACTIVE` lifecycle state specifically. A season in `SETUP` or `COMPLETED` does NOT block these commands.
- All configuration commands produce ephemeral responses visible only to the invoking user (per Bot Behavior Standards).
- The module does not add a DB migration for `AttendanceDivisionConfig` rows to the `divisions` table itself — it uses a separate join table (per constitution v2.10.0 design), consistent with the `DivisionResultsConfig` pattern in the Results & Standings module.

## Out of Scope for This Increment

The following attendance module behaviours are **explicitly deferred** to future feature increments:

- RSVP embed posting and button interaction handling.
- Reserve distribution logic at the RSVP deadline.
- Last-notice ping scheduling and sending.
- Attendance recording from submitted round results.
- Attendance point distribution (post-penalty finalization hook).
- Attendance pardon workflow inside the penalty wizard.
- Attendance sheet posting to the attendance channel.
- Autoreserve and autosack sanction enforcement execution.
- DriverRoundAttendance and AttendancePardon data entities (deferred to the RSVP/attendance implementation increment).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: League managers can enable and disable the Attendance module in a single command interaction; the enable gate (R&S dependency, season state) is enforced 100% of the time.
- **SC-002**: Automatic disable fires reliably whenever the Results & Standings module is disabled while Attendance is active, with no manual intervention required and no partial state left.
- **SC-003**: All seven `attendance config` commands and both `division` channel commands are completable in a single interaction without wizard flows.
- **SC-004**: The RSVP timing invariant is enforced on every configuration command; no combination of values that violates the constraint can be persisted.
- **SC-005**: Season approval is reliably blocked when the Attendance module is enabled and any division is missing a required channel; the error message identifies the specific division and missing channel.
- **SC-006**: The season review accurately reflects the Attendance module's status and all configured per-division channel assignments at all times.
- **SC-007**: The full test suite covers module enable/disable lifecycle (including cascading disable), all configuration commands (happy path and rejection gates), and season approval channel validation.
