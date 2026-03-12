<!--
SYNC IMPACT REPORT
==================
[2026-03-12 — Session reuse: QoL changes and bugfixes]
  - Constitution reused as-is; no principle amendments required.
  - Session intent: quality-of-life improvements and bugfixes to existing features.
  - All placeholder tokens remain resolved; no bracket tokens present.
  - Version 2.3.0 confirmed; no bump warranted (patch-level corrections and refinements
    to existing implementation — no governance or principle changes).
  - Templates confirmed aligned with Principles I–XII:
      ✅ .specify/templates/plan-template.md      — Constitution Check gate is dynamic; no
           hardcoded principle list; no changes needed.
      ✅ .specify/templates/spec-template.md      — generic; no stale references.
      ✅ .specify/templates/tasks-template.md     — generic; aligns with I–XII.
      ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
      ✅ .specify/templates/checklist-template.md  — no impact.
  - No stale agent-specific references detected.
  - No version bump required; Last Amended date remains 2026-03-11 (no content amendments).
  - Deferred TODOs (carried over):
      - TODO(FASTEST_LAP_RULE): pending project owner confirmation.
      - TODO(SCORING_TABLE_CUSTOMIZATION): pending project owner confirmation.

[2026-03-11 — v2.2.0 → v2.3.0: Race results & championship ratification + SeasonAssignment formalization]
  Version change    : 2.2.0 → 2.3.0
  Bump rationale    : MINOR — Principle XII (Race Results & Championship Integrity) added.
                      Race results recording and championship standings moved from "planned
                      future scope" to formally in-scope (Principle VI items 8–9). Both
                      added to foundational modules (Principle X). SeasonAssignment entity
                      formally defined, resolving the "normalized join table" gap present
                      since v2.0.0. RaceResult and ScoringTable entities added (v2.3.0).
                      Constitution title updated to reflect full-lifecycle mandate.
  Modified principles:
    - Principle VI (Incremental Scope Expansion) — items 8 (race results recording) and
      9 (championship standings) added to in-scope; both removed from planned future scope.
      Planned future scope now contains only penalty adjudication and financial/licensing.
    - Principle X (Modular Feature Architecture) — race results recording and championship
      standings added to the foundational modules list.
  Added sections    :
    - Principle XII: Race Results & Championship Integrity (NEW)
    - Data & State Management: SeasonAssignment, RaceResult, ScoringTable added as
      New Entities (v2.3.0). SeasonAssignment formally resolves the underdefined
      "normalized join table" referenced in DriverProfile since v2.0.0.
  Removed sections  : None
  Resolved TODOs    : None
  Deferred TODOs    :
    - TODO(FASTEST_LAP_RULE): Whether fastest-lap bonus points are available (and under
      what conditions) is a policy question pending confirmation from the project owner
      before the race results feature specification is written.
    - TODO(SCORING_TABLE_CUSTOMIZATION): Whether servers may define fully custom scoring
      tables or are restricted to the standard F1 preset must be confirmed before the race
      results feature specification is written.
  Other changes     :
    - Constitution title updated from "F1 League Weather Randomizer Bot Constitution" to
      "F1 League Bot Constitution" to reflect the bot's expanded scope mandate.
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md      — Constitution Check gate is dynamic; no
         hardcoded principle list; no changes needed.
    ✅ .specify/templates/spec-template.md      — generic; no stale references.
    ✅ .specify/templates/tasks-template.md     — generic; aligns with I–XII.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Incoherencies resolved:
    - The "normalized join table" for DriverProfile season assignments (referenced since
      v2.0.0 but never formally structured) is now defined as SeasonAssignment, including
      all position and points fields required for standings computation.
  Pending follow-up:
    - README.md title ("F1 League Weather Randomizer Bot") should be updated to reflect
      the bot's expanded scope. Flagged for the next feature increment.

[2026-03-10 — v2.1.0 → v2.2.0: Signup wizard & driver placement ratification + BAN_STATE_NAMING resolution]
  Version change    : 2.1.0 → 2.2.0
  Bump rationale    : MINOR — Signup wizard and driver assignment/placement moved from
                      "planned future scope" to formally in-scope. New Principle XI
                      (Signup Wizard Integrity) added. Principle VI in-scope list expanded
                      to 7 items. Principle VIII materially expanded: all 9 driver states
                      enumerated with a transition table, Awaiting Correction Parameter
                      formalised as an explicit state, Season Banned duration mechanics
                      resolved (BAN_STATE_NAMING TODO closed).
  Modified principles:
    - Principle VI (Incremental Scope Expansion) — items 5 (signup wizard & driver onboarding)
      and 6 (driver assignment & placement) added to in-scope; corresponding entries removed
      from planned future scope; former item 5 (Modular feature architecture) renumbered to 7.
    - Principle VIII (Driver Profile Integrity) — all 9 driver states enumerated in a table;
      full permitted-transition table added; Awaiting Correction Parameter formalised;
      Season Banned ban_races_remaining mechanics specified; server-leave rule added;
      signup data clearing on Not Signed Up transition clarified.
  Added sections    :
    - Principle XI: Signup Wizard Integrity (NEW)
    - Data & State Management: SignupRecord, SignupWizardRecord, SignupConfiguration,
      and TimeSlot entities added as New Entities (v2.2.0).
  Removed sections  : None
  Resolved TODOs    :
    - TODO(BAN_STATE_NAMING): Resolved. "Season Banned" duration = total round count of the
      season in which the ban was issued, stored as ban_races_remaining INT on DriverProfile.
      Decrements by 1 for each round completion server-wide. Transitions automatically to
      Not Signed Up when the counter reaches 0.
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md      — dynamic Constitution Check; no hardcoded
         principle list; no changes needed.
    ✅ .specify/templates/spec-template.md      — generic; no stale references.
    ✅ .specify/templates/tasks-template.md     — generic; aligns with I–XI.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Deferred TODOs    :
    - Race results recording, championship standings computation, penalty adjudication,
      and financial/licensing workflows remain pending formal ratification; each will be
      ratified as a dedicated feature increment per Principle VI.
    - The signup module specification MUST enumerate all new channel categories introduced
      (general signup channel, per-driver signup channels) and register them per Principle VII.
    - Lap time format edge cases (millisecond rounding vs. zero-padding, multi-track display
      ordering) are deferred to the signup feature specification for implementation detail.

[2026-03-07 — v2.0.0 → v2.1.0: Modular architecture ratification + full-league expansion vision]
  Version change    : 2.0.0 → 2.1.0
  Bump rationale    : MINOR — New Principle X added (Modular Feature Architecture). Principle VI
                      materially expanded to formally declare the incremental path toward full
                      league management and reclassify previously "out of scope" domains as
                      "planned future scope". Principle VII extended with a module-channel clause
                      to resolve a forward incoherency with the signup-wizard channel model.
  Modified principles:
    - Principle VI (Incremental Scope Expansion) — "Out of scope" list replaced with "Planned
      future scope" language; bot's strategic direction toward encompassing entire league business
      rules explicitly declared; ratification gate retained.
    - Principle VII (Output Channel Discipline) — Added clause permitting module-introduced
      channel categories when each is explicitly documented and registered with the same
      discipline as primary channels.
  Added sections    :
    - Principle X: Modular Feature Architecture (NEW)
  Removed sections  : None
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md      — Constitution Check gate is dynamic; no
         hardcoded principle list; no changes needed.
    ✅ .specify/templates/spec-template.md      — generic; no stale references.
    ✅ .specify/templates/tasks-template.md     — generic; aligns with I–X.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Resolved incoherencies:
    - Principle VII vs. signup-wizard channels: resolved by new module-channel clause in
      Principle VII. Per-driver signup channels and the general signup channel are module-
      introduced categories and must be documented in the signup module specification.
  Deferred TODOs    :
    - TODO(BAN_STATE_NAMING): league-functionality-specification.md describes the "Season Banned"
      driver state as lasting "for a number of races equal to the length of the season they were
      race banned for." This conflates race-ban severity with season-ban state naming. The
      specification must clarify whether (a) "Season Banned" covers the remainder of the active
      season regardless of offense, or (b) a separate "Race Banned" state is needed for
      timed-race bans. Resolution must be agreed before the ban-management feature is ratified.
    - Race results recording, championship standings computation, penalty adjudication, and
      financial/licensing workflows remain pending formal ratification; each will be ratified
      as a dedicated feature increment per Principle VI.
    - The signup module specification (feature 013 or later) MUST enumerate all new channel
      categories introduced and register them formally per Principle VII.

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

# F1 League Bot Constitution

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

The bot is on a deliberate, incremental path toward encompassing the full business rules of
an F1 game league. Scope expands one formally ratified feature at a time. The following
domains are formally in-scope as of this version:

1. **Weather generation**: the three-phase pipeline (Principle IV) remains the core function,
   delivered as an optional module (Principle X).
2. **Season and division lifecycle**: setup, activation, completion, cancellation, round
   scheduling, and amendments.
3. **Driver profile management**: state machine enforcement, Discord User ID reassignment,
   and historical participation tracking.
4. **Team management**: configurable team definitions per division, seat assignment, and
   the Reserve team ruleset.
5. **Signup wizard and driver onboarding**: the multi-step signup flow, per-driver signup
   channels, admin approval pipeline, correction request cycle, signup configuration
   (nationality toggle, time type, time-proof image requirement, time slots), and driver
   onboarding from first button-press through placement eligibility.
6. **Driver assignment and placement**: assign/unassign/sack drivers to division-team seats;
   seeded placement queue; division-role grant and revocation.
7. **Modular feature architecture**: per-server enablement and disablement of optional
   capability modules (Principle X).
8. **Race results recording**: round-by-round result entry per division, outcome modifiers
   (DNF, DNS, DSQ), and result amendments with full audit trail.
9. **Championship standings computation and display**: points accumulation per driver per
   division, tiebreaking, and derivation of current and final standings.

The following domains are **planned future scope** — each will be formally ratified as an
independent feature increment before any implementation begins:

- **Penalty and protest adjudication**.
- Financial or licensing workflows.

Every proposed new command or data concern MUST be evaluated against the current scope
boundary before implementation begins. Features not falling within a ratified domain MUST
be rejected or deferred via the governance process below.

The current output format is text-only. Image-based output is a known planned evolution
(required by the signup time-proof feature) and MUST be designed as an additive change
that does not break existing text output paths.

**Rationale**: A controlled, documented expansion path allows the bot to grow toward full
league management without sacrificing reliability or auditability. Each increment is gated
behind formal ratification to prevent unplanned feature creep.

### VII. Output Channel Discipline

The bot MUST post messages to exactly the following categories of channel, and no others
unless explicitly permitted by an active module (see below):

1. **Per-division weather forecast channel** (one per division, configured at season setup):
   receives only Phase 1, Phase 2, Phase 3 public weather messages, and amendment
   invalidation notices for that division.
2. **Calculation log channel** (one per server, configured at bot setup): receives all phase
   computation logs, configuration mutation confirmations, and audit trail entries.

**Module-introduced channels**: Optional modules (Principle X) MAY register additional
channel categories (e.g., a general signup channel, per-driver signup channels). Each such
category MUST be explicitly documented in the module's feature specification, configured
via a dedicated module-setup command, and governed by the same discipline as primary
channels — no unregistered posting, no cross-channel noise.

The bot MUST NOT post to any other channel, including the interaction channel where commands
are issued. Unsolicited messages in unregistered channels are not permitted.

**Rationale**: Keeping output in known, designated channels prevents noise in general server
channels and makes it trivial for drivers and admins to find the right information.

### VIII. Driver Profile Integrity

Every Discord user within a server is represented by at most one driver profile, keyed on
their Discord User ID in server scope. The following rules are non-negotiable:

- **State machine enforcement**: A driver's current state MUST only change via the transitions
  in the table below. Any transition not in the approved list MUST be rejected with a clear
  error. No code path may bypass the state machine to set state directly.

#### Driver States

| State | Meaning |
|-------|---------|
| Not Signed Up | Inactive; eligible to initiate signup. Default when no profile exists. |
| Pending Signup Completion | Wizard engaged; bot is collecting signup parameters. |
| Pending Admin Approval | All parameters collected; awaiting trusted-role review. |
| Awaiting Correction Parameter | Trusted user clicked "request changes"; selecting which field to re-collect (5-minute window). |
| Pending Driver Correction | Specific field flagged; driver must re-submit that field only. |
| Unassigned | Signup approved; not yet placed in any division-team seat. |
| Assigned | Placed in at least one division-team seat. |
| Season Banned | Banned for `ban_races_remaining` rounds (see Season Banned mechanics). Cannot sign up. |
| League Banned | Permanently banned. Cannot sign up until explicitly lifted by an administrator. |

#### Permitted Transitions

| From | To | Trigger / Condition |
|------|----|---------------------|
| Not Signed Up | Pending Signup Completion | Driver presses signup button (signups must be open) |
| Pending Signup Completion | Pending Admin Approval | Driver completes all wizard steps |
| Pending Signup Completion | Not Signed Up | Driver withdraws; or 24 h inactivity timeout |
| Pending Admin Approval | Awaiting Correction Parameter | Trusted user clicks "request changes" |
| Awaiting Correction Parameter | Pending Driver Correction | Trusted user selects field to correct |
| Awaiting Correction Parameter | Pending Admin Approval | 5-minute timeout with no field selected |
| Pending Driver Correction | Pending Admin Approval | Driver submits valid corrected field |
| Pending Driver Correction | Not Signed Up | Driver withdraws; or 24 h inactivity timeout |
| Pending Admin Approval | Unassigned | Trusted user approves signup |
| Pending Admin Approval | Not Signed Up | Trusted user rejects signup; or driver withdraws |
| Unassigned | Assigned | `/driver assign` places driver in their first seat |
| Assigned | Unassigned | `/driver unassign` removes driver's last seat assignment |
| Unassigned | Not Signed Up | `/driver sack` |
| Assigned | Not Signed Up | `/driver sack` |
| Any (except League Banned, Season Banned) | Season Banned | Ban command issued |
| Any (except League Banned) | League Banned | Ban command issued |
| Season Banned | Not Signed Up | `ban_races_remaining` decrements to 0 |
| League Banned | Not Signed Up | Administrator explicitly lifts ban |
| Not Signed Up | Unassigned | Test mode: admin direct-assign |
| Not Signed Up | Assigned | Test mode: admin direct-assign |

- **Season Banned mechanics**: When a Season Ban is issued, `ban_races_remaining` is set to
  the total round count of the active season at the time of issuance. This counter decrements
  by 1 for each round that completes anywhere within the server. When `ban_races_remaining`
  reaches 0, the driver automatically transitions to *Not Signed Up* under the same rules as
  any other transition to that state (immutability gate, deletion, signup-data clearing).
- **Signup data clearing**: On transition to *Not Signed Up* with `former_driver = true`, all
  signup record fields (collected parameters) MUST be nulled; the driver's signup channel
  reference is retained until the channel is pruned per Principle XI.
- **Immutability of former drivers**: Once `former_driver` is `true` (set on first round
  participation), the profile record MUST NOT be deleted — only modified. Deletion attempts
  MUST be rejected.
- **Deletion rule**: Transitioning to *Not Signed Up* with `former_driver = false` MUST delete
  the record atomically in the same transaction as the state change.
- **User ID reassignment**: Only a server administrator may change the Discord User ID.
  Both old and new IDs MUST be logged as an audit event (Principle V). Upon reassignment,
  the stored Discord username and server display name MUST be overwritten by those of the
  new account.
- **Test-mode overrides**: When test mode is active, administrators MAY directly set
  `former_driver` to `true` or `false`, and MAY assign *Not Signed Up* drivers directly to
  *Unassigned* or *Assigned*. All such overrides MUST produce audit log entries.
- **Absent profile semantics**: A Discord user with no database record is treated as
  *Not Signed Up*. The bot MUST NOT error or warn on absence — absence is the canonical
  default.
- **Server-leave rule**: If a user leaves the server while their driver profile exists, the
  profile record MUST be retained. Any active signup wizard is cancelled immediately and the
  signup channel deleted without delay.

**Rationale**: The driver profile is a long-lived, server-scoped identity record. Exhaustive
state enumeration and machine enforcement prevent data loss, support unambiguous auditability,
and provide a stable framework for all planned lifecycle extensions.

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

### X. Modular Feature Architecture

The bot is partitioned into foundational and optional modules. Module state MUST be persisted
per server and MUST survive bot restarts.

**Foundational modules** (always active, cannot be disabled):
- Division and round management
- Team management
- Driver profile management
- Season lifecycle management
- Race results recording and championship standings

**Optional modules** (disabled by default; enabled explicitly per server by a server
administrator via a dedicated `/module enable <name>` command — or its equivalent
structured subcommand):
- **Weather generation module**: arms the three-phase scheduler, registers weather channel
  configs, and processes the forecast pipeline (Principle IV).
- **Signup module**: manages the signup wizard flow, the general signup channel, per-driver
  signup channels, signup configuration (nationality toggle, time-type, time-image, time
  slots), and the driver onboarding state machine.
- Additional modules as ratified under Principle VI.

The following rules MUST hold for every optional module:

1. **Default-off**: A freshly configured server MUST have all optional modules disabled until
   explicitly enabled.
2. **Enable atomicity**: Enabling a module MUST atomically register all required configuration
   (channels, roles, settings) and create any associated scheduled jobs. If any step fails,
   the enable operation MUST be rolled back and no partial state left.
3. **Disable atomicity**: Disabling a module MUST atomically cancel all scheduled jobs
   associated with that module, delete or archive its channel/role configuration, and post
   a human-readable notice to the log channel. Historical data generated by the module
   (phase results, audit entries, signup records) MUST be retained; only live/scheduled
   artifacts are removed.
4. **Scheduling guard**: Scheduled jobs (e.g., weather phase timers) MUST only be created or
   re-armed when the relevant module is enabled. On bot restart, the bot MUST check module
   state before re-arming any job.
5. **Gate enforcement**: Any command or system action that belongs to an optional module MUST
   check the module-enabled flag before executing and return a clear, actionable error to
   the user if the module is disabled.
6. **Module configuration isolation**: Module-specific configuration is stored separately
   from core server configuration (Principle I). Disabling a module clears module config;
   re-enabling starts fresh unless a `--preserve-config` flag is explicitly supported and
   documented.

**Rationale**: The bot's growth toward full league management requires a clean separation
between always-on infrastructure (divisions, drivers, teams) and capability modules that
server administrators opt into. Mandatory modules establish the data model that all other
modules build on; optional modules add functionality only when the server is ready for it.
The default-off policy prevents accidental activation of unintended features and keeps the
initial setup experience simple.

### XI. Signup Wizard Integrity

The signup wizard is the multi-step onboarding flow initiated when a driver presses the signup
button. It operates as a secondary state machine (wizard state) orthogonal to the driver
lifecycle state (Principle VIII). The following rules are non-negotiable:

- **Isolation**: Each driver has exactly one wizard state record. Concurrent wizards for
  different drivers MUST be fully isolated; one driver's wizard MUST NOT delay, influence, or
  share state with any other.
- **Channel lifecycle**:
  - On wizard start, the bot MUST create a private channel named `<username>-signup`, visible
    only to the driver, tier-2 admins, and server administrators.
  - The channel MUST be deleted after a 24-hour hold period following any terminal event
    (approval, rejection, withdrawal, or timeout cancellation). During the hold period the
    channel is read-only for the driver.
  - The channel MUST be deleted immediately (no hold) when the driver leaves the server.
  - If a driver with an existing signup channel re-presses the signup button, the old channel
    MUST be deleted immediately and a new one created.
  - Tier-2 admins and server administrators MAY write freely in any signup channel at any time.
- **Sequential collection (normal flow)**: In the normal wizard (Pending Signup Completion),
  parameter collection MUST follow the exact order specified in the feature specification.
  Each step MUST wait for a valid response before advancing.
- **Targeted correction flow**: In the correction wizard (Pending Driver Correction), the
  wizard MUST advance directly to the flagged parameter's collection state, collect only that
  parameter, then return to Unengaged and transition the driver to Pending Admin Approval.
  No other parameters are re-collected.
- **Inactivity timeout**: Remaining in Pending Signup Completion or Pending Driver Correction
  without wizard progress for 24 consecutive hours triggers cancellation: the driver
  transitions to Not Signed Up; the channel is frozen (read-only); a cancellation notice is
  posted; the channel is deleted 24 hours later.
- **Withdrawal**: A withdrawal button MUST be visible throughout the wizard while the driver is
  in Pending Signup Completion, Pending Admin Approval, or Pending Driver Correction. Pressing
  it transitions the driver to Not Signed Up immediately.
- **Signup data persistence**: Collected answers are stored as draft data during the wizard.
  On transition to Pending Admin Approval the complete record MUST be committed atomically.
  Draft data MUST be discarded on any transition to Not Signed Up.
- **Image proof validation (configurable)**: When `time_image_required` is enabled, every
  lap-time submission MUST include an attached image; text-only submissions MUST be rejected
  with a clear explanation. The requirement MUST be stated in the channel before each
  time-collection step.
- **Lap time format**: Accepted formats are `M:ss.mss` and `M:ss:mss`. The colon-separated
  variant MUST be normalised to dot-separated. Milliseconds MUST be zero-padded to 3 digits.
  Leading and trailing whitespace MUST be stripped.
- **Configuration snapshot**: Wizard-governing configuration (nationality toggle, time type,
  image requirement, time slots, signup tracks) is read once at wizard-start and cached per
  wizard instance. Configuration changes after a wizard starts MUST NOT affect that wizard.

**Rationale**: A strictly defined, isolated wizard removes ambiguity in the onboarding process,
protects in-progress signups from mid-flow configuration changes, ensures data integrity before
trusted-user review, and maintains a clean channel lifecycle for server hygiene.

### XII. Race Results & Championship Integrity

Race outcomes MUST be recorded, persisted, and computed with the same auditability as weather
generation. Results form the authoritative competitive history of the league.

- **Authorization**: Only tier-2 admins (season/config authority, Principle I) may submit
  or amend result records.
- **Round finality gate**: Results MAY only be submitted for a round that has been explicitly
  marked as completed. Submissions against future or in-progress rounds MUST be rejected.
- **Atomic submission**: The complete driver finishing order for a round and division MUST be
  submitted in a single operation. Partial result sets are not permitted.
- **Result record**: Every result MUST carry: round ID, division ID, driver Discord User ID,
  finishing position (positive integer, 1-indexed), and an outcome modifier. Permitted
  modifiers: CLASSIFIED (driver finished; points apply per scoring table), DNF (Did Not
  Finish; 0 points), DNS (Did Not Start; 0 points), DSQ (Disqualified; 0 points).
- **Amendment**: A tier-2 admin MAY amend a previously submitted result with a stated reason.
  The prior record MUST be marked SUPERSEDED; a replacement record MUST be created. Standings
  MUST be recomputed immediately. Each amendment MUST produce an audit log entry per Principle V.
- **Scoring table**: A server-level scoring table (position → points mapping) MUST be
  configured before any results may be submitted. The default preset MUST be the standard
  F1 scoring table (25-18-15-12-10-8-6-4-2-1 for finishing positions 1–10). Fastest-lap
  bonus point eligibility is a policy detail deferred to the race results feature
  specification (see TODO below).
- **Scoring table versioning**: Changing the scoring table after any results have been
  submitted MUST trigger a full standings recomputation and produce an audit log entry.
  The prior table version MUST be retained as an immutable historical record.
- **Standings computation**: Championship standings for a division equal the sum of
  `points_awarded` across all ACTIVE (non-SUPERSEDED) RaceResult records for each driver.
  Standings MUST be persisted on the driver's SeasonAssignment record and refreshed
  atomically after every result submission or amendment.
- **Tiebreaking**: Equal points are resolved by the standard F1 rule — the driver who places
  higher in the most recent round where their finishing positions differ ranks above the other.
- **Season completion**: On season completion, current SeasonAssignment values (points,
  position) MUST be written atomically to the historical fields as part of the season-end
  transaction.

**Rationale**: Accurate, immutable result records are the backbone of any competitive league.
A deterministic, auditable computation pipeline ensures standings can always be reproduced
from the raw result log and legitimately contested.

TODO(FASTEST_LAP_RULE): Whether fastest-lap bonus points apply universally, only to top-10
finishers, or not at all is a policy decision that MUST be confirmed with the project owner
before the race results feature specification is written.

TODO(SCORING_TABLE_CUSTOMIZATION): Whether servers may define fully custom scoring tables
(arbitrary position counts and values) or are restricted to the standard F1 preset must be
confirmed before the race results feature specification is written.

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

- **SignupRecord rows**: one active record per signed-up or pending driver; cleared on
  transition to Not Signed Up; expected O(active_drivers) ≤ hundreds per server; each
  row is <2 KB (lap times stored as compact JSON strings).
- **SignupWizardRecord rows**: one per driver with any wizard history; tiny; same order of
  magnitude as DriverProfile.
- **TimeSlot rows**: expected single digits to low tens per server; negligible.

### New Entities (v2.2.0)

**SignupRecord** (per driver per server — at most one active record per driver):
- Stores the committed signup submission: `discord_username` (TEXT), `display_name` (TEXT),
  `nationality` (TEXT — ISO flag code or "other"), `platform` (ENUM: Steam/EA/Xbox/
  Playstation), `platform_id` (TEXT), `availability_slots` (JSON array of TimeSlot IDs),
  `driver_type` (ENUM: FULL_TIME/RESERVE), `preferred_teams` (JSON ordered list of ≤3 team
  IDs, or null for no preference), `preferred_teammate` (TEXT, nullable), `lap_times`
  (JSON map of track_id → normalised time string), `notes` (TEXT ≤50 chars, nullable).
- Linked 1-to-1 with DriverProfile. Fields nulled on transition to Not Signed Up when
  `former_driver = true`; record deleted with DriverProfile when `former_driver = false`.

**SignupWizardRecord** (per driver per server):
- `wizard_state` (ENUM) — current wizard step; full enumeration defined in the signup
  feature specification.
- `signup_channel_id` (TEXT, nullable) — Discord channel ID; retained through the 24-hour
  hold period after wizard completion (Principle XI).
- `partial_answers` (JSON, nullable) — draft answers in progress; cleared atomically on
  reaching Pending Admin Approval or on any transition to Not Signed Up.
- Created lazily on first wizard engagement; linked 1-to-1 with DriverProfile.

**SignupConfiguration** (per server, owned by the signup module):
- `nationality_required` (BOOLEAN, default true).
- `time_type` (ENUM: TIME_TRIAL/SHORT_QUALIFICATION, default TIME_TRIAL).
- `time_image_required` (BOOLEAN, default true).
- `signups_open` (BOOLEAN, default false).
- `signup_tracks` (JSON array of track IDs, nullable — empty means no tracks shown).
- `general_signup_channel_id` (TEXT, nullable).
- `base_role_id` (TEXT, nullable) — Discord role that can see and use the signup channel.
- `signedup_role_id` (TEXT, nullable) — Discord role granted on signup approval.

**TimeSlot** (per server):
- `slot_id` (INTEGER, server-scoped auto-increment PK).
- `day_of_week` (ENUM: Monday–Sunday).
- `time_of_day` (TEXT, HH:MM 24-hour).
- IDs are stable; removing a slot does not renumber remaining slots.

### New Entities (v2.3.0)

**SeasonAssignment** (per driver, per season, per division — formally specifies the
"normalized join table" referenced in DriverProfile since v2.0.0):
- `driver_id` (TEXT, FK → DriverProfile within server scope)
- `season_id` (INTEGER, FK → Season)
- `division_id` (INTEGER, FK → Division)
- `team_seat_id` (INTEGER, FK → TeamSeat, nullable — null until `/driver assign` runs)
- `is_historical` (BOOLEAN, default false — set to `true` on season completion)
- `current_points` (INTEGER, default 0 — sum of ACTIVE RaceResult points_awarded)
- `current_position` (INTEGER, nullable — null until first round results are posted)
- `points_gap_to_leader` (INTEGER, nullable — null until standings have been computed)
- `final_points` (INTEGER, nullable — written atomically on season completion)
- `final_position` (INTEGER, nullable — written atomically on season completion)
- Rows are created on first `/driver assign` for a season, or on admin direct-assign in
  test mode.

**RaceResult** (per driver, per round, per division):
- `result_id` (INTEGER PK, server-scoped auto-increment)
- `round_id` (INTEGER, FK → Round)
- `division_id` (INTEGER, FK → Division)
- `driver_id` (TEXT, FK → DriverProfile within server scope)
- `finishing_position` (INTEGER, positive — 1-indexed)
- `outcome_modifier` (ENUM: CLASSIFIED / DNF / DNS / DSQ)
- `points_awarded` (INTEGER, computed on submission — 0 for non-CLASSIFIED modifiers)
- `status` (ENUM: ACTIVE / SUPERSEDED, default ACTIVE)
- `submitted_by` (TEXT — Discord User ID of the submitting tier-2 admin)
- `submitted_at` (TEXT — UTC ISO 8601 timestamp)
- `superseded_at` (TEXT — UTC ISO 8601 timestamp, nullable)
- `supersession_reason` (TEXT, nullable)

**ScoringTable** (per server):
- `version` (INTEGER, auto-increment PK within server scope)
- `position_points` (TEXT — JSON map: position integer → points integer)
- `is_active` (BOOLEAN — exactly one active table per server at any time)
- Changing the active scoring table after any results exist triggers a full standings
  recomputation. Prior versions are retained as immutable audit records.

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

All pull requests MUST include a Constitution Check confirming compliance with Principles I–XII
before merge. Any deliberate violation of a principle MUST be documented in the plan's
Complexity Tracking table with a justification for why the simpler compliant path is
insufficient.

**Version**: 2.3.0 | **Ratified**: 2026-03-03 | **Last Amended**: 2026-03-11
