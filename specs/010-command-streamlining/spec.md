# Feature Specification: Command Streamlining & Quality of Life Improvements

**Feature Branch**: `010-command-streamlining`
**Created**: 2026-03-05
**Status**: Draft

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Simplified Season Setup (Priority: P1)

A trusted admin wants to begin configuring a new season. They should not need to know
in advance how many divisions the season will have, nor supply a meaningless start date.
The command should succeed when no active season exists for the server, and fail with a
clear error when one already does.

**Why this priority**: All other season-configuration commands (add division, add round,
approve) depend on a season being in setup mode. Simplifying this is the prerequisite
change for the rest of the feature.

**Independent Test**: Run `/season setup` on a server with no active season; verify setup
mode is entered without supplying any parameters. Then run it again; verify a clear error
is returned because a season is already in progress.

**Acceptance Scenarios**:

1. **Given** no active season for the server, **When** a trusted admin runs `/season setup`
   with no parameters, **Then** the season enters setup mode and the admin receives
   confirmation that setup has begun.
2. **Given** a season already exists (any state), **When** `/season setup` is run,
   **Then** the command is rejected with a clear message stating a season is already active.
3. **Given** a season in setup mode, **When** the same trusted admin runs
   `/season setup` again, **Then** the command is rejected (covered by scenario 2).

---

### User Story 2 — Auto-Derived Round Numbers with Insertion Reordering (Priority: P2)

A trusted admin adds rounds to a division by supplying the track, format, and scheduled
datetime. The round's sequential position within the division should be derived
automatically from the scheduled dates — no manual round number input. If a round is
inserted whose scheduled date falls before rounds that already exist, those later rounds
must be renumbered to reflect the new order. The same reordering must occur when an
existing round's scheduled date is changed via `/round amend`.

**Why this priority**: Removing the redundant round-number parameter reduces input errors,
while automatic reordering keeps the round list consistent at all times without admin
intervention.

**Independent Test**: Add three rounds out of chronological order to a single division;
verify the bot assigns round numbers 1, 2, 3 in date order regardless of the insertion
sequence, and that each confirmation states the assigned round number.

**Acceptance Scenarios**:

1. **Given** a division with no rounds, **When** a round is added with a given datetime,
   **Then** it is assigned round number 1 and the confirmation states "Round 1 added."
2. **Given** a division with rounds 1 (Apr 5) and 2 (Apr 12), **When** a new round is
   added with a datetime of Apr 8, **Then** the new round becomes round 2, the Apr 12
   round becomes round 3, and the confirmation states "Round 2 added."
3. **Given** a division with rounds 1 (Apr 5) and 2 (Apr 12), **When** round 1's datetime
   is amended to Apr 15, **Then** the former round 2 becomes round 1, the amended round
   becomes round 2, and the confirmation shows the updated round list for the division.
4. **Given** a round being added to a Mystery format, **When** no track is supplied,
   **Then** the round is added and auto-numbered with the track shown as "TBD".

---

### User Story 3 — Post-Modification Feedback on Rounds and Divisions (Priority: P3)

After any change to the round list of a division (add, amend, delete) the bot should
immediately show the full updated round list for that division. After any change to the
division list (add, duplicate, delete, rename) the bot should immediately show all divisions
currently configured for the season.

**Why this priority**: Admins need to verify the state of the season after each change
without issuing a separate review command. This eliminates the need for manual
`/season review` calls during setup.

**Independent Test**: Add one round to a division; verify the bot's confirmation includes
a formatted list of all rounds in that division. Add a second division; verify the
confirmation includes a list of all divisions.

**Acceptance Scenarios**:

1. **Given** a division with two rounds, **When** a third round is added, **Then** the
   confirmation includes an ordered list of all three rounds (round number, track, format,
   datetime).
2. **Given** a division with three rounds, **When** one round is deleted, **Then** the
   confirmation includes the updated two-round list with corrected round numbers.
3. **Given** a season with two divisions, **When** a third division is added, **Then**
   the confirmation includes a list of all three divisions (name, role, forecast channel).
4. **Given** a season with two divisions, **When** one division is deleted, **Then** the
   confirmation includes the updated one-division list.
5. **Given** a division is duplicated, **When** duplication succeeds, **Then** the
   confirmation shows the full updated division list and the new division's round list.6. **Given** a division is renamed, **When** the rename succeeds, **Then** the
   confirmation shows the full updated division list reflecting the new name.
---

### User Story 4 — Division Duplication During Setup (Priority: P4)

A trusted admin can duplicate an existing division, supplying a new name, role, forecast
channel, and a day + hour offset to shift all round datetimes. This allows multi-division
leagues with staggered race times to be configured quickly without re-entering every round.
This command is only available while the season is in setup mode.

**Why this priority**: Most leagues run 2–4 divisions with identical round calendars offset
by a fixed time window. This command eliminates the most repetitive part of season setup.

**Independent Test**: Create a division with three rounds. Duplicate it with a +2 day,
−1.5 hour offset. Verify the new division contains three rounds whose datetimes are each
shifted by exactly +2 days −1.5 hours relative to the originals, with all other parameters
(format, track) copied.

**Acceptance Scenarios**:

1. **Given** a division with rounds in setup mode, **When** `/division duplicate` is
   called with a valid offset, **Then** a new division is created containing the same
   rounds with all datetimes shifted by the specified offset.
2. **Given** a duplication that would produce a round datetime in the past, **When** the
   command is run, **Then** the duplication is still performed (no date validation block)
   but the admin is warned that one or more rounds fall in the past.
3. **Given** a season that is approved or active, **When** `/division duplicate` is run,
   **Then** the command is rejected with a message stating duplication is only allowed
   during setup.
4. **Given** a division name that does not exist, **When** `/division duplicate` is run
   with that name, **Then** the command is rejected with a clear error.
5. **Given** a target division name that already exists, **When** `/division duplicate` is
   run with that name as the new name, **Then** the command is rejected with a clear error.

---

### User Story 5 — Division and Round Deletion During Setup (Priority: P5)

A trusted admin can delete a division or an individual round while the season is in setup
mode. Deleting a division removes all rounds associated with it. Deleting a round
renumbers remaining rounds within the division.

**Why this priority**: Mistakes during setup are inevitable; deletion commands eliminate
the need for a full season reset when only one division or round needs to be removed.

**Independent Test**: Add a division with two rounds. Delete the first round; verify round 2
is renumbered to round 1. Delete the division; verify no divisions remain.

**Acceptance Scenarios**:

1. **Given** a division in setup mode, **When** `/division delete` is called with its name,
   **Then** the division and all its rounds are removed and the updated division list is shown.
2. **Given** a division with rounds 1 and 2 in setup mode, **When** round 1 is deleted via
   `/round delete`, **Then** former round 2 is renumbered to round 1 and the updated
   round list is shown.
3. **Given** a season that is approved or active, **When** `/division delete` or
   `/round delete` is called, **Then** the command is rejected with a message stating
   deletion during an active season requires a cancellation command instead.
4. **Given** a division or round name/identifier that does not exist, **When** the
   corresponding delete command is run, **Then** the command is rejected with a
   clear error.

---

### User Story 6 — In-Season Division and Round Cancellation (Priority: P6)

A trusted admin can cancel an entire division or a specific round during an active,
approved season — even if phases have already started for that target. Both commands
require explicit text confirmation (mirroring `/bot-reset`). On confirmation, the bot
posts a message to the affected division's forecast channel (without pinging the division
role) informing of no further weather forecast due to cancellation.

**Why this priority**: Real leagues occasionally drop a division mid-season or cancel an
individual round due to unforeseen circumstances; without these commands the only recovery
path is a full server reset.

**Independent Test**: Start an active season with one division and one pending round. Cancel
the round via `/round cancel` with the confirmation string; verify the forecast channel
receives the cancellation notice without a role mention.

**Acceptance Scenarios**:

1. **Given** an active season, **When** `/division cancel` is run and the admin confirms
   with the required string, **Then** the division is marked cancelled and the forecast
   channel receives a message stating no further weather forecast will be posted for the
   division due to cancellation.
2. **Given** an active season, **When** `/round cancel` is run for a round whose phases
   have already started, and the admin confirms, **Then** the round is marked cancelled
   and the forecast channel receives a message stating no weather forecast will be posted
   for that round due to cancellation.
3. **Given** either cancellation command, **When** the admin provides an incorrect
   confirmation string, **Then** the command is aborted with no changes.
4. **Given** a season in setup mode (not yet approved), **When** `/division cancel` or
   `/round cancel` is run, **Then** the command is rejected with a message directing the
   admin to use the delete commands instead.
5. **Given** a division that is already cancelled, **When** `/division cancel` is run
   again, **Then** the command is rejected with a clear error.

---

### User Story 7 — Full Season Cancellation (Priority: P7)

A server administrator can cancel an entire active, approved season. This requires
explicit text confirmation identical to the pattern used by `/bot-reset`. On confirmation,
the bot posts a notice to every active division's forecast channel stating the season has
been cancelled and no further weather output will follow. No division roles are mentioned.
This command is gated at the server-administrator level, not merely the interaction role.

**Why this priority**: When a full season must be abandoned (league disbands, hosting
platform changes, etc.) the only path without this command is a destructive server reset.
`/season cancel` achieves the same clean teardown as a season reaching its natural end —
all season data is deleted, configuration is preserved, and a new season can be set up
immediately.

**Independent Test**: Start an active season with two divisions. Run `/season cancel` as a
server administrator with the correct confirmation string; verify both division forecast
channels receive the cancellation notice with no role pings, the season data is deleted
from the database, and running `/season setup` afterwards succeeds.

**Acceptance Scenarios**:

1. **Given** an active approved season, **When** a server administrator runs `/season cancel`
   and supplies the correct confirmation string, **Then** each active division's forecast
   channel receives a message stating the season has been cancelled and no further weather
   forecasts will be posted (no role mention), and the season data is deleted from the
   database so that a new season may be set up immediately via `/season setup`.
2. **Given** an active approved season, **When** the administrator provides an incorrect
   confirmation string, **Then** the command is aborted with no changes.
3. **Given** a season in setup mode, **When** `/season cancel` is run, **Then** the
   command is rejected with a message directing the admin to use `/season` setup controls
   or `/bot-reset` instead.
4. **Given** an interaction-role holder who is not a server administrator, **When**
   `/season cancel` is run, **Then** the command is rejected with a permission error.
5. **Given** a season with one or more already-cancelled divisions, **When** `/season cancel`
   is confirmed, **Then** the notice is posted only to the forecast channels of divisions
   that are still active; already-cancelled divisions are skipped.

---

### User Story 8 — Division Rename During Setup (Priority: P8)

A trusted admin can rename a division while the season is in setup mode by supplying the
current name and the desired new name. The rename is instant; no confirmation prompt is
required. The full updated division list is shown on success.

**Why this priority**: Typos and naming changes during setup are common; without a rename
command the only fix is delete-and-recreate, which loses all configured rounds.

**Independent Test**: Create a division named "Pro" with two rounds. Run
`/division rename` with old name "Pro" and new name "Pro-Am"; verify the division list
shows "Pro-Am" and the rounds are unaffected.

**Acceptance Scenarios**:

1. **Given** a division named "Pro" in setup mode, **When** `/division rename` is run
   with old name "Pro" and new name "Pro-Am", **Then** the division is renamed and the
   updated division list is shown.
2. **Given** a division name that does not exist, **When** `/division rename` is run
   with that name as the old name, **Then** the command is rejected with a clear error.
3. **Given** a new name that is already used by another division, **When** `/division rename`
   is run, **Then** the command is rejected with a clear error.
4. **Given** a season that is approved or active, **When** `/division rename` is run,
   **Then** the command is rejected stating renaming is only available during setup.

---

### User Story 9 — Test Mode Restricted to Server Administrators (Priority: P9)

All `/test-mode` commands (`toggle`, `advance`, `review`) should only be accessible to
server administrators (Manage Server permission). Interaction-role holders who are not
server administrators must be rejected. This removes the ambiguity of test mode being
available to the general interaction-role population, who have no need to manipulate the
weather pipeline outside of real scheduled runs.

**Why this priority**: Test mode bypasses scheduled phase timing entirely and can post
weather output to live channels. Restricting it to server administrators prevents accidental
or unauthorised triggering of phase output during an active season.

**Independent Test**: Configure the bot with a non-admin interaction-role holder. Verify
that `/test-mode toggle`, `/test-mode advance`, and `/test-mode review` are all rejected
for that user. Verify all three succeed for a user with Manage Server permission.

**Acceptance Scenarios**:

1. **Given** a user who holds the interaction role but does not have Manage Server
   permission, **When** they invoke any `/test-mode` subcommand, **Then** the command is
   rejected with a permission error.
2. **Given** a user who is a server administrator (Manage Server permission), **When**
   they invoke any `/test-mode` subcommand under the existing preconditions (test mode
   active for `advance` and `review`), **Then** the command proceeds as normal.
3. **Given** a server administrator who does not hold the interaction role, **When** they
   invoke any `/test-mode` subcommand, **Then** the command still proceeds — server
   administrator permission is sufficient without the interaction role.

---

### Edge Cases

- What happens when a division has no rounds when it is duplicated? The new division is
  created with no rounds and the admin is warned.
- How does the system handle an offset in `/division duplicate` that results in two rounds
  landing on the exact same datetime in the new division? Duplication proceeds and the admin
  is warned of the datetime collision; auto-numbering resolves order arbitrarily for tied
  times.
- What happens when the last round of a division is cancelled? The division remains active;
  only the specific round is cancelled.
- What happens when all rounds in a division are individually cancelled? The season
  scheduler recognises no pending phases for that division; no further output is produced.
  The admin must use `/division cancel` if they wish to post a division-level notice.
- Can a round be cancelled if its Phase 3 has already completed? Yes; the round is marked
  cancelled but previously posted weather messages are not retracted (already public).
- What if all divisions are already cancelled before `/season cancel` is run? The season
  still completes the cancellation; no forecast-channel posts are sent (no active divisions
  remain), and the season data is deleted, allowing a new season to be set up.
- Can `/division rename` be used on a division that has no rounds yet? Yes; renaming an
  empty division is permitted and follows the same rules.
- Can a server administrator invoke `/test-mode` commands from outside the configured
  interaction channel? No; the out-of-channel guard still applies to all commands including
  `/test-mode`.

---

## Requirements *(mandatory)*

### Functional Requirements

**Season Setup**

- **FR-001**: The `/season setup` command MUST accept no parameters and MUST succeed only
  when no active season (in any state) exists for the server.
- **FR-002**: When a season already exists, `/season setup` MUST be rejected with a
  message identifying the conflict.

**Round Numbering**

- **FR-003**: When a round is added to a division, its round number MUST be derived
  automatically from the chronological position of its scheduled datetime among all
  rounds already in the division.
- **FR-004**: When a round is inserted whose datetime precedes one or more existing rounds,
  those rounds MUST be renumbered immediately in ascending chronological order.
- **FR-005**: When an existing round's scheduled datetime is changed via `/round amend`,
  all rounds in the division MUST be renumbered to reflect the new chronological order.
- **FR-006**: The confirmation message for any round addition MUST include the round number
  that was assigned.

**Post-Modification Feedback**

- **FR-007**: After any round addition, amendment, or deletion, the bot MUST include the
  full ordered round list for the affected division in its confirmation response.
- **FR-008**: After any division addition, duplication, deletion, or rename, the bot MUST
  include the full current division list for the season in its confirmation response.

**Division Duplication**

- **FR-009**: `/division duplicate` MUST accept: source division name, new division name,
  new role, new forecast channel, integer day offset (positive or negative), and decimal
  hour offset (positive or negative, e.g. −1.5 for minus 90 minutes).
- **FR-010**: All rounds from the source division MUST be copied to the new division with
  every scheduled datetime shifted by the specified day and hour offset. All other round
  attributes (track, format) MUST be copied unchanged.
- **FR-011**: `/division duplicate` MUST be rejected if the season is not in setup mode.
- **FR-012**: `/division duplicate` MUST be rejected if the new division name already
  exists.

**Division and Round Deletion (Setup Only)**

- **FR-013**: `/division delete` MUST remove the named division and all its rounds when
  the season is in setup mode; it MUST be rejected otherwise.
- **FR-014**: `/round delete` MUST remove the identified round and renumber remaining
  rounds within the division when the season is in setup mode; it MUST be rejected
  otherwise.

**In-Season Cancellation**

- **FR-015**: `/division cancel` MUST require the admin to supply a hard confirmation
  string before proceeding, following the same confirmation pattern as `/bot-reset`.
- **FR-016**: `/division cancel` MUST post a message to the division's configured forecast
  channel stating the division has been cancelled and no further weather forecasts will be
  posted. The message MUST NOT include a mention of the division role.
- **FR-017**: `/round cancel` MUST require the same explicit confirmation pattern.
- **FR-018**: `/round cancel` MUST post a message to the division's forecast channel
  stating the round has been cancelled and no weather forecast will be posted for it. The
  message MUST NOT include a mention of the division role.
- **FR-019**: Both cancellation commands MUST be accepted when phases have already started
  for the target. Previously posted weather messages are not retracted.
- **FR-020**: Both cancellation commands MUST be rejected when the season is in setup mode
  (redirect to delete commands).
- **FR-021**: Both cancellation commands MUST be rejected if the target is already cancelled.

**Command Naming**

- **FR-022**: All commands introduced or modified by this feature MUST follow the
  `/domain action` subcommand-group convention per the project constitution. Existing
  hyphenated commands affected by this feature MUST be migrated to the subcommand-group
  form as part of this work.

**Full Season Cancellation**

- **FR-023**: `/season cancel` MUST be gated at the server-administrator level (Manage
  Server permission). Interaction-role holders who are not server administrators MUST be
  rejected with a permission error.
- **FR-024**: `/season cancel` MUST only be accepted when a season is in `ACTIVE`
  (approved) state. It MUST be rejected for seasons in `SETUP` or `COMPLETED` state.
- **FR-025**: `/season cancel` MUST require an explicit confirmation string before
  executing, following the same confirmation pattern as `/bot-reset`.
- **FR-026**: On confirmation, `/season cancel` MUST post a message to the forecast
  channel of every division that is still `ACTIVE`, stating the season has been cancelled
  and no further weather forecasts will be posted. Divisions already in `CANCELLED` state
  MUST be skipped. No division role mentions are permitted in these messages.
- **FR-027**: After posting to forecast channels, `/season cancel` MUST delete the season
  and all associated data from the database, producing the same end state as a season
  reaching its natural conclusion. A new season MUST be configurable via `/season setup`
  immediately afterwards.

**Division Rename (Setup Only)**

- **FR-028**: `/division rename` MUST accept exactly two parameters: the current division
  name and the desired new name.
- **FR-029**: `/division rename` MUST only be accepted when the season is in `SETUP` state;
  it MUST be rejected otherwise.
- **FR-030**: `/division rename` MUST be rejected if the current name does not match any
  existing division.
- **FR-031**: `/division rename` MUST be rejected if the new name is already in use by
  another division in the same season.
- **FR-032**: A successful rename MUST leave all rounds and configuration of the division
  unchanged apart from the name.

**Test Mode Access**

- **FR-033**: All `/test-mode` subcommands (`toggle`, `advance`, `review`) MUST be
  restricted to users with the server-administrator permission (Manage Server). Interaction-
  role holders who are not server administrators MUST be rejected with a permission error.
  Server administrators MUST be accepted regardless of whether they hold the interaction
  role.

### Key Entities

- **Season**: Represents a server's race season. Carries an explicit lifecycle state:
  `SETUP`, `ACTIVE`, or `COMPLETED`. A cancelled division or round is tracked as a
  `CANCELLED` sub-state on its own record. A season cancelled via `/season cancel` is
  deleted outright (no persistent `CANCELLED` season state); only division- and
  round-level cancellations use a `CANCELLED` sub-state.
- **Division**: A race division within a season. Attributes: name, notification role,
  forecast channel, lifecycle state (`ACTIVE` / `CANCELLED`).
- **Round**: A scheduled race event within a division. Attributes: round number
  (auto-derived), scheduled datetime, format, track (nullable for Mystery), lifecycle
  state (`ACTIVE` / `CANCELLED`).

## Assumptions

- The day offset in `/division duplicate` is whole days; the hour offset is a decimal
  number of hours (e.g. `−1.5` = minus 1 hour 30 minutes). Both are supplied as
  separate parameters.
- Round identifier in `/round delete` and `/round cancel` refers to the auto-derived round
  number (position in the division's chronological schedule), not an internal database ID.
- When all rounds of a division have been individually cancelled, the division itself
  remains `ACTIVE` unless explicitly cancelled via `/division cancel`.
- The post-modification round list shown after changes is the same concise format used by
  `/season review` — round number, track, format, and datetime per line.
- The post-modification division list shows division name, role, forecast channel per line.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A trusted admin can configure a complete two-division season from a fresh
  server state in under 5 minutes using the new commands, without relying on
  `/season review` to verify state after each step.
- **SC-002**: Zero round entries in newly configured seasons have an incorrect round number
  relative to the chronological order of their scheduled datetimes.
- **SC-003**: All cancellation commands produce a visible forecast-channel message that
  contains no role mention, verifiable by inspecting the message content.
- **SC-004**: Every command introduced or migrated by this feature is reachable via
  Discord's autocomplete under the correct subcommand group with no orphaned
  hyphenated top-level commands remaining for the migrated domains.
- **SC-005**: A division duplicated from a source with N rounds always produces exactly N
  rounds in the new division, each shifted by the specified offset to within a one-minute
  tolerance.
- **SC-006**: `/season cancel` produces a forecast-channel notice in every active division
  channel and no notice in already-cancelled division channels, verifiable by inspecting
  which channels received a message after confirmation.
- **SC-007**: All three `/test-mode` subcommands are inaccessible to a user who holds
  only the interaction role without server-administrator permission, verifiable by
  confirming a permission error is returned for each.
