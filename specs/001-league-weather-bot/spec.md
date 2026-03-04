# Feature Specification: F1 League Weather Randomizer Bot — Core System

**Feature Branch**: `001-league-weather-bot`  
**Created**: 2026-03-03  
**Status**: Draft  
**Input**: User description: Full functional specification for the F1 league Discord weather randomizer bot

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Season Setup by a Trusted Admin (Priority: P1)

A Race Director (trusted admin) sets up a new season at the start of the competitive year.
They invoke the season setup command and are guided through an interactive configuration
session in which they define: the season start date, the number of divisions, the day of the
week and time for each division's races, the number of rounds and their formats and tracks,
any gap weeks in the calendar, the Discord role used to mention each division's drivers, and
the weather forecast channel for each division. When satisfied, they issue a review command,
inspect the summary, and give final approval.

**Why this priority**: Without a valid season configuration the bot cannot perform any
function for any division. Everything else depends on this story being completable.

**Independent Test**: Can be tested end-to-end by: invoking the setup command, completing
all prompts for a single division with two rounds, issuing the review command, approving, and
verifying that the bot confirms a successfully configured season and that no phase activity
occurs until the configured horizons are reached.

**Acceptance Scenarios**:

1. **Given** no season is configured, **When** a trusted admin issues the season setup command in the interaction channel, **Then** the bot opens an interactive configuration session and collects all required season parameters in order.
2. **Given** an active configuration session, **When** the admin issues the review command, **Then** the bot presents a complete summary of all entered configuration and offers approve or amend options.
3. **Given** a review summary is presented, **When** the admin amends a specific setting, **Then** only that setting is updated and the bot returns to the review state.
4. **Given** a review summary is presented, **When** the admin gives final approval, **Then** the bot stores the full season configuration and confirms it is ready to begin weather generation.
5. **Given** an unprivileged user, **When** they attempt to issue the season setup command, **Then** the bot responds with a clear permission error and does not start a session.
6. **Given** any user, **When** they issue any command outside the configured interaction channel, **Then** the bot produces no response.

---

### User Story 2 — Automated Three-Phase Weather Generation for a Round (Priority: P2)

At the three defined horizons before a scheduled round, the bot automatically carries out
each weather generation phase without any manual intervention. Drivers in the relevant
division receive time-appropriate weather updates in their division's forecast channel, and
full computation records appear in the calculation log channel.

**Why this priority**: This is the core value the bot delivers. With the season configured
(US1), this story makes the bot independently useful: 27 tracks × however many divisions
× three phases per round, all firing automatically.

**Independent Test**: Can be tested with a single-division, single-round season by advancing
system time to each horizon and confirming the correct message appears in the forecast channel
and the correct computation log appears in the log channel for each phase.

**Acceptance Scenarios**:

1. **Given** a configured season with an upcoming round, **When** the system clock reaches exactly 5 days before the scheduled round start, **Then** the bot computes Phase 1 (`Rpc`), posts the rain-probability message in the division's forecast channel, and logs all inputs and results to the calculation log channel.
2. **Given** Phase 1 has completed for a round, **When** the system clock reaches exactly 2 days before the scheduled round start, **Then** the bot uses the persisted `Rpc` to compute Phase 2 session types, posts the session-type forecast message, and logs the full draw sequence to the log channel.
3. **Given** Phase 2 has completed for a round, **When** the system clock reaches exactly 2 hours before the scheduled round start, **Then** the bot uses the persisted `Rpc` and Phase 2 results to generate per-slot weather for every session, posts the final weather layout message, and logs all draws to the log channel.
4. **Given** a round is configured as Mystery, **When** any phase horizon is reached, **Then** the bot performs no computation and posts no message to either channel for that round.
5. **Given** a Sprint round, **When** Phase 3 fires, **Then** the output message correctly names all four sessions with their individual slot sequences.
6. **Given** a mixed-type session in Phase 3, **When** all drawn slots happen to be wet-weather types, **Then** the output is posted as-is — no override — and the log records the draw faithfully.
7. **Given** a session where Phase 3 draws multiple slots all of the same exact type (e.g., three Clear slots), **When** the Phase 3 output is posted, **Then** the forecast channel shows the single simplified type label (e.g., `Clear`) and the calculation log channel shows the simplified label followed by the full raw draw sequence in parentheses (e.g., `Clear (draws: Clear, Clear, Clear)`). A session with only one drawn slot is rendererd as its single draw with no simplification treatment.

---

### User Story 3 — Post-Season-Approval Configuration Amendment (Priority: P3)

During an active season a Race Director discovers an error in the schedule or needs to change
a round's track. They use an amendment command to update the relevant round. The bot handles
all consequences: storing the change, invalidating any previously posted weather for that
round, notifying drivers, and re-running any phases whose horizons have already passed.

**Why this priority**: Amendments are operationally critical but require the full pipeline
(US1 + US2) to be meaningful. They unlock competitive resilience without requiring a full
season reset.

**Independent Test**: Configure a season, allow Phase 1 to complete for a round, then amend
that round's track. Verify the invalidation notice appears in the forecast channel, the old
Phase 1 output is marked as invalidated in the log, and Phase 1 is re-executed with the new
track's base factor.

**Acceptance Scenarios**:

1. **Given** an active season, **When** a trusted admin amends a round's track after Phase 1 has completed, **Then** the bot posts an invalidation notice to the division forecast channel, marks the previous Phase 1 output as `INVALIDATED` in the log channel, discards the cached `Rpc`, and immediately re-executes Phase 1.
2. **Given** an active season, **When** a trusted admin postpones a round's date and Phase 2 has already completed, **Then** the bot invalidates Phases 1 and 2, clears all cached phase state for that round, and re-executes both phases using the new schedule.
3. **Given** an amendment to a round whose phases have not yet fired, **When** the admin saves the change, **Then** no invalidation notice is posted and no re-execution occurs; phases will fire at the new horizons.
4. **Given** an unprivileged user, **When** they attempt an amendment command, **Then** the bot responds with a permission error and no state is changed.

---

### User Story 4 — Bot and Interaction Channel Initialisation (Priority: P4)

Before any season can be configured, a server administrator performs a one-time bot
initialisation to designate: the interaction role (who can use the bot), the interaction
channel (where commands are accepted), and the calculation log channel (where all computation
and audit records are posted). These settings are distinct from season configuration.

**Why this priority**: A prerequisite for US1, but it is a one-time setup that can be
completed trivially and then ignored for the rest of the season's lifetime.

**Independent Test**: In a fresh server, run the bot initialisation command, specify a role,
an interaction channel, and a log channel, confirm the bot acknowledges the configuration,
then verify that a command issued in the correct channel by a role holder is accepted, and
a command from outside the channel or by a non-role holder is rejected.

**Acceptance Scenarios**:

1. **Given** a fresh server with no bot config, **When** a server administrator runs the initialisation command and specifies the interaction role and channels, **Then** the bot stores the configuration and confirms readiness.
2. **Given** bot initialisation is complete, **When** a role-holding user sends a command in the interaction channel, **Then** the bot processes the command normally.
3. **Given** bot initialisation is complete, **When** any user sends a command outside the interaction channel, **Then** the bot produces no response.
4. **Given** bot initialisation is complete, **When** a user without the interaction role sends a command in the interaction channel, **Then** the bot responds with a permission error.

---

### Edge Cases

- What happens when Phase 1 has not yet completed and Phase 2's horizon is reached (e.g., the bot was offline)? Re-trigger Phase 1 immediately, then execute Phase 2 once Phase 1 completes.
- What happens when a round is amended while Phase 3 is actively being computed? The amendment must queue and be applied atomically after Phase 3 finishes; the result of the in-progress Phase 3 is then immediately invalidated.
- What happens when `Rpc` computes to a value outside the expected range (negative or >1 in fractional form) due to edge random draws? The result must be clamped to [0, 1] before any downstream use, and the anomaly logged.
- What happens when a division's forecast channel is deleted from Discord between configuration and phase execution? The bot must log the failure to the calculation log channel and alert the trusted admin via the interaction channel.
- What happens if the bot is restarted between phases? All persisted state (Rpc, Phase 2 draws, schedule) must be read from durable storage on restart; no phase should fire twice.
- What happens when the number of configured divisions changes mid-season (adding a new division)? New divisions require a fresh configuration sub-session; existing divisions are unaffected.

## Requirements *(mandatory)*

### Functional Requirements

**Bot Initialisation**

- **FR-001**: The bot MUST support a one-time initialisation command that configures the interaction role, the interaction channel, and the calculation log channel per Discord server.
- **FR-002**: The bot MUST accept commands only from users holding the configured interaction role and only when those commands are sent in the configured interaction channel. Commands sent elsewhere MUST be silently ignored.
- **FR-003**: The bot MUST distinguish a trusted admin tier (season/config authority) from the general interaction role; trusted admin status MUST be explicitly assigned and is not implied by the interaction role alone.

**Season Configuration**

- **FR-004**: The bot MUST provide an interactive, prompted configuration session started by a single command, through which a trusted admin defines: season start date, number of divisions, per-division race day and time, per-division driver mention role, per-division weather forecast channel, number of rounds, per-round format and (where applicable) track, and any gap weeks.
- **FR-005**: The bot MUST support a review command that presents a complete summary of all entered configuration settings before final approval.
- **FR-006**: During review, the trusted admin MUST be able to amend any individual setting; the bot MUST return to the review state after each amendment.
- **FR-007**: Final approval MUST store all configuration durably and arm the bot for automatic weather generation.
- **FR-008**: After final approval, a trusted admin MUST be able to amend any round's track, date/time, or format via a dedicated command at any point during the active season.

**Round Formats and Sessions**

- **FR-009**: The bot MUST support exactly four round formats: Normal (Short Qualifying + Long Race), Sprint (Short Sprint Qualifying + Long Sprint Race + Short Feature Qualifying + Long Feature Race), Mystery (no sessions; all phases skipped), Endurance (Full Qualifying + Full Race).
- **FR-010**: The maximum weather slot counts per session type MUST be fixed: Short Qualifying / Short Feature Qualifying — 2; Short Sprint Qualifying — 2; Long Race / Long Feature Race — 3; Long Sprint Race — 1; Full Qualifying — 3; Full Race — 4. These MUST NOT be overridden at runtime.
- **FR-011**: Mystery rounds MUST suppress all three phases and produce no weather output in any channel.

**Phase 1 — Rain Probability**

- **FR-012**: The bot MUST automatically execute Phase 1 exactly 5 days before each non-Mystery round's scheduled start time.
- **FR-013**: Phase 1 MUST compute `Rpc = (Btrack × rand1 × rand2) / 3.025` where `rand1` and `rand2` are independently drawn integers in [1, 98], and `Btrack` is the track's assigned base factor.
- **FR-014**: The bot MUST recognise the 27 defined tracks and their base factors. Tracks not in the defined list MUST be rejected during configuration.
- **FR-015**: `Rpc` MUST be stored against the round and division and retained until Phase 3 completes or the round is invalidated.
- **FR-016**: After Phase 1 the bot MUST post a rain probability message mentioning the division role, the track name, and `Rpc` expressed as a percentage rounded to the nearest integer, in the division's weather forecast channel.

**Phase 2 — Session Type Draw**

- **FR-017**: The bot MUST automatically execute Phase 2 exactly 2 days before each non-Mystery round's scheduled start time.
- **FR-018**: Phase 2 MUST construct a 1 000-entry weighted map of Rain, Mixed, and Sunny slots using the formulas defined in the functional specification, and draw once per session in the round.
- **FR-019**: Phase 2 draws MUST be persisted per session against the round and division until Phase 3 consumes them or the round is invalidated.
- **FR-020**: After Phase 2 the bot MUST post a session-type forecast message for all sessions in the round in the division's weather forecast channel.

**Phase 3 — Final Slot Generation**

- **FR-021**: The bot MUST automatically execute Phase 3 exactly 2 hours before each non-Mystery round's scheduled start time.
- **FR-022**: Phase 3 MUST determine a random `Nslots` for each session with a minimum of 1 (or 2 for mixed sessions) and a maximum equal to the session type's slot-count cap.
- **FR-023**: Phase 3 MUST build a per-session weighted map of the five concrete weather types (Clear, Light Cloud, Overcast, Wet, Very Wet) using the formulas defined in the functional specification, with all map entries clamped to a minimum of 0. A separate map MUST be built and discarded for each session.
- **FR-024**: After Phase 3 the bot MUST post a natural-language final weather layout message for all sessions in the round in the division's weather forecast channel; the weather slot sequence for each session MUST be rendered in a natural, readable form. When all drawn slots for a session are the exact same weather type (exact string match across the five canonical types: Clear, Light Cloud, Overcast, Wet, Very Wet), both the forecast channel post and the calculation log channel post MUST render that session's output as the single type label rather than listing each slot individually; the calculation log channel post MUST additionally append the full raw draw sequence in parentheses (e.g., `Clear (draws: Clear, Clear, Clear)`). Sessions where `Nslots = 1` are exempt from this rule and are rendered as their single draw without any simplification treatment.

**Amendment Invalidation**

- **FR-025**: If a round is amended after any phase has completed, the bot MUST atomically: post an invalidation notice in the division's weather forecast channel, mark all completed phase outputs for that round as `INVALIDATED` in the calculation log channel, clear the active phase state for that round, and re-execute all phases whose time horizons have already passed given the new schedule.
- **FR-026**: Previously invalidated phase outputs MUST be retained in the audit log; they MUST NOT be deleted.

**Output Channels**

- **FR-027**: The bot MUST post weather forecast messages only to the relevant division's configured weather forecast channel.
- **FR-028**: The bot MUST post all phase computation records, config mutation confirmations, and audit trail entries only to the configured calculation log channel.
- **FR-029**: The bot MUST NOT post unsolicited messages to any channel other than the two categories defined in FR-027 and FR-028.

**Multi-Division**

- **FR-030**: The bot MUST handle all configured divisions independently and in parallel within a single server; a change to one division's data MUST NOT affect any other division.

### Key Entities

- **Server Config**: Represents the bot's per-server initialisation — interaction role, interaction channel, calculation log channel. Independent of season data.
- **Season**: Represents a single racing year — start date, lifecycle state (SETUP / ACTIVE / COMPLETED), and the collection of divisions and rounds it contains.
- **Division**: A competitive tier within a season — identifier, Discord mention role, weather forecast channel, scheduled race day and time. Owns its own sequence of rounds.
- **Round**: A single race event within a division — round number, format, assigned track, scheduled date/time, current phase completion status, and a list of sessions derived from the format.
- **Session**: A racing session within a round — type (Short Qualifying, Long Race, etc.), Phase 2 weather-type draw, Phase 3 slot sequence.
- **Phase Result**: A stored computation artifact — which phase, round, division, UTC timestamp, all inputs and outputs, and an `ACTIVE` or `INVALIDATED` status.
- **Track**: A circuit on the calendar — name and base rain probability factor (`Btrack`). The 27 supported tracks and their values are fixed and defined in the specification.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A trusted admin can complete full season configuration — including multiple divisions and rounds — from scratch in under 15 minutes using only bot commands in Discord.
- **SC-002**: All three weather phase messages are delivered autonomously within 5 minutes of their scheduled trigger horizon, requiring zero manual intervention from any user.
- **SC-003**: A round amendment — including invalidation notice posting and re-execution of already-passed phases — completes within 10 minutes of the admin saving the change.
- **SC-004**: No weather output or computation log ever appears in any channel other than the two designated channel categories; this holds across 100% of test scenarios.
- **SC-005**: The full computation history for any division's round (all three phases, all drawn values) is retrievable from the calculation log channel without consulting any external system.
- **SC-006**: After a bot restart, all in-progress phase schedules resume correctly with no phase firing twice and no phase being skipped, for 100% of rounds in flight at restart time.

## Assumptions

- The Discord server has at most one active season at a time. Multiple concurrent seasons in the same server (e.g., a mini-season running alongside the main season) are out of scope.
- Round start times are expressed and stored in UTC. User-facing display may localise times, but all internal scheduling logic uses UTC.
- The 27 tracks and their `Btrack` base factors are fixed at this specification version. Adding new tracks is a configuration change requiring a spec amendment, not a runtime admin command.
- "Gap weeks" in the calendar are weeks where no round is scheduled in any division; the bot takes no action during gap weeks.
- The bot is expected to operate continuously (always-on hosting). If downtime causes a phase to be missed, recovery behaviour (trigger immediately after restart if horizon has passed) is assumed per FR-012/FR-017/FR-021 semantics.
- The interactive configuration session supports a single admin at a time per server; concurrent configuration sessions are not supported in this version.

## Clarifications

### Session 2026-03-04

- Q: Should the Phase 3 slot-simplification apply to the forecast channel only, or also to the calculation log channel? → A: Both channels; forecast channel shows simplified label only, log channel shows simplified label + raw draw list in parentheses.
- Q: Does slot-simplification trigger on exact weather-type string match or a broader family match (e.g., Wet + Very Wet)? → A: Exact string match only across the five canonical types (Clear, Light Cloud, Overcast, Wet, Very Wet).
- Q: Should single-slot sessions (Nslots = 1) receive the simplified rendering? → A: No; single-slot sessions are exempt and rendered as their single draw with no special treatment.
