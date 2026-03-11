# Feature Specification: Driver Placement and Team Role Configuration

**Feature Branch**: `015-driver-placement`  
**Created**: 2026-03-11  
**Status**: Draft  
**Input**: User description: "Signup wizard continuation: seeded unassigned driver listing, driver assign/unassign/sack commands with division-role and team-role grant/revoke, and team-to-role association configuration."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Configure Team–Role Associations (Priority: P1)

A server administrator maps each team to a Discord server role so that when a driver is placed in a team, they automatically receive the correct team role. This configuration is server-scoped and must be set up before any driver placement occurs.

**Why this priority**: Team roles must be configured before any driver assignment can grant them. Without this, the role-grant mechanism in all subsequent stories has no data to act on. It is a prerequisite for all placement work.

**Independent Test**: Associate a role with Ferrari; verify the mapping is persisted. Overwrite with a different role; verify the new mapping replaces the old. Confirm the command is blocked while an active season exists.

**Acceptance Scenarios**:

1. **Given** no season is active, **When** a server admin runs `/team role set Ferrari @FerrariRole`, **Then** the Ferrari–role mapping is persisted and an ephemeral confirmation is shown.
2. **Given** a Ferrari–role mapping already exists, **When** a server admin sets a different role, **Then** the prior mapping is overwritten and the new one is persisted.
3. **Given** a season is in ACTIVE state, **When** a server admin attempts to set or change any team role mapping, **Then** the command is blocked with a clear message indicating changes are not permitted during an active season.
4. **Given** a season is in SETUP state (not yet activated), **When** a server admin sets a team role mapping, **Then** the command succeeds and the mapping is persisted.
5. **Given** no role has been mapped to a team, **When** a driver is later assigned to that team, **Then** no role grant or revocation error occurs; the assignment completes successfully with no team role action.

---

### User Story 2 - View Seeded Unassigned Driver List (Priority: P2)

A tier-2 admin runs a command to see all drivers currently in the Unassigned state, ordered by seeding. Seeding is determined by the sum of their signup lap times, with the lowest total ranked first. The listing includes all info needed to make placement decisions without consulting any other screen.

**Why this priority**: Informed placement decisions require a structured, ordered view of waiting drivers. This command produces the input needed for all assignment work and is inherently read-only with no irreversible side effects.

**Independent Test**: With three Unassigned drivers having different lap time totals, run the command and verify the output is sorted by ascending total time, contains all required fields per driver, and is visible only to the invoking tier-2 user.

**Acceptance Scenarios**:

1. **Given** three Unassigned drivers with totals 3:41.423, 3:40.055, 3:40.097, **When** a tier-2 user runs the listing command, **Then** the response lists them in order: seed 1 → 3:40.055, seed 2 → 3:40.097, seed 3 → 3:41.423.
2. **Given** two drivers with identical lap time totals, **When** the listing is requested, **Then** the driver whose signup was approved earliest ranks higher (lower seed number).
3. **Given** a driver who signed up when no tracks were configured (no lap times), **When** the listing is requested, **Then** that driver appears at the bottom of the list, after all timed drivers, ordered among untimed drivers by approval timestamp.
4. **Given** no drivers are in Unassigned state, **When** the listing is requested, **Then** the bot returns a clear message indicating no unassigned drivers exist.
5. **Given** a user without the tier-2 role runs the listing command, **Then** the command is rejected with a permission error.
6. **Given** a driver is Full-Time preference, **When** their entry is displayed, **Then** preferred teams appear in their originally submitted ranked order; if Reserve preference instead, preferred teams are shown as "N/A".

---

### User Story 3 - Assign Driver to Division–Team Seat (Priority: P3)

A tier-2 admin places an Unassigned or Assigned driver into a specific team within a specific division. On success, the driver's state advances to Assigned (if previously Unassigned), the division role is granted, and the team role (if configured) is granted.

**Why this priority**: This is the central outcome of the entire signup pipeline — moving a driver from waiting to active participation. Every downstream operation (unassign, sack, standings) depends on successful placement.

**Independent Test**: Assign an Unassigned driver to Ferrari in Division 1. Verify: driver state is Assigned, one TeamSeat record is occupied, the player holds the Division 1 role and the Ferrari role (if mapped).

**Acceptance Scenarios**:

1. **Given** a driver is Unassigned and a Ferrari seat in Division 1 is open, **When** a tier-2 admin assigns the driver to Ferrari/Division 1, **Then** the driver state changes to Assigned, the seat is marked occupied, and the Division 1 role and Ferrari role (if mapped) are granted.
2. **Given** a driver is Assigned (already in Division 2), **When** a tier-2 admin assigns that same driver to Reserve in Division 1, **Then** the assignment succeeds, the state remains Assigned, and the Division 1 role is granted.
3. **Given** a driver is already assigned to a team (any team, including Reserve) in Division 1, **When** a tier-2 admin attempts to assign them to a different team in Division 1, **Then** the command is blocked with a clear error — duplicate division assignment is not permitted.
4. **Given** all Ferrari seats in Division 1 are occupied, **When** a tier-2 admin attempts to assign a driver to Ferrari/Division 1, **Then** the command is blocked with a seat-full error. Reserve is never full.
5. **Given** a driver is in any state other than Unassigned or Assigned, **When** a tier-2 admin attempts to assign them, **Then** the command is blocked with an appropriate state error.
6. **Given** the division is referenced by tier number, **When** the command is run, **Then** the bot resolves the tier to the correct division and proceeds identically to a name-based reference.

---

### User Story 4 - Unassign Driver from a Division (Priority: P4)

A tier-2 admin removes a driver's assignment from a specific division. The division role and team role (if applicable) are revoked. If this was the driver's only remaining assignment, the driver transitions back to Unassigned.

**Why this priority**: Placement decisions are occasionally wrong or need to change before a season starts. Unassignment is the safe, reversible corrective action.

**Independent Test**: Assign a driver to Division 1 only, then unassign them from Division 1. Verify: driver state returns to Unassigned, Division 1 role is revoked, Ferrari role (if mapped and no other Ferrari seat held) is revoked, the seat is freed.

**Acceptance Scenarios**:

1. **Given** a driver is Assigned to Division 1 only, **When** a tier-2 admin unassigns them from Division 1, **Then** the driver transitions to Unassigned, the Division 1 role is revoked, and the seat is freed.
2. **Given** a driver is Assigned to Division 1 and Division 2, **When** a tier-2 admin unassigns them from Division 1, **Then** the driver remains Assigned, only the Division 1 role is revoked, and the Division 2 assignment is untouched.
3. **Given** a driver holds Ferrari in Division 1 and Ferrari in Division 2, **When** unassigned from Division 1, **Then** the Ferrari team role is NOT revoked because the driver still holds a Ferrari seat in Division 2.
4. **Given** a driver holds Ferrari in Division 1 only, **When** unassigned from Division 1, **Then** the Ferrari team role IS revoked.
5. **Given** a driver is in Unassigned state, **When** a tier-2 admin attempts to unassign them from any division, **Then** the command is blocked — a driver in Unassigned has no active assignments.
6. **Given** a driver is Assigned but not in the specified division, **When** unassign is run for that division, **Then** the command is blocked with a clear error.

---

### User Story 5 - Sack Driver (Priority: P5)

A tier-2 admin removes a driver from the league entirely — clearing all their division and team assignments, revoking all division and team roles, and transitioning the driver back to Not Signed Up.

**Why this priority**: The sack operation is the league-management tool for ending a driver's participation. It completes the full placement lifecycle and is also the foundation for the future ban management flow (role removal is shared logic).

**Independent Test**: Assign a driver to two divisions, then sack them. Verify: driver state is Not Signed Up, all division and team roles are revoked, all seat records are freed, and an audit log entry exists.

**Acceptance Scenarios**:

1. **Given** a driver is Unassigned, **When** a tier-2 admin sacks them, **Then** the driver transitions to Not Signed Up and an audit log entry is produced. Since they have no assignments, no role changes occur.
2. **Given** a driver is Assigned to Division 1 (Ferrari) and Division 2 (Reserve), **When** a tier-2 admin sacks them, **Then** both seat records are freed, the Division 1 role, Division 2 role, and Ferrari role (if mapped) are all revoked, and the driver transitions to Not Signed Up.
3. **Given** a driver is in any state other than Unassigned or Assigned, **When** a tier-2 admin attempts to sack them, **Then** the command is blocked with a clear state error.
4. **Given** a driver with `former_driver = true` is sacked, **Then** the driver record MUST be retained (not deleted) and signup record fields are nulled; the profile state is set to Not Signed Up.
5. **Given** a driver with `former_driver = false` is sacked, **Then** the driver record is deleted atomically as part of the state transition.

---

### Edge Cases

- What if the same team role is mapped to two different teams? Each team stores its role independently; the same Discord role may be shared across teams. Role revocation on unassign or sack considers all current assignments — the role is only revoked when the driver no longer holds any seat in any team that maps to that role.
- What if a division's Discord role is removed from the server externally? The assignment logic continues; granting or revoking a deleted role produces a logged error but does not abort the command.
- What if a team role is removed from the server externally after being configured? Same handling as above — the assignment proceeds, the role grant fails gracefully with a logged error.
- What if a driver's Discord account is deleted? Their profile remains in the database per Principle VIII. Role operations will silently fail; the bot continues normally.
- What if two tier-2 admins simultaneously attempt to assign the same driver to the same seat? The first operation wins and occupies the seat; the second receives a seat-full or duplicate-division error.
- What if an Assigned driver has their Discord User ID changed mid-season? The existing SeasonAssignment rows are unaffected (they reference profile identity, not Discord User ID directly). The Discord role grants/revocations that follow use the new account.

## Requirements *(mandatory)*

### Functional Requirements

#### Team–Role Configuration

- **FR-001**: A server administrator MUST be able to associate a Discord server role with any team (including Reserve) via a dedicated command (`/team role set <team> <role>`). This mapping is server-scoped and persists indefinitely.
- **FR-002**: If a role is already mapped to the specified team, the new role MUST overwrite the prior mapping. No duplicate mapping may exist for the same team.
- **FR-003**: The team–role configuration command MUST be blocked while any season is in ACTIVE state. It MUST be permitted when no season exists or when the current season is in SETUP state.
- **FR-004**: All team–role configuration changes MUST produce an audit log entry recording the actor, the team, the prior role (if any), and the new role (Principle V).
- **FR-005**: If no role has been mapped to a team, driver assignment and unassignment for that team MUST complete successfully without attempting any role operation.

#### Seeded Unassigned Driver Listing

- **FR-006**: A tier-2 user MUST be able to retrieve the full list of Unassigned drivers (`/signup list-unassigned`) as an ephemeral response visible only to the invoker.
- **FR-007**: The list MUST be ordered by ascending lap time sum (seeding). The driver with the lowest total lap time is seed 1.
- **FR-008**: At the moment a driver's signup is approved (transition to Unassigned state), their total lap time sum MUST be computed from the normalised lap time strings in their SignupRecord — each converted to milliseconds and summed across all tracks — and persisted as a dedicated `total_lap_ms` field on the SignupRecord. The listing command MUST order by this stored field, not by recomputing at query time. Drivers who had no signup tracks configured at approval time (no recorded lap times) MUST have `total_lap_ms` stored as NULL and MUST appear at the end of the list.
- **FR-009**: Among drivers with equal lap time sums, the driver whose signup was approved earliest (earliest transition to Unassigned) MUST rank higher (lower seed number).
- **FR-010**: Among drivers with no lap times, ordering MUST also use earliest approval timestamp.
- **FR-011**: Each entry in the list MUST display: seed number, Discord User ID, server display name, platform, availability (time slot IDs with day and time labels), driver type preference (Full-Time / Reserve), preferred teams in submission order (or "N/A" if Reserve preference or no preference submitted), preferred teammate (or "N/A"), formatted total lap time sum, and signup notes (or blank if none).
- **FR-012**: If no drivers are in Unassigned state, the command MUST return a clear, human-readable message stating so.
- **FR-013**: The signup module MUST be enabled for this command to succeed; if not enabled, a clear error MUST be returned (Principle X).

#### Driver Assignment

- **FR-014**: A tier-2 user MUST be able to assign a driver to a team within a division via a command accepting: a Discord User ID, a division identifier (tier number or name), and a team name.
- **FR-015**: The command MUST be blocked if the driver's profile state is not Unassigned or Assigned. A clear state error MUST be returned.
- **FR-016**: The command MUST be blocked if the driver is already assigned to any team (including Reserve) in the specified division. One assignment per driver per division is the maximum.
- **FR-017**: For configurable teams (non-Reserve), the command MUST be blocked if no available seat exists in the specified team and division. A Reserve team seat is always available.
- **FR-018**: On a successful assignment: a SeasonAssignment record MUST be created (or updated) linking the driver to the team seat; the driver's state MUST transition from Unassigned to Assigned if it was Unassigned (no state change if already Assigned); the division role MUST be granted to the driver's Discord account; the team role (if one is configured for that team) MUST be granted to the driver's Discord account.
- **FR-019**: Division and team roles MUST be granted atomically as part of the assignment. If a role grant fails (e.g., role no longer exists on the server), the failure MUST be logged but MUST NOT roll back the assignment record.
- **FR-020**: All assignment operations MUST produce an audit log entry (Principle V).

#### Driver Unassignment

- **FR-021**: A tier-2 user MUST be able to unassign a driver from a specific division via a command accepting a Discord User ID and a division identifier.
- **FR-022**: The command MUST be blocked if the driver's profile state is not Assigned. A clear state error MUST be returned.
- **FR-023**: The command MUST be blocked if the driver has no current assignment in the specified division. A clear error MUST be returned.
- **FR-024**: On a successful unassignment: the SeasonAssignment record for that division MUST be removed; the division role MUST be revoked from the driver's Discord account; the team role MUST be revoked if and only if the driver no longer holds any seat in any team (across all divisions) that maps to that role; if the driver has no remaining assignments after this operation, their state MUST transition to Unassigned.
- **FR-025**: All unassignment operations MUST produce an audit log entry (Principle V).

#### Driver Sacking

- **FR-026**: A tier-2 user MUST be able to sack a driver via a command accepting a Discord User ID.
- **FR-027**: The command MUST be blocked if the driver's profile state is not Unassigned or Assigned. A clear state error MUST be returned.
- **FR-028**: On a successful sack: ALL SeasonAssignment records for the driver in the current season MUST be removed; ALL division roles held by the driver for the current season MUST be revoked; ALL team roles held by the driver (for teams in the current season) MUST be revoked; the driver's state MUST transition to Not Signed Up.
- **FR-029**: The role revocation logic used during sacking MUST be implemented as a reusable, independently callable function (preparing for future ban management commands that require the same role-stripping behaviour).
- **FR-030**: Sacking applies the standard Not Signed Up transition rules: if `former_driver = true`, the profile is retained and signup record fields are nulled; if `former_driver = false`, the profile record is deleted atomically.
- **FR-031**: All sack operations MUST produce an audit log entry (Principle V).

### Key Entities

- **TeamRoleConfiguration** (per server, new): maps a team identifier to a Discord role ID. One row per team per server. Updated in place on overwrite; server-scoped and not tied to any season.
- **SeasonAssignment** (per driver per season per division, from v2.3.0): the canonical record linking a driver to a team seat in a division for a season. Created on first assignment, removed on unassignment or sack.
- **TeamSeat** (existing): represents an individual seat within a team in a division. Carries an occupancy flag or driver reference. modified on assignment and unassignment.
- **DriverProfile** (existing): driver lifecycle state and `former_driver` flag, mutated on assignment (Unassigned → Assigned), unassignment (Assigned → Unassigned when no seats remain), and sacking (any → Not Signed Up).
- **SignupRecord** (existing): written once in this feature at approval time to persist `total_lap_ms` (INTEGER, nullable — sum of all track lap times in milliseconds, computed at the transition to Unassigned; NULL if no tracks were configured at approval). All other fields are read-only in this feature. Provides seeding key and all display fields for the unassigned listing.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A tier-2 admin can view the complete seeded listing of unassigned drivers and perform a full assign/unassign cycle for one driver in a single session, with no external reference needed.
- **SC-002**: Every successful assign, unassign, and sack command results in the correct Discord roles being granted or revoked on the driver's account within the same interaction, with no manual follow-up required.
- **SC-003**: Sacking a driver assigned to multiple divisions removes all division and team roles in a single command with zero residual role grants.
- **SC-004**: Every driver-state transition produced by this feature is persisted atomically and recoverable after a bot restart with zero data loss.
- **SC-005**: Seed ordering is always accurate: the lap time total is computed once at approval and stored on the driver's SignupRecord; the listing reads the persisted value directly with no runtime recomputation. Subsequent changes to signup track configuration do not retroactively alter any driver's stored total.
- **SC-006**: Every placement command (assign, unassign, sack) produces an audit log entry that is queryable without accessing the database directly.

## Scope

### In Scope

- Team–role association configuration (`/team role set <team> <role>`).
- Seeded listing of all Unassigned drivers (`/signup list-unassigned`), ordered by ascending lap time sum with documented tiebreaking.
- Driver assignment to a division–team seat (`/driver assign`), including division role grant and team role grant.
- Driver unassignment from a division (`/driver unassign`), including division role revocation and conditional team role revocation.
- Driver sacking (`/driver sack`), including full role revocation and state transition to Not Signed Up.
- Reusable role-revocation utility function for use by future ban management commands.

### Out of Scope (Deferred to Next Increment)

- Season ban and league ban issuance commands.
- Signed-up role revocation on sacking or ban (the signed-up role is granted in feature 014; its revocation on ban is deferred to the ban management feature).
- Race results recording and championship standings (ratified in constitution Principle XII, implemented in a subsequent increment).
- Bulk assignment tools or CSV import.
- Any changes to the signup wizard parameter collection flow (completed in feature 014).

## Assumptions

- **A-001**: Lap time sums are expressed in milliseconds internally for comparison, then formatted as `M:ss.mmm` for display. Drivers with no lap times are seeded after all timed drivers.
- **A-002**: Among drivers with equal lap time totals, the tiebreaker is earliest signup approval timestamp (earliest transition to Unassigned). Among untimed drivers, the same tiebreaker applies.
- **A-003**: The same Discord role MAY be mapped to multiple teams. A driver's team role is revoked on unassignment or sack only when they no longer hold any seat in any team that maps to that role.
- **A-004**: Team–role configuration is permitted during season SETUP phase and when no season exists, but blocked during ACTIVE. This matches the window in which seat assignment is first occurring.
- **A-005**: Reserve team MUST also receive a role mapping. The assignment and revocation rules are identical to configurable teams.
- **A-006**: Division roles referenced in this feature are those configured during division creation/setup. This feature does not create or modify division role configurations — it only grants and revokes them.
- **A-007**: Role grant/revoke failures (e.g., the Discord role was externally deleted) are logged as warnings but do not abort or roll back the command's state changes.
- **A-008**: The driver's total lap time sum is computed once at the moment their signup is approved (transition to Unassigned) and stored as `total_lap_ms` on their SignupRecord. The `/signup list-unassigned` command orders by this persisted field rather than recomputing at query time. The stored value is immutable after approval; subsequent changes to signup track configuration do not retroactively affect any driver's seeding.
