# Feature Specification: Test Mode Bug Fixes

**Feature Branch**: `009-test-mode-bugfix`
**Created**: 2026-03-05
**Status**: Implemented
**Input**: Bug reports from live test-mode usage of feature `002-test-mode`

## Clarifications

### Session 2026-03-05

- Q: Should the spec expand to cover all 6 bugs fixed on this branch (original 3 + mystery-notice-in-advance, forecast_messages-on-reset, round_number-in-logs)? → A: Yes — expand spec with User Story 4/5/6, FR-007–FR-009, and acceptance scenarios for all 3 new bugs.
- Q: Should a Glossary / Key Data Shapes subsection be added defining `PhaseEntry` fields, valid `phase_number` values, and the mystery-notice proxy? → A: Yes — add `### Glossary / Key Data Shapes` subsection.
- Q: Should the spec explicitly address the scenario where `run_mystery_notice` raises after `phase1_done` is set (silent channel-send failure)? → A: Defer — current User Story 4 scenario 3 and the code-order guarantee (DB write only after successful notice send) are sufficient; no spec change needed.
- Q: Should Bug 5 (reset + `forecast_messages` FK) have automated test coverage? → A: Yes — add a unit test requirement; the test must be written and passing before this spec is considered done.
- Q: Should `plan.md` and `tasks.md` be updated to add entries for Bugs 4/5/6, all marked done? → A: Yes — update both files to keep spec/plan/tasks triad in sync per Principle VII.

### Session 2026-03-05 (clarification round 2)

- Q: What Discord permissions does the bot require for forecast message deletion (`_discord_delete`)? → A: Only `View Channel`, `Send Messages`, and `Read Message History` in the forecast channel; `Manage Messages` is NOT required because the bot always deletes its own messages.
- Q: Should in-test-mode forecast message deletions (Phase 1, Phase 2, cross-round Phase 3) be executed immediately or deferred? → A: Deferred (intended behavior) — all deletions skipped during test-mode advances accumulate as DB rows and are executed in bulk by `flush_pending_deletions` when test mode is disabled.
- Q: How should `_discord_delete` failures (`Forbidden`, `HTTPException`) be surfaced? → A: Log-only — deletion errors are recorded in bot logs and silently discarded at the application level; no user-facing signal is required.
- Q: Should the deferred flush model have explicit test coverage in this spec? → A: Yes — add a unit test requirement: (1) `delete_forecast_message` skips the Discord call and retains the DB row when `test_mode_active = True`; (2) `flush_pending_deletions` deletes all accumulated rows; tests added to an appropriate existing test file.
- Q: Should the spec Status and test count be updated to reflect the two new unwritten test requirements? → A: Yes — set `Status: In Progress`; raise target test count to 166; update `plan.md` and `tasks.md` with a task for the two new tests.

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

### User Story 4 — Mystery round notice is dispatched when advancing via test mode (Priority: P1)

A user with the interaction role runs `/test-mode advance` on a server that has a Mystery
round whose notice has not yet been sent (`phase1_done = 0`). The bot fires the mystery
round notice to the division's forecast channel (identical to the APScheduler path) and
marked the round as noticed (`phase1_done = 1`). Subsequent calls to `/test-mode advance`
do not re-send the notice.

**Why this priority**: Without this fix, Mystery rounds are silently skipped — the APScheduler
job (`mystery_r{id}`) never fires in test mode and the notice is never sent. This leaves the
league's participants without the teaser message the feature was designed to provide.

**Independent Test**: Enable test mode; seed a Mystery round with `phase1_done = 0`; call
`/test-mode advance`; confirm notice appears in the forecast channel and `phase1_done = 1`;
call `/test-mode advance` again and confirm the Mystery round is not mentioned.

**Acceptance Scenarios**:

1. **Given** a Mystery round with `phase1_done = 0` is the next pending item, **When**
   `/test-mode advance` is issued, **Then** `run_mystery_notice` is called with the correct
   `round_id`, the round's `phase1_done` is set to `1`, and the bot replies with a success
   ephemeral confirming the notice was posted.
2. **Given** a Mystery round with `phase1_done = 1`, **When** `get_next_pending_phase` is
   called, **Then** that round is excluded from the queue (not returned as `phase_number=0`).
3. **Given** `run_mystery_notice` raises an exception, **When** `/test-mode advance` is
   issued for that round, **Then** the bot replies with an error ephemeral, does NOT set
   `phase1_done = 1`, and does NOT advance further.

---

### User Story 5 — Reset correctly removes all forecast messages (Priority: P1)

A server administrator runs `/bot-reset` on a server that has had at least one Phase 1 run
(which populates the `forecast_messages` table). The reset completes without FK constraint
violation and the `forecast_messages` table is fully cleared for that server's rounds.

**Why this priority**: Before this fix, any reset attempt on a server with Phase 1 data
resulted in an unhandled database error (`FOREIGN KEY constraint failed`) because
`forecast_messages` holds a `REFERENCES rounds(id)` FK but was not deleted before the
rounds themselves were deleted.

**Independent Test**: Run Phase 1 for at least one division; confirm rows exist in
`forecast_messages`; issue `/bot-reset`; confirm the command succeeds and
`forecast_messages` is empty for that server.

**Acceptance Scenarios**:

1. **Given** `forecast_messages` contains rows for a server's rounds, **When** `/bot-reset`
   is issued, **Then** all matching `forecast_messages` rows are deleted before the `rounds`
   rows, and the command completes without error.
2. **Given** `forecast_messages` is empty (no Phase 1 has been run), **When** `/bot-reset`
   is issued, **Then** the command still completes without error.
3. **Given** the deletion order in `reset_service`, **Then** the order is:
   sessions → phase_results → forecast_messages → rounds → divisions → seasons.

---

### User Story 6 — Test-mode advance log lines show user-visible round number (Priority: P3)

A bot operator reviewing server logs after a `/test-mode advance` can identify which
league round was advanced by the user-visible round number (e.g. `round=3`) rather than
the internal database primary key. The internal DB id is also retained in the log for
correlation with raw database queries.

**Why this priority**: Internal IDs are meaningless to league managers reviewing logs;
round numbers directly match what users see in `/season-status` and results messages,
making log triage faster and reducing support overhead.

**Independent Test**: Enable test mode; issue `/test-mode advance`; check log output;
 confirm the line contains both `round=<round_number>` and `id=<db_id>`.

**Acceptance Scenarios**:

1. **Given** a pending phase entry, **When** `/test-mode advance` dispatches a phase runner,
   **Then** the log line includes `round=<round_number>` (user-visible) and
   `id=<db_id>` (internal).
2. **Given** a Mystery notice dispatch, **When** logged, **Then** the log line includes
   `round=<round_number>` and `round_id=<db_id>`.
3. **Given** `PhaseEntry`, **Then** it exposes both `round_number: int` (user-visible) and
   `round_id: int` (DB primary key).

---

### Edge Cases

- What if the season is deleted between `get_next_pending_phase` returning `None` and the
  safety-net `get_active_season` call? The `execute_season_end` idempotency guard handles
  this: if no active season is found it returns immediately.
- What if Discord's tree sync has not yet propagated the updated `default_permissions`
  to all servers? Users may still see the old restriction until the next sync completes
  (up to 1 hour for global commands).
- What if `run_mystery_notice` fails after `phase1_done` would have been set? The cog
  updates `phase1_done` only AFTER a successful `run_mystery_notice` call, so a failure
  leaves the round re-tryable on the next `/test-mode advance`.
- What if multiple rounds' `forecast_messages` rows exist across divisions? The
  `forecast_messages` delete uses `WHERE round_id IN (...)` scoped to the reset server's
  round IDs only, leaving other servers' data untouched.
- In test mode, forecast channel messages are NOT deleted between phases or between rounds
  during `/test-mode advance`. The test-mode guard in `delete_forecast_message` silently
  skips all Discord deletions and retains the DB row. All accumulated rows are bulk-deleted
  by `flush_pending_deletions` when test mode is disabled via `/test-mode toggle`. This is
  intentional: rapid test-mode advances would otherwise cause noisy delete/re-post cycles
  in the forecast channel.

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
| FR-007 | `get_next_pending_phase` MUST return a `PhaseEntry` with `phase_number=0` for a Mystery round whose `phase1_done = 0`; MUST skip (continue) if `phase1_done = 1` | Bug 4 |
| FR-008 | When `/test-mode advance` receives a `phase_number=0` entry, it MUST call `run_mystery_notice`, set `phase1_done = 1` on success, and reply with a notice-sent confirmation; on failure it MUST reply with an error ephemeral and NOT set the flag | Bug 4 |
| FR-009 | `reset_service` MUST delete `forecast_messages` rows (scoped to the server's round IDs) AFTER `phase_results` and BEFORE `rounds` in every reset transaction | Bug 5 |
| FR-010 | `PhaseEntry` MUST expose `round_number: int` (user-visible); advance log lines MUST include `round=<round_number>` alongside `id=<round_id>` | Bug 6 |

### Non-Functional Requirements

- No new migrations, no new commands, no new dependencies.
- The bot requires `View Channel`, `Send Messages`, and `Read Message History` in every configured forecast channel. `Manage Messages` is NOT required — `_discord_delete` calls `channel.get_partial_message(id).delete()` exclusively on messages the bot itself posted. A `Forbidden` error in the forecast channel indicates a missing `View Channel` overwrite for the bot's role, not a missing `Manage Messages` grant.
- Forecast message deletion failures (`Forbidden`, `HTTPException`) are log-only. The DB row is removed regardless of Discord outcome (orphan-safe design: stale rows are not re-attempted). No user-facing error signal is emitted for deletion failures.
- Two additional tests are required for the deferred flush model (target: 166 total):
  - `test_delete_forecast_message_skips_in_test_mode` — asserts that when `test_mode_active = True`, `_discord_delete` is NOT called and the DB row is retained after `delete_forecast_message` returns.
  - `test_flush_pending_deletions_clears_accumulated_rows` — asserts that after multiple skipped deletions, `flush_pending_deletions` calls `_discord_delete` for each accumulated row and leaves `forecast_messages` empty.
  Both tests are added to `tests/unit/test_forecast_cleanup.py`.
- The `phase1_done = 1` flag on Mystery rounds is reused as the "notice sent" proxy; this
  is safe because `all_phases_complete` and `build_review_summary` already filter
  `format != 'MYSTERY'`, so the flag has no side-effects on season-end or review logic.
- All 166 unit tests must pass:
  - 162 original
  - +1 for Bug 4: `test_mystery_round_notice_pending_returns_entry` (replaces the
    now-incorrect `test_mystery_rounds_excluded`)
  - +1 for Bug 4: `test_mystery_round_notice_done_excluded` (new)
  - +1 for Bug 5: `test_reset_deletes_forecast_messages` — verifies that calling
    `execute_reset` on a server with `forecast_messages` rows completes without error and
    leaves no matching rows in `forecast_messages` (guards against FK regression)
  - +1 for deferred flush: `test_delete_forecast_message_skips_in_test_mode` — asserts
    `_discord_delete` is NOT called and the DB row is retained when `test_mode_active = True`
  - +1 for deferred flush: `test_flush_pending_deletions_clears_accumulated_rows` — asserts
    all accumulated rows are deleted and `forecast_messages` is empty after the call
- No new test files required; deferred-flush tests added to `tests/unit/test_forecast_cleanup.py`.

### Glossary / Key Data Shapes

#### `PhaseEntry` (TypedDict — `src/services/test_mode_service.py`)

| Field | Type | Description |
|-------|------|-------------|
| `round_id` | `int` | Database primary key (`rounds.id`) — for DB operations only |
| `round_number` | `int` | User-visible round number (`rounds.round_number`) — for display and logging |
| `division_id` | `int` | Database primary key of the owning division |
| `phase_number` | `int` | See valid values below |
| `track_name` | `str` | Human-readable track name; `"Mystery"` for mystery-notice entries |
| `division_name` | `str` | Human-readable division name |

**Valid `phase_number` values:**

| Value | Meaning |
|-------|---------|
| `0` | Mystery round notice sentinel — triggers `run_mystery_notice`; not a real phase |
| `1` | Phase 1 (forecast + weather roll) |
| `2` | Phase 2 (results post) |
| `3` | Phase 3 (standings update + cleanup) |

#### Mystery-notice proxy (`phase1_done` flag)

There is no dedicated `notice_sent` column. Instead, `rounds.phase1_done = 1` is reused as
the "mystery notice sent" marker for `format = 'MYSTERY'` rounds. This is safe because:

- `all_phases_complete` filters `format != 'MYSTERY'`, so the flag never counts toward
  season-end detection.
- `build_review_summary` shows "N/A" for all phases of mystery rounds regardless of flag
  state.
- `get_next_pending_phase` checks `format` first; `phase1_done` is only meaningful as an
  exclusion signal within the mystery branch.
