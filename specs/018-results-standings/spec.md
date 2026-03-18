# Feature Specification: Results & Standings — Module Registration and Channel Setup

**Feature Branch**: `018-results-standings`  
**Created**: 2026-03-18  
**Status**: Draft  
**Input**: Foundation of the Results & Standings module: module enable/disable lifecycle, decoupling division channel configuration from the division-add command, new per-division channel assignment commands, and season approval prerequisite gates.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Server admin enables the Results & Standings module (Priority: P1)

A server admin wants to activate the Results & Standings (R&S) module so that race results can be collected and standings can be posted for the league. They issue the enable command and the module becomes active for the server.

**Why this priority**: Without the module being enableable, no Results & Standings functionality can be used. This is the entry-point gate for the entire feature area.

**Independent Test**: Can be tested in isolation by having a server admin run the enable command on a server with no active season and confirming the module is now reported as enabled in module status.

**Acceptance Scenarios**:

1. **Given** the R&S module is disabled and there is no active season, **When** a server admin issues the enable command for the R&S module, **Then** the module becomes enabled for that server and the bot confirms the change ephemerally.
2. **Given** the R&S module is disabled and there is an active (ACTIVE-state) season, **When** a server admin issues the enable command for the R&S module, **Then** the command fails and the bot returns a clear error explaining that the module cannot be enabled while a season is active.
3. **Given** the R&S module is enabled, **When** a server admin issues the disable command for the R&S module, **Then** the module becomes disabled and the bot confirms the change ephemerally.
4. **Given** the R&S module is already enabled, **When** a server admin attempts to enable it again, **Then** the bot returns a clear informational message indicating the module is already enabled; no state change occurs.
5. **Given** a non-admin user (interaction role but not server admin) issues the R&S module enable command, **Then** the bot rejects the command with a clear permission error.

---

### User Story 2 — Trusted admin assigns a weather forecast channel to a division (Priority: P1)

Before this change, the weather forecast channel had to be provided when creating a division. A trusted admin now uses a dedicated command after the fact to set or update a division's weather forecast channel.

**Why this priority**: This decoupling is a prerequisite for all downstream weather and R&S functionality — the division-add command must be updated, and a clean channel-assignment path must exist before any module enablement or season approval flow can be tested.

**Independent Test**: Can be tested in isolation by creating a division (verifying no channel parameter is accepted), then assigning a weather channel to it via the new command, and confirming the channel is stored and retrievable.

**Acceptance Scenarios**:

1. **Given** a division exists in the current season setup, **When** a trusted admin runs the weather channel assignment command with a valid division name and a valid Discord channel, **Then** the weather forecast channel for that division is updated and the bot confirms ephemerally.
2. **Given** a trusted admin attempts to add a division, **When** they provide a weather forecast channel as part of the division-add command, **Then** the bot rejects the extra input (the parameter no longer exists on that command) or ignores it and does not store a channel via that path.
3. **Given** a division already has a weather channel set, **When** a trusted admin runs the weather channel assignment command with a different channel, **Then** the previous channel is replaced, and the bot confirms the new value ephemerally.
4. **Given** a trusted admin provides a non-existent division name or a non-channel value, **When** they run the weather channel assignment command, **Then** the bot rejects the input with a specific, actionable error message.

---

### User Story 3 — Trusted admin assigns results and standings channels to a division (Priority: P1)

A trusted admin sets the channel where race results will be posted and the channel where standings will be posted for a given division, so that those outputs have dedicated locations when the R&S module produces output.

**Why this priority**: These channels are required before the R&S module can post any content. Their assignment commands must exist before season approval validation can be exercised.

**Independent Test**: Can be tested by assigning both channels to a division and confirming both are stored independently. Changing one must not affect the other.

**Acceptance Scenarios**:

1. **Given** a division exists in the current season setup, **When** a [NEEDS CLARIFICATION: see NC-001] admin runs the results channel assignment command with a valid division name and a valid channel, **Then** the results channel for that division is stored and the bot confirms ephemerally.
2. **Given** a division exists in the current season setup, **When** the same type of admin runs the standings channel assignment command with a valid division name and a valid channel, **Then** the standings channel for that division is stored and the bot confirms ephemerally.
3. **Given** a division already has a results channel and it is re-assigned via the command, **When** the command runs successfully, **Then** the previous channel is replaced; only the results channel is changed, not the standings channel.
4. **Given** a division already has a standings channel and it is re-assigned via the command, **When** the command runs successfully, **Then** the previous channel is replaced; only the standings channel is changed, not the results channel.
5. **Given** an invalid division name or non-channel value is provided, **Then** the bot rejects the input with a specific, actionable error message.

---

### User Story 4 — Season approval is guarded by module-specific channel prerequisites (Priority: P2)

When an admin attempts to approve a season in SETUP, the bot validates that all enabled modules have their required channels configured before allowing the transition to ACTIVE.

**Why this priority**: Approval gates protect against starting a season in a misconfigured state. They depend on the channel assignment commands (User Stories 2 and 3) being in place first.

**Independent Test**: Can be tested after User Stories 2 and 3 by deliberately leaving channels unconfigured on at least one division and confirming season approval is blocked with a specific diagnostic, then completing the configuration and confirming approval succeeds.

**Acceptance Scenarios**:

1. **Given** the weather module is enabled and at least one division has no weather forecast channel assigned, **When** an admin attempts to approve the season, **Then** the approval is rejected and the bot identifies which divisions are missing a weather forecast channel.
2. **Given** the weather module is enabled and every division has a weather forecast channel assigned, **When** an admin attempts to approve the season (all other conditions met), **Then** the weather-channel prerequisite does not block approval.
3. **Given** the R&S module is enabled and the prerequisites are not met (see NC-002 for exact conditions), **When** an admin attempts to approve the season, **Then** the approval is rejected and the bot posts a specific diagnostic indicating which prerequisite is unmet.
4. **Given** the R&S module is enabled and all prerequisites are met, **When** an admin attempts to approve the season, **Then** the R&S prerequisite check does not block approval.
5. **Given** neither the weather module nor the R&S module is enabled, **When** an admin attempts to approve the season (other conditions met), **Then** no channel-related check blocks approval.

---

### User Story 5 — Weather module cannot be enabled on an active season with unconfigured divisions (Priority: P2)

When a season is already active, a server admin attempts to enable the weather module. If any division in the active season has no weather forecast channel, the enable must fail.

**Why this priority**: This guards against a misconfigured module silently failing to post forecasts for some divisions the moment a phase triggers.

**Independent Test**: Testable in isolation of the season approval flow: start a season with some fully configured and some unconfigured divisions, attempt to enable weather module, confirm failure with a specific diagnostic.

**Acceptance Scenarios**:

1. **Given** there is an active season and at least one division has no weather forecast channel, **When** a server admin enables the weather module, **Then** the enable fails and the bot identifies the divisions missing a weather channel.
2. **Given** there is an active season and every division has a weather forecast channel, **When** a server admin enables the weather module (other conditions met), **Then** the weather module is successfully enabled.
3. **Given** there is no active season (no season or season in SETUP), **When** a server admin enables the weather module, **Then** the weather-channel check does not apply at enable time (it is deferred to season approval).

---

### Edge Cases

- What happens when a division is added during a SETUP season after the weather module's channel prerequisite was already met for all existing divisions? The newly added division has no channels; any subsequent approval attempt must now detect the gap.
- What happens when the weather module is disabled and then re-enabled on an active season that now has all channels configured? The enable must succeed.
- What happens if a channel is assigned to a division that is not part of any season (no season exists)? This should be addressed by the implementation but is not a blocking concern for the acceptance scenarios above; behavior can be defined at planning time.
- What happens if the same channel is assigned as both results channel and standings channel for the same division? This is permitted unless the planning stage finds an explicit reason to disallow it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Results & Standings module MUST be registerable in the existing module enable/disable system, using a dedicated module identifier, per Principle X.
- **FR-002**: The R&S module MUST be disabled by default for every server.
- **FR-003**: Enabling the R&S module MUST be restricted to server administrators; it MUST fail if a season is currently in ACTIVE state.
- **FR-004**: Disabling the R&S module MUST be restricted to server administrators; it MAY be executed at any point regardless of season state.
- **FR-005**: The division-add command MUST NOT accept a weather forecast channel as a parameter. Any existing parameter for that purpose MUST be removed.
- **FR-006**: A new command MUST allow a Tier-2 admin (config authority) to assign a weather forecast channel to a named division. The command MUST accept a division name and a Discord channel reference.
- **FR-007**: A new command MUST allow a [NEEDS CLARIFICATION: NC-001] admin to assign a results channel to a named division. The command MUST accept a division name and a Discord channel reference.
- **FR-008**: A new command MUST allow a [NEEDS CLARIFICATION: NC-001] admin to assign a standings channel to a named division. The command MUST accept a division name and a Discord channel reference.
- **FR-009**: Each of the three channel assignment commands (weather, results, standings) MUST be idempotent: re-running it with a different channel replaces the previous value; re-running with the same channel is a no-op with a confirmation response.
- **FR-010**: Each channel assignment command MUST validate that the provided division name exists and that the provided channel reference is a valid Discord channel; invalid inputs MUST be rejected before any state change.
- **FR-011**: Season approval (SETUP → ACTIVE transition) MUST be blocked if the weather module is enabled and any division in the season lacks a configured weather forecast channel. The rejection message MUST name every non-compliant division.
- **FR-012**: Enabling the weather module on a server with an active season MUST be blocked if any division in that season lacks a configured weather forecast channel. The rejection message MUST name every non-compliant division.
- **FR-013**: Season approval MUST be blocked if the R&S module is enabled and the applicable prerequisite conditions are not met (exact conditions per NC-002). The rejection message MUST identify all unmet prerequisites.
- **FR-014**: The weather channel, results channel, and standings channel for each division MUST be persisted independently. Changing one MUST NOT affect the others.
- **FR-015**: All channel assignment command responses MUST be ephemeral (visible only to the invoking user), per bot behavior standards.
- **FR-016**: All module enable/disable events and all channel assignment changes MUST produce audit log entries per Principle V, recording the actor, the division (where applicable), the channel or module changed, the previous value, and the new value.

### Key Entities

- **Division channel configuration**: Per division, the weather forecast channel ID, results channel ID, and standings channel ID are stored independently. All three are nullable; absence means "not yet configured".
- **Results & Standings module state**: A per-server flag recording whether the R&S module is enabled or disabled, consistent with the module state records used by the weather and signup modules.

## Assumptions

- Tier-2 admin (config authority / season authority) is the appropriate permission level for assigning weather forecast channels, consistent with its existing use for all season-setup commands. The same level is assumed for results and standings channel assignment pending clarification of NC-001.
- Channel assignment commands may be executed at any season lifecycle state (no season, SETUP, or ACTIVE); they are not gated on season phase. This is consistent with how season amendments are permitted during ACTIVE state.
- When the R&S module is disabled, the stored results and standings channel IDs for each division are retained (not cleared). This aligns with Principle X rule 3, which states only live/scheduled artifacts are removed; configuration data is preserved for re-enablement.
- When a weather forecast channel was previously supplied as part of the division-add command, any such value already stored in the database is treated as a valid channel assignment and does not need to be manually re-entered after this change.

## Needs Clarification

### NC-001: Permission level for results and standings channel commands

The feature input describes the results channel command as "usable by trusted admins" in one sentence and "May only be used by server admins" in the next sentence of the same bullet point. The same ambiguity applies to the standings channel command.

| Option | Answer | Implications |
|--------|--------|--------------|
| A | Tier-2 admin (config authority) | Consistent with all other season-configuration commands; league managers can set channels without needing full server admin permissions |
| B | Discord server admin only | More restrictive; consistent with module enable/disable; league managers must request a server admin to configure channels |
| Custom | Provide your own answer | Specify the intended permission level |

**Your choice**: _[Awaiting response]_

---

### NC-002: Season approval gate logic for the R&S module

The feature input states that approval fails if:  
> *"the R&S module is enabled AND not all divisions have a results channel and standings configured AND there is no existing points configuration (every position gives 0 points)"*

This connects all three conditions with **AND**, which means approval is only blocked when all three are simultaneously true. That would allow approving a season with R&S enabled and channels missing, as long as at least one point-earning configuration exists — which is likely unintentional.

| Option | Answer | Implications |
|--------|--------|--------------|
| A | Block if **all three** are true simultaneously (literal reading) | Module enabled + channels missing + no points config → blocked; having either channels or a non-zero config is sufficient to proceed even if the other is absent |
| B | Block if R&S enabled **and** any division is missing results or standings channel **or** no non-zero points config exists | Stricter: both channel readiness and a meaningful points config are required before approval; approval with missing channels is never allowed |
| C | Block if R&S enabled **and** any division is missing results or standings channel (ignore points config readiness at approval time) | Channels are mandatory; points config is deferred — admins may approve and configure points before the first round |
| Custom | Provide your own answer | Describe the exact logical condition |

**Your choice**: _[Awaiting response]_

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A server admin can enable and disable the R&S module in a single command each, with no additional steps required beyond the command itself.
- **SC-002**: A division can be created without supplying any channel; all channel assignments are performed through dedicated separate commands, each completable in a single interaction.
- **SC-003**: A season with all channel prerequisites satisfied and an enabled module is approved without any channel-related diagnostic; a season with any missing prerequisite is rejected with a message that names every specific gap.
- **SC-004**: Enabling the weather module on an active season with unconfigured divisions fails with a diagnostic identifying the non-compliant divisions; no partial enablement occurs.
- **SC-005**: All channel assignment and module state changes are visible in the audit log with actor, target, previous value, and new value recorded.
