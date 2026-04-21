# Feature Specification: Attendance Module RSVP Check-in & Reserve Distribution

**Feature Branch**: `032-attendance-rsvp-checkin`  
**Created**: 2026-04-03  
**Status**: Draft  

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Automated RSVP Embed Posting (Priority: P1)

The bot automatically posts an RSVP embed in the division's configured RSVP channel at the
correct notice window before a scheduled round. The embed shows the round's title, scheduled
time, location, and event type, followed by a roster of every driver in the division grouped
by team. Each driver's RSVP status is shown inline. Three action buttons (Accept, Tentative,
Decline) appear below the embed for drivers to interact with.

**Why this priority**: Foundational to all subsequent RSVP behaviour — without the embed
being posted, no driver can respond and no distribution can run.

**Independent Test**: Can be fully tested by configuring a round with a known start time,
confirming the embed appears in the RSVP channel at the correct moment with all required
fields, and verifying the three buttons are present.

**Acceptance Scenarios**:

1. **Given** the attendance module is enabled, a division has a configured RSVP channel,
   and a round is scheduled to start in exactly `rsvp_notice_days` days,
   **When** the scheduled notice timer fires, **Then** the bot posts an RSVP embed in that
   division's RSVP channel containing the correct title, timestamp, location, event type,
   full driver roster with `()` indicators, and three action buttons.
2. **Given** no RSVP channel is configured for a division at the moment the notice job fires,
   **When** the job runs, **Then** no embed is posted, no interaction channel error is
   emitted, and an audit log entry records the skip.
3. **Given** the bot restarts before a round's notice timer has fired, **When** the bot
   comes back online, **Then** any still-pending RSVP notice jobs are re-armed and fire at
   the originally scheduled time.

---

### User Story 2 — Driver RSVP Button Interaction (Priority: P1)

A driver registered in a division presses one of the three buttons on the RSVP embed to
declare their attendance intention. The embed updates their status indicator in-place and
the choice is persisted. A driver may change their response as many times as they like until
their locking threshold is reached. Users not registered in the division are silently
rejected.

**Why this priority**: Core interactive functionality. Without driver responses, reserve
distribution has no input and the attendance system cannot function.

**Independent Test**: Can be fully tested by having registered full-time and reserve drivers
press each button before the deadline, verifying the embed updates and the database records
the correct status.

**Acceptance Scenarios**:

1. **Given** a full-time driver registered in the division presses Accept before the RSVP
   deadline, **When** the interaction is processed, **Then** the embed shows `(✅)` next to
   that driver's name and the persisted status is ACCEPTED.
2. **Given** a driver presses Tentative, **When** processed, **Then** the embed shows `(❓)`
   and the status is TENTATIVE.
3. **Given** a driver presses Decline, **When** processed, **Then** the embed shows `(❌)`
   and the status is DECLINED.
4. **Given** a driver previously accepted and now presses Decline before the deadline,
   **When** processed, **Then** the embed updates to `(❌)` and the stored status changes to
   DECLINED.
5. **Given** a driver presses the same button they already have selected (e.g. pressing
   Accept when already ACCEPTED), **When** processed, **Then** the embed is unchanged and
   the driver receives an ephemeral acknowledgement; no status change occurs.
6. **Given** a user who is not a registered full-time or reserve driver in this division
   presses any button, **When** processed, **Then** they receive an ephemeral error and the
   embed is unchanged.
7. **Given** a full-time driver presses any button after the RSVP deadline has passed,
   **When** processed, **Then** they receive an ephemeral error stating the deadline has
   passed and the embed is unchanged.

---

### User Story 3 — Reserve RSVP Extended Window (Priority: P2)

Reserve drivers have a broader window to change their RSVP response than full-time drivers,
allowing last-minute substitution flexibility. A reserve who has not accepted can change
their status all the way up to the scheduled round start time. However, once a reserve has
accepted, their choice locks at the RSVP deadline alongside full-time drivers — distribution
cannot be undone by a late status change.

**Why this priority**: Builds on the basic button interaction (P1). Important for reserve
management correctness but only relevant after the embed and basic interactions work.

**Independent Test**: Can be tested independently by verifying that a reserve driver who
has not accepted can still change their status after the full-time RSVP deadline but before
round start, while a reserve who has accepted is blocked from changing after the deadline.

**Acceptance Scenarios**:

1. **Given** a reserve driver whose current status is Tentative and the RSVP deadline has
   passed but the round has not yet started, **When** they press Accept, **Then** the embed
   updates to `(✅)` and the status is ACCEPTED.
2. **Given** a reserve driver whose current status is Declined and the scheduled round start
   time has passed, **When** they press any button, **Then** they receive an ephemeral error
   and the embed is unchanged.
3. **Given** a reserve driver who has Accepted and the RSVP deadline has passed, **When**
   they press Decline, **Then** they receive an ephemeral error and their ACCEPTED status is
   retained.

---

### User Story 4 — Reserve Distribution at RSVP Deadline (Priority: P2)

When the RSVP deadline is reached, the bot automatically distributes accepted reserves to
the teams that need substitutes, following the defined priority and tie-breaking algorithm.
A message is posted in the division's RSVP channel announcing team assignments for placed
reserves and standby status for unplaced reserves.

**Why this priority**: Core downstream outcome of the RSVP process. Provides league
participants with clear assignments before the round begins.

**Independent Test**: Can be tested independently by setting up a round with known RSVP
states (accepted full-timers, declined full-timers, accepted reserves) and verifying the
distribution output matches the expected priority-and-tie-break ordering.

**Acceptance Scenarios**:

1. **Given** the RSVP deadline is reached with accepted reserves available and teams needing
   substitutes, **When** distribution runs, **Then** reserves are assigned to teams in the
   correct priority order (NO_RSVP first, then DECLINED, then partially-staffed teams with
   empty seats, then fully-unstaffed teams, then TENTATIVE) with correct tie-breaking applied.
2. **Given** two teams are tied on priority and tie-breaker 1 (fewest accepted full-timers),
   **When** distribution runs, **Then** the team with the lower Constructors' Championship
   position receives the reserve first.
3. **Given** no championship standings exist yet for the division, **When** distribution
   runs and two teams are tied on all criteria, **Then** the teams are ordered alphabetically
   by team name as a deterministic fallback.
4. **Given** multiple reserves have accepted, **When** distribution runs, **Then** they are
   assigned in ascending order of their acceptance timestamp (earliest confirmer gets first
   pick of available teams).
5. **Given** a reserve changed their RSVP status to Accepted more than once, **When**
   distribution runs, **Then** the effective acceptance timestamp is the time of their
   most recent change to Accepted.
6. **Given** more accepted reserves exist than available team slots, **When** distribution
   runs, **Then** the unplaced reserves are classified as on standby.
7. **Given** distribution has completed, **When** the announcement is posted in the RSVP
   channel, **Then** the message mentions each assigned reserve and their team, and each
   standby reserve and their standby status.
8. **Given** no reserves have accepted at the deadline, **When** distribution runs, **Then**
   no distribution message is posted in the RSVP channel.

---

### User Story 5 — Last-Notice Ping for Non-Responding Full-Time Drivers (Priority: P2)

At a configurable number of hours before the round, the bot sends a direct mention in the
division's RSVP channel to every full-time driver who has not yet responded to the RSVP
embed. Reserve drivers are excluded — they have an extended window and pinging them at
this point would be inconsistent with their looser deadline. The last-notice ping is
optional; setting the value to 0 disables it entirely.

**Why this priority**: Provides a timely nudge to drivers who missed the initial embed,
reducing NO_RSVP counts before the deadline and improving reserve distribution quality.
Depends on US1 (embed must have been posted) and can operate independently of US3/US4.

**Independent Test**: Can be fully tested by configuring a non-zero `rsvp_last_notice_hours`
value, advancing time to the trigger threshold, and verifying that only full-time drivers
with NO_RSVP status receive a mention in the RSVP channel while full-time drivers who have
already responded and all reserve drivers are not mentioned.

**Acceptance Scenarios**:

1. **Given** `rsvp_last_notice_hours` is non-zero, a round is scheduled, and
   the last-notice threshold is reached, **When** the job fires, **Then** the bot posts a
   message in the division's RSVP channel mentioning only the full-time drivers whose RSVP
   status is still NO_RSVP at that moment.
2. **Given** all full-time drivers in a division have already responded (no NO_RSVP status)
   when the last-notice job fires, **When** the job runs, **Then** no message is posted in
   the RSVP channel for that division.
3. **Given** `rsvp_last_notice_hours` is set to 0, **When** the round schedule is processed,
   **Then** no last-notice job is created or armed for any round; no ping is ever sent.
4. **Given** the bot restarts before a last-notice job has fired, **When** the bot comes
   back online, **Then** the job is re-armed and fires at the originally scheduled time.
5. **Given** a full-time driver responds to the embed between the RSVP embed posting and
   the last-notice threshold, **When** the last-notice job fires, **Then** that driver is
   NOT mentioned in the ping.
6. **Given** a reserve driver has not responded at the last-notice threshold, **When** the
   job fires, **Then** that reserve driver is NOT mentioned in the ping.

---

### Edge Cases

- What happens if the RSVP channel is deleted or becomes inaccessible after the embed is
  posted but before a driver interacts? Button presses that cannot be acknowledged or whose
  embed cannot be edited fail gracefully; the bot logs the error but does not crash and does
  not propagate an unhandled exception.
- What if a driver is added to the division after the RSVP embed has already been posted?
  Their name does not appear in the original embed; the embed is not retroactively updated
  for roster changes after posting. They may still interact with the buttons, but their
  status will not be visible in the roster display.
- What if the bot restarts while the RSVP deadline job is pending? The deadline job is
  re-armed on restart. If the deadline has already passed while the bot was offline,
  distribution runs immediately on restart for any rounds that missed it.
- What if a team has no full-time drivers for a round (e.g., all were sacked or unassigned)?
  That team still appears in the distribution priority with zero accepted drivers and is
  ranked at the highest priority tier (tied with other teams in the same state).
- What if no teams require a reserve at all (all full-timers accepted)? Reserve distribution
  still runs at the deadline but produces no assignments and no announcement message.
- What if `rsvp_deadline_hours` is 0? The deadline is the scheduled round start time;
  distribution and full-time locking occur at round start.
- What if a round is cancelled or amended after the RSVP embed has been posted? Any
  existing RSVP responses and scheduled deadline jobs for the cancelled/amended round are
  discarded. If the round is rescheduled with a new time, a new RSVP embed is posted
  according to the new schedule.
- What if all full-time drivers respond before the last-notice threshold is reached?
  The job fires but posts nothing to the RSVP channel, as there are no NO_RSVP full-time
  drivers to mention.
- What if the last-notice threshold is reached while the bot was offline? The job is
  re-armed on restart; if the threshold time has already passed, the job is skipped
  silently (no retroactive ping is sent — the deadline may already be imminent or past).

## Requirements *(mandatory)*

### Functional Requirements

**RSVP Embed Posting**

- **FR-001**: At exactly `rsvp_notice_days` days before each round's scheduled
  start time, the bot MUST automatically post an RSVP embed to the division's configured
  RSVP channel.
- **FR-003**: The RSVP embed title MUST follow the format
  `Season <N> Round <N> — <Track canonical name>`.
- **FR-004**: The embed MUST include: the scheduled round datetime as a dynamic Discord
  timestamp; the round location (track canonical name, or "Mystery" for Mystery rounds);
  and the event type (Normal, Sprint, Endurance, or Mystery).
- **FR-005**: The embed MUST display a per-team driver roster covering all teams in the
  division including the Reserve team. For each team, every assigned driver's display name
  MUST appear with their current RSVP status indicator inline: `()` for no response,
  `(✅)` for Accepted, `(❓)` for Tentative, `(❌)` for Declined.
- **FR-006**: The embed MUST include three action buttons arranged horizontally: Accept
  (green, ✅), Tentative (grey, ❓), Decline (red, ❌).
- **FR-007**: RSVP notice jobs MUST be scheduled at season approval time for all rounds
  whose notice horizon has not yet passed. On bot restart, any pending RSVP notice
  jobs for future rounds MUST be re-armed before the bot resumes serving interactions.
- **FR-008**: If no RSVP channel is configured for a division when a notice job fires, the
  bot MUST skip posting for that division, log an audit entry recording the skip, and
  continue without error.
- **FR-009**: `DriverRoundAttendance` rows MUST be created for all full-time and reserve
  drivers in the division with status NO_RSVP when the RSVP embed is posted, so the roster
  and downstream operations have a complete, queryable attendance record from the start.

**Driver RSVP Interaction**

- **FR-010**: When a registered driver in the division (full-time or reserve) presses a
  button, their RSVP status MUST be updated in the embed and persisted in the
  `DriverRoundAttendance` record for that driver, round, and division.
- **FR-011**: Only drivers registered as full-time or reserve members of the division for
  the current season MUST be permitted to interact. All other users MUST receive an
  ephemeral error; the embed MUST remain unchanged.
- **FR-012**: The RSVP embed MUST be edited in-place to reflect the updated status; no
  new message is posted in response to a button press.
- **FR-013**: A driver pressing the same button they have already selected MUST result in
  a no-op; the driver receives an ephemeral acknowledgement and no status change occurs.

**RSVP Locking**

- **FR-014**: Full-time driver RSVP choices MUST be locked once the `rsvp_deadline_hours`
  threshold has passed. Any button press by a full-time driver after this point MUST be
  rejected with a clear ephemeral error stating that the deadline has passed.
- **FR-015**: Reserve drivers who have ACCEPTED MUST also be locked at the
  `rsvp_deadline_hours` threshold (same timing as full-time drivers).
- **FR-016**: Reserve drivers whose status is NOT ACCEPTED (NO_RSVP, TENTATIVE, or
  DECLINED) MUST remain able to change their response until the scheduled round start time,
  after which their response locks.
- **FR-017**: When `rsvp_deadline_hours` is 0, the deadline is the scheduled round start
  time; full-time locking, accepted-reserve locking, and non-accepted-reserve locking all
  occur at the same moment.

**Reserve Distribution**

- **FR-018**: At the `rsvp_deadline_hours` threshold, the bot MUST run the reserve
  distribution algorithm for every division with an active RSVP embed for the round.
- **FR-019**: Only reserves whose RSVP status is ACCEPTED at the deadline moment MUST be
  eligible for distribution.
- **FR-020**: Teams MUST be ranked as distribution candidates in the following priority
  order:
  1. Teams where all full-time seats are physically vacant (no full-time drivers
     assigned to the team at all).
  2. Teams where at least one full-time driver has NO_RSVP status.
  3. Teams where at least one full-time driver has DECLINED.
  4. Teams that have at least one physically vacant full-time seat
     (`total_assigned_full_timers < max_seats`) while still having some FT drivers
     assigned.
  5. Teams that have already received at least one reserve allocation in the current
     distribution run (second or subsequent fills). This tier is evaluated dynamically:
     after each reserve is placed the receiving team is demoted to tier 5 so that every
     needy team in tiers 1–4 receives its first reserve before any team receives a
     second one. Teams whose only vacancy reason is TENTATIVE (tier 6) remain at tier
     6 even after receiving a reserve.
  6. Teams where at least one full-time driver is TENTATIVE.
  Teams where all full-time drivers have ACCEPTED and all seats are filled are not
  distribution candidates and receive no reserve assignment.
- **FR-021**: Within each priority tier, ties MUST be broken in order by:
  1. Constructors' Championship position in the division (lowest-ranked team first,
     i.e. last place in the standings gets earliest access to reserves).
  2. Alphabetical by team name as a deterministic fallback when championship standings
     are unavailable (no rounds completed yet) or positions are equal.
- **FR-022**: When a reserve sets their RSVP status to Accepted, their acceptance timestamp
  MUST be recorded. If they change away from Accepted and back, the timestamp MUST be reset
  to the time of the most recent change to Accepted. Reserves with the earliest acceptance
  timestamp are distributed first.
- **FR-023**: Each distribution candidate team receives at most one reserve per vacancy.
  Vacancies include: each full-time driver with NO_RSVP, DECLINED, or TENTATIVE status,
  plus each seat with no full-time driver assigned at all
  (`max_seats − total_assigned_full_timers`). ACCEPTED seats are not vacancies and are
  never filled by a reserve. A single reserve fills one slot in one team only.
- **FR-024**: Accepted reserves not placed in any team due to no remaining vacancies MUST
  be classified as on standby.
- **FR-025**: After distribution, if any reserves were eligible (accepted at the deadline),
  the bot MUST post a message in the division's RSVP channel:
  - Mentioning each assigned reserve by Discord user tag and stating the team they are
    racing for.
  - Mentioning each standby reserve by Discord user tag and informing them they are on
    standby and should be prepared to substitute.
- **FR-026**: If no reserves have accepted at the deadline, no distribution message MUST
  be posted.
- **FR-027**: The deadline distribution job MUST be scheduled at season approval time
  alongside the notice job and MUST be re-armed on bot restart if the deadline has not
  yet passed. If the deadline has passed while the bot was offline, distribution MUST run
  immediately on bot startup for any affected rounds.

**Last-Notice Ping**

- **FR-028**: When `rsvp_last_notice_hours` is non-zero, a last-notice job MUST be
  scheduled for each round at exactly `rsvp_last_notice_hours` hours before
  the scheduled round start time. When the job fires, the bot MUST post a single message
  in the division's configured RSVP channel that mentions (by Discord user tag) every
  full-time driver whose `DriverRoundAttendance` status for that round is still NO_RSVP
  at the moment the job runs. Reserve drivers MUST NOT be included in the mention
  regardless of their RSVP status.
- **FR-029**: If no full-time driver has a NO_RSVP status when the job fires, no message
  MUST be posted for that division. Last-notice jobs MUST be scheduled at season approval
  time alongside the RSVP notice and deadline jobs and MUST be re-armed on bot restart if
  the threshold time has not yet passed. If the threshold time has already passed when the
  bot restarts, the job MUST be skipped silently — no retroactive ping is sent.
- **FR-030**: When `rsvp_last_notice_hours` is 0, no last-notice job MUST be created or
  armed for any round.

**Test Mode Integration**

- **FR-031**: A `/test-mode rsvp set-status` command MUST be available to league managers
  when test mode is active. It MUST accept the following parameters:
  - `driver_id` (mandatory) — the Discord user ID (snowflake) of the driver whose status
    is being set.
  - `status` (mandatory) — one of `accepted`, `tentative`, or `declined`.
  - `division` (mandatory) — the name of the division whose active RSVP round to update.
  The command MUST locate the `DriverRoundAttendance` row for the identified driver in the
  active (not yet started) round for that division and update the `rsvp_status` field
  accordingly, applying the same `accepted_at` management rules as a real button press
  (FR-022). The RSVP embed MUST be edited in-place to reflect the updated status. The
  command MUST be rejected with a clear ephemeral error if test mode is not active, if no
  active RSVP embed exists for the division, or if the driver has no `DriverRoundAttendance`
  row for the current round.

### Key Entities *(include if feature involves data)*

- **DriverRoundAttendance** (new): RSVP state per driver per round per division. Records
  RSVP status (NO_RSVP / ACCEPTED / TENTATIVE / DECLINED); acceptance timestamp (null
  until first accepted; reset on each re-accept); round team assignment set by distribution
  (null if not distributed or not a reserve); standby flag (false by default, set true when
  classified as standby after distribution); attended flag (null — populated in a future
  increment when round results are submitted).
- **Round** (existing): Carries start time, format, division, track reference, and round
  number within a season. Drives RSVP job scheduling.
- **SeasonAssignment / TeamSeat** (existing): Determines which drivers are full-time (non-
  Reserve seat) vs. reserve (Reserve team seat) in a division for the current season.
- **AttendanceDivisionConfig** (existing): Holds the RSVP channel ID and attendance channel
  ID per division per server.
- **AttendanceConfig** (existing): Server-level parameters including `rsvp_notice_days`,
  `rsvp_deadline_hours`, and `rsvp_last_notice_hours`.

## Assumptions

1. The Constructors' Championship standings used for distribution tie-breaking are derived
   from the most recent `TeamStandingsSnapshot` for the division. If no such snapshot
   exists (first round of the season), alphabetical team name order is the deterministic
   fallback.
2. Full-time drivers are all drivers assigned to a non-Reserve team seat in the division
   for the current season. Reserve drivers are those assigned to the Reserve team seat.
   Unassigned drivers do not appear in the RSVP roster.
3. The RSVP embed is a single persistent message that is edited in-place when statuses
   change; it is never deleted and reposted.
4. The bot persists the Discord message ID of each RSVP embed (associated with its round
   and division) so the message can be edited on button interactions and after distribution.
5. Reserve distribution assigns at most one reserve per absent full-time driver slot in a
   team. If a team has two absent full-timers, it may receive two reserves (if available).
6. If `rsvp_deadline_hours` is 0, the full-time driver locking, accepted-reserve locking,
   and distribution all occur at the scheduled round start time.
7. Round cancellation or amendment after the RSVP embed is posted invalidates all existing
   RSVP responses and their scheduled jobs; a fresh embed is posted for the rescheduled
   round if applicable.

## Out of Scope (This Increment)

- Attendance recording from submitted round results
- Attendance point distribution and pardon workflow
- Attendance sheet posting to the attendance channel
- Autoreserve and autosack sanction enforcement

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every RSVP embed is posted within a 1-minute window of its scheduled notice
  time across all configured divisions and active rounds.
- **SC-002**: Driver status changes (Accept / Tentative / Decline) are reflected in the
  embed within 3 seconds of a button press.
- **SC-003**: The reserve distribution algorithm produces a deterministic, reproducible
  result for any given input state — the same RSVP statuses, timestamps, and standings
  always yield the same assignment outcome.
- **SC-004**: The distribution announcement message is posted in the RSVP channel within
  30 seconds of the deadline for each affected division.
- **SC-005**: No silent failures — every skipped posting, rejected interaction, or
  distribution error produces either an ephemeral user-facing message or an audit log
  entry; no command or scheduled job is silently discarded.
- **SC-006**: Bot restart results in no missed RSVP notice, last-notice ping, or
  distribution jobs for any round whose trigger time has not yet passed.
- **SC-007**: The last-notice ping is delivered to exclusively the correct set of
  recipients — full-time drivers with NO_RSVP status only; no reserve driver and no
  driver who has already responded is ever included in a last-notice mention.
