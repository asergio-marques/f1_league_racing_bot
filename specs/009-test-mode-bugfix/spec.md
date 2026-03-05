# Feature Specification: Test Mode Bug Fixes

**Feature Branch**: `009-test-mode-bugfix`
**Created**: 2026-03-05
**Status**: Implemented
**Input**: Bug reports from live test-mode usage of feature `002-test-mode`

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Mystery rounds must not appear as "next round" (Priority: P1)

A user with the interaction role runs `/season-status` on a server that has a season with
at least one Mystery round. The bot displays "Next round: None remaining" for any division
whose only remaining rounds are Mystery rounds, rather than incorrectly citing the Mystery
round as pending.

**Why this priority**: This is a data-correctness regression. Showing a Mystery round as
"next round" gives misleading information about season progress and blocks recognising that
a division is actually complete.

**Independent Test**: Configure a season with one normal round (all three phases done) and
one Mystery round; run `/season-status` and verify the division reports "None remaining",
not the Mystery round.

**Acceptance Scenarios**:

1. **Given** an active season where all non-Mystery rounds for a division have all three
   phases done, **When** `/season-status` is issued, **Then** the bot reports
   "None remaining" for that division's next round.
2. **Given** an active season where a Mystery round is the only uncompleted round in a
   division, **When** `/season-status` is issued, **Then** the Mystery round does NOT
   appear as the next pending round.
3. **Given** an active season where a normal round is still pending and a Mystery round
   follows it, **When** `/season-status` is issued, **Then** the normal round is correctly
   reported as next; the Mystery round is not mentioned.

---

### User Story 2 — Season ends correctly after advancing all phases via test mode (Priority: P2)

A user with the interaction role advances through all non-Mystery phases of every round using
`/test-mode advance`. After the final Phase 3 advance, the season ends and data is cleared.
If the user calls `/test-mode advance` one extra time (e.g. to confirm everything is done),
the bot either reports season complete and clears data (if it was not already cleared) or
reports "nothing to advance" (if already cleared).

**Why this priority**: Without this fix, a season can become permanently stuck as `ACTIVE`
after all test-mode phases are advanced, preventing the server from starting a new season
and making `/season-status` show stale data indefinitely.

**Independent Test**: Enable test mode; advance all phases for all non-Mystery rounds until
`get_next_pending_phase` returns `None`; verify the season is cleared and
`/season-status` reports "No active season found."

**Acceptance Scenarios**:

1. **Given** all non-Mystery phases have been advanced and Phase 3 of the last round was
   just run, **When** the bot checks for season end inside `run_phase3` and succeeds,
   **Then** the season is ended and data is cleared immediately.
2. **Given** all non-Mystery phases have been advanced but the season is still `ACTIVE`
   (season-end inside `run_phase3` was skipped), **When** `/test-mode advance` is issued,
   **Then** the bot detects an active season with an empty phase queue, calls
   `execute_season_end`, and responds with a season-complete confirmation.
3. **Given** the season has already been cleared (normal path), **When** `/test-mode advance`
   is issued with an empty queue, **Then** the bot responds "nothing left to advance" without
   error.

---

### User Story 3 — Test-mode commands respect the configured interaction role (Priority: P2)

A user who holds the server's configured interaction role (set via `/bot-init`) can use all
three `/test-mode` subcommands from the configured interaction channel. A server administrator
who does NOT hold the interaction role cannot use the commands. The commands do not appear
usable in DMs.

**Why this priority**: The original implementation applied Discord's default permission
restrictions to the command group, which on some servers resolved to requiring `Manage Server`
permission (admin-only), directly violating Principle I of the constitution.

**Independent Test**: Configure the bot with an interaction role that is NOT held by the
server owner; have the owner (admin, no interaction role) attempt `/test-mode toggle`; verify
it is rejected. Then have a non-admin user with the interaction role issue the command and
verify it is accepted.

**Acceptance Scenarios**:

1. **Given** a user holds the configured interaction role and is in the configured
   interaction channel, **When** they issue `/test-mode toggle`, `/test-mode advance`, or
   `/test-mode review`, **Then** the bot processes the command normally.
2. **Given** a user has `Manage Server` (admin) permission but does NOT hold the interaction
   role, **When** they issue any `/test-mode` command, **Then** the bot responds with a
   permission error (ephemeral) — the same as any other non-role user.
3. **Given** any user, **When** they attempt to use `/test-mode` in a DM,
   **Then** the command is not available (Discord-level guild-only restriction).

---

### Edge Cases

- What if the season is deleted between `get_next_pending_phase` returning `None` and the
  safety-net `get_active_season` call? The `execute_season_end` idempotency guard handles
  this: if no active season is found it returns immediately.
- What if Discord's tree sync has not yet propagated the updated `default_permissions`
  to all servers? Users may still see the old restriction until the next sync completes
  (up to 1 hour for global commands).

## Requirements *(mandatory)*

### Functional Requirements

| ID | Requirement | Bug |
|----|-------------|-----|
| FR-001 | `/season-status` MUST exclude `MYSTERY` format rounds when finding the next pending round for a division | Bug 1 |
| FR-002 | When `/test-mode advance` yields an empty phase queue and an active season still exists, the bot MUST call `execute_season_end` and report season completion | Bug 2 |
| FR-003 | When `/test-mode advance` yields an empty queue and no active season exists, the bot MUST report "nothing to advance" | Bug 2 |
| FR-004 | The `test_mode` `app_commands.Group` MUST have `guild_only=True` | Bug 3 |
| FR-005 | The `test_mode` `app_commands.Group` MUST have `default_permissions=None` so Discord applies no platform-level restriction | Bug 3 |
| FR-006 | All `/test-mode` subcommands MUST remain gated by `channel_guard` (interaction role + interaction channel) | Bug 3 |

### Non-Functional Requirements

- No new migrations, no new commands, no new dependencies.
- All existing 162 unit tests must continue to pass.
- No new test files required (existing `test_test_mode_service.py` already covers
  `get_next_pending_phase` mystery exclusion and the empty-queue path).
