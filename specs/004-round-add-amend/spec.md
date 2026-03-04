# Feature Specification: Round-Add Duplicate Guard & Round-Amend During Setup

**Feature Branch**: `004-round-add-amend`
**Created**: 2026-03-04
**Status**: Draft
**Input**: User description: "Allow round-amend while season is pending approval; detect duplicate round numbers on round-add and prompt the user to insert before, insert after, replace, or cancel"

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Correct a round mistake during season setup (Priority: P1)

An admin is building out a season using `/season-setup` → `/division-add` → `/round-add`. Before approving the season they notice they entered the wrong track or wrong date for one round. Today they must abandon the whole setup and start over — there is no way to fix a round once it has been added to a pending config. The `/round-amend` command only works after the season is approved.

This feature allows the admin to run `/round-amend` against a pending (not-yet-approved) season, making corrections before committing the config.

**Why this priority**: Eliminates the most painful point in the setup flow. Forcing a full restart to fix a single round detail is a significant friction point and is the most likely reason for a support request.

**Independent Test**: Complete a partial setup with `/season-setup` and `/round-add`. Before running `/season-approve`, run `/round-amend` with a corrected track. Verify the change appears in `/season-review` and the approved season uses the corrected value.

**Acceptance Scenarios**:

1. **Given** a pending season setup with at least one round added, **When** the admin runs `/round-amend` with a valid correction (track, date, or format), **Then** the round in the pending config is updated and the response confirms the change ephemerally.
2. **Given** a pending season setup, **When** the admin runs `/round-amend` targeting a round number that does not exist in the pending config, **Then** the command returns a descriptive error ephemeral.
3. **Given** a pending season setup, **When** the admin runs `/round-amend` targeting a division name that does not exist in the pending config, **Then** the command returns a descriptive error ephemeral.
4. **Given** no pending season setup and no active season, **When** `/round-amend` is run, **Then** an error states that no season exists to amend.
5. **Given** an active (approved) season, **When** `/round-amend` is run, **Then** it behaves exactly as today — no change to existing active-season amend behaviour.
6. **Given** a pending season setup, **When** `/round-amend` changes the format to MYSTERY, **Then** the track is cleared; the change is reflected in `/season-review`.
7. **Given** a pending season setup, **When** `/round-amend` changes the format away from MYSTERY to a non-Mystery format, **Then** the command requires a track to be specified and rejects the amendment if none is provided.

---

### User Story 2 — Prevent accidental duplicate round numbers during setup (Priority: P1)

An admin runs `/round-add` for division "Pro" and accidentally enters round number 3 twice. Today the bot silently appends the second round, creating two rounds with the same number and causing ambiguity in the schedule.

When a duplicate round number is detected, the bot must pause and ask the admin what they intended: insert the new round before the existing one (shifting subsequent round numbers up), insert after (shifting subsequent numbers up from the next position), replace the existing round entirely, or cancel and make no change.

**Why this priority**: A silent duplicate is worse than an outright error — it produces an inconsistent schedule that is not caught until review or beyond. Ranked equal to User Story 1 because both block correct season configuration.

**Independent Test**: Run `/round-add` for a division that already has a round with the same number. The bot must present the four resolution options. Select each option in separate test runs and verify the resulting round list matches the expected state.

**Acceptance Scenarios**:

1. **Given** a pending setup where division "Pro" already has round 3, **When** `/round-add` is called with `round_number=3` for division "Pro", **Then** the bot responds with an interactive ephemeral prompt listing four options: Insert Before, Insert After, Replace, Cancel.
2. **Given** the prompt is shown and the admin selects **Insert Before**, **Then** the new round is assigned number 3 and the previously-existing round 3 (and any rounds with number >= 3) are each renumbered +1; all other fields of those rounds are preserved.
3. **Given** the prompt is shown and the admin selects **Insert After**, **Then** the new round is assigned number 4 (original round 3 stays as 3; any rounds with number > 3 are each renumbered +1); all other fields of those rounds are preserved.
4. **Given** the prompt is shown and the admin selects **Replace**, **Then** the existing round 3 is removed and the new round takes number 3; all other round numbers are unchanged.
5. **Given** the prompt is shown and the admin selects **Cancel**, **Then** no change is made; the division's round list is identical to its state before the command was issued.
6. **Given** a `/round-add` with a round number that does not conflict with any existing round in that division, **Then** the round is added immediately with the existing success message — no prompt is shown.
7. **Given** the interactive prompt is shown and the admin does not interact within 60 seconds, **Then** the interaction times out, the prompt updates to a timeout message, and no change is made to the round list.
8. **Given** a division already has rounds 1 and 3 with their respective dates, **When** `/round-add` is called with `round_number=2` but a `scheduled_at` that is earlier than round 1's date or later than round 3's date, **Then** the command returns a descriptive error naming the offending neighbour round and its date; no round is added and no prompt is shown. Same-day rounds (equal `scheduled_at`) are permitted.

---

### Edge Cases

- What if "Insert Before" is chosen and there is already a round at `conflict_number + 1`? → The shift cascades: every round at or above the conflict number is incremented, so no gaps or collisions are introduced.
- What if the pending config is abandoned (bot restarts) while awaiting a duplicate-resolution response? → The interaction expires; the round is not added and the in-memory config is unchanged.
- What if the admin running `/round-amend` on a pending setup is a different user from the one who started the setup? → Any server admin holding the appropriate Discord permission (`@admin_only`) may amend any pending setup for their server. The cog scans all in-memory pending configs for the invoking `guild_id` rather than looking up by `user_id`. This is consistent with how other admin commands work and allows one admin to pick up where another left off.
- What if `/round-amend` changes a round format on a pending config? → Phase data is not computed during the setup phase; no phase-invalidation logic is required for pending-config amendments.
- What if `/round-amend` is applied to a pending config and changes format to non-MYSTERY but no track is supplied and the existing round already has a track stored? → The track MUST be preserved (only fields explicitly supplied are changed); no error should be raised if the existing track is already non-empty.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `/round-amend` MUST check for a pending in-memory season setup belonging to the invoking server (`guild_id`) before checking for an active (approved) season in the database. Any admin passing `@admin_only` may amend any pending setup for their server.
- **FR-002**: When `/round-amend` targets a pending config, the amendment MUST update the matching round dict in memory **and** immediately snapshot the full pending config to the database (status=SETUP) for crash-recovery safety. No phase-invalidation logic MUST run.
- **FR-003**: When `/round-amend` targets a pending config and changes the format to MYSTERY, the track field of that round MUST be cleared to `None`.
- **FR-004**: When `/round-amend` targets a pending config and changes the format away from MYSTERY to a non-Mystery format, a `track` value MUST be provided; if not supplied and the stored track is already empty, the amendment MUST be rejected with a descriptive error.
- **FR-005**: When `/round-amend` targets a pending config, the phase-invalidation logic used for active-season amendments MUST NOT run.
- **FR-006**: When `/round-add` detects that the supplied `round_number` already exists in the target division, the bot MUST NOT append the new round. Instead it MUST respond with an interactive ephemeral prompt offering four buttons: **Insert Before**, **Insert After**, **Replace**, **Cancel**.
- **FR-007**: On **Insert Before**, every round in the division with `round_number >= conflict_number` MUST have its `round_number` incremented by 1, then the new round is inserted at `conflict_number`.
- **FR-008**: On **Insert After**, every round in the division with `round_number > conflict_number` MUST have its `round_number` incremented by 1, then the new round is inserted at `conflict_number + 1`.
- **FR-009**: On **Replace**, the existing round at `conflict_number` MUST be removed and the new round inserted at that number; all other round numbers MUST remain unchanged.
- **FR-010**: On **Cancel**, the division's round list MUST be identical to its pre-command state.
- **FR-011**: The duplicate-resolution prompt MUST expire after 60 seconds; on expiry the prompt MUST update to indicate the timeout and no round change MUST occur.
- **FR-012**: The duplicate-resolution prompt MUST be ephemeral.
- **FR-013**: `/round-add` with no conflicting round number MUST continue to work exactly as before — no prompt, immediate success message.
- **FR-014**: Every mutation of the pending season config (`/season-setup`, `/division-add`, `/round-add`, duplicate-resolution buttons, `/round-amend` on pending) MUST atomically persist the full config to the database as a `SETUP`-status season record. On bot startup, all `SETUP`-status seasons MUST be loaded back into the in-memory pending config store so setup can continue without restarting from scratch.
- **FR-015**: Before adding a round (both the no-conflict path and prior to showing the duplicate prompt), `/round-add` MUST validate that the supplied `scheduled_at` is chronologically consistent with the round's neighbours by number: it MUST NOT be strictly earlier than the latest `scheduled_at` among lower-numbered rounds, and MUST NOT be strictly later than the earliest `scheduled_at` among higher-numbered rounds. Equal datetimes (same-day rounds) MUST be accepted. A violation MUST return a descriptive ephemeral error naming the offending neighbour round number and its datetime.
- **FR-016**: During `/season-approve`, all rounds MUST be registered with APScheduler before `transition_to_active` is called on the DB. A failure during scheduling MUST leave the season in `SETUP` status so the admin can retry approval rather than finding an `ACTIVE` season with no phase jobs running.

### Key Entities

- **PendingConfig**: In-memory dataclass keyed by `user_id`, holding a server's not-yet-approved season configuration. Contains a list of `PendingDivision` objects each with a list of round dicts.
- **Round dict** (inside PendingDivision.rounds): `{round_number, format, track_name, scheduled_at}`. Mutable in-place for pending-config amendments and duplicate resolution.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An admin can correct any single field of any round in a pending config without abandoning the in-progress setup.
- **SC-002**: A `/season-review` output after a pending-config amendment reflects only the corrected values.
- **SC-003**: A duplicate `round_number` in `/round-add` never silently produces two rounds with the same number in the same division.
- **SC-004**: After each of the four resolution choices, the division's round list contains unique round numbers in ascending order with no gaps caused by the operation.
- **SC-005**: All existing tests continue to pass; new tests cover the pending-amend path (US1) and all four duplicate-resolution branches (US2).
- **SC-006**: The interactive duplicate-resolution prompt times out cleanly after 60 seconds, leaving the round list unchanged.
- **SC-007**: A bot restart mid-setup does not lose any config entered prior to the crash; admins can resume with `/division-add`, `/round-add`, or `/season-review` immediately after the bot reconnects.

---

## Assumptions

- The pending config is looked up by `guild_id` (server) for all admin commands that mutate it (`/round-amend`, `/division-add`, `/round-add`, `/season-review`, `/season-approve`). A user-id key may exist in the in-memory dict (from the initial `/season-setup` invocation), but all commands also perform a fallback guild-id scan so any server admin can continue a setup started by another admin.
- Round numbers are positive integers; the shift logic never produces round number 0 or negative numbers in any normal flow.
- The interactive prompt (US2) uses four Discord buttons in a single row, as the options are mutually exclusive and fit the 5-button Discord limit.

---

## Clarifications

### Session 2026-03-04

- Q: Should `/round-amend` on a pending setup be restricted to the admin who started it, or open to any server admin? → A: Any server admin holding `@admin_only` permission may amend any pending setup for their server. The cog looks up by `guild_id` when handling `/round-amend` (FR-001, Assumptions updated).
- Q: Should FR-002 reflect that a DB snapshot is written after each pending-config amendment, or keep it as "no database write occurs"? → A: Update FR-002 to state that a SETUP-status DB snapshot is taken immediately after the in-memory update for crash-recovery safety; phase-invalidation still does not run. (FR-002 updated, Assumptions updated.)
- Q: Should FR-015 (date ordering validation on `/round-add`) and a matching acceptance scenario be added? → A: Yes — add FR-015 and acceptance scenario 8 to US2. Rounds with equal `scheduled_at` are permitted; strict earlier/later comparisons reject chronologically inconsistent entries. (FR-015 and US2 scenario 8 added.)
- Q: Should FR-016 (schedule-before-transition ordering guarantee at approval) be added? → A: Yes — all rounds must be scheduled with APScheduler before `transition_to_active` is called; a scheduling failure leaves the season in SETUP status. (FR-016 added.)
- Q: Should the "Go Back to Edit" button response message content be specified in the spec? → A: No — treat as a cosmetic implementation detail; no FR or UX note added.
