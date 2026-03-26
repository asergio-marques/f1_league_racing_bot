# Feature Specification: Season Archive & Driver Profile Identity

**Feature Branch**: `024-season-archive-driver-id`  
**Created**: 2026-03-26  
**Status**: Draft  
**Input**: User description: Season archival on completion (immutable retention), game edition on season setup, season number derived from archive count, season setup gating on active season, driver profile unique internal ID decoupled from Discord user ID.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Season Data Preserved on Completion (Priority: P1)

When a season ends, all of its data — divisions, rounds, sessions, results, driver assignments, standings, points configuration snapshots, and the audit trail — is retained permanently in a read-only archived state. Nothing is deleted.

**Why this priority**: This is the foundational change. Season numbering from archive count, setup gating, and future statistics all depend on completed seasons persisting. Without this, the remaining stories cannot function correctly.

**Independent Test**: Trigger season completion on a server with at least one division, round, and result submitted. Verify every associated record is still present and correct immediately after completion, and remains present and unchanged after an additional season is set up and activated.

**Acceptance Scenarios**:

1. **Given** an active season with divisions, rounds, results, and driver assignments, **When** season completion is triggered, **Then** the season's status becomes COMPLETED and every associated record is still accessible with all original values intact.
2. **Given** a COMPLETED season in the archive, **When** any user or admin action attempts to modify that season's data (amend a round, change a result, alter an assignment), **Then** the system rejects the mutation with a clear error and no data changes.
3. **Given** a server with one COMPLETED and one new ACTIVE season, **When** either season's data is queried, **Then** both return correct independent data with no cross-contamination.

---

### User Story 2 - Season Setup Gating (Priority: P2)

An admin can only invoke `/season setup` when all existing seasons for the server are in COMPLETED state. Any season currently in SETUP or ACTIVE state blocks setup of a new one.

**Why this priority**: With completed seasons now persisting, the previous block-on-any-existing-season check would permanently prevent any second season from ever being started. The gating logic must be corrected before any server can run a second season.

**Independent Test**: Complete a full season on a test server, then verify `/season setup` succeeds. Immediately attempt a second `/season setup` while the new one is in SETUP state and verify it is rejected.

**Acceptance Scenarios**:

1. **Given** a server with an ACTIVE season, **When** `/season setup` is invoked, **Then** the command is rejected with a message indicating a season is already running.
2. **Given** a server with a season already in SETUP state, **When** `/season setup` is invoked by any admin, **Then** the command is rejected and the existing setup is not disturbed.
3. **Given** all seasons for the server are in COMPLETED state, **When** `/season setup` is invoked, **Then** setup succeeds and a new season configuration begins.
4. **Given** a server with no seasons at all, **When** `/season setup` is invoked, **Then** setup succeeds (empty archive is a valid starting state).

---

### User Story 3 - Game Edition Recorded at Season Setup (Priority: P3)

When configuring a new season, the admin must provide an integer identifying the game edition (e.g. the release year of the game). The season record stores this value and it is retained in the archive on completion.

**Why this priority**: Enables future statistics and analysis features to differentiate results across different game editions. The data must be captured at setup time; it cannot be retrofitted reliably after the fact.

**Independent Test**: Run `/season setup` without providing a game edition and verify rejection. Run it with a valid edition value and verify the value is stored on the season and visible in the season status output.

**Acceptance Scenarios**:

1. **Given** an admin invokes `/season setup` without providing a `game_edition` value, **Then** the command is rejected before any season data is created.
2. **Given** an admin invokes `/season setup` with `game_edition` set to a positive integer (e.g. `25`), **When** the season setup is approved, **Then** the season record stores that game edition value.
3. **Given** a season with a stored `game_edition`, **When** the season is completed and archived, **Then** the archived record retains the original `game_edition` value unchanged.

---

### User Story 4 - Season Number Derived from Archive Count (Priority: P4)

The human-readable number assigned to a new season is automatically computed as the count of completed seasons already in the archive plus one. The lone integer counter previously used for this purpose is superseded.

**Why this priority**: The archive-count derivation is guaranteed to be self-consistent with the actual history; the legacy counter could drift out of sync. This story is dependent on P1 (archive retention) being in place first.

**Independent Test**: Complete two seasons in sequence on a test server and verify the third is automatically numbered Season 3 without any manual counter intervention.

**Acceptance Scenarios**:

1. **Given** a server with zero completed seasons, **When** a new season is set up, **Then** it is automatically numbered Season 1.
2. **Given** a server with three completed seasons in the archive, **When** a new season is set up, **Then** it is automatically numbered Season 4.
3. **Given** an existing server where the legacy counter is present in the database, **When** a new season is set up after migration, **Then** the number assigned reflects the actual count of completed seasons, not the legacy counter value.

---

### User Story 5 - Driver Profile Internal Identity Decoupled from Discord (Priority: P5)

Each driver profile carries a unique, stable internal identifier that is decoupled from the driver's Discord account. All internal data records (season assignments, results, standings, team seat occupancy) reference this identifier. Commands still accept Discord user mentions as input, resolved to the internal identifier at the boundary.

**Why this priority**: Without this, reassigning a driver's Discord account (via the existing `/driver reassign` command) risks orphaning or misattributing historical records. This is a correctness and data integrity concern that grows in severity as the archive accumulates history.

**Independent Test**: Assign a driver to a division, submit results for them, then use `/driver reassign` to move the profile to a different Discord account. Verify all prior results and standings remain correctly attributed to the driver profile, and that commands targeting the new Discord account return the same historical data as before the reassignment.

**Acceptance Scenarios**:

1. **Given** a driver has season assignments, results, and standings on record, **When** their Discord user ID is reassigned to a new account, **Then** all existing records remain correctly linked through the internal identifier with no data loss.
2. **Given** internal database records that associate data with a driver, **When** those records are inspected, **Then** they reference the driver's internal identifier, not the Discord user ID.
3. **Given** a command that targets a driver by Discord mention or user ID, **When** the bot processes that command, **Then** it resolves the Discord user ID to the driver's internal identifier before performing any data operation.
4. **Given** the existing integer primary key on `driver_profiles` as the unique internal identifier, **When** the feature is complete, **Then** all modules consistently reference drivers through this identifier internally. *(No new schema field is required; scope is limited to updating signup records and results tables to use this key consistently.)*

---

### Edge Cases

- What happens to the `previous_season_number` legacy counter in `server_configs` for servers with existing data when this feature is deployed? A migration must produce an archive count consistent with the actual number of completed seasons already present.
- What if two admin sessions simultaneously attempt `/season setup`? Only the first may succeed; the second must be rejected once an in-progress SETUP season is detected.
- What if a bot restart occurs mid-setup? Recovery from the SETUP state in the database must not assign a duplicate or incorrect season number.
- What happens if a season is stuck in SETUP indefinitely (admin never approves)? The gating rule still blocks a second setup attempt until the first is cancelled or approved and then completed.
- What happens to `signup_records` for drivers not yet approved (no driver profile created at the time of archival)? Signup records not linked to a live profile remain keyed by Discord user ID; they are only migrated to the internal identifier upon profile creation at approval time.
- What if a driver's internal ID is referenced in results but the profile has since been deleted? This scenario must be prevented by data integrity constraints; a profile must not be deletable while active historical records reference it.

## Requirements *(mandatory)*

### Functional Requirements

**Season Archival**

- **FR-001**: When a season transitions to `COMPLETED`, the system MUST retain all associated data — divisions, rounds, sessions, results, driver assignments, standings snapshots, points configuration snapshots, and the full audit trail — permanently.
- **FR-002**: A season in `COMPLETED` state MUST be immutable. No user command or scheduled process may alter any field of that season record or any of its associated records.
- **FR-003**: The system MUST NOT delete any season data as part of the season-completion process.
- **FR-004**: Archived (COMPLETED) seasons and their associated data MUST remain queryable by read-only commands and by future statistics features.

**Season Setup & Numbering**

- **FR-005**: The `/season setup` command MUST accept a mandatory integer `game_edition` parameter. The command MUST be rejected if this parameter is not supplied.
- **FR-006**: The `game_edition` value MUST be stored on the season record at setup time and retained unchanged when the season is archived on completion.
- **FR-007**: A new season's human-readable number MUST be automatically assigned as (count of COMPLETED seasons for this server) + 1 at the point of first setup.
- **FR-008**: The legacy `previous_season_number` counter in server configuration MUST be superseded by the archive-count-based derivation for all new season number assignments.
- **FR-009**: The `/season setup` command MUST be rejected if the server has any season in `SETUP` or `ACTIVE` state.
- **FR-010**: The `/season setup` command MUST be permitted when all existing seasons for the server are in `COMPLETED` state, including servers that have no prior seasons at all (empty archive).

**Driver Profile Identity**

- **FR-011**: Each driver profile MUST have a unique, stable internal identifier — the existing integer primary key on the `driver_profiles` table — that does not change when the driver's Discord user ID is reassigned. No new schema field is required; the scope of this story is migrating signup records and results tables to reference this key consistently.
- **FR-012**: All internal database records that associate data with a specific driver (season assignments, session results, standings snapshots, team seat occupancy, season history entries) MUST reference the driver profile's internal identifier rather than the Discord user ID.
- **FR-013**: The signup module MUST be updated so that once a driver profile is created (at approval), all subsequent data associations for that driver use the internal identifier rather than the Discord user ID.
- **FR-014**: The results and standings module MUST be updated so that driver-specific result records and standings entries reference the driver profile's internal identifier.
- **FR-015**: All commands that target a driver by Discord mention or raw Discord user ID MUST resolve that input to the driver profile's internal identifier before performing any database read or write operation.
- **FR-016**: If a driver profile's Discord user ID is reassigned via `/driver reassign`, all pre-existing records referencing the driver's internal identifier MUST remain correctly associated with no data loss or misattribution.

### Key Entities

- **Season**: Server-scoped record with `game_edition` (new mandatory integer field), `season_number` (human-readable auto-assigned display number), and `status` (SETUP → ACTIVE → COMPLETED). A season in COMPLETED state is an archived record and is fully immutable. All related records (divisions, rounds, results, assignments, audit trail) are logically part of the season and share its immutability once archived.
- **Season Archive** *(logical)*: The collection of all COMPLETED seasons for a given server, constituted by all Season rows (and their related records) where `status = COMPLETED`. The archive is append-only and read-only.
- **DriverProfile**: Server-scoped identity record for a driver. Carries a unique, stable internal identifier and a Discord user ID. The internal identifier is the authoritative reference for all inter-entity data associations. The Discord user ID is the lookup key used in commands.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a season is completed, 100% of its associated records (rounds, results, driver assignments, standings) are still accessible and return identical values as before completion.
- **SC-002**: A new season can be set up immediately after the previous season completes, with no manual counter-reset or data-cleanup action required by an admin.
- **SC-003**: The auto-assigned season number for each new season exactly equals (count of completed seasons) + 1, verified consistently across a sequence of at least three completed seasons on the same server.
- **SC-004**: After a `/driver reassign` operation, every prior result, assignment, and standing associated with that driver remains accessible and correctly attributed, with zero data loss.
- **SC-005**: `/season setup` is rejected in 100% of attempts when an ACTIVE or SETUP season exists, and succeeds in 100% of attempts when only COMPLETED seasons (or no seasons) exist.
- **SC-006**: 100% of internal driver data records (assignments, results, standings) reference drivers through the internal identifier, verifiable by inspecting the database schema and runtime queries.

## Dependencies

- **Constitution v2.5.0 — Season Archive governance**: This feature is the concrete implementation of the Season Archive paradigm introduced in constitution v2.5.0. All rules stated in the Season Archive section (append-only, full data retention, immutability, read-only access after archival) are non-negotiable.
- **Feature 012 (Driver Profiles & Teams)**: The driver profile model, state machine, and assignment schema are prerequisites that this feature extends.
- **Feature 019 (Results Submission & Standings)**: Results and standings data structures that reference driver identities are in scope for the driver identity migration (FR-014).
- **Feature 023 (Post-Submit Penalty Flow)**: Any data recorded by the penalty flow that references drivers must align with the internal identifier requirement (FR-012).

## Assumptions

- The `season_number` field already exists on the Season record (confirmed in codebase); no new display-number field is needed — the existing field carries the archive-count-derived number.
- `game_edition` is a plain positive integer (e.g. `25` for F1 25); no enumeration or preset list is required at this stage.
- For the driver profile internal identifier: the existing integer primary key on the `driver_profiles` table is the confirmed "unique internal ID". The scope of this story is updating the signup records and results tables to reference this key consistently, rather than adding a new field.
- Signup records for drivers not yet approved remain keyed by Discord user ID until a driver profile is created at approval time; this is an acceptable transient gap.
- The legacy `previous_season_number` counter can be safely disregarded for new seasons once the archive-count mechanism is in place; existing servers do not need a retroactive correction so long as the migration sets the archived season count correctly.
