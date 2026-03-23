# Feature Specification: Results & Standings — Standings Design, Sync Command, and Sort-Key Correction

**Feature Branch**: `022-results-standings-verification`
**Created**: 2026-03-23
**Status**: Draft
**Input**: Formal standings design ratification; identification and correction of implementation conflicts with the provided standings specification; addition of the `/standings sync` command.

---

## Context & Scope

This specification ratifies the canonical standings design for the Results & Standings module and corrects two implementation gaps identified when comparing the existing codebase against the user-provided standings rules:

1. **Sort-key tiebreak defect** — the existing standings computation uses a per-entity `max_pos` to build finish-count vectors, which causes incorrect tiebreak ordering when two entities have achieved different sets of finishing positions.
2. **Missing `/standings sync` command** — no command exists to force a standings repost for a given division without requiring a new round submission.

The specification also formally ratifies two items already partially implemented: the `/results reserves toggle` command and the reserve-driver point-continuity rule when a driver moves from the Reserve team to a configurable team.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Standings correctly rank drivers and teams by the specified tiebreak hierarchy (Priority: P1)

A league admin expects that, when two drivers are tied on points and wins, the driver with more 2nd-place Feature Race finishes ranks above the other; if still tied, 3rd-place finishes are compared; and so on through every finishing position. The driver or team that first achieved the highest position for which a difference exists wins the final tiebreaker.

**Why this priority**: Incorrect standings ordering is a competitive integrity failure and the most critical correctness guarantee in the module. All downstream display and archival depends on this computation being exact.

**Independent Test**: Construct a division with two drivers equal on total points and wins but differing only at P3 Feature Race finishes. Confirm the driver with more P3 finishes ranks higher. Extend with three-way ties to verify every tiebreak level.

**Acceptance Scenarios**:

1. **Given** two drivers with equal total points and Feature Race win counts but Driver A has 2 P2 finishes and Driver B has 1 P2 finish, **When** standings are computed, **Then** Driver A ranks above Driver B.
2. **Given** two drivers equal on points, wins, and P2s, but Driver A has 1 P3 finish and Driver B has 0 P3 finishes, **When** standings are computed, **Then** Driver A ranks above Driver B.
3. **Given** two drivers equal on points, wins, P2s, and P3s (all finish counts identical), but Driver A first achieved P2 in Round 1 and Driver B first achieved P2 in Round 3, **When** standings are computed, **Then** Driver A ranks above Driver B.
4. **Given** two teams equal on total points, **When** standings are computed, **Then** the same tiebreak hierarchy (Feature Race finish counts, then first-achieved round) is applied.
5. **Given** a driver has P2 finishes but no P1, P3, or any other finish, and another driver has only P3 finishes, **When** standings are computed, **Then** the driver with P2 finishes ranks above the driver with only P3 finishes (higher finish count for P2 > 0 P2 finishes), regardless of the round-number vector lengths.

---

### User Story 2 — Reserve driver visibility toggle controls standings output (Priority: P1)

A trusted admin decides that reserve drivers should not appear in the public standings post for a division, but wants their points to continue accruing internally. They toggle the setting off, and the next standings post excludes reserve drivers. Toggling back on restores their public appearance.

**Why this priority**: This is an existing implemented command that requires formal specification. Correctness must be verified end-to-end.

**Independent Test**: Submit a round with a reserve driver who scores points. Toggle reserves visibility off. Confirm the standings post omits the reserve driver. Toggle on and repost. Confirm the reserve driver appears with correct accumulated points.

**Acceptance Scenarios**:

1. **Given** a division's reserves visibility toggle is on (default), **When** standings are posted for that division, **Then** reserve drivers appear in the standings with their correct accumulated points and position.
2. **Given** a trusted admin runs `/results reserves toggle <division>` and the current state is on, **When** the command executes, **Then** the toggle is set to off, the bot confirms ephemerally, and the next standings post for that division excludes reserve drivers.
3. **Given** the toggle is off, **When** a trusted admin runs `/results reserves toggle <division>` again, **Then** the toggle is restored to on, the bot confirms ephemerally, and the next standings post includes reserve drivers.
4. **Given** the toggle is off and a reserve driver scores points in a round, **When** standings are computed and posted, **Then** the reserve driver does not appear in the public post but their points are stored in the internal snapshot.
5. **Given** a trusted admin provides a division name that does not exist, **When** they run the reserves toggle command, **Then** the bot returns a clear error and no state is changed.

---

### User Story 3 — Trusted admin forces a standings repost via the sync command (Priority: P2)

A trusted admin wants to refresh the standings post for a division without waiting for a new round submission — for example, after toggling reserve visibility, or after a manual database correction. They run `/standings sync <division>` and the bot recomputes and reposts the latest standings for that division.

**Why this priority**: The sync command provides an administrative safety valve. Without it, a visibility toggle or corrective action has no immediate visible effect until the next round.

**Independent Test**: With an active season with at least one completed round, toggle reserves visibility off. Run `/standings sync`. Confirm the standings channel receives a new post reflecting the current toggle state. No round result is modified.

**Acceptance Scenarios**:

1. **Given** an active season with at least one completed round in a valid division, **When** a trusted admin runs `/standings sync <division>`, **Then** the bot recomputes standings from all completed rounds and reposts the driver and team standings to that division's standings channel.
2. **Given** reserves visibility is toggled off before a sync, **When** `/standings sync <division>` is run, **Then** the reposted standings exclude reserve drivers.
3. **Given** no completed rounds exist for the division, **When** `/standings sync <division>` is run, **Then** the bot responds with a clear message indicating no standings data is available; no post is made to the standings channel.
4. **Given** the division name provided does not exist, **When** the command runs, **Then** the bot returns a clear error message and takes no action.
5. **Given** the R&S module is disabled, **When** the command runs, **Then** the bot returns a clear module-disabled error.

---

### User Story 4 — Reserve driver points persist correctly when the driver moves to a configurable team (Priority: P2)

A driver participates in several rounds as a reserve, accruing points attributed to whichever configurable team's car they drove in each session. When they are then assigned to a configurable team as a regular seat, all previously accrued points remain on record. Their standing in the division does not change as a result of the team re-assignment itself.

**Why this priority**: This is a data integrity guarantee affecting competitive history. Its correctness is already asserted by the data model but requires formal verification against the specification.

**Independent Test**: Submit rounds with a reserve driver driving for Team A. Assign that driver to Team A as a regular seat. Run `/standings sync` and confirm the driver's points and position are identical before and after the assignment.

**Acceptance Scenarios**:

1. **Given** a driver is on the Reserve team and has scored points across multiple rounds, **When** their team assignment is changed to a configurable team, **Then** their `driver_session_results` records are not modified and their accumulated points remain the same.
2. **Given** a reserve driver's historical sessions each specify a `team_role_id` for the team they drove for, **When** standings are recomputed after a team re-assignment, **Then** those historical `team_role_id` values are unchanged; the team standings for those previous rounds are unaffected.
3. **Given** a driver previously on the Reserve team is now on a configurable team, **When** they submit results in a subsequent round, **Then** their new results are attributed to their current configurable team, and their old results remain attributed to the team they drove for in each prior session.

---

### Edge Cases

- Two entities fully tied on all finish counts and all first-achieved rounds: the ordering is stable but arbitrary (sort stability or insertion order). No race condition exists because snapshots are persisted atomically.
- A driver who has participated in rounds in the division but has accumulated 0 total points still appears in driver standings.
- A driver who participated in two different divisions: their standings in Division X are entirely independent of their standings in Division Y; no cross-contamination.
- A round in which all sessions were CANCELLED produces no result rows; driver and team standings snapshots from the previous round are carried forward as the current standings state.
- The sort-key vector must be padded to a consistent global length across all entities before comparison; using per-entity `max_pos` produces incorrect results when entities have different finite sets of achieved positions.

---

## Requirements *(mandatory)*

### Functional Requirements

**Standings Ranking**

- **FR-001**: The system MUST rank drivers and teams in the following order: (1) total accumulated Feature Race and race-session points, highest first; (2) count of Feature Race 1st-place finishes, most first; (3) count of Feature Race 2nd-place finishes, most first; continuing through every finishing position until a difference is found.
- **FR-002**: If two entities remain tied after all finish-count comparisons, the system MUST rank higher the entity that first achieved the finishing position at which the counts first diverged — i.e., the entity with the earlier round number for that position wins the tiebreaker.
- **FR-003**: For all countback tiebreakers (finish counts and first-achieved round), ONLY Feature Race sessions are authoritative. Sprint Race, qualifying sessions, and any other session types MUST NOT contribute to countback computation.
- **FR-004**: When building finish-count and first-achieved-round sort vectors, the system MUST use the global maximum finishing position achieved by any entity in that division up to the round being computed, not a per-entity maximum. Vectors shorter than this global length MUST be padded with 0 finish count and ∞ first-achieved round respectively.
- **FR-005**: Driver standings MUST include all drivers who have participated in the division in at least one non-cancelled session, regardless of whether they have accumulated any points.
- **FR-006**: Team standings MUST rank teams by the aggregate of all points and Feature Race finishes scored by all drivers driving under that team's banner in each individual session. A reserve driver's session points accrue to the team whose car they drove for in that specific session.
- **FR-007**: Driver and team standings MUST be recomputed and persisted as a snapshot after every round whose results are fully submitted (all sessions submitted or explicitly cancelled).
- **FR-008**: When a result amendment or penalty is applied to a session in a round, the system MUST recompute standings for that round and all subsequent rounds in the division, overwriting the affected snapshots atomically.
- **FR-009**: A driver's results and standings in one division MUST NOT be affected by their participation in any other division.

**Reserve Driver Visibility**

- **FR-010**: The system MUST persist a per-division reserves-visibility flag, defaulting to on (visible).
- **FR-011**: When the reserves-visibility flag is off, reserve drivers MUST be excluded from the publicly posted standings output but MUST still be included in internal snapshot computations and storage.
- **FR-012**: The `/results reserves toggle <division>` command MUST flip the current flag state for the specified division, confirm the new state ephemerally, and require the R&S module to be enabled.

**Reserve Driver Point Continuity**

- **FR-013**: When a driver's team assignment is changed from the Reserve team to a configurable team, all existing `driver_session_results` records for that driver MUST remain unchanged. Their previously accumulated points and finish counts MUST remain attributed to the teams they drove for in each individual session.
- **FR-014**: A driver's standing position in driver standings MUST NOT change as a direct result of their team re-assignment; only future round results will alter their standing.

**Standings Sync Command**

- **FR-015**: A `/standings sync <division>` command MUST be available to trusted admins.
- **FR-016**: The sync command MUST recompute driver and team standings from all completed rounds in the specified division up to and including the most recent completed round, then repost both standings to the division's configured standings channel.
- **FR-017**: If no completed rounds exist for the division, the sync command MUST respond with a clear informational message and MUST NOT post to the standings channel.
- **FR-018**: The sync command MUST require the R&S module to be enabled and MUST enforce tier-2 admin access.

### Key Entities

- **DriverStandingsSnapshot**: Per-driver, per-round standings state. Key attributes: `round_id`, `division_id`, `driver_user_id`, `standing_position`, `total_points`, `finish_counts` (JSON map of position → count, Feature Race only), `first_finish_rounds` (JSON map of position → earliest round number, Feature Race only).
- **TeamStandingsSnapshot**: Equivalent to DriverStandingsSnapshot but keyed on `team_role_id` instead of `driver_user_id`.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Given any set of driver results where tiebreaks must be resolved at positions P2 through Pn, the standings output matches the ordering produced by applying the tiebreak rules manually — verified by automated unit tests covering at least 5 distinct tiebreak scenarios.
- **SC-002**: The sort-key defect (per-entity `max_pos`) is eliminated; the corrected implementation passes all existing standings tests and the new tiebreak-correctness tests without modification to the test inputs.
- **SC-003**: The `/standings sync` command successfully triggers a standings repost in 100% of cases where at least one completed round exists, as verified by integration tests.
- **SC-004**: After toggling reserves visibility off and running `/standings sync`, reserve drivers are absent from the standings post and their absence is confirmed by automated assertion.
- **SC-005**: A driver's accumulated points and standing are identical before and after a team re-assignment, as confirmed by a deterministic integration test.

---

## Conflicts with Prior Implementation

The following implementation issues were identified by comparing the existing codebase against this specification. These MUST be resolved in this feature branch:

### C1 — Sort-key vector length inconsistency (standings_service.py)

**Location**: `src/services/standings_service.py` — `compute_driver_standings` and `compute_team_standings`, `_sort_key` inner function.

**Issue**: `max_pos = max(fc.keys(), default=0)` is computed per-entity. When comparing two entities in the sort, their count vectors have different lengths. Python's tuple comparison treats a shorter tuple as "less than" a longer one when all shared elements are equal — this causes an entity with no P3 finish (vector length 2) to incorrectly outrank an entity with 1 P3 finish (vector length 3).

**Required fix**: Compute a single `global_max_pos = max((max(fc.keys(), default=0) for fc in all_finish_counts.values()), default=0)` before building sort keys, then use `range(1, global_max_pos + 1)` uniformly for all entities' count and first-round vectors.

### C2 — Missing `/standings sync` command

**Location**: `src/cogs/results_cog.py` — no `standings` command group or sync subcommand exists.

**Required addition**: A `standings` app_commands group under the top-level `results_group` (or as a standalone top-level group `/standings sync <division>`), implementing FR-015 through FR-018. The implementation must call an existing or new `repost_standings` service path that respects the reserves-visibility flag.
