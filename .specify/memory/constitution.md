<!--
SYNC IMPACT REPORT
==================
[2026-03-06 — v1.2.0 → v2.0.0: Formal scope expansion — driver profiles, teams, season management]
  Version change    : 1.2.0 → 2.0.0
  Bump rationale    : MAJOR — Principle VI backward-incompatibly redefined. The prior scope
                      restriction ("strictly limited to weather + schedule only") has been
                      replaced with an explicit incremental-expansion policy that formally
                      admits driver profile management, team management, and enhanced season
                      lifecycle tracking as ratified additions to the bot's mandate.
  Modified principles:
    - Principle V (Observability & Change Audit Trail) — extended to cover driver-state
      transitions and team mutations alongside weather/schedule changes.
    - Principle VI (Simplicity & Focused Scope → Incremental Scope Expansion) — scope gate
      redefined; still guards against uncontrolled expansion but now explicitly admits driver
      profile management, team management, and extended season lifecycle as in-scope domains.
    - Data & State Management — new entities (DriverProfile, TeamSeat) documented; season
      counter and division tier ordering rule added; performance and storage footprint note
      added per user request.
  Added sections    :
    - Principle VIII: Driver Profile Integrity (NEW)
    - Principle IX: Team & Division Structural Integrity (NEW)
  Removed sections  : None
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md      — Constitution Check gate is dynamic; no
         hardcoded principle list; no changes needed.
    ✅ .specify/templates/spec-template.md      — generic; no stale references.
    ✅ .specify/templates/tasks-template.md     — generic; aligns with I–IX.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Deferred TODOs    : Race results recording, raw driver points calculation, and penalty
                      management remain explicitly out of scope pending future formal
                      ratification under Principle VI's incremental-expansion process.

[2026-03-05 — v1.1.0 → v1.2.0: UX streamlining command standards]
  Version change    : 1.1.0 → 1.2.0
  Bump rationale    : MINOR — materially expanded guidance on command naming and UX
                      requirements. Added explicit subcommand-group mandate, command
                      grouping rule, single-interaction preference rule, and
                      hyphenated-command migration requirement to Bot Behavior
                      Standards.
  Modified sections :
    - Bot Behavior Standards: command naming expanded from a one-line convention
      to a multi-rule standard. Hyphenated top-level commands disallowed for new
      features; migration required for existing ones. Command grouping requirement
      added. Single-interaction preference rule added.
  Added sections    : None
  Removed sections  : None
  Templates confirmed aligned (no structural changes required):
    ✅ .specify/templates/plan-template.md      — generic; no hardcoded principle list.
    ✅ .specify/templates/spec-template.md      — generic; no stale references.
    ✅ .specify/templates/tasks-template.md     — generic; aligns with I–VII.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale refs.
    ✅ .specify/templates/checklist-template.md  — not impacted.
  Deferred TODOs    : None. All placeholders resolved.

[2026-03-05 — Bug fix: test mode mystery-round completion + permission]
  - Session intent: fix two bugs in the existing test-mode feature.
  - Constitution reused as-is; no principle amendments required.
  - Version 1.1.0 confirmed; no bump warranted (patch-level corrections to
    existing implementation — no governance or principle changes).

  Bug 1 — Mystery rounds incorrectly shown as "next round" in /season-status
    Root cause : `season_status` used `not (phase1_done AND phase2_done AND
                 phase3_done)` to find next pending round; mystery rounds have
                 all three permanently False → always reported as "next."
    Fix        : src/cogs/season_cog.py — added `r.format != RoundFormat.MYSTERY`
                 guard to the `next_round` generator expression.
    Principle  : IV (mystery rounds skip all phases), VI (focused output).

  Bug 2 — Season not ending after advancing all non-mystery phases via test mode
    Root cause : The "all phases done" early-return path in /test-mode advance
                 returned "nothing to advance" without attempting season end,
                 leaving the season active if the previous Phase-3 advance's
                 internal execute_season_end call was skipped (e.g. past-dates
                 fast-path cleared data before the cog's own check could run,
                 or a Discord API error aborted the call mid-execution).
    Fix        : src/cogs/test_mode_cog.py — replaced the bare followup.send
                 early return with a check: if an active season still exists
                 when the queue is empty, cancel any pending scheduled job and
                 call execute_season_end immediately; otherwise send the
                 "nothing to advance" message.
    Principle  : IV (season lifecycle), V (no silent state mutations).

  Bug 3 — Test-mode commands accessible only to server admins, not to
           interaction-role holders configured via /bot-init
    Root cause : app_commands.Group for /test-mode had no `default_permissions`
                 specified (discord.py MISSING sentinel), leaving Discord to use
                 any previously cached per-server permission that may have been
                 set to manage_guild from an earlier sync. Also missing
                 `guild_only=True`, meaning the group was technically usable in
                 DMs where `channel_guard`'s Member check would block all users.
    Fix        : src/cogs/test_mode_cog.py — added `guild_only=True` and
                 `default_permissions=None` to the Group definition.
                 `default_permissions=None` forces Discord to reset to
                 "no Discord-level restriction" on next tree sync, leaving
                 `channel_guard` (interaction_role_id check) as the sole gate,
                 which already satisfies Principle I Tier-1 access control.
    Principle  : I (interaction role gates all commands), VII (guild channel only).

  Bug 4 — Mystery round notice never fires during test-mode advance
    Root cause : APScheduler job `mystery_r{id}` fires on a real-time schedule;
                 in test mode the scheduler never runs, so Mystery round player-
                 facing notices were silently skipped. `get_next_pending_phase`
                 also filtered out Mystery rounds entirely, making them invisible
                 to the advance queue.
    Fix        : src/services/test_mode_service.py — widened query to include all
                 rounds; returns `PhaseEntry(phase_number=0)` sentinel when a
                 Mystery round has `phase1_done=0`; skips if `phase1_done=1`.
                 src/cogs/test_mode_cog.py — added `phase_number == 0` dispatch
                 block: calls `run_mystery_notice`, then sets `phase1_done=1` on
                 success. `phase1_done` reused as "notice sent" proxy; safe
                 because `all_phases_complete` and `build_review_summary` already
                 filter `format != 'MYSTERY'`.
    Principle  : IV (mystery rounds have no phases but still have a pre-pipeline
                 notice step), V (no silent skips of expected bot actions).

  Bug 5 — Reset raises FOREIGN KEY constraint failed when forecast_messages exists
    Root cause : `reset_service` deleted `sessions` and `phase_results` before
                 `rounds`, but omitted `forecast_messages` which has
                 `REFERENCES rounds(id)` with FK enforcement ON. Any reset after
                 Phase 1 had run violated the FK and aborted the transaction.
    Fix        : src/services/reset_service.py — added
                 `DELETE FROM forecast_messages WHERE round_id IN (...)`
                 after `phase_results` and before `rounds` in the FK-safe chain.
                 Regression test added: `test_reset_deletes_forecast_messages`.
    Principle  : III (reset must complete cleanly to allow a fresh season start),
                 V (no silent data integrity failures).

  Bug 6 — Advance logs use internal DB id instead of user-visible round number
    Root cause : Log lines in the advance command emitted `entry["round_id"]`
                 (the `rounds.id` primary key), which is meaningless to league
                 managers reading logs. `PhaseEntry` had no `round_number` field.
    Fix        : src/services/test_mode_service.py — added `round_number: int`
                 field to `PhaseEntry`; SELECT now includes `r.round_number`.
                 src/cogs/test_mode_cog.py — log line now emits
                 `round=<round_number>` and `id=<round_id>` for all paths.
    Principle  : V (observable, human-legible audit trail).

  Templates confirmed aligned (no changes needed):
    ✅ .specify/templates/plan-template.md
    ✅ .specify/templates/spec-template.md
    ✅ .specify/templates/tasks-template.md
    ✅ .specify/templates/agent-file-template.md
    ✅ .specify/templates/checklist-template.md
  Files modified:
    ✅ src/cogs/season_cog.py            — next_round mystery exclusion (Bug 1)
    ✅ src/cogs/test_mode_cog.py         — advance safety net + Group permissions
                                           + mystery notice dispatch + round_number log
                                           (Bugs 2, 3, 4, 6)
    ✅ src/services/test_mode_service.py — PhaseEntry.round_number + phase_number=0
                                           sentinel in get_next_pending_phase (Bugs 4, 6)
    ✅ src/services/reset_service.py     — forecast_messages FK-safe delete (Bug 5)
    ✅ tests/unit/test_test_mode_service.py — updated mystery tests (Bug 4)
    ✅ tests/unit/test_reset_service.py  — regression test for FK reset (Bug 5)
  No deferred TODOs. Last Amended date remains 2026-03-03 (no principle changes).

[2026-03-05 — Bug fix: visual output correction pass]
  - Constitution reused as-is; no principle amendments required for visual output bug fixes.
  - Session intent: identify and correct bugs in the bot's visual/message output on an
    already-existing SpecKit-driven codebase.
  - All placeholder tokens remain resolved; no bracket tokens present.
  - Version 1.1.0 consistent across all sections; no version bump warranted (no content
    amendments — reuse session only).
  - Templates confirmed aligned with Principles I–VII:
      ✅ .specify/templates/plan-template.md    — Constitution Check gate is dynamic; no
           hardcoded principle list; no changes needed.
      ✅ .specify/templates/spec-template.md    — generic structure; no stale references.
      ✅ .specify/templates/tasks-template.md   — phase structure generic; aligns with I–VII.
      ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
      ✅ .specify/templates/checklist-template.md  — no issues.
  - No stale agent-specific references detected.
  - Last Amended date remains 2026-03-03 (no content amendments this session).
  - No deferred TODOs.

[2026-03-04 — New feature addition: constitution validation pass]
  - Constitution reused as-is; no new principles required for incremental feature work.
  - Session intent: validate constitution readiness before beginning a new SpecKit feature
    on an already-existing codebase.
  - All placeholder tokens remain resolved; no bracket tokens present.
  - Version 1.1.0 footer consistent with all sections.
  - Templates confirmed aligned:
      ✅ .specify/templates/plan-template.md    — Constitution Check gate is dynamic ("based
           on constitution file"), no hardcoded principle list; no changes needed.
      ✅ .specify/templates/spec-template.md    — generic structure; no stale references.
      ✅ .specify/templates/tasks-template.md   — phase structure generic; aligns with I–VII.
      ✅ .specify/templates/agent-file-template.md — all generic placeholders; no stale names.
      ✅ .specify/templates/checklist-template.md  — not in scope for this pass; no issues.
  - No stale agent-specific references detected.
  - No version bump required; Last Amended date remains 2026-03-03 (no content amendments).
  - No deferred TODOs.

[2026-03-04 — Session reuse: behavior correction]
  - Constitution reused as-is from previous session (no principle amendments).
  - Session intent: identify and correct a bug / incorrect runtime behavior in the application.
  - All placeholder tokens remain resolved; no bracket tokens present.
  - Version 1.1.0 footer consistent with all sections.
  - Templates (plan, spec, tasks, agent-file) confirmed aligned with Principles I–VII.
  - No stale agent-specific references detected.
  - No version bump required; Last Amended date remains 2026-03-03.
  - No deferred TODOs.

[2026-03-03 — v1.0.0 → v1.1.0]
Version change    : 1.0.0 → 1.1.0
Modified principles:
  - Principle I (Trusted Configuration Authority) — split into two explicit access tiers:
      bot-interaction role (general commands) vs. trusted/config role (season management)
  - Principle IV (Deterministic & Auditable Weather Generation) — replaced generic seeding
      language with the concrete three-phase pipeline as a non-negotiable architectural
      constraint (Phase 1 T-5d, Phase 2 T-2d, Phase 3 T-2h), Mystery Round exception,
      and amendment invalidation semantics
  - Principle V (Observability & Change Audit Trail) — explicitly names the calculation
      log channel as the target for phase computation records
Added sections    :
  - Principle VII: Output Channel Discipline (new)
  - Bot Behavior Standards: round format taxonomy, weather slot counts, text-first note
  - Data & State Management: inter-phase state persistence, amendment invalidation clearing
Removed sections  : None

Templates requiring updates:
  ✅ .specify/templates/constitution-template.md — source template; no changes required
  ✅ .specify/templates/plan-template.md — Constitution Check gates now reference I–VII;
       template is generic enough; no structural edits needed
  ✅ .specify/templates/spec-template.md — generic structure; no domain-specific changes needed
  ✅ .specify/templates/tasks-template.md — phase structure aligns with updated principles
  ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale references
  (no files found in .specify/templates/commands/)

Follow-up TODOs   : None — all placeholders resolved
-->

# F1 League Weather Randomizer Bot Constitution

## Core Principles

### I. Trusted Configuration Authority

Two distinct access tiers MUST be maintained and configured independently:

1. **Interaction role**: A server-level Discord role that gates all bot commands. Only members
   holding this role may issue any command to the bot. Commands MUST be accepted only when
   sent in a single, administrator-configured interaction channel. Both the role and the
   channel are set during initial bot setup, separately from season configuration.

2. **Season/config authority**: A subset of interaction-role members (e.g., Race Director,
   Admin) who are additionally permitted to create or mutate season data — divisions, track
   schedules, race dates/times, round formats, and any amendments. This tier MUST also be
   explicitly configured; holding the general interaction role alone is insufficient.

The bot MUST reject out-of-channel commands silently (no response) and MUST reject
unauthorized configuration commands with a clear, actionable permission error.
No implicit super-user status exists for either tier.

**Rationale**: Separating "who can read weather" from "who can change the season" prevents
casual members from accidentally triggering configuration commands, while still allowing
the broader league membership to interact with the bot in controlled ways.

### II. Multi-Division Isolation

The bot MUST support multiple divisions (e.g., Pro, Am, Open) operating concurrently within
a single Discord server. Each division's calendar, weather outputs, and runtime state MUST be
stored and evaluated as a fully independent data domain. A command or mutation targeting
Division A MUST NOT read, write, or in any way affect Division B. Division identifiers MUST
be explicit in every configuration command and every output message.

**Rationale**: League servers routinely run tiered divisions in parallel. Cross-contamination
of schedules or weather seeds would undermine competitive fairness and create administrative
confusion.

### III. Resilient Schedule Management

The bot MUST accommodate mid-season plan changes at any point in an active season:

- **Track substitutions**: replace a scheduled circuit with another.
- **Postponements**: shift a race date and/or time forward without losing round identity.
- **Cancellations**: remove a round and resequence the calendar cleanly.

Each change MUST be applied atomically; partial updates are not permitted. The bot MUST
preserve the original schedule alongside the current one so the full amendment history is
recoverable. Re-generating weather after a schedule change MUST use a fresh, distinct seed
and MUST log the reason for re-generation.

**Rationale**: Real leagues face unavoidable logistical disruptions. The bot MUST absorb these
without requiring a full season reset or manual data repair.

### IV. Three-Phase Weather Pipeline (NON-NEGOTIABLE)

Weather generation for every non-Mystery round MUST follow exactly three sequential phases,
each triggered automatically at a fixed horizon before the scheduled round start time:

- **Phase 1 — Rain Probability** (T − 5 days): Compute `Rpc` from the track base factor and
  two independent random draws. Log all inputs and the result. Post a public probability
  message to the division's weather forecast channel.
- **Phase 2 — Session Type Draw** (T − 2 days): Use the `Rpc` value persisted in Phase 1 to
  populate a 1 000-entry weighted map of Rain / Mixed / Sunny slots; draw once per session
  in the round. Log inputs, weights, and draws. Post session-type forecasts to the division
  channel.
- **Phase 3 — Final Slot Generation** (T − 2 hours): Use the `Rpc` value and each session's
  Phase 2 type to build per-session weighted maps; draw `Nslots` times (randomly chosen
  within the session-type slot-count bounds). Log the full draw sequence. Post the final
  weather layout to the division channel.

**Mystery Rounds** are the sole exception: Phases 1, 2, and 3 MUST NOT be executed and the
bot MUST NOT post any weather message for that round.

**Amendment invalidation**: If a round is amended (track change, postponement, format change)
after any phase has completed, ALL previously posted weather outputs for that round are
invalidated. The bot MUST immediately post an invalidation notice to the division channel and
re-execute whichever phases have already passed their horizon. Previously computed `Rpc`,
session-type draws, and slot draws MUST be discarded from active state but retained in the
audit log with an `INVALIDATED` status marker.

All random draws MUST be logged with the input state at the moment of drawing so any result
can be independently audited or challenged.

**Rationale**: A locked pipeline with defined horizons gives drivers predictable information
cadence and eliminates any window for post-hoc manipulation. The Mystery Round exception
preserves competitive surprise by design.

### V. Observability & Change Audit Trail

Every configuration mutation — season setup, track substitution, postponement, cancellation,
format change, trusted-role grant or revoke, driver-state transition, team assignment change,
and team definition add/modify/remove — MUST produce a timestamped audit log entry recording:
actor (Discord user ID and display name), division (where applicable), change type, previous
value, and new value.

All three weather phases MUST log their full computation to the designated calculation log
channel (configured separately from the division weather forecast channels): inputs, random
draws, intermediate values, and final outputs. Phase log entries MUST include the round
identifier, division, and UTC timestamp.

All mutations that affect a published schedule MUST post a human-readable confirmation to the
calculation log channel. The bot MUST NOT silently accept or silently discard any command.

**Rationale**: League administrators and drivers need an unambiguous, channel-visible record
of computations and changes, especially when disputing weather outcomes or schedule
alterations.

### VI. Incremental Scope Expansion

The bot's scope expands incrementally, one ratified feature at a time. The following domains
are formally in-scope as of this version:

1. **Weather generation**: the three-phase pipeline (Principle IV) remains the core function.
2. **Season and division lifecycle**: setup, activation, completion, cancellation, round
   scheduling, and amendments.
3. **Driver profile management**: signup workflow, state machine enforcement, Discord User ID
   reassignment, and historical participation tracking.
4. **Team management**: configurable team definitions per division, seat assignment, and
   the Reserve team ruleset.

The following domains remain explicitly **out of scope** until separately ratified:

- Race results recording and raw score entry.
- Driver championship standings computation.
- Penalty and protest adjudication.
- Financial or licensing workflows.

Every proposed new command or data concern MUST be evaluated against the current scope
boundary before implementation begins. Features that do not fall within a ratified domain
MUST be rejected or deferred via the governance process below.

The current output format is text-only. Image-based output is a known planned evolution and
MUST be designed as an additive change that does not break existing text output paths.

**Rationale**: Controlled, documented scope expansions allow the bot to grow toward full
league management without sacrificing reliability or auditability. Each expansion is gated
behind a formal ratification to prevent unplanned feature creep.

### VII. Output Channel Discipline

The bot MUST post messages to exactly two categories of channel, and no others:

1. **Per-division weather forecast channel** (one per division, configured at season setup):
   receives only Phase 1, Phase 2, Phase 3 public weather messages, and amendment
   invalidation notices for that division.
2. **Calculation log channel** (one per server, configured at bot setup): receives all phase
   computation logs, configuration mutation confirmations, and audit trail entries.

The bot MUST NOT post to any other channel, including the interaction channel where commands
are issued. Unsolicited messages in unregistered channels are not permitted.

**Rationale**: Keeping output in known, designated channels prevents noise in general server
channels and makes it trivial for drivers and admins to find the right information.

### VIII. Driver Profile Integrity

Every Discord user within a server is represented by at most one driver profile, keyed on
their Discord User ID in server scope. The following rules are non-negotiable:

- **State machine enforcement**: A driver's current state MUST only change via the transitions
  listed in the specification. Any transition not in the approved list MUST be rejected with a
  clear error. No code path may bypass the state machine to set state directly.
- **Immutability of former drivers**: Once the `former_driver` flag is set to `true` (triggered
  by the driver's first participation in a round), the profile record MUST NOT be deleted — only
  modified. An attempt to delete such a record MUST be rejected.
- **Deletion rule**: If a driver transitions to *Not Signed Up* and `former_driver` is `false`,
  their database record MUST be deleted automatically as part of the same transaction.
- **User ID reassignment**: Only a server administrator may change the Discord User ID
  associated with a driver profile (to handle account changes). The reassignment MUST be logged
  as an audit event (Principle V) with both the old and new User ID.
- **Test-mode overrides**: When test mode is active, administrators MAY manually set
  `former_driver` to `true` or `false`, and MAY directly assign *Not Signed Up* drivers to
  *Unassigned* or *Assigned* state, bypassing the normal signup flow. All such overrides MUST
  still produce audit log entries.
- **Absent profile semantics**: A Discord user with no database record is treated as *Not Signed
  Up*. The bot MUST NOT error or warn on absence — it is the canonical default state.

**Rationale**: The driver profile is a long-lived, server-scoped identity record. Strict state
machine enforcement and immutability guarantees prevent data loss from accidental operations
and ensure the historical participation record is always trustworthy.

### IX. Team & Division Structural Integrity

Teams and division tiers carry structural invariants that MUST be enforced at every mutation
point:

- **Reserve team**: The Reserve team MUST always exist in every division and MUST NOT be
  removable, renameable, or otherwise modified by any user command. Its seat count is
  unlimited.
- **Configurable teams**: The standard ten constructor teams (Alpine, Aston Martin, Ferrari,
  Haas, McLaren, Mercedes, Racing Bulls, Red Bull, Sauber, Williams) each carry exactly 2 seats
  by default. A server administrator MAY add, modify, or remove configurable teams from the
  server-level default set at any time. Changes to the default set MAY be applied to all
  divisions of the current season ONLY during the `SETUP` lifecycle phase.
- **Division isolation**: A team definition or seat assignment in Division A MUST NOT affect
  Division B. Team data is partitioned per division, per season.
- **Sequential tier ordering**: Before a season may be approved (transitioned from `SETUP` to
  `ACTIVE`), all configured divisions MUST have tier values that form a gapless sequence
  starting at 1 (e.g., 1, 2, 3 — not 1, 3). The bot MUST block season approval and return a
  clear diagnostic if this rule is violated. Divisions are stored and displayed in ascending
  tier order, with tier 1 representing the highest tier.
- **Tier as supplementary ID**: A division's tier MAY be used as a secondary identifier in
  commands and logs, but the division name remains the canonical label in all bot output.

**Rationale**: Structural invariants on teams and tiers prevent silent misconfiguration that
would compromise competitive fairness — a division with a gap in its tier sequence or a
missing Reserve team would produce ambiguous or incorrect league operations.

## Bot Behavior Standards

All Discord slash commands MUST follow the `/domain action` subcommand-group convention — a
top-level slash command group (`/domain`) with named action subcommands. Hyphenated top-level
commands (e.g. `/season-setup`, `/round-add`) are NOT permitted for new features. Any existing
hyphenated command MUST be migrated to the subcommand-group form (e.g. `/season setup`,
`/round add`) in the same change window as any UX-streamlining work targeting that domain.

- **Command grouping**: Commands that share an operational domain (season lifecycle, track
  configuration, round amendments) MUST be registered under a single command group so that
  Discord's autocomplete surfaces all related actions together. Lone top-level commands for
  domain-specific actions are not acceptable for new features.
- **Single-interaction preference**: Every command MUST be completable in a single Discord
  interaction where technically feasible. Multi-step wizard flows are permitted ONLY when
  Discord's API cannot accommodate all required inputs in one command (e.g., more than
  25 parameters); in such cases, each step MUST provide clear inline guidance on what the
  user must do next.
- Commands that mutate persistent state MUST present an ephemeral confirm/cancel prompt before
  executing, except where the change is trivially reversible within the same interaction.
- Configuration command responses MUST be ephemeral (visible only to the invoking user).
  Weather generation results MUST be posted publicly per Principle VII.
- The bot MUST acknowledge any command within 3 seconds; long-running operations MUST use
  Discord's deferred response mechanism to avoid timeout failures.
- Error messages MUST identify the specific problem and suggest a corrective action. Generic
  "something went wrong" messages are not acceptable.
- The bot MUST validate all inputs before executing any command; invalid inputs MUST be
  rejected with feedback before any state is modified.

### Round Formats

Four round formats are defined. Session composition and weather slot capacities are fixed per
format and MUST NOT be altered at runtime:

| Format | Sessions | Slot capacities |
|--------|----------|-----------------|
| Normal | Short Qualifying, Long Race | Qual: 2 · Race: 3 |
| Sprint | Short Sprint Qual, Long Sprint Race, Short Feature Qual, Long Feature Race | SQ: 2 · SR: 1 · FQ: 2 · FR: 3 |
| Mystery | (none — all phases skipped) | — |
| Endurance | Full Qualifying, Full Race | Qual: 3 · Race: 4 |

Session types and their maximum weather slot counts are the authoritative values used by
Phase 3 when determining `Nslots`. No session may have fewer than 1 slot (or 2 if determined
mixed by Phase 2).

## Data & State Management

- All season data (divisions, rounds, tracks, dates, weather results, audit log) MUST be
  persisted to durable storage. In-memory state alone is not acceptable.
- Each season MUST carry an explicit lifecycle state: `SETUP` → `ACTIVE` → `COMPLETED`.
  - In `SETUP`: divisions, tracks, schedules, and round formats may be freely configured.
  - In `ACTIVE`: amendments (track substitutions, postponements, format changes, cancellations)
    are permitted; wholesale reconfiguration of the base schedule is not.
  - In `COMPLETED`: the season is read-only; no mutations are allowed.
- **Inter-phase state**: The `Rpc` value computed in Phase 1 MUST be persisted against its
  round and division and remain available until Phase 3 completes or the round is cancelled.
  Phase 2 session-type draws MUST similarly be persisted per session until Phase 3 consumes
  them. In-memory caching of these values is permitted only as a read-through layer; the
  durable store is always authoritative.
- **Amendment invalidation**: When a round amendment triggers phase invalidation (Principle IV),
  the bot MUST atomically: (a) mark existing phase outputs `INVALIDATED` in the audit log,
  (b) clear active phase state for that round, and (c) re-execute all phases whose time
  horizons have already passed. This MUST happen in a single transaction; a partial update
  is not permitted.
- Data schemas MUST be versioned. Migrations MUST be applied automatically on bot startup with
  a clear log of which migrations ran.
- A full data export of any division's season (schedule, amendments, weather log, phase
  computation records, audit trail) MUST be available to trusted users on demand.

### New Entities (v2.0.0)

**DriverProfile** (server-scoped, one row per Discord user per server):
- `discord_user_id` (TEXT, PK within server) — canonical key; may be updated by admin only.
- `current_state` (ENUM) — enforced by state machine (Principle VIII).
- `former_driver` (BOOLEAN, default false) — immutability gate (Principle VIII).
- `ban_counts` (race_bans INT, season_bans INT, league_bans INT) — accumulated ban history.
- Current and historical season assignment data linked via a normalized join table,
  avoiding redundant column-per-division patterns.

**TeamSeat** (per division, per season):
- Tracks which driver (if any) occupies each seat of each team in each division.
- Reserve team rows are auto-created on division creation; configurable team rows follow
  the server-level default set unless overridden during `SETUP`.

**Season counter** (server-scoped scalar):
- A single integer per server recording the highest completed-or-cancelled season number.
  Defaults to 0. Incremented on season cancellation or completion. New seasons display
  this value + 1 as their number.

### Performance & Storage Considerations

The bot is designed for small-to-medium Discord servers (tens to low hundreds of concurrent
drivers per server). The projected storage growth per season per division is modest:

- **DriverProfile rows**: O(number of ever-signed-up drivers) — expected dozens to low hundreds
  per server; each row is <1 KB.
- **TeamSeat rows**: one row per seat per team per division per season; with 10 standard teams
  × 2 seats + Reserve = ~21 rows per division per season.
- **Audit log rows**: one entry per mutation event; expected hundreds per season; small.
- **Phase result rows**: unchanged from v1.x; 3 rows per round per division.

No bulk computation, aggregation queries, or full-table scans are expected in hot paths.
All primary access patterns are single-row lookups by surrogate key or short-range scans
by (server_id, season_id, division_id). Standard SQLite indexes on these columns are
sufficient; no additional caching layer is required at the current scale. If the server
population grows beyond ~500 concurrent drivers, migrating the backing store from SQLite
to a client-server RDBMS (e.g., PostgreSQL) should be evaluated.

## Governance

This constitution supersedes all other development practices and conventions for this project.
Amendments require:

1. A documented rationale for the proposed change.
2. A version bump per the semantic versioning policy below.
3. Updates to all affected templates and runtime guidance files before the amendment is merged.

**Versioning policy**:

- **MAJOR**: Removal or backward-incompatible redefinition of a Core Principle.
- **MINOR**: Addition of a new principle, section, or materially expanded guidance.
- **PATCH**: Clarifications, wording improvements, or non-semantic refinements.

All pull requests MUST include a Constitution Check confirming compliance with Principles I–IX
before merge. Any deliberate violation of a principle MUST be documented in the plan's
Complexity Tracking table with a justification for why the simpler compliant path is
insufficient.

**Version**: 2.0.0 | **Ratified**: 2026-03-03 | **Last Amended**: 2026-03-06
