<!--
SYNC IMPACT REPORT
==================
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
format change, and trusted-role grant or revoke — MUST produce a timestamped audit log entry
recording: actor (Discord user ID and display name), division, change type, previous value,
and new value.

All three weather phases MUST log their full computation to the designated calculation log
channel (configured separately from the division weather forecast channels): inputs, random
draws, intermediate values, and final outputs. Phase log entries MUST include the round
identifier, division, and UTC timestamp.

All mutations that affect a published schedule MUST post a human-readable confirmation to the
calculation log channel. The bot MUST NOT silently accept or silently discard any command.

**Rationale**: League administrators and drivers need an unambiguous, channel-visible record
of computations and changes, especially when disputing weather outcomes or schedule
alterations.

### VI. Simplicity & Focused Scope

The bot's scope is strictly limited to: season and division configuration, schedule management
(including amendments), and weather generation via the three-phase pipeline. It MUST NOT
expand into race results recording, driver standings calculation, penalty management, or any
other league administration feature unless a formal scope amendment is ratified under the
governance process defined below. Every proposed new command MUST be evaluated against this
scope boundary before implementation begins; commands that do not clearly serve weather
randomization or schedule management MUST be rejected or deferred.

The current output format is text-only. Image-based output is a known planned evolution and
MUST be designed for as an additive change that does not break existing text output paths.

**Rationale**: Scope creep degrades reliability and maintainability. A focused tool does one
job well and is easier to test, audit, and reason about.

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

## Bot Behavior Standards

All Discord slash commands MUST follow the naming convention `/[domain] [action]`
(e.g., `/season setup`, `/division add`, `/race postpone`, `/weather generate`).

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

All pull requests MUST include a Constitution Check confirming compliance with Principles I–VII
before merge. Any deliberate violation of a principle MUST be documented in the plan's
Complexity Tracking table with a justification for why the simpler compliant path is
insufficient.

**Version**: 1.1.0 | **Ratified**: 2026-03-03 | **Last Amended**: 2026-03-03
