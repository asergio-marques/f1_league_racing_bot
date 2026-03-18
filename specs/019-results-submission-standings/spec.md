# Feature Specification: Results & Standings — Points Config, Submission, and Standings

**Feature Branch**: `019-results-submission-standings`  
**Created**: 2026-03-18  
**Status**: Draft  
**Input**: Results and standings module core: points configuration store management, season config attachment, round result submission wizard, standings computation and posting, mid-season scoring amendment flow, and result penalty and amendment wizards.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Trusted admin configures points and attaches configs to a season (Priority: P1)

A trusted admin wants to define how many points each finishing position earns in each session type. They create one or more named configurations in the server's points store, set per-position points and optional fastest-lap bonuses, then attach those configurations to the current season while it is being set up. On season approval the configurations are locked into the season independently of the server store.

**Why this priority**: Without at least one points configuration attached and approved, the entire results module cannot operate — no points can be awarded in any submitted session. This is the configuration prerequisite for all downstream functionality.

**Independent Test**: Create a config, set points for several positions across session types, attach it to a season in SETUP, invoke season review, and approve. Confirm the config is listed and locked into the season. Modify the server store config post-approval and confirm the season's config is unaffected.

**Acceptance Scenarios**:

1. **Given** the R&S module is enabled, **When** a trusted admin creates a named configuration (e.g., "100%") in the server points store, **Then** the configuration exists in the server store with all positions defaulting to 0 points.
2. **Given** a configuration exists, **When** a trusted admin sets position 1 to 25 points for the Feature Race session type, **Then** that specific position-session entry is updated; all other entries remain unchanged.
3. **Given** a configuration exists, **When** a trusted admin sets fastest-lap bonus points and position limit for a race session type, **Then** those values are stored; attempting to set FL bonus for a qualifying session type is rejected with a clear error.
4. **Given** a season is in SETUP, **When** a trusted admin attaches a configuration to the season, **Then** it appears in the season's attached config list and is listed during season review.
5. **Given** a season is in SETUP with a config attached, **When** the admin approves the season, **Then** the config entries are snapshotted into the season's own points store; subsequent changes to the server store config do not affect the season store.
6. **Given** an active (ACTIVE-state) season, **When** a trusted admin attempts to attach or detach a configuration, **Then** the command fails with a clear error.
7. **Given** a config attached to a season in SETUP has session entries where position 3 earns more than position 2, **When** the admin attempts to approve the season, **Then** approval is blocked and the bot identifies the specific config, session type, and violating positions.

---

### User Story 2 — Trusted admin submits round results via the submission channel (Priority: P1)

At the scheduled time of a round, a transient submission channel appears in the division's channel area. A trusted admin enters the results for each applicable session in sequence (qualifying then race), validates them, and selects a points configuration for each session. The channel closes once all sessions are submitted.

**Why this priority**: Result submission is the core runtime action of the module. Without it, no results exist and no standings can be computed. Every other feature in this increment depends on results being submitted.

**Independent Test**: With an active season and a scheduled round, confirm a submission channel appears at round time. Submit valid qualifying and race results, confirm each is accepted and a config button is shown, and confirm the channel closes after the final session.

**Acceptance Scenarios**:

1. **Given** a round is active and the R&S module is enabled, **When** the round's scheduled time arrives, **Then** the bot creates a submission channel adjacent to the division's results channel and notifies trusted admins.
2. **Given** the submission channel is open, **When** a trusted admin submits qualifying results in the correct format (Position, Driver tag, Team role, Tyre, Best Lap, Gap per line), **Then** the results are accepted, the bot presents one button per attached seasonal config for the admin to choose, and collection advances to the next session.
3. **Given** the submission channel is open, **When** a trusted admin submits race results in the correct format (Position, Driver tag, Team role, Total Time, Fastest Lap, Time Penalties per line), **Then** the results are accepted, the bot presents config buttons, and collection advances.
4. **Given** a submitted result has invalid content (non-sequential positions, a driver not assigned to the division, a role not matching a team, or a malformed time), **When** the bot evaluates it, **Then** the bot rejects the input with a specific explanation and re-requests the session's results.
5. **Given** a trusted admin enters "CANCELLED" for a session, **Then** that session is recorded as cancelled with no driver entries and no config selection, and collection advances to the next session.
6. **Given** a round cancel command is issued after the submission channel has already been opened, **Then** the cancel command is rejected with a clear error stating the reason.
7. **Given** all sessions in a round have been submitted or cancelled, **Then** the submission channel closes and no further input is accepted.
8. **Given** the round type is Normal or Endurance (no sprint sessions), **Then** the bot collects only Feature Qualifying and Feature Race results; Sprint Qualifying and Sprint Race are skipped entirely.

---

### User Story 3 — Bot posts formatted results and standings after round completion (Priority: P1)

Once all sessions in a round are submitted (or cancelled), the bot posts a formatted results table for each non-cancelled session to the division's results channel, then computes and posts updated driver and team standings to the division's standings channel.

**Why this priority**: The posted output is the primary user-visible value of the module. It must match the submitted data precisely, apply the chosen points configuration correctly, and reflect standings accurately. This closes the submission loop.

**Independent Test**: Submit a full round's results. Confirm a formatted table appears in the results channel for each non-cancelled session. Confirm a standings post appears in the standings channel with correct points totals and ranking order. Amend a result and confirm both channels are updated.

**Acceptance Scenarios**:

1. **Given** a qualifying session is submitted, **When** all round sessions are complete, **Then** the results channel receives a table with columns: Position, Driver name, Team, Tyre, Best Lap, Gap to 1st, Points Gained.
2. **Given** a race session is submitted, **When** all round sessions are complete, **Then** the results channel receives a table with columns: Position, Driver name, Team, Total Time, Fastest Lap, Time Penalties, Points Gained.
3. **Given** a driver finishes with outcome CLASSIFIED and has the lowest lap time in the session, **When** their finishing position is at or above the config's fastest-lap position limit, **Then** their Points Gained includes the fastest-lap bonus.
4. **Given** a driver finishes with outcome DNF and has the lowest lap time, **When** their finishing position is at or above the config's position limit, **Then** they are ineligible for finishing-position points but are still eligible for the fastest-lap bonus.
5. **Given** a driver finishes with outcome DNS or DSQ, **Then** their Points Gained is 0 and they are ineligible for the fastest-lap bonus regardless of their lap time.
6. **Given** results are posted for a round, **When** standings are computed, **Then** the standings channel post shows drivers ranked first by total points, with Feature Race finish counts used as tiebreakers in descending position order, and the earliest round of the highest diverging position used as the final tiebreaker.
7. **Given** a division has the reserves visibility toggle turned off, **When** standings are posted, **Then** reserve-team drivers are excluded from the public standings post but their points are still computed and stored internally.
8. **Given** a driver participates in two divisions, **When** standings are computed, **Then** their points in Division A are not affected by their results in Division B.

---

### User Story 4 — Trusted admin views the season's points configuration (Priority: P2)

A trusted admin wants to review the exact points table currently applied to the active season. They invoke a view command with a configuration name and optionally a session type. The bot replies with the full points breakdown, collapsing any trailing zeros into a single "xth+" entry.

**Why this priority**: Visibility into the exact applied config is necessary for admins to verify correctness before and after submission, and to explain scoring to drivers. It is needed before amendment workflows can be verified.

**Independent Test**: With an active season with two configs, invoke the view command for one config with no session filter. Confirm all four session types are shown, with trailing zero positions collapsed. Re-invoke with a specific session filter. Confirm only that session's table is shown.

**Acceptance Scenarios**:

1. **Given** an active season with a config named "100%", **When** a trusted admin runs the config view command with name "100%" and no session filter, **Then** the bot posts the full points breakdown for all session types in that config.
2. **Given** a config view is requested for a specific session type, **Then** only that session's table is shown.
3. **Given** a session's points table has: 1st = 25, 2nd = 18, 3rd+ = 0 (positions 3 onward all zero), **When** the table is posted, **Then** it shows "1st: 25, 2nd: 18, 3rd+: 0" rather than listing every position individually.
4. **Given** no active or SETUP season exists, **When** the view command is invoked, **Then** the bot responds with a clear error that no season is active.

---

### User Story 5 — Trusted admin applies post-race penalties or disqualifications (Priority: P2)

After a round's results are posted, a trusted admin discovers that one or more drivers should receive a time penalty or disqualification. They invoke the penalty wizard, select the session and drivers, specify the penalties, review the list, and approve. The bot recalculates affected positions and reposts results and standings from that round onwards.

**Why this priority**: Post-race steward decisions are a normal part of league racing. The penalty wizard provides a structured path to apply corrections without requiring a full re-submission.

**Independent Test**: Submit a round with valid results. Invoke the penalty wizard, apply a +5s penalty to the 1st-place driver and a DSQ to the 3rd-place driver, proceed through review, approve. Confirm the results channel and standings channel are updated with corrected positions and points.

**Acceptance Scenarios**:

1. **Given** a completed round, **When** a trusted admin invokes the penalty wizard, **Then** the bot presents session buttons, a cancel button, and (once penalties are staged) a review button.
2. **Given** a race session is selected in the wizard, **When** a valid time penalty in seconds is entered for a driver, **Then** that penalty is staged; the bot requests the next driver ID.
3. **Given** a qualifying session is selected in the wizard, **When** a trusted admin tries to enter a time penalty, **Then** the bot rejects it and informs them only DSQ is accepted for qualifying.
4. **Given** a DSQ is applied to a driver, **When** approved, **Then** that driver is moved to the bottom of the session results and their points set to 0.
5. **Given** the review step is reached, **When** the admin presses "Make changes", **Then** the wizard returns to the session selection state with all staged penalties preserved.
6. **Given** penalties are approved, **Then** results and standings for the affected round and all subsequent rounds in the division are recomputed and reposted; an audit log entry is produced.

---

### User Story 6 — Trusted admin fully amends a session's results (Priority: P2)

A trusted admin discovers that an entire session's results were submitted incorrectly and need to be re-entered. They invoke the amendment command for a specific round (and optionally a specific session), re-submit results in the same format as first submission, and the bot recomputes standings from that round onwards.

**Why this priority**: Full re-submission covers corrections that are too extensive for the penalty wizard (e.g., wrong drivers, wrong positions throughout).

**Independent Test**: Submit a round. Invoke the amendment command targeting a specific session. Re-enter corrected results. Confirm the session result is replaced and standings are updated from that round onwards.

**Acceptance Scenarios**:

1. **Given** a completed round, **When** a trusted admin invokes the amendment command with division and round number only, **Then** the bot presents buttons for each session in that round.
2. **Given** a session is selected (or specified as a parameter), **When** the trusted admin submits corrected results in the valid format, **Then** the previous session result is superseded, standings from that round onwards are recomputed, and results and standings channels are updated.
3. **Given** the re-submitted results fail format or content validation, **Then** the bot rejects them with specific guidance and re-requests.
4. **Given** the amendment is applied, **Then** an audit log entry is produced identifying the actor, season, division, and round.

---

### User Story 7 — Server admin amends the points system mid-season (Priority: P2)

The league decides to change the points system partway through a season. A server admin enables amendment mode, trusted admins modify configurations in the modification store, the server admin reviews the diff and approves. The bot overwrites the season points store and reposts all affected results and standings.

**Why this priority**: Mid-season scoring amendments are infrequent but consequential when they occur. The modification store workflow ensures changes are reviewed before being applied irreversibly.

**Independent Test**: Enable amendment mode. Modify Feature Race points for config "100%". Confirm modified flag is set and toggle-off is blocked. Invoke revert, confirm modified flag is cleared. Re-modify and invoke amendment review. Approve. Confirm season store is updated and all results/standings across all divisions are reposted.

**Acceptance Scenarios**:

1. **Given** an active season, **When** a server admin toggles amendment mode on, **Then** the season's current points store is copied into the modification store; the modified flag is false.
2. **Given** amendment mode is on, **When** a trusted admin modifies a session's points in the modification store, **Then** the modified flag is set to true.
3. **Given** the modified flag is true, **When** a server admin attempts to toggle amendment mode off, **Then** the command fails with a clear error.
4. **Given** the modified flag is true, **When** a trusted admin invokes revert, **Then** the modification store is overwritten by the current season store, the modified flag is cleared, and amendment mode remains on.
5. **Given** amendment mode is on, **When** a server admin invokes amendment review, **Then** the bot displays the modification store's contents alongside Approve and Reject buttons.
6. **Given** the admin approves, **Then** the season points store is atomically overwritten by the modification store; all results and standings for all divisions are recomputed and reposted; the modified flag is cleared and the modification store is purged.
7. **Given** the admin rejects, **Then** no changes are made; the modification store and amendment mode remain active.

---

### User Story 8 — Trusted admin toggles reserve driver visibility in standings (Priority: P3)

A trusted admin wants to hide reserve drivers from the publicly posted standings for a division without affecting their point accrual or internal records.

**Why this priority**: Reserve driver visibility is a display preference with no bearing on correctness. It can be deferred without blocking any other functionality.

**Independent Test**: Toggle off reserve visibility for a division. Submit a round with a reserve driver who scores points. Confirm the standings post excludes that driver. Toggle on and confirm the driver appears.

**Acceptance Scenarios**:

1. **Given** a division has the reserves visibility toggle set to on (default), **When** a reserve driver scores points, **Then** they appear in the standings post with their correct position.
2. **Given** a trusted admin toggles reserves visibility off for a division, **When** standings are next posted, **Then** reserve drivers are excluded from the post but their points are computed and stored.
3. **Given** a reserve driver is later assigned to a configurable team, **Then** all previously accrued points remain attributed to that driver and are reflected in standings when they next appear.

---

### Edge Cases

- What happens when no non-cancelled sessions exist in a round (all sessions submitted as CANCELLED)? The bot must still close the submission channel; no results or standings post is made for that round, but a standings snapshot is still persisted showing no change.
- What happens if the only driver to score points in a race is on a DSQ? Total points for the round are zero; standings are unchanged for that round.
- A driver who has participated in previous rounds but does not appear in the current round's results still retains their accumulated standings snapshot from the previous round.
- Two rounds across different divisions scheduled simultaneously each get their own independent submission channel without interference.
- A driver tagged in a session who belongs to the reserve team: valid if the division has a reserve team; their points accrue to whatever team they drove for in that session.

## Requirements *(mandatory)*

### Functional Requirements

**Points Configuration Management**

- **FR-001**: The system MUST allow trusted admins to create named points configurations in the server-level configuration store; names serve as unique identifiers per server.
- **FR-002**: The system MUST allow trusted admins to remove named configurations from the server-level store; removal does not automatically detach them from a season in SETUP.
- **FR-003**: The system MUST allow trusted admins to set the points awarded for a specific finishing position within a specific session type (Sprint Qualifying, Sprint Race, Feature Qualifying, Feature Race) of a named server-level configuration; unset positions default to 0.
- **FR-004**: The system MUST allow trusted admins to set a fastest-lap bonus and a position eligibility limit for race session types (Sprint Race, Feature Race) within a server-level configuration; attempting to configure fastest-lap for qualifying session types MUST be rejected.
- **FR-005**: The system MUST allow trusted admins to attach named configurations from the server store to a season in SETUP; attachment is rejected if no season is in SETUP or if a season is already ACTIVE.
- **FR-006**: The system MUST allow trusted admins to detach named configurations from a season in SETUP under the same conditions as attachment.
- **FR-007**: On season approval, attached configurations MUST be snapshotted (copied) into the season's own points store; from that point on, changes to the server store do not affect the season store.
- **FR-008**: At season approval, the system MUST verify that within each attached configuration and session type, points are monotonically non-increasing with finishing position; any violation MUST block approval with a diagnostic identifying the config, session type, and violating positions.
- **FR-009**: The season review display MUST list all configurations attached to the season by name.

**Round Result Submission**

- **FR-010**: At each round's scheduled start time, the system MUST create a transient submission channel adjacent to the configured results channel for that division and notify trusted admins.
- **FR-011**: Session results MUST be collected sequentially: Sprint Qualifying → Sprint Race → Feature Qualifying → Feature Race; sprint sessions (Sprint Qualifying, Sprint Race) MUST be omitted for Normal and Endurance rounds.
- **FR-012**: For qualifying sessions, the system MUST accept inputs in the format: Position, Driver (Discord mention), Team (Discord role mention), Tyre, Best Lap, Gap per line.
- **FR-013**: For race sessions, the system MUST accept inputs in the format: Position, Driver (Discord mention), Team (Discord role mention), Total Time, Fastest Lap, Time Penalties per line.
- **FR-014**: Qualifying inputs MUST be validated against: sequential positions with no gaps, all drivers assigned to the division, all team roles valid, each driver assigned to their stated team or the reserve team, Best Lap in a valid time format or DNS/DNF/DSQ, Gap in a valid delta format or N/A.
- **FR-015**: Race inputs MUST be validated against: sequential positions with no gaps, all drivers assigned to the division, all team roles valid, each driver assigned to their stated team or the reserve team, 1st-place Total Time in an absolute time format, other positions in an absolute or delta format or DNS/DNF/DSQ or lap-gap format, Time Penalties in a valid time format.
- **FR-016**: Invalid submissions MUST be rejected with specific feedback and the session MUST be re-requested.
- **FR-017**: Any session MAY be submitted as "CANCELLED"; cancelled sessions persist no driver entries and no config selection.
- **FR-018**: After each session's results are accepted, the system MUST present one button per attached seasonal configuration for the trusted admin to select; this selection MUST be persisted with the session.
- **FR-019**: All raw result inputs MUST be logged to the server's configured log channel, including season number, division, and round number.
- **FR-020**: A round cancel command MUST be rejected if the submission channel for that round is already open.

**Points Eligibility**

- **FR-021**: A driver with outcome CLASSIFIED is eligible for finishing-position points and (if within the position limit) the fastest-lap bonus.
- **FR-022**: A driver with outcome DNF is ineligible for finishing-position points but remains eligible for the fastest-lap bonus if their finishing position is at or above the configured position limit.
- **FR-023**: Drivers with outcome DNS or DSQ are ineligible for all point types (finishing-position and fastest-lap bonus).

**Results and Standings Output**

- **FR-024**: After all sessions in a round are submitted (or cancelled), the system MUST post formatted results per non-cancelled session to the division's configured results channel.
- **FR-025**: Qualifying session results MUST be posted with columns: Position, Driver display name, Team, Tyre, Best Lap, Gap to 1st, Points Gained.
- **FR-026**: Race session results MUST be posted with columns: Position, Driver display name, Team, Total Time, Fastest Lap, Time Penalties, Points Gained.
- **FR-027**: After results are posted, the system MUST compute and post updated driver and team standings to the division's configured standings channel.
- **FR-028**: Driver standings MUST rank by: (1) total points descending, (2) Feature Race win count descending, (3) Feature Race 2nd-place count descending, continuing through all finish positions; if still tied, the driver who first achieved the highest diverging finish position wins.
- **FR-029**: Team standings MUST apply the same ranking hierarchy to the aggregate Feature Race finishes and points of all drivers who scored under each team's banner in each individual session.
- **FR-030**: A standings snapshot (total points, standing position, per-position finish counts, first-round number per position) MUST be persisted per driver and per team per round.
- **FR-031**: Reserve driver visibility in posted driver standings MUST be controlled by a per-division toggle (default: visible); reserve drivers always appear in internal snapshots regardless of toggle state.

**Result Amendment and Penalties**

- **FR-032**: Trusted admins MUST be able to fully re-submit the results of any session in a completed round via an amendment command; the previous session result is superseded and standings from that round onwards are recomputed and reposted.
- **FR-033**: Trusted admins MUST be able to apply post-race time penalties or disqualifications per driver via a guided penalty wizard; for qualifying sessions only disqualification is accepted (no time penalties).
- **FR-034**: On penalty approval, finishing positions, gap to leader, and standings from the affected round onwards MUST be recomputed and reposted.
- **FR-035**: Every result amendment and penalty application MUST produce an audit log entry (actor, season, division, round, session, change).

**Mid-Season Points Amendment**

- **FR-036**: Server admins MUST be able to enable amendment mode; on enablement, the season's current points store is copied into a modification store.
- **FR-037**: Trusted admins MUST be able to modify session points, fastest-lap bonuses, and position limits within the modification store; each successful modification sets the modified flag to true.
- **FR-038**: Server admins MUST NOT be able to disable amendment mode while the modified flag is true.
- **FR-039**: Trusted admins MUST be able to revert the modification store to the current season store, clearing the modified flag.
- **FR-040**: Server admins MUST be able to review the modification store and approve or reject it; on approval, the season points store is atomically overwritten, the modified flag is cleared, the modification store is purged, and all results and standings across all divisions are recomputed and reposted.

**Config Visibility**

- **FR-041**: Trusted admins MUST be able to view the points configurations applied to the current season by name, with an optional session type filter; trailing zero-point positions MUST be collapsed to a single "xth+" entry.

### Key Entities

- **PointsConfigStore**: A named server-level configuration record (name is the identifier per server). Contains zero or more PointsConfigEntry and PointsConfigFastestLap records.
- **PointsConfigEntry**: Points awarded for one finishing position in one session type within one named server config; defaults to 0.
- **PointsConfigFastestLap**: Fastest-lap bonus and optional position eligibility limit for one race session type within one named server config.
- **SeasonPointsLink**: Weak attachment record linking a server-level config name to a season in SETUP; discarded on season approval after the snapshot is created.
- **SeasonPointsStore**: Immutable snapshot of config entries scoped to an approved season; overwritten atomically and completely on mid-season amendment approval.
- **SeasonAmendmentState**: Per-server record tracking whether amendment mode is active and whether uncommitted changes exist (modified flag).
- **SeasonModificationStore**: Working copy of the season points store, existing only while amendment mode is active; discarded on revert or approval.
- **SessionResult**: Top-level result record per session per round per division — holds session type, status (ACTIVE/CANCELLED), applied config name, submitter, and submission timestamp.
- **DriverSessionResult**: Per-driver row within a SessionResult — holds finishing position, outcome modifier, session-type-specific time fields, computed points awarded, fastest-lap flag, and a supersession flag for amendment tracking.
- **DriverStandingsSnapshot**: Standings state per driver per round per division — holds standing position, total points, JSON finish-count map, and JSON first-round-per-position map.
- **TeamStandingsSnapshot**: Same structure as DriverStandingsSnapshot, aggregated per team.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A trusted admin can create a points configuration, set all desired position values, and attach it to a season without consulting documentation — all commands accept inputs and confirm changes within a single interaction each.
- **SC-002**: Round result submission completes in one contiguous channel session; trusted admins are not required to leave the channel or use external tools to determine the expected format.
- **SC-003**: Formatted results and updated standings appear in the correct division channels within seconds of the final session in a round being accepted.
- **SC-004**: Standings remain accurate (correct ranking, correct points totals) following any result amendment, penalty application, or mid-season scoring change, with no manual re-trigger required.
- **SC-005**: Every result input and scoring mutation produces an audit log entry that uniquely identifies season, division, round, and acting user; entries are searchable by those identifiers in the log channel.
- **SC-006**: Historical standings at any completed round are recoverable from stored snapshots without recomputing from raw results.
- **SC-007**: A mid-season points amendment that changes scoring can be fully reviewed and approved (or reverted) by server admins without any data loss or partial-state risk.

## Assumptions

- The implementation uses the same technology stack already in use (Python, discord.py, SQLite via aiosqlite, APScheduler). No technology changes are required or introduced.
- The "results config view" command operates on the season's points store (for ACTIVE seasons) or on the attached server-level configs (for seasons in SETUP).
- **Scoring rule correction**: The source document [results_module_specification.md](../../results_module_specification.md) groups DNF with DNS/DSQ as fully ineligible for points. This specification adopts the authoritative rule from the project constitution: DNF drivers are ineligible for finishing-position points but remain eligible for the fastest-lap bonus if their finishing position is at or above the configured limit. DNS and DSQ drivers are ineligible for all point types.
- **Results output column correction**: The source document inadvertently swaps the output columns for qualifying and race sessions. This specification uses the logically correct mapping: qualifying output uses Tyre/Best Lap/Gap; race output uses Total Time/Fastest Lap/Time Penalties. These map directly to the respective submission input formats.
- When all sessions in a round are cancelled, the submission channel closes, no results post is made, and standings remain unchanged (no new snapshot is written for that round).
- The transient submission channel is deleted once all sessions are submitted or after a round is cancelled (where the cancel succeeds). Its category matches the division's results channel category. Its name follows the existing bot channel-naming conventions.
- In the penalty wizard, if the same driver receives both a time penalty and a DSQ in the same session, only the DSQ is applied (it supersedes any time adjustment).
- "Adjacent channel" in the submission context means the channel is created within the same category as the division's results channel, not necessarily at the adjacent position in the Discord channel list.


**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]
2. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

### User Story 2 - [Brief Title] (Priority: P2)

[Describe this user journey in plain language]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

### User Story 3 - [Brief Title] (Priority: P3)

[Describe this user journey in plain language]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

[Add more user stories as needed, each with an assigned priority]

### Edge Cases

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right edge cases.
-->

- What happens when [boundary condition]?
- How does system handle [error scenario]?

## Requirements *(mandatory)*

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right functional requirements.
-->

### Functional Requirements

- **FR-001**: System MUST [specific capability, e.g., "allow users to create accounts"]
- **FR-002**: System MUST [specific capability, e.g., "validate email addresses"]  
- **FR-003**: Users MUST be able to [key interaction, e.g., "reset their password"]
- **FR-004**: System MUST [data requirement, e.g., "persist user preferences"]
- **FR-005**: System MUST [behavior, e.g., "log all security events"]

*Example of marking unclear requirements:*

- **FR-006**: System MUST authenticate users via [NEEDS CLARIFICATION: auth method not specified - email/password, SSO, OAuth?]
- **FR-007**: System MUST retain user data for [NEEDS CLARIFICATION: retention period not specified]

### Key Entities *(include if feature involves data)*

- **[Entity 1]**: [What it represents, key attributes without implementation]
- **[Entity 2]**: [What it represents, relationships to other entities]

## Success Criteria *(mandatory)*

<!--
  ACTION REQUIRED: Define measurable success criteria.
  These must be technology-agnostic and measurable.
-->

### Measurable Outcomes

- **SC-001**: [Measurable metric, e.g., "Users can complete account creation in under 2 minutes"]
- **SC-002**: [Measurable metric, e.g., "System handles 1000 concurrent users without degradation"]
- **SC-003**: [User satisfaction metric, e.g., "90% of users successfully complete primary task on first attempt"]
- **SC-004**: [Business metric, e.g., "Reduce support tickets related to [X] by 50%"]
