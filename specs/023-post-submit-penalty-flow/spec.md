# Feature Specification: Results & Standings — Inline Post-Submission Penalty Review

**Feature Branch**: `023-post-submit-penalty-flow`  
**Created**: 2026-03-25  
**Status**: Draft  
**Input**: Testing revealed that the standalone `round results penalize` command is impractical. After all sessions in a round are submitted the transient submission channel should transition to an inline penalty review state rather than closing. Penalties (time or DSQ) are entered and approved there before the round is finalized. Time penalties may be positive or negative. In test mode, advancing to the next scheduled event is blocked until the current round is finalized.

---

## Context & Scope

This specification replaces the design of the standalone `round results penalize` command (User Story 5 of spec 019) with an inline penalty review state that is part of the submission wizard itself.

Key behavioural changes:

1. The `round results penalize` standalone command is removed.
2. After the final session of a round is submitted, the submission channel does **not** close; it transitions to a **Post-Round Penalties** state.
3. Trusted admins apply penalties (or confirm no penalties) within the same channel before finalizing.
4. Time penalties are **signed** — positive values add time, negative values subtract time. Both are valid.
5. The round reaches a **FINALIZED** state only after the penalty review is approved.
6. On finalization the bots posts **final** results and standings, replacing the **interim** posts that were created at the end of session submission.
7. In test mode, advancing to the next scheduled event is blocked while any round in the division is not in FINALIZED state.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Submission channel transitions to Post-Round Penalties state after all sessions are submitted (Priority: P1)

After a trusted admin submits or cancels the final session of a round, the bot posts interim results and standings as it did before, but the transient submission channel does not close. Instead it transitions to a **Post-Round Penalties** state and the bot presents a penalty entry interface listing all drivers across every non-cancelled session.

**Why this priority**: This transition is the entry point for the entire new flow. Without it, the penalty state cannot be reached, the round cannot be finalized, and all downstream behaviour is blocked.

**Independent Test**: Submit all sessions in a round. Confirm the submission channel remains open, interim results and standings posts appear in their respective channels, and the penalty entry interface appears in the submission channel.

**Acceptance Scenarios**:

1. **Given** all sessions in a round have been submitted or marked CANCELLED, **When** the final session is processed, **Then** the bot posts an interim results table for each non-cancelled session to the division's results channel and an interim standings post to the standings channel, and the submission channel remains open.
2. **Given** the submission channel remains open after the final session, **When** the penalty state is entered, **Then** the bot posts a penalty entry prompt in the submission channel showing all drivers from non-cancelled sessions alongside their current positions, a list of staged penalties (initially empty), controls to add a penalty, and controls to confirm no penalties.
3. **Given** the penalty state is active, **When** any user who is not a trusted admin attempts to interact with the penalty interface, **Then** the bot ignores or rejects the interaction with a permissions error.
4. **Given** the penalty state is active, **When** a trusted admin attempts to submit new session result text in the submission channel, **Then** the bot rejects the input with a clear message that the round is in the penalty review state.
5. **Given** all sessions in a round are marked CANCELLED (no driver results at all), **When** the penalty state is entered, **Then** the bot presents only the "No penalties / confirm" option; no driver entry or add-penalty controls are shown.

---

### User Story 2 — Trusted admin enters, reviews, and adjusts staged penalties (Priority: P1)

Within the penalty state a trusted admin may stage one or more penalties against specific driver–session pairs. Time penalties (positive to add time, negative to subtract time) are accepted for race sessions; DSQ is accepted for any session type. The admin can remove individual staged penalties before proceeding to the review step. When satisfied (or choosing no penalties) they advance to confirmation.

**Why this priority**: This is the direct functional replacement for the removed `round results penalize` command. It must cover all penalty types the old command supported, plus the new signed time-penalty behaviour.

**Independent Test**: With a round in penalty state, stage a +5 s time penalty on the race session leader, a −3 s time penalty on another race driver, and a DSQ on a qualifying driver. Verify all three appear in the staged list. Remove the DSQ. Advance to the approval step and verify only the two time penalties remain.

**Acceptance Scenarios**:

1. **Given** the penalty state is active and a race session is selected, **When** a trusted admin enters a driver ID and a time value in seconds (positive or negative integer), **Then** that signed time penalty is staged for that driver–session pair and the bot displays the updated staged list.
2. **Given** a time value of zero is entered, **Then** the bot rejects it with a clear error stating that a zero-second penalty has no effect and must not be staged.
3. **Given** the penalty state is active, **When** a trusted admin applies a DSQ to a driver in any session type, **Then** a DSQ penalty entry is staged for that driver–session pair.
4. **Given** a trusted admin attempts to enter a time penalty for a driver in a qualifying session, **Then** the bot rejects the input and informs them that only DSQ penalties are accepted for qualifying sessions.
5. **Given** the staged list contains one or more entries, **When** a trusted admin removes a specific staged penalty by its list index or identifier, **Then** only that entry is removed; all other staged entries remain unchanged.
6. **Given** no penalties have been staged, **When** a trusted admin selects "No penalties", **Then** the wizard advances directly to the approval step presenting an empty penalty list.
7. **Given** one or more penalties are staged, **When** a trusted admin selects "No penalties", **Then** the bot requests explicit confirmation before clearing the staged list; if confirmed, the list is cleared and the wizard advances to the approval step with an empty list.
8. **Given** the review step is displayed, **When** the trusted admin selects "Make changes", **Then** the wizard returns to the penalty entry state and all previously staged penalties are restored exactly as they were.

---

### User Story 3 — Trusted admin approves penalty review; round is finalized and final posts replace interim posts (Priority: P1)

A trusted admin that has reviewed the penalty list (empty or non-empty) presses approve. The bot applies penalties to the relevant session results, recomputes positions and points, and posts final results and standings to the respective channels — deleting the interim posts. The round state moves to FINALIZED and the submission channel closes.

**Why this priority**: Finalization is the terminal action that closes the round lifecycle and produces the authoritative, permanent record visible to all division participants.

**Independent Test**: Submit a round, stage a +5 s penalty on the 1st-place race driver, and approve. Confirm the interim results post is deleted and replaced by a final post showing the corrected position order. Confirm the standings post is updated. Confirm the round is FINALIZED and the submission channel is closed.

**Acceptance Scenarios**:

1. **Given** a non-empty staged penalty list at the approval step, **When** the admin approves, **Then** every staged penalty is applied to the corresponding driver–session result, final positions and points for those sessions are recomputed, and final results tables are posted to the results channel.
2. **Given** an empty staged penalty list at the approval step, **When** the admin approves, **Then** final results tables identical to the interim tables are posted to the results channel and no positions or points change.
3. **Given** final results are posted, **Then** the corresponding interim results posts and the interim standings post are deleted from their respective channels, leaving only the new final posts as the canonical record.
4. **Given** a positive time penalty is applied to a race driver, **When** final positions are computed, **Then** the penalty value is added to their total race time; their position is recalculated relative to all other drivers; points are reassigned accordingly.
5. **Given** a negative time penalty is applied to a race driver, **When** final positions are computed, **Then** the penalty value is subtracted from their total race time; if the adjusted time is less than the driver ahead of them, the penalized driver's position moves up; points are reassigned accordingly.
6. **Given** a DSQ penalty is applied to a driver, **When** final results are computed, **Then** that driver is moved to the bottom of their session results, their points set to 0, and they are ineligible for the fastest-lap bonus.
7. **Given** a DSQ is applied to the driver who held the fastest lap, **When** final results are computed, **Then** the fastest-lap bonus is not redistributed to any other driver; the bonus is forfeited for that session.
8. **Given** all penalties are applied and final results are posted, **When** finalization completes, **Then** the submission channel is closed, the round state is set to FINALIZED, and an audit log entry is produced recording the actor, division, round number, and full list of applied penalties (including the empty case).
9. **Given** the round is FINALIZED and subsequent rounds in the division already have standings computed, **When** the bot updates standings after finalization, **Then** standings for all rounds from the finalized round onwards are recomputed and their posts are updated.

---

### User Story 4 — Test mode blocks advancing to next scheduled event until the current round is finalized (Priority: P2)

In test mode, once the current round's submission channel has opened, any attempt to advance to the next scheduled event is blocked unless the current round is in FINALIZED state. The bot returns a clear, actionable error identifying the division and round that must be finalized first.

**Why this priority**: Test mode is the primary way to validate the complete round lifecycle. The gate ensures no round is silently left in penalty-pending state while the next event is already being processed.

**Independent Test**: In test mode, submit a round but do not approve the penalty state. Attempt to advance to the next event. Confirm the bot refuses with an error naming the pending round. Approve the penalty state. Retry the advance command. Confirm it succeeds.

**Acceptance Scenarios**:

1. **Given** test mode is active and a round is in the Post-Round Penalties state (not yet FINALIZED), **When** a trusted admin attempts to advance to the next scheduled event, **Then** the bot blocks the action and returns an error identifying the division and round number that must be finalized first.
2. **Given** test mode is active and the current round is in FINALIZED state, **When** a trusted admin advances to the next scheduled event, **Then** the advancement proceeds normally with no penalty-state block.
3. **Given** test mode is active and multiple divisions each have an open round, **When** any of those rounds are not in FINALIZED state, **Then** the advancement command for the affected divisions is blocked independently per division.
4. **Given** the bot is not running in test mode, **When** any round is in the Post-Round Penalties state and a trusted admin issues the advance command, **Then** the FINALIZED-state gate does not apply and the command is not blocked by this rule.

---

### User Story 5 — Standalone `round results penalize` command is removed (Priority: P2)

The previously available command for applying post-race penalties outside the submission wizard is deregistered. Any attempt to invoke it returns a notice that it has been removed and directs the user to complete the submission wizard to apply penalties.

**Why this priority**: Removing the old command eliminates a parallel, inconsistent path for penalty application. Keeping it would allow penalties to be entered without the finalization gate, bypassing the audit trail and the interim-to-final post replacement.

**Independent Test**: After this change is deployed, attempt to invoke the old penalize command from Discord. Confirm the command is no longer listed in the slash command menu, or that invoking it returns a removal notice.

**Acceptance Scenarios**:

1. **Given** the `round results penalize` command previously existed, **When** any user attempts to invoke it after this change, **Then** either the command is absent from the Discord slash command registry entirely, or the bot responds with a message stating the command has been removed and that penalties are now applied through the results submission wizard.
2. **Given** a round is already in FINALIZED state and new penalties must be applied retrospectively, **When** a trusted admin needs to correct results, **Then** they are directed to use the existing round amendment flow (User Story 6 of spec 019) as the appropriate correction path.

---

### Edge Cases

- An empty penalty list is a fully valid outcome. Approving with no penalties finalizes the round and produces final posts identical to the interim posts; no positions or points change.
- A negative time penalty is valid and must be processed correctly. There is no lower bound enforced on a driver's adjusted total time relative to other drivers; a sufficiently large negative penalty can move a driver from last place to first.
- If two drivers end up with identical total times after penalties are applied, standard tiebreak rules (earlier original submitted position) are used to resolve position order.
- If a round has only qualifying sessions (all race sessions are CANCELLED), time penalties are never applicable; only DSQ entries may be staged. The bot must not present a time-penalty path for such rounds.
- A DSQ applied to the driver who logged the fastest lap forfeits the fastest-lap bonus for that session. No other driver inherits it.
- If the division's reserves visibility toggle is off, the finalization behaviour is unchanged; reserve drivers are excluded from the final standings post in the same way they were excluded from the interim post.
- After a round is FINALIZED, the inline penalty state cannot be re-entered. Corrections to finalized rounds go through the amendment flow (spec 019 User Story 6).
- If the bot crashes or restarts while a round is in the Post-Round Penalties state, the wizard state must be recoverable; the submission channel must remain open and the penalty prompt must be re-posted on recovery so the round can still be finalized.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: After the final session of a round is submitted or marked CANCELLED, the submission channel MUST remain open and transition to the Post-Round Penalties state.
- **FR-002**: On entering the Post-Round Penalties state, the bot MUST post a penalty entry prompt listing all drivers from non-cancelled sessions, a staged penalty list (initially empty), controls to add a penalty, and controls to confirm no penalties.
- **FR-003**: Trusted admins MUST be able to stage a signed integer time penalty (positive or negative, in seconds, non-zero) against a named driver in a race session.
- **FR-004**: Trusted admins MUST be able to stage a DSQ penalty against a named driver in any session type.
- **FR-005**: The system MUST reject time penalty entries for qualifying sessions; only DSQ is permitted for qualifying.
- **FR-006**: The system MUST reject a staged time penalty with a value of zero.
- **FR-007**: Trusted admins MUST be able to remove individually staged penalties before approval.
- **FR-008**: The system MUST allow approving an empty penalty list ("No penalties"), which finalizes the round with no changes to any result.
- **FR-009**: On approval, the system MUST apply all staged penalties to the affected session results, recompute final driver positions and points, and post final results tables to the results channel.
- **FR-010**: On approval, the system MUST delete or replace the interim results posts and interim standings post with the corresponding final posts.
- **FR-011**: After approval, the round state MUST be set to FINALIZED, the submission channel MUST be closed, and an audit log entry MUST be written recording the actor, division, round, and applied penalties.
- **FR-012**: In test mode, the bot MUST block the advance-to-next-event action for any division where the current round is not in FINALIZED state, and MUST return an error identifying the blocking round.
- **FR-013**: The `round results penalize` standalone command MUST be removed or deregistered from the bot.
- **FR-014**: If the bot restarts while a round is in the Post-Round Penalties state, the penalty prompt MUST be recoverable and the round MUST remain finalizable after restart.

### Key Entities

- **Round State**: Gains a FINALIZED terminal state. The state after all sessions are submitted (previously the terminal) becomes an intermediate "Post-Round Penalties" state pending penalty review and approval.
- **Staged Penalty Entry**: A transient wizard-session record holding a driver reference, session reference, penalty type (TIME or DSQ), and a signed integer value in seconds (TIME only). Not persisted to the database until approval.
- **Penalty Application Record**: A persisted record attached to a driver's session result after finalization, storing penalty type, signed value, and the approving actor's identity.
- **Interim Post**: A provisional results or standings Discord message created immediately after session submission. Replaced by the corresponding final post on round finalization.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A trusted admin can complete the full round lifecycle — session submission through penalty review to finalization — without leaving the submission channel or invoking any commands outside the wizard.
- **SC-002**: A round with no penalties can be finalized in no more than two wizard interactions (select "No penalties" → confirm/approve) after the last session is submitted.
- **SC-003**: All interim posts (results and standings) are replaced by final posts within the same response turn as penalty approval; no interim posts remain visible after finalization.
- **SC-004**: In test mode, every attempt to advance past a non-finalized round is blocked with an error that names the specific round and division.
- **SC-005**: Negative time penalty values produce correct final position orderings in all cases; no test case with a valid signed penalty results in an incorrect standing or points total.
