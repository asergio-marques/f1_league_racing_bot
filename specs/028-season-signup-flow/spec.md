# Feature Specification: Season-Signup Flow Alignment

**Feature Branch**: `028-season-signup-flow`  
**Created**: 2025-07-01  
**Status**: Draft  
**Input**: User description: "Correct the season setup flow so it matches real-world league operation: signups open independently of season state, driver assignment targets a setup season, roles deferred to approval, season review and approval post lineups and calendars per division, lineup messages update live, force-close preserves approved drivers."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Season-Independent Signup Window (Priority: P1)

An admin wants to announce the new season and start collecting driver signups immediately, before divisions have been created or a season shell has been set up. Currently this is blocked — the bot requires an approved ACTIVE season before signups can be opened. The corrected flow removes this dependency: the signup window opens and closes freely regardless of what season state exists, including no season at all or an in-progress ACTIVE season.

**Why this priority**: This is the root blocker. Without it, the entire real-world league flow cannot proceed in the correct order. Every other story depends on signups being reachable before a season exists.

**Independent Test**: Admins can open signups with no season in the database, confirm drivers can complete the wizard and reach PENDING_ADMIN_APPROVAL, then close signups — all without ever running `/season setup`.

**Acceptance Scenarios**:

1. **Given** no season exists for the server, **When** an admin runs `/signup open`, **Then** signups open successfully without any season-related error message.
2. **Given** a season is in SETUP state and signups are closed, **When** an admin runs `/signup open`, **Then** signups open successfully.
3. **Given** a season is in ACTIVE state, **When** an admin runs `/signup open`, **Then** signups open successfully (supports mid-season intake).
4. **Given** signups are open and some drivers are in PENDING_ADMIN_APPROVAL state, **When** an admin runs `/signup close`, **Then** those drivers remain in PENDING_ADMIN_APPROVAL and are NOT transitioned to NOT_SIGNED_UP.
5. **Given** signups are open and some drivers are in PENDING_DRIVER_CORRECTION state, **When** an admin runs `/signup close`, **Then** those drivers remain in PENDING_DRIVER_CORRECTION and are NOT transitioned to NOT_SIGNED_UP.
6. **Given** signups are open and a driver is in PENDING_SIGNUP_COMPLETION state, **When** an admin runs `/signup close`, **Then** that driver IS transitioned to NOT_SIGNED_UP (existing behaviour preserved for incomplete signups).

---

### User Story 2 — Driver Assignment Targets Setup Season (Priority: P1)

After signups close and division structures are created, an admin wants to run `/driver assign` to place approved drivers into teams. The bot must allow this to work against a SETUP season (one that has not yet been approved). Roles are withheld until the season is formally approved, so no premature role changes reach the driver.

**Why this priority**: Without this story driver placement is impossible until the season is manually approved, which inverts the correct flow (approve → assign vs. assign → approve).

**Independent Test**: Admin creates a SETUP season, approves a driver signup (driver reaches UNASSIGNED), calls `/driver assign`, confirms the driver is moved to ASSIGNED and placed in a team, but holds no division or team Discord roles until `/season approve` runs.

**Acceptance Scenarios**:

1. **Given** a season is in SETUP state and a driver is UNASSIGNED, **When** an admin runs `/driver assign`, **Then** the driver is assigned to the specified division and team with no Discord role changes (roles will be granted in bulk when the season is approved).
2. **Given** a season is in ACTIVE state and a driver is UNASSIGNED, **When** an admin runs `/driver assign`, **Then** the driver is assigned and Discord tier + team roles are granted immediately.
3. **Given** no season exists, **When** an admin runs `/driver assign`, **Then** the command is rejected with a clear error message.
4. **Given** a season is in SETUP state and a driver is ASSIGNED, **When** an admin runs `/driver unassign`, **Then** the placement is removed, driver reverts to UNASSIGNED, and no Discord role changes occur (driver never held roles; nothing to revoke).
5. **Given** a season is in ACTIVE state and a driver is ASSIGNED, **When** an admin runs `/driver unassign`, **Then** the placement is removed and previously granted roles are revoked immediately.
6. **Given** an admin runs `/driver sack` on a driver regardless of season state, **Then** the driver is removed from all assignments, roles revoked, and driver state reset to NOT_SIGNED_UP.

---

### User Story 3 — Season Review Includes Driver Lineups (Priority: P2)

When an admin runs `/season review` before approving a season, the output should show who has been assigned to each division and flag anyone still listed as UNASSIGNED. This gives the admin confidence that the lineup is complete before committing.

**Why this priority**: Admins need visibility into lineup state at review time. Without it they cannot catch gaps (e.g. unplaced drivers) before approval.

**Independent Test**: Assign two drivers to a division, leave one approved driver UNASSIGNED, run `/season review`, confirm the output shows the two assigned drivers by team and explicitly calls out the UNASSIGNED driver.

**Acceptance Scenarios**:

1. **Given** a SETUP season with drivers assigned to divisions, **When** an admin runs `/season review`, **Then** the output lists each driver under their division and team.
2. **Given** one or more drivers in UNASSIGNED state, **When** `/season review` is run, **Then** those drivers are identified in the output with a visible warning.
3. **Given** a division with no assigned drivers at all, **When** `/season review` is run, **Then** the output still shows that division with an indication that no drivers are assigned.

---

### User Story 4 — Season Approval Publishes Lineups and Calendars (Priority: P2)

When a season is approved, the bot automatically posts the driver lineup to each division's configured lineup channel and the race calendar to each division's configured calendar channel. This eliminates the need for admins to manually announce these after approval.

**Why this priority**: Posting accuracy and timing matters — automated posting on approval removes human error in announcing line-ups and schedules to the community.

**Independent Test**: Configure lineup and calendar channels for all divisions, run `/season approve`, verify a lineup embed/message appears in each lineup channel and a calendar message appears in each calendar channel.

**Acceptance Scenarios**:

1. **Given** a season approval is triggered and all divisions have a lineup channel configured, **When** the approval completes, **Then** a lineup message is posted to each division's lineup channel.
2. **Given** a season approval is triggered and all divisions have a calendar channel configured, **When** the approval completes, **Then** a calendar message (listing rounds with track and date) is posted to each division's calendar channel.
3. **Given** drivers were assigned to divisions while the season was in SETUP state, **When** the season is approved, **Then** the bot grants tier and team Discord roles to every driver currently in ASSIGNED state across all divisions.
4. **Given** a division has no lineup channel configured, **When** the season is approved, **Then** lineup posting for that division is skipped silently (approval still succeeds).
5. **Given** a division has no calendar channel configured, **When** the season is approved, **Then** calendar posting for that division is skipped silently (approval still succeeds).
6. **Given** an admin configures a calendar channel via `/division calendar-channel`, **When** the season is later approved, **Then** the calendar is posted to that channel.

---

### User Story 5 — Live Lineup Updates After Assignment Changes (Priority: P3)

After the initial lineup post goes up, an admin reassigns a driver mid-preparation. The lineup message in the lineup channel should automatically reflect the change: the old message is deleted and a fresh one is posted in its place.

**Why this priority**: Stale lineup posts cause confusion if teams change. Automatic updates ensure the channel always shows the canonical state.

**Independent Test**: Approve a season (lineup posted). Run `/driver assign` to change a driver's team. Confirm the previous lineup message in the lineup channel is gone and a new one showing the updated assignment is present.

**Acceptance Scenarios**:

1. **Given** a lineup message has been posted for a division, **When** a driver is assigned to that division, **Then** the old lineup message is deleted and a new updated one is posted to the same channel.
2. **Given** a lineup message has been posted for a division, **When** a driver is unassigned from that division, **Then** the old message is deleted and an updated one is posted.
3. **Given** no lineup message has been posted for a division yet, **When** a driver assignment changes, **Then** a new lineup message is posted if a lineup channel is configured; no error occurs if no channel is configured.
4. **Given** the lineup message was deleted externally (e.g. manually by an admin), **When** an assignment change occurs, **Then** the bot posts a fresh lineup message without error (graceful handling of missing message).

---

### Edge Cases

- What happens when `/division calendar-channel` is called but no season exists? The command should return a clear error.
- What happens when the lineup channel is deleted after the message was posted? The bot should catch the Discord channel-not-found error gracefully and not block assignment operations.
- What happens when an assignment change occurs while the season is still in SETUP (i.e., before any calendar/lineup was posted on approval)? The bot should post a lineup message to the division's lineup channel if one is configured, since live updates should also work in the pre-approval window.
- If the bot restarts between an assignment change and the lineup post, is the lineup message ID recoverable? The message ID must be persisted to the database, not held in memory.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The bot MUST allow admins to open the signup window regardless of whether a season exists, is in SETUP state, or is in ACTIVE state. The existing guard that requires an ACTIVE season MUST be removed from `/signup open`.
- **FR-002**: When the signup window is force-closed, drivers in PENDING_ADMIN_APPROVAL state MUST NOT be transitioned to NOT_SIGNED_UP; they MUST retain their current state.
- **FR-003**: When the signup window is force-closed, drivers in PENDING_DRIVER_CORRECTION state MUST NOT be transitioned to NOT_SIGNED_UP; they MUST retain their current state.
- **FR-004**: `/driver assign` MUST require a season in SETUP or ACTIVE state and MUST reject the command with a clear error if no such season exists.
- **FR-005**: `/driver unassign` MUST require a season in SETUP or ACTIVE state and MUST reject the command with a clear error if no such season exists.
- **FR-006**: When a driver is assigned or reassigned while the season is in SETUP state, Discord tier and team roles MUST NOT be granted at assignment time. Upon season approval, the bot MUST perform a bulk role-grant, giving tier and team roles to every driver currently in ASSIGNED state across all divisions of that season. Drivers unassigned during SETUP hold no roles and require no revocation.
- **FR-007**: When a driver is assigned or reassigned while the season is in ACTIVE state, Discord tier and team roles MUST be granted immediately, matching the existing behaviour.
- **FR-008**: `/season review` MUST include a section per division listing all currently assigned drivers grouped by team.
- **FR-009**: `/season review` MUST explicitly identify any approved drivers (UNASSIGNED state) who have not been placed in a division, warning the admin that placement is incomplete.
- **FR-010**: Upon season approval, the bot MUST post a driver lineup message to each division's configured lineup channel. If no lineup channel is configured for a division, posting for that division is silently skipped.
- **FR-011**: Upon season approval, the bot MUST post a race calendar message to each division's configured calendar channel. If no calendar channel is configured for a division, posting for that division is silently skipped.
- **FR-012**: A new `/division calendar-channel` command MUST allow admins to configure a calendar posting channel for a specific division. This follows the same pattern as the existing `/division lineup-channel` command.
- **FR-013**: Whenever a driver assignment changes (via `/driver assign` or `/driver unassign`), the bot MUST delete the previously posted lineup message for the affected division (if one exists and a lineup channel is configured) and post a new updated lineup message in its place.
- **FR-014**: The bot MUST persist the ID of the currently posted lineup message for each division to the database so it survives bot restarts.
- **FR-015**: The per-division calendar channel ID MUST be persisted to the database so it is available at season approval time and for future reference.

### Key Entities

- **Signup Window**: The open/closed state of the signup system. After this change it is no longer coupled to season lifecycle, and opening it requires only that the signup module is configured and at least one time slot exists.
- **Driver Assignment**: The pairing of an approved driver (UNASSIGNED state) to a division and team within a SETUP or ACTIVE season. Role grant timing is determined by the season's current state at the moment of assignment.
- **Division Lineup Post**: A single Discord message posted to a division's lineup channel that lists current team assignments. Its message ID is stored per division so it can be deleted and replaced when assignments change.
- **Division Calendar Post**: A Discord message posted to a division's calendar channel upon season approval. It lists each round with its track name and scheduled date.
- **Per-Division Calendar Channel**: A new `calendar_channel_id` column on the `divisions` table (alongside `results_channel_id`, `standings_channel_id`, etc.) that records where the race calendar should be posted.
- **Per-Division Lineup Message ID**: A new `lineup_message_id` column on the `divisions` table that records the Discord message ID of the most recently posted lineup message for a division. The `lineup_channel_id` column also moves to the `divisions` table from `signup_division_config`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An admin can complete the full pre-season sequence — open signups with no season, collect and approve drivers, create divisions, assign drivers, run `/season review` (seeing lineups), and run `/season approve` (triggering automatic lineup and calendar posts) — without encountering any season-state errors or manual posting steps.
- **SC-002**: After force-closing signups, zero drivers who had reached PENDING_ADMIN_APPROVAL or later are reset to NOT_SIGNED_UP.
- **SC-003**: After any driver assignment or unassignment, the lineup message in the affected division's lineup channel is updated within the same command response cycle, with no stale message remaining.
- **SC-004**: Upon season approval, a lineup message and a calendar message each appear in every division channel that has those channels configured, requiring no additional admin action.
- **SC-005**: Admins can configure a calendar channel for any division using a single command, and the configuration is reflected immediately in `/season review` output.

## Assumptions

- **A-001**: Both `lineup_channel_id` and `calendar_channel_id` belong on the `divisions` table (alongside `results_channel_id`, `standings_channel_id`, etc.), not on `signup_division_config`. The `lineup_message_id` tracking field follows to the same table. The existing `/division lineup-channel` command's write target changes from `signup_division_config` to `divisions`; the `signup_division_config` table requires no new columns for this feature.
- **A-002**: The calendar post lists rounds chronologically with round number, track name (or "Mystery" for unrevealed tracks), and scheduled datetime rendered as a Discord dynamic timestamp (`<t:UNIX:F>`) so each reader sees the time in their local timezone. This format applies only to the calendar post introduced in this feature; other existing time displays in the bot are out of scope.
- **A-003**: The lineup post format groups drivers by team within each division, displaying each driver as a Discord mention (`@username`). Driver type (Full-Time / Reserve) is shown where recorded.
- **A-004**: `/division calendar-channel` is NOT gated on the signup module. It is available to admins whenever a season exists, consistent with other division channel commands (`/division results-channel`, `/division standings-channel`, etc.).
- **A-005**: Role grant timing follows a strict four-case matrix: (1) SETUP + assign → deferred; roles granted in bulk at season approval. (2) SETUP + unassign → no change; driver never held tier/team roles. (3) ACTIVE + assign → roles granted immediately. (4) ACTIVE + unassign → roles revoked immediately. The signed-up (complete) role granted during wizard approval is unaffected by this matrix — it is already granted at the earlier admin-approval stage.
- **A-006**: When lineup or calendar posting fails due to a missing or inaccessible channel, the failure is logged but does NOT block season approval from completing.
- **A-007**: `/driver sack` continues to function regardless of season state (existing behaviour), since removing a driver entirely is an administrative action independent of pre-season preparation.

## Clarifications

### Session 2026-03-30

- Q: What triggers role grants for drivers assigned during SETUP — bulk at approval, or individually once ACTIVE? → A: Four-case matrix: SETUP+assign = deferred, bulk-granted at season approval; SETUP+unassign = no role change (driver never held roles); ACTIVE+assign = immediate grant; ACTIVE+unassign = immediate revoke.
- Q: How should each driver's name appear in the lineup post? → A: Discord mention only (`@username`).
- Q: Where should `lineup_channel_id` and `calendar_channel_id` be stored — `signup_division_config` or `divisions`? → A: Both belong on the `divisions` table. `lineup_message_id` follows to the same table. `signup_division_config` requires no new columns.
- Q: Should `/division calendar-channel` be gated on the signup module being enabled? → A: No — available to admins whenever a season exists, same as other division channel commands.
- Q: How should scheduled datetimes be displayed in the calendar post? → A: Discord dynamic timestamp (`<t:UNIX:F>`) only. Scope limited to the calendar post; other time displays in the bot are out of scope for this feature.
