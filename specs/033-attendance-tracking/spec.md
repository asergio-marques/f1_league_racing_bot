# Feature Specification: Attendance Tracking

**Feature Branch**: `033-attendance-tracking`  
**Created**: 2026-04-03  
**Status**: Draft  
**Constitution**: v2.10.1 (Principle XIII — Attendance & Check-in Integrity)

## Overview

This increment completes the Attendance module by implementing the core post-round tracking
pipeline. The RSVP embed, button interactions, and reserve distribution at the deadline are
covered by `032-attendance-rsvp-checkin`. This feature picks up once round results begin
to arrive and covers: automatic attendance recording from submitted results, point
distribution after penalty finalization, the attendance pardon workflow inside the penalty
wizard, attendance sheet posting, and automatic sanction enforcement (autoreserve and
autosack). Recalculation of all of the above on round amendment is also in scope.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Automatic Round Attendance Recording (Priority: P1)

A league admin submits the first session results for a round. The bot automatically marks
which full-time drivers attended and which did not, based solely on whether they appear in
any submitted session for that round. Reserve drivers for that division are excluded from
this process entirely.

**Why this priority**: This is the foundational step for all downstream processing —
pardon validation, point distribution, the attendance sheet, and sanctions all depend on
the `attended` flag being accurate before they execute. Nothing else in this increment
works without it.

**Independent Test**: Can be fully tested by submitting a single session result for a round
with a known roster. Verify that `DriverRoundAttendance.attended` is set for all full-time
drivers, that reserve-team drivers have no attendance row updated, and that results from
later sessions in the same round do not overwrite the initial flag.

**Acceptance Scenarios**:

1. **Given** the Attendance module is enabled and a round exists with full-time and reserve
   drivers, **When** the first session result for that round is accepted, **Then** the bot
   populates `attended = true` for every full-time driver appearing in any `DriverSessionResult`
   for that round and `attended = false` for every full-time driver absent from all sessions.
2. **Given** a second session result is submitted for the same round, **When** it is accepted,
   **Then** a full-time driver who was absent in session 1 but present in session 2 has their
   `attended` flag updated to `true`; a driver already marked `attended = true` remains `true`.
3. **Given** a driver is seated in the Reserve team of the division for this round,
   **When** any session result is accepted, **Then** that driver's `DriverRoundAttendance` row
   is not created or updated by the attendance recording step.
4. **Given** the Attendance module is disabled, **When** session results are accepted,
   **Then** no `DriverRoundAttendance` rows are written and no attendance recording occurs.

---

### User Story 2 — Attendance Pardon in Penalty Wizard (Priority: P2)

During the penalty review stage of the penalty wizard, a tier-2 admin needs to excuse a
driver's attendance infraction (they did not RSVP but had a valid reason, or they RSVP'd
and then could not attend). The admin presses an "Attendance Pardon" button, fills in a
short form, and the pardon is staged alongside any other penalties for that round.

**Why this priority**: Pardons must be staged and validated before attendance points are
distributed. They cannot be applied retroactively after finalization (FR-010), so they must
be available at exactly the right stage of the workflow. Without this story, attendance
points cannot be waived and all automated sanctions would be unappealable via this path.

**Independent Test**: Can be fully tested by opening a penalty wizard for a round that has
at least one full-time driver with `NO_RSVP` status. Press the pardon button, submit a valid
pardon, verify it appears in the wizard summary, and confirm it is applied correctly when
penalties are finalized. A driver with a valid pardon must not receive attendance points for
the waived event type.

**Acceptance Scenarios**:

1. **Given** a penalty wizard is in the penalty review stage and at least one full-time
   driver has a qualifying attendance event, **When** the tier-2 admin presses the
   "Attendance Pardon" button, **Then** a modal form opens requesting: Discord User ID,
   pardon type (NO_RSVP / NO_ATTEND / NO_SHOW), and a free-text justification.
2. **Given** the admin submits a NO_RSVP pardon for driver A whose `rsvp_status` is
   `ACCEPTED`, **When** the form is submitted, **Then** the bot rejects the pardon with a
   clear error ("driver RSVP'd; NO_RSVP pardon is not applicable").
3. **Given** the admin submits a NO_SHOW pardon for driver B whose `rsvp_status` is `NO_RSVP`,
   **When** the form is submitted, **Then** the bot rejects the pardon ("driver did not
   RSVP; NO_SHOW pardon requires ACCEPTED status").
4. **Given** the admin submits a valid NO_RSVP pardon for driver C (`rsvp_status = NO_RSVP`),
   **When** the form is submitted, **Then** the pardon is staged; the penalty wizard summary
   is updated to show the staged pardon alongside any staged penalties; and the justification
   is logged to the calculation log channel and not displayed elsewhere.
5. **Given** a staged NO_RSVP pardon and a staged NO_ATTEND pardon for the same driver exist,
   **When** penalties are approved, **Then** that driver receives 0 attendance points for
   the round (both penalty events are fully waived).
6. **Given** the wizard is in the appeals review stage (not the penalty review stage),
   **When** the bot renders the wizard controls, **Then** the "Attendance Pardon" button
   is absent.
7. **Given** post-race penalties have already been approved for a round,
   **When** any attempt is made to apply an attendance pardon for that round,
   **Then** the bot rejects it with a clear error.

---

### User Story 3 — Attendance Point Distribution at Finalization (Priority: P2)

After the tier-2 admin approves post-race penalties for a round, the bot automatically
distributes attendance points to all full-time drivers based on their RSVP status,
actual attendance, and any pardons that were staged. Drivers who do not incur any
penalty (attended with any RSVP response) accumulate 0 points.

**Why this priority**: This is the core accountability mechanism of the Attendance module.
It directly feeds the attendance sheet and the autosanction checks. It must run after
penalties are finalized (not provisional), so that drivers are not incorrectly penalized
for submission errors corrected at the penalty stage.

**Independent Test**: Can be fully tested by finalizing penalties for a round containing
drivers in all four penalty-eligible scenarios (no-rsvp + attended; no-rsvp + absent;
accepted + absent; any-rsvp + attended) and verifying that each driver's
`DriverRoundAttendance.points_awarded` and `total_points_after` match the expected values
derived from the configured penalty multipliers and any staged pardons.

**Attendance Point Rules**:

| RSVP Status | Attended | Points Awarded |
|---|---|---|
| NO_RSVP | Yes | `no_rsvp_penalty` |
| NO_RSVP | No | `no_rsvp_penalty` + `no_attend_penalty` |
| ACCEPTED / TENTATIVE / DECLINED | Yes | 0 |
| ACCEPTED / TENTATIVE / DECLINED | No | `no_show_penalty` |

A pardon of type NO_RSVP waives the `no_rsvp_penalty` component. A pardon of type
NO_ATTEND waives the `no_attend_penalty` component. A pardon of type NO_SHOW waives the
`no_show_penalty` component. Multiple pardons for the same driver are additive.

**Acceptance Scenarios**:

1. **Given** driver A has `rsvp_status = NO_RSVP` and `attended = true`, and no pardons,
   **When** post-race penalties are approved, **Then** driver A accumulates `no_rsvp_penalty`
   points.
2. **Given** driver B has `rsvp_status = NO_RSVP` and `attended = false`, and no pardons,
   **When** post-race penalties are approved, **Then** driver B accumulates
   `no_rsvp_penalty + no_attend_penalty` points.
3. **Given** driver C has `rsvp_status = ACCEPTED` and `attended = false`, and no pardons,
   **When** post-race penalties are approved, **Then** driver C accumulates `no_show_penalty`
   points.
4. **Given** driver D has `rsvp_status = TENTATIVE` and `attended = false`, and no pardons,
   **When** post-race penalties are approved, **Then** driver D accumulates `no_show_penalty`
   points.
4b. **Given** driver D2 has `rsvp_status = DECLINED` and `attended = false`, and no pardons,
   **When** post-race penalties are approved, **Then** driver D2 accumulates `no_show_penalty`
   points.
5. **Given** driver E has `rsvp_status = NO_RSVP`, `attended = false`, a staged NO_RSVP
   pardon and a staged NO_ATTEND pardon, **When** post-race penalties are approved,
   **Then** driver E accumulates 0 points (both penalties waived).
6. **Given** driver F has `rsvp_status = NO_RSVP`, `attended = false`, and only a staged
   NO_RSVP pardon, **When** post-race penalties are approved, **Then** driver F accumulates
   only `no_attend_penalty` points (no_rsvp component waived; no_attend component not waived).

---

### User Story 4 — Attendance Sheet Posting (Priority: P3)

After attendance points are distributed, the bot posts an updated attendance sheet to the
division's configured attendance channel. The sheet lists all full-time drivers in descending
order of total accumulated attendance points, each mentioned by Discord user, and ends with a
threshold footer showing the autoreserve and autosack limits. The previous sheet message for
that division is deleted before the new one is posted.

**Why this priority**: The sheet is the visible output of the attendance system — it gives
admins and drivers a clear picture of standing and upcoming sanctions. Automating its
posting removes manual upkeep. It is lower priority than recording and distribution
because the sheet is derived data; the underlying numbers must be correct before it can
be trusted.

**Independent Test**: Can be fully tested by finalizing penalties for a round and confirming
the sheet message appears in the attendance channel with the correct ordering, formatting,
mentions, and footer. A second round's finalization should delete the first message and post
an updated one. Verify old message is gone.

**Acceptance Scenarios**:

1. **Given** post-race penalties have been approved and points distributed, **When** the bot
   posts the attendance sheet, **Then** it posts to the division's configured attendance
   channel a message listing every full-time driver in descending order of total accumulated
   attendance points, formatted as `@mention — X attendance points` per line.
2. **Given** the sheet is posted and a prior sheet message exists in the attendance channel,
   **When** the new sheet is posted, **Then** the old message is deleted first.
3. **Given** the prior sheet message has been deleted manually before a new round finalizes,
   **When** the new sheet is posted, **Then** the deletion step is skipped silently and the
   new sheet is posted without error.
4. **Given** the `autoreserve_threshold` is configured, **When** the sheet is posted,
   **Then** the footer includes: "Drivers who reach X points will be moved to reserve."
5. **Given** the `autosack_threshold` is configured, **When** the sheet is posted,
   **Then** the footer includes: "Drivers who reach X points will be removed from all
   driving roles in all divisions."
6. **Given** `autoreserve_threshold` is disabled (null or 0), **When** the sheet is posted,
   **Then** the autoreserve sentence is omitted from the footer entirely.
7. **Given** `autosack_threshold` is disabled (null or 0), **When** the sheet is posted,
   **Then** the autosack sentence is omitted from the footer entirely.
8. **Given** two or more drivers have equal total attendance points, **When** the sheet is
   posted, **Then** tied drivers are listed in a consistent order (e.g., alphabetical by
   display name).

---

### User Story 5 — Automatic Sanction Enforcement (Priority: P3)

After the attendance sheet is posted, the bot evaluates every full-time driver's total
accumulated attendance points against the configured autoreserve and autosack thresholds.
Drivers who cross the autosack threshold lose all team seats across all divisions. Drivers
who cross only the autoreserve threshold are moved to the Reserve team of the division in
which the threshold was crossed. All sanction actions produce audit log entries.

**Why this priority**: Autosanctions are the most consequential action in the module. They
follow naturally from accurate point totals and a posted sheet, hence lower priority than
those foundational steps. Without the sheet (US4) existing first, sanctions would act on
data the admin has not yet reviewed.

**Independent Test**: Can be fully tested by configuring low threshold values, finalizing a
round where one driver crosses autoreserve and another crosses autosack, and verifying the
expected seat mutations, audit log entries, and absence of autoreserve for drivers already
in the Reserve team.

**Acceptance Scenarios**:

1. **Given** driver A's `total_points_after` meets or exceeds `autosack_threshold`,
   **When** sanction evaluation runs, **Then** driver A is unassigned from every team seat
   in every division, their driver role is revoked, and one audit log entry per affected
   division is produced.
2. **Given** driver B's `total_points_after` meets or exceeds `autoreserve_threshold` but
   is below `autosack_threshold`, **When** sanction evaluation runs, **Then** driver B is
   unassigned from their current team seat in the affected division and assigned to the
   Reserve team of that division; one audit log entry is produced.
3. **Given** driver C's `total_points_after` meets or exceeds both `autoreserve_threshold`
   and `autosack_threshold`, **When** sanction evaluation runs, **Then** autosack supersedes
   autoreserve — driver C is sacked from all divisions (per scenario 1) and the autoreserve
   action is not additionally applied.
4. **Given** driver D is already seated in the Reserve team of the affected division and their
   total meets or exceeds `autoreserve_threshold` (but not autosack), **When** sanction
   evaluation runs, **Then** no autoreserve action is taken for driver D.
5. **Given** `autoreserve_threshold` is disabled (null or 0), **When** sanction evaluation
   runs, **Then** no driver is autoreserved regardless of their points total.
6. **Given** `autosack_threshold` is disabled (null or 0), **When** sanction evaluation
   runs, **Then** no driver is autosacked regardless of their points total.

---

### User Story 6 — Attendance Recalculation on Round Amendment (Priority: P3)

When a tier-2 admin amends a round's results via the results amendment flow, the bot
recalculates attendance for that round from the updated result set. Previously staged pardons
are preserved and applied to the recalculated totals, a new attendance sheet is posted
(replacing the previous one), and sanctions are re-evaluated.

**Why this priority**: Without recalculation on amendment, the attendance record becomes
stale and inaccurate — a driver incorrectly omitted on first submission and added via
amendment would retain the penalty of a non-attendee indefinitely. This story ensures
data integrity over the full results lifecycle.

**Independent Test**: Can be fully tested by finalizing penalties (triggering initial
attendance recording and sheet posting), then amending results to add a previously absent
driver and confirming that driver's `attended` flag flips, their points are reduced, the
sheet is reposted, and autosanctions are re-evaluated against the corrected totals. Confirm
any pardon granted before the amendment still applies.

**Acceptance Scenarios**:

1. **Given** round results have been finalized and an attendance sheet posted, **When** a
   tier-2 admin amends the round results, **Then** all `DriverRoundAttendance.attended` flags
   for that round are recomputed from the updated `DriverSessionResult` rows.
2. **Given** driver A was originally absent (no result row) but is added via amendment,
   **When** recalculation runs, **Then** driver A's `attended` flag is set to `true`, their
   `points_awarded` is recalculated, and their running total is updated.
3. **Given** a NO_RSVP pardon was staged for driver B before the original finalization,
   **When** recalculation runs after amendment, **Then** the pardon remains in effect and
   is applied to the recalculated points.
4. **Given** recalculation completes, **When** the updated attendance sheet is posted,
   **Then** the previous sheet message in the attendance channel is deleted first.
5. **Given** recalculation completes and a driver previously below the autosack threshold
   now crosses it due to amended points, **When** sanctions are re-evaluated, **Then** that
   driver is autosacked and the autosack audit entry is produced.

---

### Edge Cases

- A full-time driver's only session result for a round carries an outcome modifier of `DSQ`
  or `DNS` — they still count as **attended** (any row in `DriverSessionResult` for the round
  is sufficient; outcome modifier is irrelevant to attendance).
- All drivers in a division are on the Reserve team — the bot finds no full-time drivers to
  record attendance for; no `DriverRoundAttendance` rows are written; the sheet posts an
  empty list (only the footer is shown if thresholds are configured).
- `no_rsvp_penalty`, `no_attend_penalty`, and `no_show_penalty` are all 0 — all drivers
  accumulate 0 points; the sheet posts but contains no meaningful ranking; no sanctions fire.
- The attendance channel has been deleted or the bot loses access to it — sheet posting fails;
  the bot logs the failure and does not block penalty finalization or sanction enforcement.
- A driver is autosacked for the second time (already sacked from all seats) — the bot
  finds no seats to remove and produces a no-op log entry rather than an error.
- Autosack threshold equals autoreserve threshold — autosack supersedes; no autoreserve
  action is taken.

## Requirements *(mandatory)*

### Functional Requirements

#### Attendance Recording

- **FR-001**: Upon the first `SessionResult` row being accepted for a round in a division,
  the bot MUST populate `DriverRoundAttendance.attended` for every full-time driver in that
  division: `true` if a `DriverSessionResult` row exists for that driver in any session of
  that round, `false` otherwise.
- **FR-002**: A driver seated in the Reserve team of the division for that round MUST be
  excluded from attendance recording; no `DriverRoundAttendance.attended` update is made
  for them.
- **FR-003**: Subsequent session result submissions for the same round MUST update the
  `attended` flag for any driver whose presence status changes (absent in earlier sessions
  but present in a later one); a driver already marked `attended = true` MUST NOT be
  reverted to `false` by a later session result.
- **FR-004**: When the Attendance module is disabled, FR-001 through FR-003 MUST NOT
  execute; no attendance rows are written.

#### Attendance Pardon Workflow

- **FR-005**: A tier-2 admin MUST be able to stage attendance pardons exclusively during
  the penalty review stage of the penalty wizard. The "Attendance Pardon" button MUST NOT
  appear during the appeals review stage.
- **FR-006**: Pressing the "Attendance Pardon" button MUST open a modal form requesting:
  (1) Discord User ID of the driver, (2) pardon type — one of NO_RSVP, NO_ATTEND, or
  NO_SHOW, (3) free-text justification.
- **FR-007**: The bot MUST validate each pardon submission against the current RSVP and
  attendance state of the named driver:
  - NO_RSVP pardon: driver's `rsvp_status` MUST be `NO_RSVP`; rejected otherwise.
  - NO_ATTEND pardon: driver's `attended` MUST be `false`; rejected otherwise.
  - NO_SHOW pardon: driver's `rsvp_status` MUST be `ACCEPTED` and `attended` MUST be
    `false`; rejected otherwise.
- **FR-008**: Multiple pardons of different types MAY be staged for the same driver in the
  same round; the limit is one pardon per event type per driver per round.
- **FR-009**: Staged attendance pardons MUST be displayed in the penalty wizard summary
  alongside staged penalties, clearly labeled as pardons.
- **FR-010**: The pardon justification MUST be logged to the calculation log channel only
  and MUST NOT appear in any other output visible to non-admin users.
- **FR-011**: After post-race penalties have been approved and finalized for a round,
  no further attendance pardons MAY be applied to that round. Any attempt MUST be rejected
  with a clear error.

#### Attendance Point Distribution

- **FR-012**: Attendance points MUST be distributed immediately after post-race penalties
  are finalized (approved), not at the time of result submission.
- **FR-013**: Points for each full-time driver in the division MUST be computed per the
  rules in US3 (Attendance Point Rules table), taking staged pardons into account.
- **FR-014**: Each driver's `DriverRoundAttendance.points_awarded` MUST be set to the net
  points after pardons are applied. `total_points_after` MUST be set to the driver's
  cumulative attendance points across all rounds in this division up to and including
  the current round.
- **FR-015**: Pardons waive the corresponding point component only; un-pardoned components
  of the same event are still applied (e.g., a NO_RSVP pardon does not waive the
  `no_attend_penalty` component for a driver who also did not attend).

#### Attendance Sheet Posting

- **FR-016**: Immediately after attendance points are distributed, the bot MUST post an
  attendance sheet to the division's configured attendance channel.
- **FR-017**: The sheet MUST list all full-time drivers in the division in descending order
  of `total_points_after` (most points first). Each entry MUST be formatted as:
  `@mention — X attendance points`.
- **FR-018**: If two or more drivers share the same `total_points_after`, their relative
  order MUST be consistent (alphabetical by display name as a tiebreaker).
- **FR-019**: The sheet MUST append a footer after the driver list. Each line in the footer
  is conditional:
  - If `autoreserve_threshold` is configured (non-null, non-zero): "Drivers who reach
    `<threshold>` points will be moved to reserve."
  - If `autosack_threshold` is configured (non-null, non-zero): "Drivers who reach
    `<threshold>` points will be removed from all driving roles in all divisions."
  - Lines whose threshold is disabled MUST be omitted entirely.
- **FR-020**: Before posting the new sheet, the bot MUST delete the previous attendance
  sheet message in that channel (identified by a stored message ID). If the message no
  longer exists (already deleted), the deletion step MUST be skipped silently.
- **FR-021**: The message ID of each newly posted sheet MUST be persisted so it can be
  deleted before the next sheet is posted (FR-020).

#### Automatic Sanction Enforcement

- **FR-022**: After the attendance sheet is posted, the bot MUST evaluate every full-time
  driver's `total_points_after` against the configured thresholds (in a single pass).
- **FR-023**: If a driver's `total_points_after` meets or exceeds `autosack_threshold` (and
  the threshold is enabled), the bot MUST unassign that driver from all team seats across all
  divisions and revoke their driver role. One audit log entry per affected division MUST be
  produced (Principle V).
- **FR-024**: If a driver's `total_points_after` meets or exceeds `autoreserve_threshold`
  (and the threshold is enabled), and the driver is NOT already seated in the Reserve team
  of the affected division, the bot MUST unassign them from their current team seat in that
  division and assign them to the Reserve team of that division. One audit log entry MUST be
  produced.
- **FR-025**: If a driver's total meets or exceeds both thresholds simultaneously, autosack
  supersedes autoreserve; the autoreserve action MUST NOT additionally be applied.
- **FR-026**: Autoreserve MUST NOT be applied to a driver already seated in the Reserve team
  of the division, even if their total meets the threshold.
- **FR-027**: If either threshold is disabled (null or 0), the corresponding sanction MUST
  NOT fire.

#### Amendment Recalculation

- **FR-028**: When round results are amended via the amendment flow, the bot MUST recompute
  `DriverRoundAttendance.attended` for all full-time drivers in the affected division using
  the updated `DriverSessionResult` rows, applying FR-001 and FR-002 rules; FR-003
  (upgrade-only) does NOT apply — amendment is a deliberate correction and may flip
  `attended` in either direction.
- **FR-029**: Previously staged `AttendancePardon` rows for the amended round MUST be
  preserved and applied to the recalculated `points_awarded`.
- **FR-030**: After recalculation, the bot MUST recompute `total_points_after` for all
  affected drivers (the delta from the amended round propagates forward through any
  subsequent rounds in the same division).
- **FR-031**: After recalculation, the bot MUST re-post the attendance sheet (applying
  FR-016 through FR-021) and re-evaluate sanctions (applying FR-022 through FR-027).

### Key Entities

These entities are fully defined in constitution v2.10.0 (Principle XIII, Data & State
Management). This feature creates rows in them; no schema additions are required except
the attendance sheet message ID tracking noted below.

- **DriverRoundAttendance**: One row per full-time driver per round per division.
  Tracks `rsvp_status` (populated by RSVP sub-increment), `attended` (populated here),
  `points_awarded` (populated at finalization), and `total_points_after` (running cumulative
  total, populated at finalization).
- **AttendancePardon**: One row per driver per round per pardon type. Staged during the
  penalty wizard and applied at finalization. Uniquely constrained on
  `(attendance_id, pardon_type)`.
- **AttendanceConfig**: Server-level configuration including `no_rsvp_penalty`,
  `no_attend_penalty`, `no_show_penalty`, `autoreserve_threshold`, and `autosack_threshold`.
  All read-only during this feature's execution path.
- **AttendanceDivisionConfig**: Per-division config holding `attendance_channel_id` (sheet
  destination) and `rsvp_channel_id` (owned by the RSVP sub-increment). Requires a new
  `attendance_message_id` field to satisfy FR-021.

**Schema amendment required**:

- **AttendanceDivisionConfig** — add `attendance_message_id` (TEXT, nullable): stores the
  Discord message ID of the most recently posted attendance sheet for this division. Written
  on each successful sheet post; used by FR-020 to delete the prior message.

## Assumptions

- The RSVP sub-increment (`032-attendance-rsvp-checkin`) has populated `rsvp_status` and
  `rsvp_timestamp` on all `DriverRoundAttendance` rows before the penalty wizard opens for
  a round; this feature reads those fields but does not write them.
- The penalty wizard's approval path (which triggers FR-012) is already implemented and
  exposes a post-approval hook that this feature attaches to.
- "Driver role" referenced in FR-023 (revoked on autosack) is the Discord role automatically
  granted when a signup is approved, as described in Principle VIII and Principle XI of the
  constitution.
- Multiple pardons submitted for the same driver+type in the same round are blocked at the
  data layer (unique constraint on `attendance_id, pardon_type`). The UI presents a clear
  error if the admin attempts a duplicate.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every finalized round (post-penalty approval), 100% of full-time drivers
  in the division have a `DriverRoundAttendance` row with a non-null `attended`, `points_awarded`,
  and `total_points_after` value; no Reserve-team drivers appear in that set.
- **SC-002**: An attendance pardon granted before finalization is always reflected in the
  driver's `points_awarded` — the penalized component for the waived event type is 0 regardless
  of the driver's RSVP and attendance state.
- **SC-003**: The attendance sheet posted to the channel correctly reflects the top-to-bottom
  ordering and exact point totals from the database for every driver in every test scenario,
  with no manual intervention needed after penalty finalization.
- **SC-004**: When `autoreserve_threshold` or `autosack_threshold` is crossed, the
  corresponding seat mutation and audit log entry are produced in the same processing pass as
  the sheet posting, within the same session interaction (no deferred or missed sanctions).
- **SC-005**: After a round amendment, the attendance sheet and all affected drivers'
  cumulative totals are updated to match the amended result set within the same processing pass
  as the amendment approval, with prior pardons preserved.
- **SC-006**: All sanction actions (autoreserve, autosack) produce audit log entries per
  Principle V; zero sanction actions are silent.
