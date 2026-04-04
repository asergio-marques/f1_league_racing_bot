<!--
SYNC IMPACT REPORT
==================
[2026-04-03 — v2.10.0 → v2.10.1: PATCH — governance section reference corrected; attendance tracking branch initialised]
  Version change    : 2.10.0 → 2.10.1
  Bump rationale    : PATCH — Two non-semantic corrections:
                        1. Governance section's pull-request compliance line was stale,
                           referencing "Principles I–XII" after Principle XIII was added
                           at v2.10.0. Corrected to "I–XIII".
                        2. Session-initialisation entry for the attendance tracking sub-
                           increment (feature branch 033-attendance-tracking) added to
                           the Sync Impact Report.
  Feature branch    : 033-attendance-tracking (created 2026-04-03 from main)
  Session intent    : Implement the core attendance tracking features left out of scope
                      in 032-attendance-rsvp-checkin:
                        - Attendance recording hook (first SessionResult row accepted for
                          the round triggers DriverRoundAttendance.attended population).
                        - Attendance point distribution (post-penalty finalization hook;
                          deferred from RSVP sub-increment per Principle XIII).
                        - Attendance pardon workflow integrated into the penalty wizard
                          (NO_RSVP / NO_ATTEND / NO_SHOW modal, staged display, approval).
                        - Attendance sheet posting to the division's attendance channel
                          (descending points list with threshold footer).
                        - Autoreserve and autosack sanction enforcement after point
                          distribution (threshold evaluation, driver seat mutations,
                          audit log entries per Principle V).
                      All functionality is already governed by Principle XIII; no new
                      governance principle additions or amendments are required.
  Implementation status at session start:
      ✅ 031-attendance-module — fully merged to main (2026-04-03, PR #50).
         Covers all 30 module configuration tasks; 20/20 unit tests passing.
      🔄 032-attendance-rsvp-checkin — in progress (branch created 2026-04-03 from main);
         latest commit: feat(032): attendance RSVP check-in & reserve distribution.
         Covers: RSVP embed posting, driver button interactions, reserve extension window,
         reserve distribution at RSVP deadline, last-notice ping (FR-001–FR-030; US1–US5).
         Out of scope for that branch: attendance recording, point distribution, pardons,
         attendance sheet, autosanctions.
  Modified principles : None
  Added sections      : None
  Removed sections    : None
  Fixes               : Governance section line — "I–XII" corrected to "I–XIII".
  Templates confirmed aligned:
      ✅ .specify/templates/plan-template.md       — dynamic Constitution Check; no changes.
      ✅ .specify/templates/spec-template.md       — generic structure; no stale references.
      ✅ .specify/templates/tasks-template.md      — generic; aligns with I–XIII.
      ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
      ✅ .specify/templates/checklist-template.md  — no impact.
  Deferred TODOs (carried from v2.10.0):
      - Exact command naming for appeal submission and review commands to be confirmed
        against the 026-penalty-posting-appeals implementation.
      - Whether the existing penalty wizard loose-text fields on DriverSessionResult
        (post_race_time_penalties, post_stewarding_total_time) have been fully superseded
        by PenaltyRecord rows — migration confirmation required.

[2026-04-03 — Session reuse: Attendance module RSVP & check-in implementation — feature branch created]
  - Constitution reused as-is; no principle amendments required at session start.
  - Session intent: implement the RSVP check-in embed and button interactions;
    reserve distribution at the RSVP deadline; last-notice ping scheduling and sending;
    attendance recording from submitted round results (first SessionResult row hook);
    attendance point distribution (post-penalty finalization hook); attendance pardon
    workflow inside the penalty wizard; attendance sheet posting to the attendance channel;
    and automatic sanction enforcement (autoreserve and autosack).
    DriverRoundAttendance and AttendancePardon data entities to be introduced as part of
    this increment.
  - Feature branch: 032-attendance-rsvp-checkin (created 2026-04-03 from main).
  - Implementation status at session start:
      ✅ 031-attendance-module — fully merged to main (2026-04-03, PR #50); all tasks [x]
         complete. Covers: Attendance module enable/disable lifecycle (Results & Standings
         dependency gate, ACTIVE-season gate, cascading auto-disable on R&S disable);
         /division rsvp-channel and /division attendance-channel commands; season approval
         Gate 4 (both RSVP and attendance channels required per division); /attendance
         config timing commands (rsvp-notice, rsvp-last-notice, rsvp-deadline) with
         invariant enforcement; /attendance config penalty commands (no-rsvp-penalty,
         no-attend-penalty, no-show-penalty, autosack, autoreserve); season review
         attendance status and per-division channel display; full unit test suite (20
         tests passing).
  - All placeholder tokens remain resolved; constitution is fully resolved at v2.10.0.
  - No version bump required; Last Amended date remains 2026-04-03 (no content amendments).
  - All templates confirmed aligned with Principles I–XIII:
      ✅ .specify/templates/plan-template.md       — dynamic Constitution Check; no changes.
      ✅ .specify/templates/spec-template.md       — generic structure; no stale references.
      ✅ .specify/templates/tasks-template.md      — generic; aligns with I–XIII.
      ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
      ✅ .specify/templates/checklist-template.md  — no impact.
  - Deferred TODOs (carried from v2.10.0):
      - Exact command naming for appeal submission and review commands to be confirmed
        against the 026-penalty-posting-appeals implementation.
      - Whether the existing penalty wizard loose-text fields on DriverSessionResult
        (post_race_time_penalties, post_stewarding_total_time) have been superseded by
        PenaltyRecord rows — migration confirmation required.
  - Pending: speckit.specify to define exact scope and task ordering for this sub-increment;
    constitution will be re-evaluated if any new governance requirements are identified.

[2026-04-03 — v2.9.0 → v2.10.0: Attendance module ratified — Principle XIII added]
  Version change    : 2.9.0 → 2.10.0
  Bump rationale    : MINOR — The Attendance module is formally ratified as a new optional
                      module. Driver check-in management and attendance tracking were
                      previously unaddressed; this amendment:
                        1. Adds "Driver attendance management" (check-in RSVP flow,
                           attendance point accumulation, reserve distribution, and automatic
                           sanction enforcement) to the formally in-scope domain list in
                           Principle VI as item 11.
                        2. Registers the Attendance module in Principle X's optional modules
                           list, noting its dependency on the Results & Standings module.
                        3. Introduces Principle XIII (Attendance & Check-in Integrity):
                           module dependency gate, season-lifecycle constraints, RSVP notice
                           timing invariants, reserve distribution rules, attendance point
                           accumulation and pardon mechanics, autosack/autoreserve sanction
                           automation, and channel discipline.
                        4. Defines new data entities: AttendanceConfig, AttendanceDivision-
                           Config, DriverRoundAttendance, AttendancePardon.
  Feature branch    : 031-attendance-module (created 2026-04-03 from main)
  Session intent    : Initial configuration of the Attendance module: governance
                      ratification only. Implementation (commands, scheduler jobs, DB
                      migrations, tests) to follow in dedicated sub-increments.
  Modified principles:
    - Principle VI (Incremental Scope Expansion) — item 11 (attendance management) added
      to in-scope; corresponding entry removed from planned future scope (was not listed
      there; no prior reference).
    - Principle X (Modular Feature Architecture) — Attendance module added to optional
      modules list with its dependency constraint.
  Added sections    :
    - Principle XIII: Attendance & Check-in Integrity (NEW)
    - Data & State Management: New Entities (v2.10.0) — AttendanceConfig,
      AttendanceDivisionConfig, DriverRoundAttendance, AttendancePardon.
  Removed sections  : None
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md       — dynamic Constitution Check; no changes.
    ✅ .specify/templates/spec-template.md       — generic structure; no stale references.
    ✅ .specify/templates/tasks-template.md      — generic; aligns with I–XIII.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Deferred TODOs (carried from prior sessions):
    - Exact command naming for appeal submission and review commands to be confirmed
      against the 026-penalty-posting-appeals implementation.
    - Whether the existing penalty wizard loose-text fields on DriverSessionResult
      (post_race_time_penalties, post_stewarding_total_time) have been superseded by
      PenaltyRecord rows — migration confirmation required.

[2026-04-03 — v2.8.0 → v2.9.0: Track entity formalised + track/tier stats preparation]
  Version change    : 2.8.0 → 2.9.0
  Bump rationale    : MINOR — The Track registry has been a de-facto bot entity since v1.0.0
                      (used for weather parameter resolution and round identification) but was
                      never formally defined as a governed data entity. This amendment:
                        1. Formally defines the Track entity in Data & State Management,
                           expanding its documented dataset to include canonical name, country,
                           circuit name, and weather defaults alongside the existing server-
                           override mechanism.
                        2. Notes that the Track entity is the authoritative lookup basis for
                           future track-based and tier-based statistics derivable from
                           SessionResult + Round data (Principle VI planned: season history
                           and statistics).
                      No new governance principle is required; the changes land entirely in
                      Data & State Management and are a natural extension of the existing
                      season/division lifecycle and future statistics roadmap.
  Feature branch    : 030-track-data-expansion (created 2026-04-03 from main)
  Session intent    : Expand the Track dataset (names, country, circuit identity) and enable
                      track-based/tier-based stats queries in preparation for future modules.
                      Minor finetuning to division commands to align with richer Track data.
                      README to be updated as needed.
  Modified principles: None
  Added sections    :
    - Data & State Management: New Entities (v2.9.0) — formal Track entity definition.
  Removed sections  : None
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md       — dynamic Constitution Check; no changes.
    ✅ .specify/templates/spec-template.md       — generic structure; no stale references.
    ✅ .specify/templates/tasks-template.md      — generic; aligns with I–XII.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Deferred TODOs (carried from prior sessions):
    - Exact command naming for appeal submission and review commands to be confirmed
      against the 026-penalty-posting-appeals implementation.
    - Whether the existing penalty wizard loose-text fields on DriverSessionResult
      (post_race_time_penalties, post_stewarding_total_time) have been superseded by
      PenaltyRecord rows — migration confirmation required.
  Pending: speckit.specify to define exact scope of track entity expansion and division
    command finetuning; constitution will be re-evaluated if any new governance requirements
    are identified during that process.

[2026-04-02 — Session start: Results & Weather improvements — feature branch created]
  - Constitution footer corrected from v2.7.0 to v2.8.0. The body already contained
    v2.8.0 amendments (New Entities v2.8.0, Principle XI v2.8.0 notes) applied during the
    028-season-signup-flow session; the footer was inadvertently not updated at that time.
    Last Amended date updated to 2026-04-02 to reflect this correction.
  - Version bump rationale: no governance content changes this entry; footer is a
    PATCH-level correction restoring accurate versioning (body and footer now agree at v2.8.0).
  - Session intent: make targeted improvements to two existing optional modules:
      1. Results & Standings module — improvements to flexibility and error tolerance
         (e.g., ability to correct or amend initial submission mistakes more easily).
      2. Weather generation module — behavioral flexibility improvements
         (e.g., configurable or more tolerant pipeline behavior).
    Exact scope to be defined via speckit.specify before implementation begins.
  - Feature branch: 029-results-weather-improvements (created 2026-04-02 from main).
  - Implementation status at session start (post-028 merge):
      ✅ 028-season-signup-flow — fully merged to main (2026-04-02).
         Covers: signup close-timer scope narrowed to PENDING_SIGNUP_COMPLETION only;
         lineup_channel_id and calendar_channel_id moved to divisions table;
         lineup_message_id added to divisions; /division calendar-channel no longer
         module-gated.
  - All placeholder tokens remain resolved; constitution is fully resolved at v2.8.0.
  - No principle amendments required at session start; constitution will be re-evaluated
    and amended once the scope of each module improvement is formally defined.
  - All templates confirmed aligned with Principles I–XII:
      ✅ .specify/templates/plan-template.md       — dynamic Constitution Check; no changes.
      ✅ .specify/templates/spec-template.md       — generic structure; no stale references.
      ✅ .specify/templates/tasks-template.md      — generic; aligns with I–XII.
      ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
      ✅ .specify/templates/checklist-template.md  — no impact.
  - Deferred TODOs (carried from v2.7.0):
      - Exact command naming for appeal submission and review commands to be confirmed
        against the 026-penalty-posting-appeals implementation.
      - Whether the existing penalty wizard loose-text fields on DriverSessionResult
        (post_race_time_penalties, post_stewarding_total_time) have been superseded by
        PenaltyRecord rows — migration confirmation required.
  - Pending: user to define exact scope of results and weather improvements; constitution
    will be re-evaluated and amended once any new governance requirements are identified.

[2025-07-01 — v2.7.0 → v2.8.0: Season-signup flow alignment — close-timer scope + channel ownership]
  Version change    : 2.7.0 → 2.8.0
  Bump rationale    : MINOR — Two targeted amendments to Principle XI (Signup Wizard Integrity):
                        1. Signup close timer scope narrowed: only PENDING_SIGNUP_COMPLETION
                           drivers are transitioned to NOT_SIGNED_UP on forced close. Drivers
                           in PENDING_ADMIN_APPROVAL or PENDING_DRIVER_CORRECTION retain their
                           state — their completed/reviewed submissions are preserved.
                        2. Lineup and calendar channel ownership moved to the `divisions` table.
                           `lineup_channel_id` migrated from `signup_division_config` to
                           `divisions`. New `calendar_channel_id` and `lineup_message_id`
                           columns added to `divisions`. `/division calendar-channel` is no
                           longer gated on the signup module.
  Feature branch    : 028-season-signup-flow (created 2025-07-01 from main)
  Modified principles:
    - Principle XI (Signup Wizard Integrity) — signup close timer clause amended (scope
      narrowed); lineup announcement channel clause amended (channel ownership + calendar
      channel added; module gate removed).
  Added sections    :
    - Data & State Management: Division entity amended — lineup_channel_id moved from
      SignupDivisionConfig, calendar_channel_id and lineup_message_id added.
    - Data & State Management: SignupDivisionConfig amended — lineup_channel_id dropped.
  Removed sections  : None
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md       — dynamic Constitution Check; no changes.
    ✅ .specify/templates/spec-template.md       — generic structure; no stale references.
    ✅ .specify/templates/tasks-template.md      — generic; aligns with I–XII.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Deferred TODOs    : None.
[2026-03-30 — Session reuse: Signup module modifications + minor feature additions]
  - Constitution reused as-is; no principle amendments required at session start.
  - Session intent: modify existing signup wizard functionality and add minor features
    to the signup module. All proposed work falls within the already-ratified domains
    of Principle VI (item 5: signup wizard and driver onboarding) and Principle XI
    (Signup Wizard Integrity). Feature branch to be created from main after scope is
    confirmed.
  - Implementation status at session start (post-026 merge):
      ✅ 026-penalty-posting-appeals — fully merged to main (2026-03-30).
         Covers: PenaltyRecord and AppealRecord entities; penalty announcement channel
         (DivisionResultsConfig.penalty_channel_id); admin-driven appeals review wizard;
         penalty and appeal outcome posting; full test suite.
  - No placeholder tokens present; constitution is fully resolved at v2.7.0.
  - No version bump required; Last Amended date remains 2026-03-29 (no content amendments
    at session start).
  - All templates confirmed aligned with Principles I–XII:
      ✅ .specify/templates/plan-template.md       — dynamic Constitution Check; no changes.
      ✅ .specify/templates/spec-template.md       — generic structure; no stale references.
      ✅ .specify/templates/tasks-template.md      — generic; aligns with I–XII.
      ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
      ✅ .specify/templates/checklist-template.md  — no impact.
  - Deferred TODOs (carried from v2.7.0):
      - Exact command naming for appeal submission and review commands to be defined in
        the feature specification.
      - Whether the existing penalty-wizard loose-text fields on DriverSessionResult
        (post_race_time_penalties, post_stewarding_total_time) have been fully superseded
        by PenaltyRecord rows — migration confirmation required.
  - Pending: user to confirm exact scope of signup modifications and new features;
    constitution will be re-evaluated and amended once any new governance requirements
    are identified.

[2026-03-29 — v2.6.0 → v2.7.0: Penalty posting channel + appeals workflow formalized]
  Version change    : 2.6.0 → 2.7.0
  Bump rationale    : MINOR — "Penalty and protest adjudication" promoted from planned
                      future scope to formally in-scope (Principle VI item 10). Principle
                      XII extended with two new subsections:
                        1. Penalty Announcements: penalties applied via the wizard MUST
                           be posted to a configured per-division penalty announcement
                           channel (module-introduced channel, fallback to results channel).
                        2. Penalty Appeals: a second review tier allowing interaction-role
                           members to appeal their own penalty; resolved by a tier-2 admin
                           via Uphold / Overturn; outcome posted to the same channel.
                      New data entities: PenaltyRecord, AppealRecord. DivisionResultsConfig
                      amended to add penalty_channel_id.
  Feature branch    : 026-penalty-posting-appeals (created 2026-03-29 from main)
  Modified principles:
    - Principle VI (Incremental Scope Expansion) — item 10 (penalty adjudication) added
      to in-scope; corresponding entry removed from planned future scope.
    - Principle XII (Race Results & Championship Integrity) — Amendment & Penalty section
      extended: Penalty Announcements and Penalty Appeals subsections added.
  Added sections    :
    - Data & State Management: New Entities (v2.7.0) — PenaltyRecord, AppealRecord;
      DivisionResultsConfig amendment note (penalty_channel_id).
  Removed sections  : None
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md       — dynamic Constitution Check; no changes.
    ✅ .specify/templates/spec-template.md       — generic structure; no stale references.
    ✅ .specify/templates/tasks-template.md      — generic; aligns with I–XII.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Deferred TODOs    :
    - Exact command naming for appeal submission and review commands to be defined in
      the feature specification.
    - Whether appeals are driver-initiated only or also administratively triggered to be
      confirmed in the feature specification.
    - Whether a penalty announcement channel is required before the module may be enabled
      (or only before a penalty can be posted) to be defined in the feature specification.
  Follow-up TODOs   :
    - The existing penalty wizard in DriverSessionResult uses loose text fields
      (post_race_time_penalties, post_stewarding_total_time); these MUST be superseded
      by PenaltyRecord rows in the feature increment. Migration required.

[2026-03-27 — v2.5.0 → v2.6.0: Signup close timer, lineup announcements, module-config decoupling]
  Version change    : 2.5.0 → 2.6.0
  Bump rationale    : MINOR — Three governance additions:
                        1. Principle X (Enable atomicity): Clarified to decouple module
                           configuration (channels, roles, settings) from the module-enable
                           action. Enabling now atomically sets the module-enabled flag and
                           arms scheduled jobs only; all other configuration is handled via
                           dedicated commands independently of enable.
                        2. Principle XI (Signup Wizard Integrity):
                           (a) Signup close timer — optional close-at duration set when
                           signups are opened; fires automatically with the same cancellation
                           semantics as a manually confirmed close; re-armed on bot restart.
                           (b) Lineup announcement channel — optional per-division channel
                           for driver assignment change notices; module-introduced category
                           per Principle VII; not required for module activation.
                        3. Data & State Management: SignupConfiguration amended to add
                           close_at; new SignupDivisionConfig entity introduced.
  Feature branch    : 025-signup-expansion (created 2026-03-27 from main)
  Modified principles:
    - Principle X (Modular Feature Architecture) — Enable atomicity rule refined.
    - Principle XI (Signup Wizard Integrity) — close timer + lineup channel added.
  Added sections    :
    - Data & State Management: New Entities (v2.6.0) — SignupDivisionConfig entity;
      SignupConfiguration amendment note (close_at).
  Removed sections  : None
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md       — dynamic Constitution Check; no changes.
    ✅ .specify/templates/spec-template.md       — generic structure; no stale references.
    ✅ .specify/templates/tasks-template.md      — generic; aligns with I–XII.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Deferred TODOs    :
    - Exact trigger semantics for lineup announcement posts (per-event vs. on-demand)
      to be defined in the feature specification.
    - Command naming for the new dedicated signup configuration commands (channel set,
      base-role set, signedup-role set) to be defined in the feature specification.
  Follow-up TODOs   :
    - The existing `/module enable signup` implementation accepts channel, base_role,
      and signedup_role parameters; these MUST be removed and replaced with dedicated
      configuration commands in the feature increment.

[2026-03-26 — v2.4.1 → v2.5.0: Season Archive paradigm — seasons persist on completion]
  Version change    : 2.4.1 → 2.5.0
  Bump rationale    : MINOR — New governance concept added: Season Archive, formalising that
                      completed seasons are retained permanently in an append-only,
                      server-scoped archive rather than wiped or discarded. This supersedes
                      the prior implicit ephemeral-season paradigm. Changes land in three
                      places:
                        1. Principle VI (Incremental Scope Expansion) — "Season history and
                           statistics" added to planned future scope as the consumer of the
                           archive.
                        2. Data & State Management — COMPLETED lifecycle state description
                           extended to reference archival.
                        3. New Season Archive section in Data & State Management, defining
                           append-only semantics, zero-to-many cardinality, full data
                           retention, and read-only access rules.
  Feature branch    : 024-season-archive (created 2026-03-26 from main)
  Modified principles:
    - Principle VI (Incremental Scope Expansion) — "Season history and statistics" added
      to planned future scope.
  Added sections    :
    - Data & State Management: Season Archive (new governance section)
    - Data & State Management: New Entities (v2.5.0) note
  Removed sections  : None
  Paradigm superseded:
    - Prior practice of wiping/discarding season data on completion is formally superseded.
      Completed Season records (and all related tables) are now permanently retained.
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md      — dynamic Constitution Check; no changes.
    ✅ .specify/templates/spec-template.md      — generic structure; no stale references.
    ✅ .specify/templates/tasks-template.md     — generic; aligns with I–XII.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Deferred TODOs    :
    - Concrete schema additions for archive indexing, migration tooling, and the
      stats-module query layer are deferred to the feature specification for the season
      persistence increment (speckit.specify to be called next).
  Follow-up TODOs   :
    - If a `reset` or "wipe season" command currently exists in the implementation it
      MUST be deprecated or removed as part of the season persistence feature increment;
      this will be enforced in the feature spec.

[2026-03-23 — Session reuse: Results & Standings specification & incremental verification — feature branch created]
  - Constitution reused as-is; no principle amendments required at session start.
  - Session intent: provide a fresh specification for the Results & Standings module and
    verify the existing implementation against it incrementally. Any conflicts between the
    current implementation and the specification provided in this session will be resolved
    in favour of the specification. Feature branch `022-results-standings-verification`
    created from main.
  - Existing implementation status at session start:
      ✅ specs/018-results-standings/  — fully merged to main; all tasks [X] complete.
      ✅ specs/019-results-submission-standings/  — fully merged to main; all tasks [X]
         complete.
      ✅ specs/020-results-standings-session/  — fully merged to main (session branch).
      ✅ specs/021-results-spec-alignment/  — fully merged to main; spec-alignment
         corrections applied (submission validation, penalty wizard two-step flow).
  - All placeholder tokens remain resolved; constitution is fully resolved at v2.4.1.
  - No version bump required (no content amendments at session start).
  - All templates confirmed aligned with Principles I–XII:
      ✅ .specify/templates/plan-template.md
      ✅ .specify/templates/spec-template.md
      ✅ .specify/templates/tasks-template.md
      ✅ .specify/templates/agent-file-template.md
      ✅ .specify/templates/checklist-template.md
  - Deferred TODOs: none.
  - Pending: user to provide new specification for this session; constitution will be
    re-evaluated and amended once the scope of new work is defined.

[2026-03-19 — Session reuse: Results & Standings continuation — feature branch created]
  - Constitution reused as-is; no principle amendments required.
  - Session intent: begin a new session for results & standings specification and
    incremental implementation verification. Feature branch `020-results-standings-session`
    created from main.
  - Existing implementation status at session start:
      ✅ specs/018-results-standings/  — fully merged to main; all tasks [X] complete.
         Covers: R&S module enable/disable lifecycle; weather-channel decoupling;
         /division weather-channel, /division results-channel, /division standings-channel
         commands; season-approval prerequisite gates (weather + R&S + points-config).
      ✅ specs/019-results-submission-standings/  — fully merged to main; all tasks [X]
         complete (T016 results_formatter.py confirmed present despite unchecked box).
         Covers: points-config store CRUD; season config attachment + snapshot; submission
         wizard with transient channel; results and standings posting; config view; penalty
         wizard; full session amendment; mid-season amendment mode; reserves visibility
         toggle; full unit + integration test suite (T028–T035).
  - No placeholder tokens present; constitution is fully resolved at v2.4.1.
  - No version bump required (no content amendments this session).
  - All templates confirmed aligned with Principles I–XII:
      ✅ .specify/templates/plan-template.md      — dynamic Constitution Check; no changes.
      ✅ .specify/templates/spec-template.md      — generic structure; no stale references.
      ✅ .specify/templates/tasks-template.md     — generic; aligns with I–XII.
      ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
      ✅ .specify/templates/checklist-template.md  — no impact.
  - No stale agent-specific references detected.
  - Deferred TODOs: none.
  - Pending: user to provide new specification for this session; constitution will be
    re-evaluated and amended once the scope of new work is defined.

[2026-03-18 — v2.4.0 → v2.4.1: PATCH clarifications for Results & Standings module]
  Version change    : 2.4.0 → 2.4.1
  Bump rationale    : PATCH — Non-semantic clarifications to Principle XII covering three
                      gaps identified when cross-checking results_module_specification.md
                      against the constitution:
                        1. Endurance round session-type mapping for results not explicit.
                        2. Round-cancel constraint (fail if submission channel already open)
                           not stated.
                        3. Amendment-toggle disable constraint (cannot disable while
                           modified_flag is true) not stated.
  Modified principles:
    - Principle XII (Race Results & Championship Integrity):
        * Result Submission: added explicit session-type mapping for all four round
          formats (Normal, Sprint, Endurance, Mystery); Endurance Full Qualifying /
          Full Race → Feature Qualifying / Feature Race respectively.
        * Amendment & Penalty: added round-cancel-while-submission-open constraint;
          added amendment-toggle-off-while-modified constraint.
  Added sections    : None
  Removed sections  : None
  Resolved spec incoherencies (spec errors — to be corrected in feature spec, not here):
    1. results_module_specification.md §"Sprint Race and Feature Race" states that
       DNF/DNS/DSQ drivers "shall not be eligible to receive points" — this omits the
       constitution's explicit allowance that DNF drivers MAY still receive the fastest-lap
       bonus (provided the position limit is met). The feature spec MUST be updated to
       read: "DNF drivers are ineligible for finishing-position points but remain eligible
       for the fastest-lap bonus under the position-limit condition."
    2. results_module_specification.md §"Assigning channels to divisions" uses logical AND
       for the R&S approval gate ("module enabled AND not all channels configured AND no
       valid points config"), which would incorrectly allow approval when only channels or
       only the config prerequisite is missing. The constitution (Principle XII, Authorization
       & Module Gate) uses OR — each missing prerequisite independently blocks approval.
       The feature spec MUST be corrected to use OR semantics.
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md      — no changes needed.
    ✅ .specify/templates/spec-template.md      — no changes needed.
    ✅ .specify/templates/tasks-template.md     — no changes needed.
    ✅ .specify/templates/agent-file-template.md — no changes needed.
    ✅ .specify/templates/checklist-template.md  — no changes needed.
  Feature branch status:
    - `018-results-standings` already exists; foundational phases 1–8 implemented
      (module enable/disable, channel decoupling, channel assignment commands,
      season approval gates, and unit tests).
    - Remaining work: points configuration management, results submission wizard,
      standings computation, amendment/penalty flow, and all associated commands.
    - Next step: speckit.specify for the next increment within 018.
  No deferred TODOs remaining.

[2026-03-18 — v2.3.0 → v2.4.0: Results & Standings module formal specification]
  Version change    : 2.3.0 → 2.4.0
  Bump rationale    : MINOR — Principle XII (Race Results & Championship Integrity)
                      materially expanded and corrected. Principle X amended: race results
                      recording and championship standings moved from foundational to the new
                      optional Results & Standings module. Both previously deferred TODOs
                      (FASTEST_LAP_RULE and SCORING_TABLE_CUSTOMIZATION) resolved.
                      Data entities for v2.3.0 (RaceResult, ScoringTable) superseded by the
                      correct session-level and configuration-store schema (v2.4.0).
  Modified principles:
    - Principle X (Modular Feature Architecture) — "Race results recording and championship
      standings" removed from foundational modules; "Results & standings module" added as a
      new optional module.
    - Principle XII (Race Results & Championship Integrity) — full rewrite:
        * Corrected: no default scoring preset (zero-points default, not the F1 table).
        * Corrected: results are session-level, not round-level (sequential per-session
          submission per round).
        * Corrected: tiebreaking uses Feature Race finishes only (not a generic "most recent
          round" criterion).
        * Added: named multi-configuration points store (server-scope and season-scope).
        * Added: fastest-lap bonus mechanics (per session per config, position-limit).
        * Added: mid-season amendment flow (modification store, modified flag, approval gate).
        * Added: reserve driver standings visibility toggle per division.
        * Added: standings snapshot per round (points, per-position finish counts, first
          finish round).
        * Added: results channel and standings channel as module-introduced channel categories.
        * Added: season approval gate for the Results & Standings module.
  Added sections    : New Entities (v2.4.0)
  Removed sections  : None
  Resolved TODOs    :
    - TODO(FASTEST_LAP_RULE): Resolved — fastest-lap bonus points apply per session per
      named configuration; qualifying sessions are excluded; position-limit eligibility is
      configurable per session per configuration.
    - TODO(SCORING_TABLE_CUSTOMIZATION): Resolved — servers define fully custom named
      configurations; no F1 preset is provided; the default for any unspecified position
      is 0 points.
  Data entities     :
    - Superseded (v2.3.0): RaceResult, ScoringTable.
      *Reason: designed for a simplified single-round/single-table model inconsistent with
      the session-level multi-config schema mandated by the feature specification.*
    - SeasonAssignment (v2.3.0) amended: standings live-state fields (current_points,
      current_position, points_gap_to_leader) removed; authoritative standings state
      moved to DriverStandingsSnapshot (v2.4.0).
    - New (v2.4.0): PointsConfigStore, PointsConfigEntry, PointsConfigFastestLap,
      SeasonPointsLink, SeasonPointsStore, SeasonModificationStore, SeasonAmendmentState,
      SessionResult, DriverSessionResult, DriverStandingsSnapshot, TeamStandingsSnapshot,
      ResultsModuleConfig.
  Templates confirmed aligned:
    ✅ .specify/templates/plan-template.md      — Constitution Check gate is dynamic; no
         hardcoded principle list; no changes needed.
    ✅ .specify/templates/spec-template.md      — generic; no stale references.
    ✅ .specify/templates/tasks-template.md     — generic; aligns with I–XII.
    ✅ .specify/templates/agent-file-template.md — generic placeholders; no stale names.
    ✅ .specify/templates/checklist-template.md  — no impact.
  Incoherencies resolved:
    - Principle X listed "Race results recording and championship standings" as foundational
      (cannot be disabled), but the feature specification requires an optional module.
      Corrected.
    - Principle XII stated the default scoring table is the standard F1 preset; the feature
      specification states the default is 0 points for all positions. Corrected.
    - Principle XII described round-level "atomic submission" (one operation per round);
      the feature specification requires session-level sequential submission. Corrected.
    - Principle XII's tiebreaker was "driver who places higher in most recent round"; the
      feature specification defines a detailed countback hierarchy restricted to Feature Race
      finishes. Corrected.
    - v2.3.0 RaceResult modelled results at round/driver level; the feature requires results
      at session/driver level with per-session config choices. Superseded.
    - v2.3.0 ScoringTable modelled a single server-level table; the feature requires named
      multi-config stores at both server and season scope. Superseded.
  Follow-up notes   :
    - The "results channel", "standings channel", and transient "round submission channel"
      per division are module-introduced channel categories and MUST be explicitly documented
      in the feature specification per Principle VII.
    - Division-level channel config (results channel, standings channel) MUST be specified
      in the feature spec (stored on Division or a new DivisionResultsConfig entity).
    - SeasonAssignment live-state fields already implemented code-side from v2.3.0 will
      require a migration to drop or ignore the removed columns.

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
10. **Penalty adjudication and appeals**: application of post-race penalties (time
    penalties, disqualifications) via a stewards workflow, posting of penalty decisions
    to a dedicated channel, and a second-level appeals process allowing interaction-role
    members to contest a penalty, resolved by a tier-2 admin.
11. **Driver attendance management**: round RSVP check-in flow, reserve distribution at
    the RSVP deadline, attendance tracking once round results are submitted, attendance
    point accumulation per driver, attendance pardon workflow inside the penalty wizard,
    and automatic sanction enforcement (autoreserve and autosack thresholds).

The following domains are **planned future scope** — each will be formally ratified as an
independent feature increment before any implementation begins:

- **Season history and statistics**: aggregated career records and cross-season metrics
  derived from the Season Archive (see Data & State Management).
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

**Optional modules** (disabled by default; enabled explicitly per server by a server
administrator via a dedicated `/module enable <name>` command — or its equivalent
structured subcommand):
- **Weather generation module**: arms the three-phase scheduler, registers weather channel
  configs, and processes the forecast pipeline (Principle IV).
- **Signup module**: manages the signup wizard flow, the general signup channel, per-driver
  signup channels, signup configuration (nationality toggle, time-type, time-image, time
  slots), and the driver onboarding state machine.
- **Results & standings module**: delivers the named points-configuration store, season
  attachment, session-by-session round result submission, standings computation, and results
  and standings channel posting (Principle XII).
- **Attendance module**: manages round RSVP notices and check-in embeds, attendance
  tracking per round, attendance point accumulation per driver, reserve distribution at
  the RSVP deadline, and automatic sanction enforcement (autoreserve and autosack)
  (Principle XIII). MUST NOT be enabled while the Results & Standings module is disabled;
  if the Results & Standings module is disabled while Attendance is active, the Attendance
  module is disabled automatically.
- Additional modules as ratified under Principle VI.

The following rules MUST hold for every optional module:

1. **Default-off**: A freshly configured server MUST have all optional modules disabled until
   explicitly enabled.
2. **Enable atomicity**: Enabling a module MUST atomically set the module-enabled flag and
   create or arm any associated scheduled jobs. Module-specific configuration (channels,
   roles, settings) MAY be set via dedicated commands independently of the enable action —
   before or after enabling. If any step of the enable operation itself fails, it MUST be
   rolled back and no partial state left. Any module function that depends on configuration
   not yet provided MUST validate those prerequisites before executing and return a clear,
   actionable error; the module-enabled flag alone does not guarantee all configuration is
   complete.
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
- **Signup close timer (optional)**: When signups are opened, an optional close duration MAY
  be specified. If provided, it is resolved to an absolute UTC timestamp (`close_at`) and
  persisted on the server's signup configuration. When the timer fires, signups are closed
  automatically: only drivers in Pending Signup Completion are transitioned to Not Signed Up
  (applying the same cancellation semantics as a manually confirmed close with in-progress
  drivers); drivers in Pending Driver Correction or Pending Admin Approval retain their
  current state — their submitted records are preserved for admin review. The signup button
  is removed; and a "signups closed" notice is posted in the general signup channel. The
  timer is cleared when signups are closed manually before it fires. On bot restart, any
  active close timer MUST be re-armed.
  *(Amended v2.8.0: close-timer now preserves PENDING_ADMIN_APPROVAL and
  PENDING_DRIVER_CORRECTION drivers; only PENDING_SIGNUP_COMPLETION is cleared.)*
- **Lineup and calendar announcement channels**: Optional per-division Discord channels for
  lineup and calendar posts. Both `lineup_channel_id` and `calendar_channel_id` are stored
  on the `divisions` table (alongside `results_channel_id`, `standings_channel_id`, etc.)
  and are NOT scoped to the signup module. `/division calendar-channel` is available
  whenever a season exists, without requiring the signup module to be enabled. When
  `lineup_channel_id` is configured for a division, the bot MUST delete the previous lineup
  message (tracked via `lineup_message_id` on the `divisions` row) and post a fresh lineup
  message whenever a driver's assignment in that division changes (assign, unassign, or sack).
  When `calendar_channel_id` is configured, a calendar message is posted to that channel
  upon season approval. If neither channel is configured for a division, no messages are
  posted for that division.
  *(Amended v2.8.0: lineup channel ownership moved from signup_division_config to divisions;
  calendar_channel_id and lineup_message_id added to divisions; /division calendar-channel
  command is not module-gated.)*

**Rationale**: A strictly defined, isolated wizard removes ambiguity in the onboarding process,
protects in-progress signups from mid-flow configuration changes, ensures data integrity before
trusted-user review, and maintains a clean channel lifecycle for server hygiene.

### XII. Race Results & Championship Integrity

Race outcomes MUST be recorded, persisted, and computed with the same auditability as weather
generation. Results form the authoritative competitive history of the league. This principle
governs the **Results & Standings optional module** (Principle X).

#### Authorization & Module Gate

- Only tier-2 admins (season/config authority, Principle I) may submit, amend, or penalise
  result records.
- All commands in this module MUST check that the Results & Standings module is enabled before
  executing, and return a clear error if it is not (Principle X, rule 5).
- The module MAY NOT be enabled in the middle of an active season.
- A season in `SETUP` MAY NOT be approved if the module is enabled and any division lacks a
  configured results channel or standings channel, or if no points configuration exists that
  yields at least one non-zero position value for any session.

#### Points Configuration Store

- A server maintains a **server points config store**: a keyed set of named configurations.
  Each configuration defines, per session type, the points awarded per finishing position
  and optionally a fastest-lap bonus and a position eligibility limit for that bonus.
- The default for any position or bonus not explicitly configured is **0 points**. There is
  no preset.
- Valid session types for point awards are: Sprint Qualifying, Sprint Race, Feature
  Qualifying, Feature Race. Fastest-lap awards are valid only for Sprint Race and Feature
  Race; configuring fastest-lap for qualifying sessions MUST be rejected.
- Within a single configuration and session type, positions MUST be monotonically
  non-increasing in points (higher position ≥ lower position). Season approval MUST be
  blocked if any configuration attached to the season violates this rule.
- Named configurations from the server store are **attached** (weakly linked) to a season
  in `SETUP` to form that season's **season points store**. Attachment is a copy-on-approve
  action: on season approval the attached configurations' settings are snapshotted into the
  season points store and become independent of the server store.
- Modifications to the server store after a season is approved do NOT affect that season's
  store.

#### Result Submission

- Results are submitted **per session**, sequentially within a round, in the order: Sprint
  Qualifying → Sprint Race → Feature Qualifying → Feature Race (sprint-type sessions
  omitted for Normal/Endurance rounds). Each session's results MUST be validated and
  accepted before the next session's collection begins. Session-type mapping by round
  format:
    - **Normal**: Feature Qualifying, Feature Race.
    - **Sprint**: Sprint Qualifying, Sprint Race, Feature Qualifying, Feature Race.
    - **Endurance**: the Full Qualifying session maps to Feature Qualifying; the Full Race
      session maps to Feature Race for result-type and points-configuration purposes.
    - **Mystery**: no result sessions; result collection MUST NOT be triggered.
- The bot creates a transient submission channel adjacent to the division's results channel
  at the scheduled round start time, notifying tier-2 admins to enter results. This channel
  is a module-introduced channel category registered per Principle VII.
- Each session result row MUST carry: session type, round ID, division ID, driver Discord
  User ID, finishing position (1-indexed positive integer), team role, tyre (qualifying)
  or total race time and fastest lap (race), and an outcome modifier. Permitted modifiers:
  CLASSIFIED (eligible for points), DNF / DNS / DSQ (0 points, ineligible for fastest-lap
  bonus except as noted). A special CANCELLED result MAY be recorded for sessions not run.
- After a session's results are accepted, the bot presents one button per named seasonal
  configuration; the tier-2 admin MUST choose one. The chosen configuration name is
  persisted with the session result and used for all points calculations for that session.
- A CLASSIFIED driver is eligible for the chosen configuration's fastest-lap bonus if, and
  only if, their finishing position is at or above the configured position limit for that
  session (i.e., finishing_position ≤ fastest_lap_position_limit).
- DNF, DNS, and DSQ drivers are NEVER eligible for finishing-position points. DSQ and DNS
  drivers are NEVER eligible for fastest-lap points. DNF drivers are eligible for
  fastest-lap points provided the position limit condition is met.

#### Amendment & Penalty

- A tier-2 admin MAY amend any session's results entirely (full re-entry) or apply
  targeted time penalties or disqualifications per driver via a guided wizard. Each
  amendment or penalty MUST produce an audit log entry per Principle V.
- On amendment or penalty application, standings for the affected round and all subsequent
  rounds in that division MUST be recomputed and reposted atomically.
- If the transient round results submission channel has already been opened for a round,
  any request to cancel that round MUST be rejected with a clear error. The round MAY
  only be cancelled once all pending result sessions have been submitted or explicitly
  marked CANCELLED.
- The amendment-mode toggle MUST reject a request to disable (toggle off) while
  `modified_flag` is `true`. Tier-2 admins MUST first either approve (overwriting the
  season points store) or revert (discarding the modification store) before amendment
  mode may be disabled.
- Mid-season scoring table amendments follow a **modification store** workflow: a copy of
  the season points store is placed into a modification store; changes are applied there;
  only upon tier-2 admin approval does the modification store overwrite the season store.
  On approval, all affected results and standings MUST be reposted. A `modified_flag`
  (default false) tracks uncommitted changes; it is set on any modification and cleared
  on approval or revert.

#### Penalty Announcements

- When a penalty (time penalty or DSQ) is applied via the penalty wizard, the bot MUST
  post a penalty announcement notice to the division's configured **penalty announcement
  channel** (`penalty_channel_id` on `DivisionResultsConfig`).
- The announcement MUST include: the affected driver's display name, the round name, the
  session type, the penalty type and magnitude (e.g., "+5 seconds" or "DSQ"), a reason if
  provided by the tier-2 admin, and the Discord display name of the admin who applied it.
- If no penalty announcement channel is configured for a division, announcements fall back
  to that division's results channel.
- The penalty announcement channel is a module-introduced channel category governed by
  Principle VII. Configuring it is not required for module activation; if absent the
  fallback ensures the notice is always posted.

#### Penalty Appeals

- The appeals stage is fully admin-driven. After the penalty review is approved, a tier-2
  admin runs an appeals review wizard (mirroring the penalty review wizard) in the same
  transient submission channel. The admin stages corrections and approves them; no driver
  submission step exists in this increment.
- Appeals follow a two-outcome lifecycle: **Upheld** (correction applied to the result) or
  **Overturned** (no change; reserved for future use). Every correction staged and approved
  in this increment is stored as `UPHELD`. A `PENDING` state and driver-initiated appeal
  submission are explicitly deferred to a future stewarding module.
- The appeals review MUST produce an audit log entry per correction, including description
  and justification (Principle V).
- On approving corrections: the affected `DriverSessionResult` rows are updated; standings
  for the affected round and all subsequent rounds in that division MUST be recomputed and
  reposted atomically, consistent with the amendment recomputation rule above.
- On approving with no staged corrections: the round advances to `FINAL` with results
  identical to the `Post-Race Penalty Results` post. No result changes occur.
- Each applied correction MUST produce one announcement post to the division's configured
  verdicts channel (if accessible). Announcement skipped silently if channel is
  inaccessible; finalization is never blocked by an announcement failure.

#### Standings Computation

- **Driver standings**: all drivers who have participated in a division, ranked by (1)
  total points, (2) count of Feature Race wins, (3) count of Feature Race 2nd-place
  finishes, … (n) count of Feature Race nth-place finishes, (n+1) earliest round in which
  the highest diverging finish was first achieved. Only Feature Race sessions are
  authoritative for countback tiebreaking.
- **Team standings**: teams ranked by the same hierarchy applied to the aggregate points and
  Feature Race finishes of all drivers scoring under that team's banner in each session.
  A reserve driver's points and finishes accrue to whichever team they drove for in each
  individual session.
- A standings **snapshot** MUST be persisted after every round: per driver (and per team),
  the total points accumulated to that round and the per-position finish counts (and
  the round number on which each position was first obtained) MUST be stored. These
  snapshots form the authoritative historical record and allow reconstruction of standings
  at any point in the season.
- Reserve drivers' appearance in the public driver standings is governed by a per-division
  **reserves visibility toggle** (default on). When toggled off, reserve drivers still
  accrue points and are included in internal snapshots, but are excluded from posted output.
- On season completion, each driver's final points and position MUST be written atomically
  to their SeasonAssignment `final_points` and `final_position` fields as part of the
  season-end transaction.

**Rationale**: Accurate, immutable, session-level result records are the backbone of any
competitive league. A deterministic, auditable computation pipeline with named configurations
and snapshot-based standings history ensures results can always be reproduced from raw input
and legitimately contested.

### XIII. Attendance & Check-in Integrity

The Attendance module governs driver RSVP check-ins before each round and the resulting
attendance tracking and point accumulation. It operates as an optional module (Principle X)
and depends entirely on the Results & Standings module (Principle XII). The following rules
are non-negotiable.

#### Module Dependency & Lifecycle Gate

- The Attendance module MUST NOT be enabled while the Results & Standings module is
  disabled. Any attempt MUST be rejected with a clear error.
- If the Results & Standings module is disabled while the Attendance module is active,
  the Attendance module MUST be disabled automatically (applying the same disable atomicity
  rules of Principle X, rule 3).
- The Attendance module MUST NOT be enabled once a season is in the `ACTIVE` lifecycle
  state. It MAY only be enabled during `SETUP` or while no season exists.
- The Attendance module activation status MUST be displayed in the season review output
  alongside other module states.
- The Attendance module MUST function correctly with fake driver rosters created under
  test mode.

#### Season Validation Gates

- Before a season may be approved, if the Attendance module is enabled, every configured
  division MUST have both an RSVP channel and an attendance channel configured. Missing
  either channel for any division MUST block season approval with a clear diagnostic.
- Both channel IDs are stored in **AttendanceDivisionConfig** (one row per division per
  server). They are displayed in the season review alongside other division channels
  (results, standings, weather, etc.).

#### RSVP Timing Configuration

Three timing parameters govern the RSVP lifecycle and MUST satisfy the invariant
**notice_days × 24 > last_notice_hours > deadline_hours** at all times:

- `rsvp_notice_days` (default 5) — days before a round at which the RSVP embed is posted.
- `rsvp_last_notice_hours` (default 24) — hours before a round at which drivers who have
  not yet RSVP'd are directly notified. A value of 0 disables the last-notice ping entirely
  (the `> deadline_hours` comparison is skipped). Any non-zero value MUST be strictly
  greater than `rsvp_deadline_hours`.
- `rsvp_deadline_hours` (default 2) — hours before a round at which the RSVP choices
  become locked. A value of 0 means locking occurs at the scheduled round start time.

Any configuration command that would violate the invariant MUST be rejected with a clear
error. All three commands MUST be rejected if a season is currently `ACTIVE`.

#### RSVP Check-in Embed

At `rsvp_notice_days` days before a round, the bot MUST post an RSVP embed in the
division's configured RSVP channel. The embed MUST contain:

- **Title**: `Season <X> Round <X> — <canonical_name of track>` (or `Mystery` if the
  round type is Mystery and track identity is withheld).
- **Fields**: scheduled datetime as a dynamic Discord timestamp; location (canonical circuit
  name, or "Mystery" for Mystery rounds); event type (Normal / Sprint / Mystery / Endurance).
- **Driver roster**: a mini-list per team (including the Reserve team) showing each driver's
  display name alongside their current RSVP status indicator (empty brackets `()` if not
  yet responded; ✅ if accepted; ❓ if tentative; ❌ if declined).
- **Three action buttons** (horizontal): Accept (green ✅), Tentative (grey ❓), Decline
  (red ❌). Pressing any button updates the embed's status indicator for that driver
  atomically.

RSVP status MUST be persisted in **DriverRoundAttendance** rows and is authoritative for
all downstream operations.

#### RSVP Locking Rules

- **Full-time drivers**: RSVP choice locks at the `rsvp_deadline_hours` threshold. No
  changes are permitted after that point.
- **Reserve drivers**: RSVP choice locks at the scheduled round start time, but ONLY if
  they have accepted the check-in. A reserve who remains tentative or declined is
  locked out at round start regardless.
- After the RSVP deadline, the bot MUST process the reserve distribution (see below) and
  post a standby/assignment message to the division's RSVP channel.

#### Reserve Distribution

Once the RSVP deadline is reached, reserves who have confirmed `ACCEPTED` are distributed
to teams in the following priority order:

1. Teams where at least one driver failed to RSVP (no response).
2. Teams where at least one driver declined.
3. Teams where at least one driver is tentative.

Within each priority tier, tie-breaking is applied in order:
1. Number of `ACCEPTED` drivers already assigned to the team (lowest first — prefer teams
   with zero confirmed drivers).
2. Constructors' Championship position in that division (lowest-ranked team first).

Reserves are picked in the order they confirmed `ACCEPTED` (earliest timestamp wins).
Every time a reserve changes their status back to `ACCEPTED`, their timestamp resets.

Reserves confirmed `ACCEPTED` who remain unplaced are classified as **on standby**. After
distribution is determined, the bot MUST post a message in the division's RSVP channel:
- Mentioning each assigned reserve by Discord user and stating which team they are
  racing for.
- Mentioning each standby reserve and informing them they are on standby and should be
  ready to substitute.

#### Attendance Recording

Once the initial round results are submitted (first `SessionResult` row accepted for the
round), the bot MUST populate attendance status in each `DriverRoundAttendance` row:
- A driver is counted as **attended** if they appear in any submitted session result for
  that round (any `DriverSessionResult` row for that round in that division), regardless
  of outcome modifier.
- Drivers seated in the Reserve team of that division for this round are excluded from
  attendance tracking.

Attendance points are NOT distributed at this stage; they are deferred to post-penalty
finalization (see below).

#### Attendance Pardon Workflow

A dedicated **Attendance Pardon** button MUST be available in the penalty wizard,
exclusively during the penalty review stage (NOT during the appeals stage). When pressed,
a modal form MUST request:

1. The Discord User ID of the driver being pardoned.
2. The type of attendance event being waived: NO_RSVP, NO_ATTEND, or NO_SHOW.
3. A free-text justification (logged to the calculation log channel; never displayed
   elsewhere for privacy reasons).

Pardon validation rules:
- A NO_RSVP pardon requires the driver's RSVP status to be NO_RSVP.
- A NO_ATTEND pardon requires the driver to have not attended (and RSVP status is
  irrelevant for this pardon type).
- A NO_SHOW pardon requires the driver to have been RSVP `ACCEPTED` but not attended.
- Multiple pardons for the same driver are permitted (e.g., both NO_RSVP and NO_ATTEND
  can be waived to eliminate both penalties for a driver who did not RSVP and did not
  attend).

Staged attendance pardons MUST be displayed alongside staged penalties in the penalty
review summary.

After post-race penalties are approved, attendance pardons can no longer be applied for
that round.

#### Attendance Point Distribution

Attendance points are distributed once the post-race pen­alty results are finalised
(approved), to prevent erroneous automatic sanctions due to provisional result errors.
Points are awarded per driver per round as follows:

| RSVP status | Attended | Points gained |
|-------------|----------|---------------|
| NO_RSVP | Attended | `no_rsvp_penalty` |
| NO_RSVP | Did not attend | `no_rsvp_penalty` + `no_attend_penalty` |
| Any (ACCEPTED/TENTATIVE/DECLINED) | Attended | 0 |
| ACCEPTED | Did not attend | `no_show_penalty` |
| TENTATIVE or DECLINED | Did not attend | 0 |

Pardons waive the corresponding point award(s). A driver who receives a pardon for a given
event type does NOT accumulate points for that event.

#### Attendance Sheet Posting

Once post-race penalties are approved and posted, the bot MUST post an updated attendance
sheet to the division's configured attendance channel. The post MUST:

- List all drivers in descending order of total accumulated attendance points (most first).
- Format each entry as `@mention — X attendance points`.
- Append the following footer at the end:
  > Drivers who reach `<autoreserve_threshold>` points will be moved to reserve.
  > Drivers who reach `<autosack_threshold>` points will be removed from all driving roles
  > in all divisions.
  If either threshold is disabled (value 0 / null), the corresponding sentence MUST be
  omitted.

#### Automatic Sanction Enforcement (Autoreserve & Autosack)

After attendance points are distributed, the bot MUST evaluate each driver's total:

- **Autoreserve** (`autoreserve_threshold`, default disabled): if a full-time driver's
  total attendance points meet or exceed this threshold, the bot MUST unassign them from
  their current team seat and assign them to the Reserve team of the same division,
  producing an audit log entry (Principle V). This action MUST NOT be applied to drivers
  already seated in the Reserve team.
- **Autosack** (`autosack_threshold`, default disabled): if a driver's total attendance
  points meet or exceed this threshold, the bot MUST remove them from all team seats
  across all divisions (equivalent to `/driver sack`), producing an audit log entry per
  division affected (Principle V). Autosack supersedes autoreserve when both thresholds
  are met simultaneously.

Both thresholds are evaluated in a single pass after point distribution. A threshold value
of 0 or null means the corresponding sanction is disabled.

**Rationale**: Reliable attendance management is essential for competitive fairness in a
multi-division league. A governed RSVP workflow with clear locking semantics, a proper
reserve distribution protocol, and an auditable point accumulation pipeline give league
admins a transparent mechanism to enforce attendance requirements without ad-hoc manual
intervention. Deferring attendance point distribution to post-penalty finalization prevents
incorrect automatic sanctions from provisional result errors.

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
  - In `COMPLETED`: the season is finalised and moved into the Season Archive (see below).
    All data associated with the season — divisions, rounds, results, standings, driver
    assignments, and the full audit trail — is retained permanently and becomes fully
    immutable. No mutations are permitted. The archived record forms the authoritative
    historical basis for future statistics and reporting features (Principle VI).
### Season Archive

A server maintains a **Season Archive**: a persistent, append-only collection of all
completed seasons for that server. The following rules are non-negotiable:

- **Append-only**: When a season transitions to `COMPLETED`, the season record and all
  associated data are added to the archive atomically as the final step of the season-end
  transaction. A season already in the archive MUST NOT be deleted, overwritten, or mutated
  by any user command or automated system process.
- **Zero-to-many cardinality**: A server's archive MAY contain zero or more completed
  seasons. An empty archive is the canonical initial state for a newly configured server.
- **Full data retention**: Every archived season retains all associated records: division
  configurations, round schedules and amendment history, weather phase outputs, session
  results and driver results, standings snapshots, driver and team seasonal assignments,
  points configuration snapshots, and the full audit trail. No associated data is discarded
  on season completion.
- **Read-only access**: Archived season data MAY be read by any command or module with
  appropriate authorisation. No write path targets archived records outside of the single
  append operation triggered by season completion.
- **Future statistics foundation**: The Season Archive is the authoritative data source for
  all planned season history and statistics features (Principle VI). Any implementation
  consuming archived data MUST treat the archive as immutable and MUST NOT rely on derived
  or cached state not persisted at completion time.

The archive is constituted by the existing `Season` records (and all related tables) in the
`COMPLETED` lifecycle state. Concrete schema additions for archive indexing, querying, or
migration from the prior ephemeral-season model are deferred to the feature specification
for the season persistence increment.

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
- `close_at` (TEXT, nullable) — ISO 8601 UTC timestamp; set when signups are opened with
  an optional close duration; cleared on manual or automatic close; re-armed on bot restart
  if non-null (Principle XI, signup close timer).

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
- `final_points` (INTEGER, nullable — written atomically on season completion)
- `final_position` (INTEGER, nullable — written atomically on season completion)
- Rows are created on first `/driver assign` for a season, or on admin direct-assign in
  test mode.

*Note: `current_points`, `current_position`, and `points_gap_to_leader` fields previously
defined here (v2.3.0 draft) are superseded; authoritative live standings state is now
held in DriverStandingsSnapshot (v2.4.0).*

*Note: RaceResult and ScoringTable entities previously defined here (v2.3.0 draft) are
superseded by the session-level schema in v2.4.0 below.*

### New Entities (v2.4.0)

**PointsConfigStore** (per server — the server-level named configuration store):
- `config_id` (TEXT, server-scoped — user-supplied name/ID, e.g. "100%", "50%")
- `server_id` (TEXT, FK → Server)
- One row per named configuration per server. Deleting a config from the store does not
  automatically detach it from a season in SETUP.

**PointsConfigEntry** (per server config, per session type, per finishing position):
- `config_id` (TEXT, FK → PointsConfigStore)
- `server_id` (TEXT)
- `session_type` (ENUM: SPRINT_QUALIFYING / SPRINT_RACE / FEATURE_QUALIFYING / FEATURE_RACE)
- `position` (INTEGER, 1-indexed)
- `points` (INTEGER, default 0)
- Uniquely keyed on (server_id, config_id, session_type, position).

**PointsConfigFastestLap** (per server config, per race session type):
- `config_id` (TEXT, FK → PointsConfigStore)
- `server_id` (TEXT)
- `session_type` (ENUM: SPRINT_RACE / FEATURE_RACE only)
- `fl_points` (INTEGER, default 0)
- `fl_position_limit` (INTEGER, nullable — null means no limit; otherwise driver must finish
  at or above this position to be eligible)
- Uniquely keyed on (server_id, config_id, session_type).

**SeasonPointsLink** (attachment record — weak link between server config and a season in
SETUP; discarded on approval after snapshot copied to SeasonPointsStore):
- `server_id` (TEXT)
- `season_id` (INTEGER, FK → Season)
- `config_id` (TEXT, FK → PointsConfigStore)
- Uniquely keyed on (server_id, season_id, config_id).

**SeasonPointsStore** (season-scoped snapshot of PointsConfigEntry rows — created on season
approval from the attached SeasonPointsLinks; completely independent of server store):
- Mirrors the schema of PointsConfigEntry with an added `season_id` column.
- Immutable after creation unless the mid-season amendment flow produces an approved
  replacement (at which point existing rows are replaced atomically).

**SeasonAmendmentState** (per server — tracks mid-season points amendment lifecycle):
- `server_id` (TEXT, PK)
- `season_id` (INTEGER, FK → Season)
- `amendment_active` (BOOLEAN, default false — true when `results amend toggle` has
  enabled amendment mode)
- `modified_flag` (BOOLEAN, default false — true once any modification is made to the
  modification store since the last revert or approval)

**SeasonModificationStore** (working copy of SeasonPointsStore during mid-season amendment;
mirrors SeasonPointsStore schema with an added `season_id` and `is_modification` flag;
cleared on successful amendment approval or explicit revert).

**ResultsModuleConfig** (per server — module-introduced configuration for the Results &
Standings module):
- `server_id` (TEXT, PK)
- `module_enabled` (BOOLEAN, default false)
- Per-division result and standings channel IDs are stored on a **DivisionResultsConfig**
  record (per division, per server):
  - `division_id` (INTEGER, FK → Division)
  - `results_channel_id` (TEXT, nullable)
  - `standings_channel_id` (TEXT, nullable)
  - `reserves_in_standings` (BOOLEAN, default true — the reserves visibility toggle)

**SessionResult** (per session, per round, per division — top-level result container):
- `session_result_id` (INTEGER PK, server-scoped auto-increment)
- `round_id` (INTEGER, FK → Round)
- `division_id` (INTEGER, FK → Division)
- `session_type` (ENUM: SPRINT_QUALIFYING / SPRINT_RACE / FEATURE_QUALIFYING / FEATURE_RACE)
- `status` (ENUM: ACTIVE / CANCELLED — CANCELLED when the special "CANCELLED" input is used)
- `applied_config_id` (TEXT, nullable — name of the seasonal config chosen for this session;
  null if CANCELLED)
- `submitted_by` (TEXT — Discord User ID of submitting tier-2 admin)
- `submitted_at` (TEXT — UTC ISO 8601 timestamp)

**DriverSessionResult** (per driver, per SessionResult):
- `driver_session_result_id` (INTEGER PK, server-scoped auto-increment)
- `session_result_id` (INTEGER, FK → SessionResult)
- `driver_id` (TEXT, FK → DriverProfile within server scope)
- `team_id` (INTEGER, FK → Team — the team the driver represented in this session)
- `finishing_position` (INTEGER, 1-indexed; null for CANCELLED sessions)
- `outcome_modifier` (ENUM: CLASSIFIED / DNF / DNS / DSQ)
- `tyre` (TEXT, nullable — qualifying sessions only)
- `best_lap` (TEXT, nullable — lap time string or DNS/DNF/DSQ marker; qualifying sessions)
- `gap` (TEXT, nullable — qualifying sessions)
- `total_time` (TEXT, nullable — race sessions)
- `fastest_lap` (TEXT, nullable — race sessions)
- `time_penalties` (TEXT, nullable — race sessions; raw input value)
- `post_stewarding_total_time` (TEXT, nullable — reserved for post-stewarding corrections)
- `post_race_time_penalties` (TEXT, nullable — reserved for post-race penalty records)
- `points_awarded` (INTEGER, computed — 0 if outcome_modifier ≠ CLASSIFIED or session
  CANCELLED; otherwise sum of position points + fastest-lap bonus if eligible)
- `has_fastest_lap` (BOOLEAN, default false)
- `status` (ENUM: ACTIVE / SUPERSEDED, default ACTIVE)
- `superseded_at` (TEXT, nullable)
- `supersession_reason` (TEXT, nullable)

**DriverStandingsSnapshot** (per driver, per round, per division — standings state after
that round's results are finalised):
- `snapshot_id` (INTEGER PK, server-scoped auto-increment)
- `round_id` (INTEGER, FK → Round)
- `division_id` (INTEGER, FK → Division)
- `driver_id` (TEXT, FK → DriverProfile within server scope)
- `total_points` (INTEGER)
- `position` (INTEGER — driver's rank in the division at this round)
- `position_finish_counts` (TEXT — JSON map: position integer → finish count integer)
- `position_first_round` (TEXT — JSON map: position integer → round number integer,
  recording the first round in which this driver obtained each finishing position)

**TeamStandingsSnapshot** (per team, per round, per division — mirrors DriverStandingsSnapshot
for team-level aggregates):
- `snapshot_id` (INTEGER PK, server-scoped auto-increment)
- `round_id` (INTEGER, FK → Round)
- `division_id` (INTEGER, FK → Division)
- `team_id` (INTEGER, FK → Team)
- `total_points` (INTEGER)
- `position` (INTEGER)
- `position_finish_counts` (TEXT — JSON map)
- `position_first_round` (TEXT — JSON map)

### New Entities (v2.7.0)

**PenaltyRecord** (per `DriverSessionResult` — one row per applied penalty):
- `penalty_id` (INTEGER PK, server-scoped auto-increment)
- `driver_session_result_id` (INTEGER, FK → DriverSessionResult)
- `penalty_type` (ENUM: TIME_PENALTY / DSQ)
- `time_seconds` (INTEGER, nullable — magnitude in seconds; null for DSQ)
- `reason` (TEXT, nullable — free-text reason supplied by the tier-2 admin)
- `applied_by` (TEXT — Discord User ID of the tier-2 admin who applied the penalty)
- `applied_at` (TEXT — UTC ISO 8601 timestamp)
- `voided` (BOOLEAN, default false — set to true when an AppealRecord with status
  OVERTURNED is resolved against this penalty)
- `announcement_channel_id` (TEXT, nullable — the channel ID where the penalty notice
  was posted; retained to enable the appeal outcome follow-up post to the same channel)
- Replaces the loose `post_race_time_penalties` and `post_stewarding_total_time` fields
  on DriverSessionResult; those fields are retained for backwards compatibility during
  migration but are superseded by PenaltyRecord rows.

**AppealRecord** (per `PenaltyRecord` — at most one per penalty lifetime):
- `appeal_id` (INTEGER PK, server-scoped auto-increment)
- `penalty_id` (INTEGER, FK → PenaltyRecord)
- `status` (ENUM: PENDING / UPHELD / OVERTURNED, default PENDING)
- `submitted_by` (TEXT — Discord User ID of the driver submitting the appeal)
- `submitted_at` (TEXT — UTC ISO 8601 timestamp)
- `reviewed_by` (TEXT, nullable — Discord User ID of the reviewing tier-2 admin)
- `reviewed_at` (TEXT, nullable — UTC ISO 8601 timestamp)
- `review_reason` (TEXT, nullable — free-text outcome reason supplied by the reviewer)
- Uniquely keyed on `penalty_id`; a second appeal row for the same penalty MUST be
  rejected at the data layer.

*Amendment to DivisionResultsConfig (v2.4.0 entity, updated v2.7.0)*:
- `penalty_channel_id` (TEXT, nullable) added — when set, penalty announcements and
  appeal outcomes for this division are posted to this channel; if null, the bot falls
  back to `results_channel_id`.

### New Entities (v2.10.0)

**AttendanceConfig** (per server, owned by the Attendance module):
- `server_id` (TEXT, PK)
- `module_enabled` (BOOLEAN, default false)
- `rsvp_notice_days` (INTEGER, default 5) — days before a round for RSVP embed posting.
- `rsvp_last_notice_hours` (INTEGER, default 1) — hours before round for un-RSVP'd ping;
  0 disables the last-notice ping.
- `rsvp_deadline_hours` (INTEGER, default 2) — hours before round when RSVP choices lock;
  0 means choices lock at round start time.
- `no_rsvp_penalty` (INTEGER, default 1) — attendance points per no-RSVP event.
- `no_attend_penalty` (INTEGER, default 1) — attendance points per no-attend event
  (added on top of no_rsvp_penalty when driver also did not RSVP).
- `no_show_penalty` (INTEGER, default 1) — attendance points per no-show-after-acceptance
  event.
- `autoreserve_threshold` (INTEGER, nullable — null means disabled) — total attendance
  points at which a full-time driver is automatically moved to Reserve.
- `autosack_threshold` (INTEGER, nullable — null means disabled) — total attendance points
  at which a driver is automatically removed from all team seats in all divisions.

**AttendanceDivisionConfig** (per server, per division, owned by the Attendance module):
- `server_id` (TEXT)
- `division_id` (INTEGER, FK → Division)
- `rsvp_channel_id` (TEXT, nullable) — channel for RSVP embeds and reserve distribution
  notices. Required before season approval when module is enabled.
- `attendance_channel_id` (TEXT, nullable) — channel for post-round attendance sheet posts.
  Required before season approval when module is enabled.
- Uniquely keyed on (server_id, division_id).

**DriverRoundAttendance** (per driver, per round, per division — one row per driver per
round while the Attendance module is enabled):
- `attendance_id` (INTEGER PK, server-scoped auto-increment)
- `round_id` (INTEGER, FK → Round)
- `division_id` (INTEGER, FK → Division)
- `driver_id` (TEXT, FK → DriverProfile within server scope)
- `rsvp_status` (ENUM: ACCEPTED / TENTATIVE / DECLINED / NO_RSVP, default NO_RSVP)
- `rsvp_timestamp` (TEXT, nullable — UTC ISO 8601; last time driver set status to
  ACCEPTED; reset each time driver returns to ACCEPTED)
- `rsvp_locked` (BOOLEAN, default false — set true at deadline or round start per locking
  rules in Principle XIII)
- `attended` (BOOLEAN, nullable — null until initial round results are submitted; true if
  driver appears in any DriverSessionResult for this round and division)
- `points_awarded` (INTEGER, nullable — null until post-race penalties are finalized;
  net points after pardons applied)
- `total_points_after` (INTEGER, nullable — cumulative attendance points for this driver
  in this division after this round's distribution)

**AttendancePardon** (per driver, per round, per attendance event type):
- `pardon_id` (INTEGER PK, server-scoped auto-increment)
- `attendance_id` (INTEGER, FK → DriverRoundAttendance)
- `pardon_type` (ENUM: NO_RSVP / NO_ATTEND / NO_SHOW)
- `justification` (TEXT, nullable — logged to calculation log channel only; never
  displayed in public-facing output)
- `applied_by` (TEXT — Discord User ID of the tier-2 admin who applied the pardon)
- `applied_at` (TEXT — UTC ISO 8601 timestamp)
- Uniquely keyed on (attendance_id, pardon_type) — at most one pardon per event type
  per driver per round.

### New Entities (v2.9.0)

**Track** (bot-packaged static registry — 27 circuits as of this version):

The Track registry is the authoritative lookup table for all circuit data used across
rounds, weather generation, and future statistics. Each entry is bot-packaged and
immutable at the registry level; individual weather parameters may be overridden
per server via the `track_rpc_params` DB table (`/track config`).

Fields per track entry:

- `track_id` (TEXT — zero-padded two-digit string, e.g. `"01"`, `"27"`; stable PK within
  the registry; referenced by rounds and by autocomplete commands).
- `canonical_name` (TEXT — the short display name used in all bot output, e.g.
  `"United Kingdom"`, `"Las Vegas"`).
- `country` (TEXT — the country or territory in which the circuit is located, e.g.
  `"Great Britain"`, `"United States"`).
- `circuit_name` (TEXT — the formal circuit/venue name, e.g. `"Silverstone Circuit"`,
  `"Las Vegas Strip Circuit"`).
- `mu_default` (REAL — bot-packaged mean rain probability; fractional 0–1).
- `sigma_default` (REAL — bot-packaged Beta dispersion; fractional 0–1).

The effective `(mu, sigma)` pair resolved at Phase 1 is: the server override stored in
`track_rpc_params` if present; otherwise `(mu_default, sigma_default)`.

**Track-based and tier-based statistics** (future module preparation):

Track-based stats (e.g., a driver's finishing positions or points scored at a specific
circuit) are derivable by joining `DriverSessionResult` → `SessionResult` → `Round`
→ `Track`. Tier-based stats (e.g., aggregated performance within a specific division tier)
are derivable by further joining via `Division.tier`. No additional entity is introduced
at this governance layer; the `Track` entity formalisation and the existing `Division.tier`
column are the authoritative structural prerequisites for these queries in the planned
"Season history and statistics" module (Principle VI).

### New Entities (v2.8.0)

*Amendment to Division (v1.0 entity, updated v2.8.0)*:
- `lineup_channel_id` (INTEGER, nullable) added to `divisions` — moved from
  `SignupDivisionConfig.lineup_channel_id`. When set, the bot deletes the previous lineup
  message and posts a fresh one to this channel on driver assignment changes in this division
  (Principle XI). Existing `lineup_channel_id` data is migrated from `signup_division_config`
  in migration 027.
- `calendar_channel_id` (INTEGER, nullable) added to `divisions` — when set, a calendar
  message listing all rounds is posted to this channel upon season approval (Principle XI).
- `lineup_message_id` (INTEGER, nullable) added to `divisions` — stores the Discord message
  ID of the most recently posted lineup message for this division (Principle XI, FR-014).
  Persisted to survive bot restarts.

*Amendment to SignupDivisionConfig (v2.6.0 entity, updated v2.8.0)*:
- `lineup_channel_id` removed — migrated to `divisions.lineup_channel_id` (migration 027).
- Remaining columns: `id`, `server_id`, `division_id`, `UNIQUE(server_id, division_id)`.
- The table is retained as an existence record for signup module per-division registrations.

### New Entities (v2.6.0)

**SignupDivisionConfig** (per server, per division — owned by the signup module):
- `server_id` (TEXT)
- `division_id` (INTEGER, FK → Division)
- `lineup_channel_id` (TEXT, nullable) — *removed v2.8.0; migrated to divisions table* (Principle XI).
- Uniquely keyed on (server_id, division_id). Created lazily on first per-division signup
  configuration; if absent, no lineup notices are posted for that division.

*Amendment to SignupConfiguration (v2.2.0 entity, updated v2.6.0)*:
- `close_at` (TEXT, nullable) added — see SignupConfiguration definition above.

### New Entities (v2.5.0)

No new database schema entities are introduced at this governance layer. The Season Archive
is a governance concept formalising that `Season` records (and all their associated data
— Division, Round, SessionResult, DriverStandingsSnapshot, SeasonAssignment, etc.) in the
`COMPLETED` state are permanently retained. Concrete schema additions (e.g., archive
indexing tables, a dedicated stats-query layer, or migration scaffolding to clear any prior
ephemeral-deletion logic) are scoped to the season persistence feature specification.

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

All pull requests MUST include a Constitution Check confirming compliance with Principles I–XIII
before merge. Any deliberate violation of a principle MUST be documented in the plan's
Complexity Tracking table with a justification for why the simpler compliant path is
insufficient.

**Version**: 2.10.0 | **Ratified**: 2026-03-03 | **Last Amended**: 2026-04-03
