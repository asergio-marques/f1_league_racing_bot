# Research: Season Archive & Driver Profile Identity

**Branch**: `024-season-archive` | **Date**: 2026-03-26

All items below were resolved by inspecting the existing codebase. No external research was required; all decisions are grounded in established project patterns.

---

## R-001: Season Completion — Archive vs Delete

**Question**: How should season completion retain data rather than delete it?

**Finding**: `execute_season_end` in `src/services/season_end_service.py` currently:
1. Posts a completion message to the log channel.
2. Calls `reset_server_data` (which hard-deletes all season rows, divisions, rounds, sessions, results, forecast messages).
3. Calls `increment_previous_season_number` to advance the display counter.

**Decision**: Replace step 2 with a status transition (`UPDATE seasons SET status = 'COMPLETED' WHERE id = ?`). Remove the `reset_server_data` call entirely from the season-end path. Persist `DriverHistoryEntry` records for all season assignments before transitioning status. Remove step 3 (counter increment) — season number is now derived from archive count at setup time, not maintained via a mutable counter.

**Rationale**: Setting status = COMPLETED is the minimal, reversible change. Existing cascade-delete FK constraints on `seasons(id)` must NOT be relied upon to fire; data must be preserved. The `DriverHistoryEntry` write must happen before the status flip so that history is recorded even if a subsequent step fails.

**Alternatives considered**:
- Soft-delete flag on `seasons`: Rejected — the existing `status` field already models lifecycle state; a separate soft-delete column is redundant.
- Keep deletion and copy to an archive table: Rejected — introduces a parallel schema that diverges over time. The `COMPLETED` status approach is self-consistent with the existing state machine.

---

## R-002: Season Setup Gating Logic

**Question**: What is the current gating logic and what must it become?

**Finding**: `season_cog.py::season_setup` calls `has_existing_season(server_id)` which returns `True` if **any** season row exists (any status). This permanently blocks second seasons once archival is in place.

`season_service.py` already has `has_active_or_completed_season` but that is not the guard needed here.

**Decision**: Add `has_active_or_setup_season(server_id) -> bool` to `SeasonService`:
```sql
SELECT 1 FROM seasons
WHERE server_id = ? AND status IN ('ACTIVE', 'SETUP')
LIMIT 1
```
Replace the `has_existing_season` call in `season_setup` with `has_active_or_setup_season`. The error message must be updated to name the actual blocking condition (an ACTIVE or SETUP season is running).

**Rationale**: Only ACTIVE or SETUP seasons are a blocking condition. A server full of COMPLETED seasons implies all prior work is complete and a new season may begin.

**Alternatives considered**:
- Reuse `has_existing_season` with an added status filter in the cog: Rejected — the filter belongs in the service layer to be testable and reusable.
- Add a separate `can_start_new_season` method that encodes both checks: Considered but rejected as over-engineering for a single boolean predicate.

---

## R-003: Season Number Derivation

**Question**: How should the new season number be computed?

**Finding**: `save_pending_snapshot` in `season_service.py` currently reads `server_configs.previous_season_number + 1` for the first snapshot of a new season and preserves the existing number on re-snapshot. After migration, `previous_season_number` will no longer be incremented on season end.

**Decision**: Replace the `previous_season_number` read with:
```sql
SELECT COUNT(id) FROM seasons
WHERE server_id = ? AND status = 'COMPLETED'
```
and set `season_number = count + 1`.

This query is run **once**, at the time of the first snapshot (when `existing_season_id == 0`). Subsequent re-snapshots (amend-and-re-save) preserve the already-computed `season_number` exactly as before.

**Rationale**: The archive count is the only self-consistent source of truth. A derived value cannot drift out of sync with actual history. The legacy counter remains in the schema (no migration removes it) to avoid breaking servers that have not yet completed their first season under the new code, but it is never written to again after this feature lands.

**Alternatives considered**:
- MAX(season_number) + 1: Rejected — fragile if a row with a higher number was ever manually inserted or if a SETUP season was created and discarded, leaving a gap.
- Keep the counter and increment on archival: Rejected — the spec explicitly supersedes the counter; keeping it would create two sources of truth.

---

## R-004: game_edition Schema Placement

**Question**: Where does `game_edition` live in the schema?

**Finding**: The `seasons` table (migration 001) currently has: `id`, `server_id`, `start_date`, `status`, `season_number` (added in migration 008). No edition field exists.

**Decision**: Add `game_edition INTEGER NOT NULL DEFAULT 0` via migration 020. The DEFAULT 0 satisfies SQLite's ALTER TABLE requirement for existing rows. Code must treat 0 as "unset/legacy" — but the command will reject it at validation before a row with edition=0 is ever written for new seasons.

**Rationale**: Minimal schema delta; existing rows default to 0 (legacy, pre-edition data). A game_edition of 25 (for F1 25) is stored as the integer `25`; no enumeration table is needed.

**Alternatives considered**:
- Store as TEXT: Rejected — the spec specifies a positive integer; TEXT leaves validation to the application layer without schema support.
- Separate `season_configs` table: Rejected — over-engineered for a single scalar field that belongs on the season record.

---

## R-005: Driver Profile ID Migration in Results and Standings Tables

**Question**: How should `driver_session_results.driver_user_id` and `driver_standings_snapshots.driver_user_id` be migrated to reference `driver_profile_id`?

**Finding**:
- Both columns are `INTEGER NOT NULL` and currently store the Discord user ID (integer form of a `str` Discord snowflake).
- `driver_profiles` has an integer PK `id` which is the stable internal identifier.
- The existing code in `results_cog.py` and `penalty_wizard.py` resolves drivers from Discord mentions and uses raw `int(discord_user_id)` when writing to these tables.
- The `results_formatter.py` uses `driver_user_id` to render Discord mentions (`<@{row.driver_user_id}>`).

**Decision**: Add nullable FK columns `driver_profile_id INTEGER REFERENCES driver_profiles(id)` to both tables in migration 020. Backfill via a join:
```sql
UPDATE driver_session_results
SET driver_profile_id = (
    SELECT dp.id FROM driver_profiles dp
    WHERE CAST(dp.discord_user_id AS INTEGER) = driver_session_results.driver_user_id
      AND dp.server_id = (
          SELECT s.server_id FROM sessions se
          JOIN rounds r ON r.id = se.round_id
          JOIN divisions d ON d.id = r.division_id
          JOIN seasons s ON s.id = d.season_id
          WHERE se.id = (
              SELECT sr.id FROM session_results sr WHERE sr.id = driver_session_results.session_result_id
          )
      )
    LIMIT 1
)
WHERE driver_profile_id IS NULL;
```
(Apply an equivalent UPDATE for `driver_standings_snapshots`.)

Going forward, the submission path resolves Discord mention → `driver_profile_id` before writing. The `driver_user_id` column is kept as a cached Discord-ID for display purposes; `results_formatter.py` continues to use it for mention rendering. The internal identity for all data operations (lookups, joins, re-submissions) switches to `driver_profile_id`.

**Rationale**: Adding a new nullable column avoids recreating tables (which would be disruptive in SQLite and risk data loss). The backfill is a best-effort migration — rows with no matching profile (e.g., Discord ID no longer associated with any profile) remain with `driver_profile_id = NULL` and are treated as legacy data. All *new* result writes will have `driver_profile_id` populated.

**Alternatives considered**:
- Rename `driver_user_id` to `driver_profile_id` (full table recreation): Rejected — high risk, longer migration, and formatter still needs the Discord snowflake for mention rendering, so the column serves a display purpose distinct from the identity key.
- Single-step replace with no backfill: Rejected — existing rows would have NULL profile IDs permanently, making historical data non-queryable by internal ID.

---

## R-006: Immutability Enforcement for Completed Seasons

**Question**: Where must the COMPLETED-season mutation guard be applied?

**Finding**: The following command paths can mutate season-related data:
- `amendment_cog.py` / `amendment_service.py`: `/round cancel`, `/round postpone`, `/round amend`
- `season_cog.py`: `/season cancel`, `/division cancel`, `/round cancel`, `/round delete`, `/round add` (setup-only paths already guard with `status == SETUP`, but cancel paths operate on ACTIVE seasons and need a COMPLETED guard too)
- `results_cog.py` / `results_service.py`: `/results submit`, `/results amend`
- `penalty_wizard.py`: penalty application writes to `driver_session_results`
- `driver_cog.py`: `/driver assign`, `/driver unassign`, `/driver sack` (these target a season's assignment table)

**Decision**: Add an `assert_season_mutable(season: Season)` helper (raises `SeasonImmutableError`) in `season_service.py`. Call it at the start of every mutation that touches a season record or its related data. For commands that load the season inline (e.g. by round → division → season chain), add the check after the season is resolved.

**Rationale**: A single shared helper ensures consistent error messaging and is easily testable in isolation. No command-layer duplication of the guard logic.

**Alternatives considered**:
- Guard only at the DB layer (trigger): Rejected — SQLite triggers are not used in this project; application-layer rejection is consistent with existing validation patterns.
- Separate `ImmutableSeasonError` exception class: Accepted — distinct from generic `ValueError` for clarity in error handling and test assertions.

---

## R-007: DriverHistoryEntry at Season Completion

**Question**: When exactly are `DriverHistoryEntry` records created?

**Finding**: `DriverHistoryEntry` records already exist in the schema (migration 008, `driver_history_entries` table) and model (`src/models/driver_profile.py`). They are written today… actually, looking at the code they are currently written at the `execute_season_end` call. With the new archive model, season data is not deleted so the history entry is still useful as a denormalised summary for cross-season stats.

**Decision**: Write `DriverHistoryEntry` records for all ASSIGNED drivers in the season immediately before marking the season COMPLETED. Use the final standings snapshot (most recent `driver_standings_snapshots` per driver per division) to fill in `final_position` and `final_points`. The season's `season_number` and division name/tier are sourced from the existing rows.

**Rationale**: History entries give feature 026+ (season statistics) a fast denormalised read path without joining all the way through rounds and results tables. Even if the raw data is preserved, the summary row is valuable.

---

## R-008: Simultaneous /season setup Race Condition

**Question**: How is the concurrent-admin race condition handled?

**Finding**: The bot is a single asyncio process. Discord interactions are processed sequentially in the event loop. Python's GIL ensures no true parallel DB writes from two interaction handlers at the same time in a single process instance.

**Decision**: No additional locking mechanism required. The existing pattern of checking DB state at the start of every interaction and returning early on conflict is sufficient. Document this assumption in the code comment.

**Rationale**: The race condition is only a concern in a horizontally-scaled multi-process deployment. This bot is documented as a single-process service (see Technical Context). The in-memory `_pending` dict in `season_cog.py` already provides a second guard layer.

---

## R-009: Legacy previous_season_number Handling for Existing Servers

**Question**: Do existing servers need a counter migration?

**Finding**: `server_configs.previous_season_number` is currently the counter that increments on every `execute_season_end`. After this feature, season number is derived from `COUNT(COMPLETED seasons)`. For a server that has run N seasons before this feature lands, the COMPLETED seasons would already be in the DB with `status = 'COMPLETED'` — but wait: *before this feature, `execute_season_end` deleted all season rows*. So existing servers with data will have 0 COMPLETED seasons in the DB, and the counter value in `previous_season_number` reflects actual completed seasons.

**Decision**: No retroactive data migration is needed. For servers where all prior seasons were deleted (the old behaviour), `COUNT(COMPLETED) = 0` naturally, and the next season will be Season 1. This is acceptable per the spec Assumptions section: "existing servers do not need a retroactive correction so long as the migration sets the archived season count correctly." The counter is left in place but will not be written to after this feature.

**Rationale**: There is no historical data to correct because prior `execute_season_end` deleted it. The counter resets naturally to 0 → Season 1 for all existing servers.
