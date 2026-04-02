# Feature Specification: Results Resubmission & Weather Phase Configurability

**Feature Branch**: `029-results-weather-improvements`  
**Created**: 2026-04-02  
**Status**: Draft  
**Input**: User description: "Results: resubmit initial results hotfix button during penalty wizard with staged-penalty discard. Weather: configurable phase deadlines via new commands with ordering validation and active-season gate."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Results Hotfix Resubmission During Penalty Wizard (Priority: P1)

A tier-2 admin has just submitted a session's results. Provisional results have been posted to the division's results channel and standings have been recomputed. The penalty wizard is now open and the admin has begun staging one or more penalties. They notice a data-entry error in the original result (e.g., a driver's finishing position is wrong). They press the **"Resubmit Initial Results"** button. Any penalties they had staged but not yet committed are discarded. The admin re-enters the complete session results from scratch. Once the new results pass validation, updated provisional results are posted to the results channel with **(amended)** in the title, standings are recomputed and reposted, and the penalty wizard reopens clean against the corrected results.

**Why this priority**: A submission error discovered immediately after posting can cascade into incorrect standings and flawed penalty applications. A zero-interruption in-wizard fix is higher priority than weather configurability and prevents the need for a more disruptive post-finalization amendment.

**Independent Test**: Can be fully tested by deliberately submitting incorrect results, verifying provisional results and standings are posted, staging a penalty in the wizard, pressing "Resubmit Initial Results", entering corrected results, and confirming: (a) amended provisional results appear with "(amended)" marker, (b) standings are recalculated, (c) the previously staged penalty is absent from the record, and (d) audit log entries exist for the discard and resubmission.

**Acceptance Scenarios**:

1. **Given** provisional results have been posted and the penalty wizard is open with no staged penalties, **When** the tier-2 admin presses "Resubmit Initial Results", **Then** the system prompts for full session result re-entry.
2. **Given** the penalty wizard is open and one or more penalties have been staged (not yet committed), **When** "Resubmit Initial Results" is pressed, **Then** all staged penalties are discarded before re-entry begins and a confirmation of the discard is shown.
3. **Given** the re-entry prompt is active, **When** the admin submits valid complete results, **Then** updated provisional results are posted to the division's results channel with "(amended)" appended to the session title.
4. **Given** new provisional results have been posted, **Then** standings for the affected round and all subsequent rounds in that division are recomputed and reposted atomically.
5. **Given** the resubmission completes, **Then** the penalty wizard reopens, reflecting the new results with no staged penalties.
6. **Given** any use of the "Resubmit Initial Results" button, **Then** an audit log entry is recorded for the staged-penalty discard and another for the result replacement, including the acting admin's identity and the before/after state.
7. **Given** the re-entry prompt is active, **When** the admin submits results that fail validation (e.g., duplicate finishing positions), **Then** the submission is rejected with a specific error and the prompt remains open for correction.

---

### User Story 2 — Configure Weather Phase Deadlines (Priority: P2)

A league manager wants to adjust the weather schedule to fit their league's race week rhythm. Their races happen on Friday nights, so a 7-day Phase 1 window makes more sense than the default 5. They run `/weather config phase-1-deadline 7`. The server's Phase 1 horizon is updated and confirmed. Subsequent rounds have their Phase 1 weather job scheduled at T−7 days.

**Why this priority**: Configurable deadlines broaden the weather module's usefulness across leagues with different race cadences without requiring any changes to the core pipeline logic.

**Independent Test**: Can be fully tested independently by setting a deadline value, verifying persistence across a bot restart, and confirming the new value is used when scheduling the next round's weather phases.

**Acceptance Scenarios**:

1. **Given** the weather module is enabled and no season is currently ACTIVE, **When** a league manager runs `/weather config phase-1-deadline 7`, **Then** the server's Phase 1 deadline is updated to 7 days and a confirmation is sent.
2. **Given** the weather module is enabled and no season is currently ACTIVE, **When** a league manager runs `/weather config phase-2-deadline 3`, **Then** the server's Phase 2 deadline is updated to 3 days and a confirmation is sent.
3. **Given** the weather module is enabled and no season is currently ACTIVE, **When** a league manager runs `/weather config phase-3-deadline 4`, **Then** the server's Phase 3 deadline is updated to 4 hours and a confirmation is sent.
4. **Given** a season is currently ACTIVE, **When** any of the three phase deadline commands is run, **Then** the command is rejected with a clear error stating that deadline changes are not permitted during an active season, and no value is changed.
5. **Given** a valid deadline change is applied, **Then** an audit log entry is recorded per Principle V, including old and new values and the acting user.
6. **Given** the bot is restarted after deadline values are configured, **Then** the configured values are loaded and used for all subsequent scheduling decisions.

---

### User Story 3 — Phase Deadline Ordering Validation (Priority: P2)

A league manager accidentally attempts to set Phase 2 to 6 days while Phase 1 is still at the default 5 days. This would make Phase 2 fire before Phase 1, violating the pipeline order. The bot rejects the command with a clear explanation referencing the ordering rule and the current conflicting value.

**Why this priority**: Preventing invalid configurations at input time is essential to preserving the integrity of the three-phase weather pipeline. It accompanies User Story 2 directly.

**Independent Test**: Can be fully tested independently by attempting to set each phase's deadline to a value that violates the ordering rule (P1_days × 24 > P2_days × 24 > P3_hours) and verifying rejection with no state change.

**Acceptance Scenarios**:

1. **Given** current settings are P1=5d, P2=2d, P3=2h, **When** `/weather config phase-1-deadline 1` is run (1 × 24 = 24h, which is not greater than P2's 2 × 24 = 48h), **Then** the command is rejected with a message citing the ordering rule and the conflicting P2 value.
2. **Given** current settings are P1=5d, P2=2d, P3=2h, **When** `/weather config phase-2-deadline 6` is run (6 × 24 = 144h, which exceeds P1's 5 × 24 = 120h), **Then** the command is rejected with a message citing the ordering rule and the conflicting P1 value.
3. **Given** current settings are P1=5d, P2=2d, P3=2h, **When** `/weather config phase-3-deadline 72` is run (72h, which is not less than P2's 2 × 24 = 48h), **Then** the command is rejected with a message citing the ordering rule and the conflicting P2 value.
4. **Given** any rejected deadline change, **Then** no state change occurs and the existing deadline values remain exactly as they were.

---

### Edge Cases

- What if "Resubmit Initial Results" is pressed when no penalties have been staged? The resubmission flow begins immediately; no discard notice is required since nothing was staged.
- What if the re-entered results after resubmission are identical to the original? They are accepted as valid; the "(amended)" marker still appears to make the event auditable.
- What if Phase 1 and Phase 2 are set to the same number of days (e.g., both 2d)? This produces P1 × 24 = P2 × 24, which violates the strict greater-than rule. Both values must produce a strictly decreasing sequence. Rejected.
- What if a phase deadline configuration command is run but the weather module is not enabled? The command should be accepted or rejected following the same module-gate rule that applies to all weather commands (Principle X, rule 5).
- What if a round's weather phases have already been scheduled using the old deadline values when a new deadline is configured (deadline change before season is active)? Deadline changes only affect future round phase scheduling. Already-scheduled phase jobs for a SETUP-phase season are not retroactively rescheduled; they remain on the horizons computed at the time of scheduling.

## Requirements *(mandatory)*

### Functional Requirements

#### Results Resubmission

- **FR-001**: During an active penalty wizard session (after provisional results have been posted for a session), the submission interface MUST display a "Resubmit Initial Results" button.
- **FR-002**: When "Resubmit Initial Results" is pressed, all staged penalties that have not been committed to the record MUST be discarded atomically before re-entry begins.
- **FR-003**: The system MUST prompt the tier-2 admin to re-enter the complete session results after pressing "Resubmit Initial Results", using the same collection flow as the initial submission.
- **FR-004**: Re-entered results MUST undergo the same validation rules as the initial submission (e.g., no duplicate finishing positions, valid outcome modifiers, valid tyre and time fields per session type).
- **FR-005**: Upon successful re-entry and validation, the system MUST post updated provisional results to the division's results channel with "(amended)" appended to the session title.
- **FR-006**: Upon posting updated provisional results, the system MUST recompute and repost standings for the affected round and all subsequent rounds in that division, atomically.
- **FR-007**: The discarding of staged penalties MUST produce an audit log entry recording the acting admin, the session, and the number/identity of discarded staged penalties.
- **FR-008**: The result replacement MUST produce an audit log entry per Principle V, recording the acting admin, the session, and sufficient detail to reconstruct the change.
- **FR-009**: Re-entered results that fail validation MUST be rejected with a specific, actionable error; the re-entry prompt MUST remain open for correction.

#### Weather Phase Deadline Configuration

- **FR-010**: The system MUST provide a `/weather config phase-1-deadline <days>` command accepting a positive integer representing the number of days before a round that Phase 1 is published.
- **FR-011**: The system MUST provide a `/weather config phase-2-deadline <days>` command accepting a positive integer representing the number of days before a round that Phase 2 is published.
- **FR-012**: The system MUST provide a `/weather config phase-3-deadline <hours>` command accepting a positive integer representing the number of hours before a round that Phase 3 is published.
- **FR-013**: The default value for the Phase 1 deadline MUST be 5 (days), for Phase 2 MUST be 2 (days), and for Phase 3 MUST be 2 (hours), matching the previously hardcoded horizons.
- **FR-014**: All three deadline commands MUST be rejected with a clear, actionable error if a season is currently in the ACTIVE lifecycle state.
- **FR-015**: Before accepting any deadline update, the system MUST validate that the resulting configuration satisfies the ordering rule: (P1_days × 24) > (P2_days × 24) > P3_hours, using strict inequality at every step. A command that would violate this rule MUST be rejected before any state change occurs.
- **FR-016**: The error on an ordering violation MUST identify which current value or values conflict with the proposed change.
- **FR-017**: Accepted deadline values MUST be persisted per server and survive bot restarts.
- **FR-018**: Any successful deadline change MUST produce an audit log entry per Principle V, recording old value, new value, and acting user.
- **FR-019**: The three deadline commands MUST follow the `/domain action subcommand` convention (Principle I Bot Behavior Standards) and MUST be accessible only to league managers (tier-2 admins, Principle I).

### Key Entities *(include if feature involves data)*

- **WeatherPipelineConfig** (new per-server entity, owned by the weather module): stores `phase_1_days` (INTEGER, default 5), `phase_2_days` (INTEGER, default 2), `phase_3_hours` (INTEGER, default 2). Created with default values on first use or module enable. The three values collectively define the server's active phase horizon schedule.

## Assumptions

- Phase 2 input unit is **days**. The ordering rule `P1×24 > P2×24 > P3` — where P1 and P2 are multiplied by 24 to convert to hours and P3 is already in hours — is satisfied when P1 and P2 are in days and P3 is in hours. Confirmed by the project owner.
- "Staged penalties" are defined as penalties entered into the penalty wizard that have not yet been confirmed/committed as `PenaltyRecord` rows. Discarding staged penalties means resetting the wizard to its initial (empty) state; no `PenaltyRecord` rows exist for them.
- The "Resubmit Initial Results" button is available throughout the penalty wizard phase, from when the wizard opens until the tier-2 admin finalises the penalty review. It is not available during the appeals wizard (a distinct subsequent phase).
- Deadline changes made while a season is in `SETUP` (not yet ACTIVE) take effect immediately. Any round phase jobs already scheduled in a SETUP season keep their previously computed horizons; the new deadline applies to rounds whose phase jobs have not yet been scheduled.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A tier-2 admin can complete a results resubmission entirely within the active penalty wizard session, with no bot restart or separate amendment command required.
- **SC-002**: After resubmission, amended provisional results visible in the results channel carry the "(amended)" marker, making them unambiguously distinguishable from the original post.
- **SC-003**: All staged (uncommitted) penalties are cleared before re-entry begins; zero staged-penalty data survives the resubmission event in the final record.
- **SC-004**: Standings visible in the standings channel after a resubmission reflect the corrected results, not the original.
- **SC-005**: A league manager can set any valid phase deadline to a new value and receive confirmation within a single command interaction.
- **SC-006**: Every invalid phase deadline attempt (ordering violation or active-season gate) is rejected before any state change occurs, with a clear error that identifies the specific constraint breached.
- **SC-007**: Configured phase deadlines are used for all subsequent round weather scheduling after the change, with no manual restart required.
- **SC-008**: All actions covered by this feature (resubmission, staged-penalty discard, deadline changes) produce traceable audit log entries viewable by league managers after the fact.
