# Research: Results & Standings — Standings Design, Sync Command, and Sort-Key Correction

**Feature branch**: `022-results-standings-verification`
**Date**: 2026-03-23

---

## R1 — Python tuple comparison behaviour for vectors of unequal length

**Decision**: Use a globally uniform vector length (padded with sentinel values) for all sort-key tuples.

**Rationale**: Python compares tuples element-by-element. When one tuple is exhausted before the other, the shorter tuple compares as *less than* the longer one if all shared elements are equal. This means `(0, -1)` < `(0, -1, -1)` — a driver with 0 P1s and 1 P2 would incorrectly rank *below* a driver with 0 P1s, 1 P2, and 1 P3 when using descending finish counts (negated). Padding all vectors to the same global maximum position avoids this: both become e.g. `(0, -1, 0)` vs `(0, -1, -1)`, which resolves correctly in favour of the driver with a P3.

**Alternatives considered**:

- *Custom `__lt__` comparator class*: possible but verbose; sorted() with a key function is idiomatic Python and avoids per-comparison object overhead.
- *itertools.zip_longest with fill*: equivalent to padding; key-function approach is simpler and more readable.
- *heapq.nsmallest*: no advantage; same tuple comparison semantics.

---

## R2 — Global max position computation strategy

**Decision**: Compute `global_max_pos` once per standings call by iterating over all entities' `finish_counts` dicts before building any sort key.

**Rationale**: The maximum finishing position is bounded by the number of entrants per session (typically ≤ 20). A single O(n) pass over all entities' dicts is negligible. Computing it inside the `_sort_key` closure would re-evaluate n times and still work, but the pre-computed approach is cleaner and avoids closure over a mutable dict.

**Alternatives considered**:

- *Database-level `MAX(finishing_position)`*: would require an extra query; more complexity for no benefit at this scale.
- *Hard-code 20*: fragile and wrong if a division has more or fewer entrants.

---

## R3 — Tiebreaker scope: Feature Race only for countback

**Decision**: Feature Race sessions are the *only* session type that feeds finish-count and first-achieved-round tiebreakers. Sprint Race points contribute to total, but Sprint Race finishes do NOT feed countback.

**Rationale**: Confirmed by user specification ("For countback tiebreakers, only Feature Race sessions are relevant") and by the existing constitution (Principle XII: "Only Feature Race sessions are authoritative for countback tiebreaking"). No change to existing code logic required for this aspect — the filter is already present in `standings_service.py`.

**Alternatives considered**: None; this is a hard specification requirement.

---

## R4 — `/standings sync` command placement and service path

**Decision**: Add `/standings sync` as a subcommand of a new `standings_group` under the existing `results_group` in `ResultsCog` (i.e., `/results standings sync <division>`). The command calls a new `repost_standings_for_division()` helper in `results_post_service.py`.

**Rationale**: Placing it under `/results standings sync` keeps all standing-related commands under the `/results` umbrella consistent with the existing command grouping (constitution Bot Behavior Standards: same operational domain → same command group). A standalone `/standings` top-level group would violate the grouping rule.

The new `repost_standings_for_division()` helper:
1. Finds the most recent completed `round_id` for the division.
2. Calls `compute_driver_standings` and `compute_team_standings` from `standings_service`.
3. Fetches the division's configured standings channel.
4. Calls `post_standings` (already exists) with `show_reserves` from `_get_show_reserves`.

This reuses maximum existing service infrastructure and adds no duplicate logic.

**Alternatives considered**:

- *`/standings sync` as a top-level group*: violates Bot Behavior Standards command grouping rule (Principle constitition: commands sharing operational domain must share a group).
- *Inline all logic in the cog handler*: defeats service separation; hard to test.

---

## R5 — Reserve driver identification for standings filtering

**Decision**: Retain current approach: reserve user IDs are determined at posting time by querying `team_seats` → `team_instances` for `is_reserve = 1` in the division. This is a live query, not a snapshot field.

**Rationale**: A driver's reserve status can change between rounds (they may be promoted to a configurable seat). The filter correctly reflects their *current* assignment when the standings are posted. Points already accrued remain attributed to the session-level `team_role_id` at submission time and are unaffected.

**Alternatives considered**:

- *Snapshot reserve flag per standings row*: denormalises data that's already stored in `team_seats`; adds complexity with no benefit.

---

## R6 — Reserve driver point continuity across team re-assignment

**Decision**: No code change required. The `driver_session_results` table stores `team_role_id` at submission time; it is never modified by a team re-assignment. `compute_driver_standings` aggregates `driver_user_id`-scoped rows regardless of current team. The team re-assignment only affects future sessions.

**Rationale**: The data model already enforces this guarantee by design. Verification is a read-only audit of the mutation path (`driver_service.py`, `team_service.py`) to confirm no code path modifies historic `driver_session_results.team_role_id`.

**Alternatives considered**: None; this is a data model property, not a design choice.

---

## R7 — Unit test coverage plan for sort-key fix

**Decision**: Add five focused unit tests to `tests/unit/test_standings_service.py` covering:

1. **Two-driver P2 tiebreak**: A has 2 P2s, B has 1 P2 → A ranks higher.
2. **P3 tiebreak with absent P3 in one driver**: A has 0 P3s (no P3 key in dict), B has 1 P3 → B ranks higher than A in P3.
3. **First-achieved-round tiebreak**: A and B have identical finish counts; A achieved P2 first (earlier round) → A ranks higher.
4. **Three-way tie**: Tests that the sort correctly differentiates all three entities.
5. **Cross-position-set correctness**: A has achieved P2 only (max_pos=2), B has achieved P3 only (max_pos=3) — verifies the pre-fix defect does not regress.

All tests use the existing in-memory SQLite fixture pattern (`@pytest.fixture async def db_path`).

**Alternatives considered**: Integration-level test only — rejected because integration tests are slower and harder to target at a specific sort scenario.
