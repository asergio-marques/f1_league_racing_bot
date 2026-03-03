<!--
SYNC IMPACT REPORT
==================
Version change    : 0.0.0 (uninitialized template) → 1.0.0
Modified principles: All placeholders replaced — initial constitution fill
Added sections    :
  - Core Principles (I. Trusted Configuration Authority through VI. Simplicity & Focused Scope)
  - Bot Behavior Standards
  - Data & State Management
  - Governance
Removed sections  : None (first fill)

Templates requiring updates:
  ✅ .specify/templates/constitution-template.md — source template; no changes required
  ✅ .specify/templates/plan-template.md — Constitution Check section is generic; gates now
       derivable from Principles I–VI without structural template edits
  ✅ .specify/templates/spec-template.md — generic structure; no domain-specific changes needed
  ✅ .specify/templates/tasks-template.md — phase structure aligns with principles; no edits needed
  ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale references
  (no files found in .specify/templates/commands/)

Follow-up TODOs   : None — all placeholders resolved for this initial version
-->

# F1 League Weather Randomizer Bot Constitution

## Core Principles

### I. Trusted Configuration Authority

Only users holding an explicitly designated trusted role (e.g., Race Director, Admin) MUST be
permitted to create or mutate season data: divisions, track schedules, race dates/times, and
any amendments such as postponements or track swaps. The bot MUST reject configuration
commands from unprivileged users with a clear, actionable permission error. Trusted roles
MUST be explicitly assigned per Discord server; no implicit super-user status exists.

**Rationale**: Season configuration errors propagate to every driver in a division. Restricting
write access to a named set of trusted actors prevents accidental or malicious schedule
corruption and preserves competitive legitimacy.

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

### IV. Deterministic & Auditable Weather Generation

Weather for any race MUST be generated from a deterministic algorithm seeded by a
reproducible, trackable value composed of stable inputs (e.g., division ID + round number +
season key). The same inputs MUST always produce the same output. Both the seed and the
generated result MUST be surfaced in the bot's output message so any driver or admin can
independently verify or challenge the outcome. Amending a race (track change, postponement)
MUST produce a new distinct seed; the old seed and its associated output MUST be retained in
the audit log.

**Rationale**: Drivers MUST be able to trust that weather was not manipulated after the fact.
Full auditability is a prerequisite for the tool's competitive legitimacy.

### V. Observability & Change Audit Trail

Every configuration mutation — season setup, track substitution, postponement, cancellation,
and trusted-role grant or revoke — MUST produce a timestamped audit log entry recording:
actor (Discord user ID and display name), division, change type, previous value, and new
value. All mutations that affect a published schedule MUST also post a human-readable
confirmation message to a configured audit or announcements channel. The bot MUST NOT silently
accept or silently discard any command.

**Rationale**: League administrators need an unambiguous record of who changed what and when,
particularly when disputes arise over schedule alterations or weather results.

### VI. Simplicity & Focused Scope

The bot's scope is strictly limited to: season and division configuration, schedule management
(including amendments), and weather generation. It MUST NOT expand into race results
recording, driver standings calculation, penalty management, or any other league administration
feature unless a formal scope amendment is ratified under the governance process defined below.
Every proposed new command MUST be evaluated against this scope boundary before implementation
begins; commands that do not clearly serve weather randomization or schedule management MUST
be rejected or deferred.

**Rationale**: Scope creep degrades reliability and maintainability. A focused tool does one
job well and is easier to test, audit, and reason about.

## Bot Behavior Standards

All Discord slash commands MUST follow the naming convention `/[domain] [action]`
(e.g., `/season setup`, `/division add`, `/race postpone`, `/weather generate`).

- Commands that mutate persistent state MUST present an ephemeral confirm/cancel prompt before
  executing, except where the change is trivially reversible within the same interaction.
- Configuration command responses MUST be ephemeral (visible only to the invoking user).
  Weather generation results MUST be posted publicly to the relevant division channel.
- The bot MUST acknowledge any command within 3 seconds; long-running operations MUST use
  Discord's deferred response mechanism to avoid timeout failures.
- Error messages MUST identify the specific problem and suggest a corrective action. Generic
  "something went wrong" messages are not acceptable.
- The bot MUST validate all inputs before executing any command; invalid inputs MUST be
  rejected with feedback before any state is modified.

## Data & State Management

- All season data (divisions, rounds, tracks, dates, weather results, audit log) MUST be
  persisted to durable storage. In-memory state alone is not acceptable.
- Each season MUST carry an explicit lifecycle state: `SETUP` → `ACTIVE` → `COMPLETED`.
  - In `SETUP`: divisions, tracks, and schedules may be freely configured.
  - In `ACTIVE`: amendments (track substitutions, postponements, cancellations) are permitted;
    wholesale reconfiguration of the base schedule is not.
  - In `COMPLETED`: the season is read-only; no mutations are allowed.
- Data schemas MUST be versioned. Migrations MUST be applied automatically on bot startup with
  a clear log of which migrations ran.
- A full data export of any division's season (schedule, amendments, weather log, audit trail)
  MUST be available to trusted users on demand.

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

All pull requests MUST include a Constitution Check confirming compliance with Principles I–VI
before merge. Any deliberate violation of a principle MUST be documented in the plan's
Complexity Tracking table with a justification for why the simpler compliant path is
insufficient.

**Version**: 1.0.0 | **Ratified**: 2026-03-03 | **Last Amended**: 2026-03-03
