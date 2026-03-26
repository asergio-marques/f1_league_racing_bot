# Feature Specification: Team Command QoL Simplification

**Feature Branch**: `016-team-cmd-qol`  
**Created**: 2026-03-11  
**Status**: Draft  
**Input**: User description: "Simplify team commands: replace /team default, /team season, and /team role subgroups with unified /team add, /team remove, /team rename, and /team list commands that operate on the server list and automatically apply to the current SETUP season if one exists."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Add a Team to the Server (Priority: P1)

A trusted admin runs `/team add` to create a new team entry, providing the team name and a required Discord role. If a season is currently in SETUP, the team is simultaneously added to every division in that pending season.

**Why this priority**: Adding teams is the first action an admin takes when configuring a season. Without it, no other team workflow proceeds.

**Independent Test**: Run `/team add` with a name and role in isolation (no active season). Verify the team appears in the server list with the role mapped.

**Acceptance Scenarios**:

1. **Given** no active SETUP season exists, **When** an admin runs `/team add name:"Red Bull" role:@RedBull`, **Then** the team is added to the server list with the role mapping recorded and a confirmation is returned.
2. **Given** a SETUP season is active with two divisions, **When** an admin runs `/team add name:"Red Bull" role:@RedBull`, **Then** the team is added to the server list, the role is mapped, and the team (with the configured seat count) is inserted into every division of the pending season.
3. **Given** a team with that name already exists in the server list, **When** an admin runs `/team add` with the same name, **Then** the command is rejected with a clear duplicate-name error.
4. **Given** no role is provided, **When** an admin runs `/team add name:"Red Bull"`, **Then** Discord rejects the command before it reaches the bot (role is a required parameter).

---

### User Story 2 - Remove a Team from the Server (Priority: P1)

A trusted admin runs `/team remove` to delete a team from the server list. Its role mapping is dropped. If a season is in SETUP, the team is simultaneously removed from every division in that pending season.

**Why this priority**: Teams need to be removable to maintain an accurate server configuration; this is symmetric with add.

**Independent Test**: Add a team via `/team add`, then remove it via `/team remove`. Verify the team no longer appears in the server list and any role mapping is gone.

**Acceptance Scenarios**:

1. **Given** a team exists in the server list with no active SETUP season, **When** an admin runs `/team remove name:"Red Bull"`, **Then** the team is deleted from the server list, the role mapping is dropped, and a confirmation is returned.
2. **Given** a team exists in the server list and a SETUP season is active, **When** an admin runs `/team remove name:"Red Bull"`, **Then** the team is deleted from the server list, the role mapping is dropped, and the team is removed from every division in the pending season.
3. **Given** no team with that name exists in the server list, **When** an admin runs `/team remove name:"Unknown"`, **Then** the command is rejected with a clear not-found error.
4. **Given** a team has drivers assigned in an active (non-SETUP) season, **When** an admin attempts to remove the team from the server list, **Then** the server list update succeeds but the active season data is unaffected (only the persistent list and SETUP-season divisions are touched).

---

### User Story 3 - Rename a Team in the Server (Priority: P2)

A trusted admin runs `/team rename` to change a team's name. If a season is in SETUP, the rename propagates to every occurrence of that team across all divisions in the pending season.

**Why this priority**: Renaming is needed less often than add/remove but is important for correcting mistakes without a full remove-and-re-add cycle.

**Independent Test**: Add a team, then rename it. Verify the old name is gone and the new name appears with the same role mapping.

**Acceptance Scenarios**:

1. **Given** a team exists in the server list with no active SETUP season, **When** an admin runs `/team rename current_name:"Red Bull" new_name:"Oracle Red Bull"`, **Then** the team name is updated in the server list, the role mapping key is updated, and a confirmation is returned.
2. **Given** a team exists in the server list and a SETUP season is active, **When** an admin runs `/team rename`, **Then** the name is updated in the server list and propagated to every division entry in the pending season.
3. **Given** no team with `current_name` exists, **When** an admin runs `/team rename`, **Then** the command is rejected with a clear not-found error.
4. **Given** `new_name` is already taken by another team in the server list, **When** an admin runs `/team rename`, **Then** the command is rejected with a duplicate-name error.

---

### User Story 4 - List all Teams with their Roles (Priority: P2)

A trusted admin runs `/team list` to view the full server team list with each team's mapped role. If a SETUP season is active and its team configuration differs from the server list, both lists are displayed for comparison.

**Why this priority**: Visibility into current configuration is essential before approving a season.

**Independent Test**: Add two teams with roles and run `/team list`. Verify both names and their roles are shown.

**Acceptance Scenarios**:

1. **Given** the server list contains teams, **When** an admin runs `/team list` with no active SETUP season, **Then** all teams and their mapped roles (or "no role" if unmapped) are displayed in a single list.
2. **Given** a SETUP season is active and its per-division team list matches the server list, **When** an admin runs `/team list`, **Then** only the unified list is shown (no duplication).
3. **Given** a SETUP season is active and its per-division team configuration differs from the server list (e.g., extra or missing teams), **When** an admin runs `/team list`, **Then** the server list and the season's effective team list are both displayed with a note indicating the discrepancy.
4. **Given** no teams exist in the server list, **When** an admin runs `/team list`, **Then** an appropriate empty-state message is returned.

---

### Edge Cases

- What happens when `/team add` is issued during an **active** (non-SETUP) season? The team is added to the persistent server list only; the active season is not modified.
- What happens when `/team remove` is issued for a team that exists in a SETUP season but not in the server list? This state should not be reachable through normal use; the command operates only on the server list.
- What happens if the Discord role provided to `/team add` is later deleted from the server? The mapping entry remains, but role grant/revoke operations silently skip the missing role (existing behaviour for role operations elsewhere in the bot).
- What does `/team list` show when teams within a SETUP season span multiple divisions with different seat counts? It shows the season team list once per unique team name, with any role mapping noted; seat-count details per division are out of scope for this command.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The bot MUST expose a `/team add` command that accepts a team name (required) and a Discord role (required) and adds the team to the persistent server team list.
- **FR-002**: When `/team add` is invoked and a season in SETUP state is active, the bot MUST also insert the team into every division of that pending season.
- **FR-003**: The bot MUST reject `/team add` if a team with the same name already exists in the server list, returning a clear error message.
- **FR-004**: The bot MUST expose a `/team remove` command that accepts a team name and removes that team and its role mapping from the persistent server team list.
- **FR-005**: When `/team remove` is invoked and a season in SETUP state is active, the bot MUST also remove the team from every division of that pending season.
- **FR-006**: The bot MUST reject `/team remove` if no team with that name exists in the server list, returning a clear not-found error.
- **FR-007**: The bot MUST expose a `/team rename` command that accepts the current team name and a new team name, updating the server list and any role mapping key.
- **FR-008**: When `/team rename` is invoked and a season in SETUP state is active, the bot MUST propagate the rename to every division entry in the pending season.
- **FR-009**: The bot MUST reject `/team rename` if the current name is not found, or if the new name is already in use by another team.
- **FR-010**: The bot MUST expose a `/team list` command that displays all teams in the server list alongside their mapped Discord roles (or a "no role" indicator if unmapped).
- **FR-011**: When `/team list` is invoked and a SETUP season is active with a team configuration that differs from the server list, BOTH the server list and the season's effective team configuration MUST be displayed, with a note indicating the discrepancy.
- **FR-012**: All four commands MUST be restricted to the trusted admin role and the configured interaction channel, consistent with all other admin commands in the bot.
- **FR-013**: The existing `/team default`, `/team season`, and `/team role` subcommand groups MUST be removed and replaced entirely by the four new commands.

### Key Entities

- **Server Team List**: The persistent, server-scoped catalogue of teams, each with a name and an optional Discord role mapping. Previously split between "default list" and "role mappings".
- **Division Team Entry**: A team's presence within a specific division of a SETUP season, carrying name and seat count. Created/updated/deleted as a side-effect of server list operations when a SETUP season is active.
- **Role Mapping**: The association between a team name and a Discord role, used when granting/revoking roles on driver placement and unassignment. Merged into the team record rather than being a separate `/team role` subcommand.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An admin can add, remove, rename, and list teams using exactly four top-level `/team` subcommands — no nested subcommand groups required.
- **SC-002**: Every team write operation (`add`, `remove`, `rename`) that is performed while a SETUP season is active is automatically reflected in all divisions of that season without any additional commands.
- **SC-003**: The role mapping for a team is managed entirely within `/team add` and `/team remove`, eliminating the need for a separate `/team role set` step.
- **SC-004**: Admins can view the complete team and role configuration, and any divergence between the server list and the active pending season, from a single `/team list` command.
- **SC-005**: No existing active-season driver placements or role assignments are disrupted when any of the new commands are executed outside of a SETUP phase.

## Assumptions

- Seat count is not a parameter of the new `/team add` command; teams are added to SETUP seasons with the server default seat count (2) unless the team already exists in the season with a different count. This can be revisited if per-team seat configuration is needed.
- The "server list" (formerly called "default list") is the single source of truth for team names and role mappings. SETUP-season divisions always start from a copy of this list.
- Active (non-SETUP) seasons are read-only from the perspective of these team commands; edits during an active season affect only the persistent server list and will be picked up at the next season setup.
