# Quickstart: Season Archive & Driver Profile Identity

**Branch**: `024-season-archive` | **Date**: 2026-03-26

This guide covers how to implement each story in order and verify the feature works end-to-end.

---

## Prerequisites

- Python 3.13.2, `pip install -r requirements.txt`
- Existing test database from `src/db/migrations/` (applied on bot startup)
- Run tests from `src/`: `cd src && pytest ../tests/`

---

## Implementation Order

Follow this order strictly — each step depends on the previous one.

### Step 1 — Migration 020 (Schema foundation)

Create `src/db/migrations/020_season_archive.sql`:

1. Add `game_edition INTEGER NOT NULL DEFAULT 0` to `seasons`
2. Add `driver_profile_id INTEGER REFERENCES driver_profiles(id)` to `driver_session_results`
3. Add `driver_profile_id INTEGER REFERENCES driver_profiles(id)` to `driver_standings_snapshots`
4. Add backfill UPDATE statements for both new columns
5. Add indexes on new FK columns

**Verify**: `sqlite3 /tmp/testbot.db ".schema seasons"` — `game_edition` column present.

---

### Step 2 — Model updates

Update three model files:

- `src/models/season.py`: add `game_edition: int = 0` to `Season` dataclass; update `_row_to_season` to read index 5.
- `src/models/session_result.py`: add `driver_profile_id: int | None = None` to `DriverSessionResult`.
- `src/models/standings_snapshot.py`: add `driver_profile_id: int | None = None` to `DriverStandingsSnapshot`.

**Verify**: `python -c "from models.season import Season; s = Season(1,1,'2026-01-01','SETUP', 1, 25); print(s.game_edition)"` → `25`

---

### Step 3 — SeasonService additions

In `src/services/season_service.py`:

1. Add `async def has_active_or_setup_season(server_id: int) -> bool` — queries `status IN ('ACTIVE', 'SETUP')`.
2. Add `async def count_completed_seasons(server_id: int) -> int` — queries `COUNT(id) WHERE status = 'COMPLETED'`.
3. Add `async def complete_season(season_id: int) -> None` — `UPDATE seasons SET status = 'COMPLETED' WHERE id = ?`.
4. Modify `save_pending_snapshot`: when `existing_season_id == 0`, replace `previous_season_number + 1` with `count_completed_seasons(server_id) + 1`.
5. Add `async def assert_season_mutable(season: Season) -> None` — raises `SeasonImmutableError` if `season.status == SeasonStatus.COMPLETED`.
6. Add `class SeasonImmutableError(Exception): pass` (single line, no docstring needed).

**Verify**: Unit test `test_season_service.py` — mock DB, assert `has_active_or_setup_season` returns True for ACTIVE and SETUP, False for COMPLETED-only.

---

### Step 4 — season_end_service rewrite

In `src/services/season_end_service.py`:

**Replace `execute_season_end` body**:

1. Load active season (`get_active_season`). Return early if None (idempotency guard).
2. Cancel any pending scheduler job.
3. Load all ASSIGNED driver season assignments for the season.
4. For each assigned driver × division: read the most recent `driver_standings_snapshots` row for that driver; write a `DriverHistoryEntry` (season_number, division_name, division_tier, final_position, final_points, points_gap_to_winner).
5. Call `season_service.complete_season(season.id)` — sets `status = COMPLETED`.
6. Post completion message to the log channel:  
   `🏁 **Season {season.season_number} Complete!** All data has been preserved in the archive.`
7. **Do NOT call `reset_server_data`**.
8. **Do NOT call `increment_previous_season_number`**.

Remove any import of `reset_service` from this file.

**Verify**: Unit test with a seeded DB containing one active season, two assigned drivers with standings snapshots. Assert:
- `seasons` row still present, `status = 'COMPLETED'`
- `divisions`, `rounds`, `driver_season_assignments` still present
- `driver_history_entries` has two new rows
- Log channel received the completion message

---

### Step 5 — season_cog: game_edition + new gating

In `src/cogs/season_cog.py`:

1. Add `game_edition: app_commands.Range[int, 1, 9999]` parameter to `season_setup`.
2. Replace `has_existing_season` check with `has_active_or_setup_season`: separate ACTIVE and SETUP into two distinct error messages (see contracts doc).
3. Thread `game_edition` through `PendingConfig` (add `game_edition: int = 0` field) and into `save_pending_snapshot` (update the INSERT to include `game_edition`).
4. Update `/season review` summary to show `game_edition` (e.g. `Season #1 (F1 25)`).

**Verify**: Integration test — complete one season, then call `season_setup` with `game_edition=25`. Assert setup succeeds. Call again immediately → rejected (SETUP blocking).

---

### Step 6 — Immutability guard on mutation commands

Apply `assert_season_mutable` in each mutation service/cog path:

| File | Location | Guard call |
|------|----------|------------|
| `season_cog.py` | `/season cancel` | After loading season |
| `season_cog.py` | `/division cancel` | After resolving season from division |
| `season_cog.py` | `/round cancel`, `/round delete` | After resolving season from round |
| `amendment_service.py` | `cancel_round`, `postpone_round`, `amend_round` | After loading season |
| `results_cog.py` / `results_service.py` | `submit_results`, `amend_results` | After loading season |
| `penalty_wizard.py` | Before applying penalty | After loading session → round → season |
| `driver_cog.py` | `/driver assign`, `/driver unassign`, `/driver sack` | After resolving season for assignment |

All guards raise `SeasonImmutableError`; callers catch it and respond with the ephemeral error message defined in the contracts doc.

**Verify**: Unit test each guarded path with a mocked COMPLETED season — assert `SeasonImmutableError` raised.

---

### Step 7 — driver_profile_id resolution in results/standings write paths

In `src/services/results_service.py` (or wherever driver results are written):

1. Add helper `async def resolve_driver_profile_id(server_id: int, discord_user_id: int, db) -> int | None` — looks up `driver_profiles.id` by `(server_id, CAST(discord_user_id AS TEXT))`.
2. Call this helper before every INSERT into `driver_session_results` and `driver_standings_snapshots`. Pass the resolved `driver_profile_id` as a column value.
3. Update the `DriverSessionResult` and `DriverStandingsSnapshot` constructor calls to include `driver_profile_id`.

In `src/services/penalty_wizard.py`:
4. Same resolution step before writing penalty target rows.

**Verify**: Integration test — submit results for a driver → assert `driver_session_results.driver_profile_id` is non-NULL and equals the driver's `driver_profiles.id`.

---

### Step 8 — Unit and integration tests

**New test file**: `tests/integration/test_season_archive.py`  
Covers the full multi-season lifecycle:

```python
# Pseudo-scenario:
# 1. Setup season 1 (game_edition=24), approve, activate, submit results, complete
# 2. Assert season 1 rows still present with status=COMPLETED and game_edition=24
# 3. Setup season 2 (game_edition=25), assert season_number=2
# 4. Assert season 1 data untouched after season 2 setup
# 5. Attempt to mutate season 1 round → SeasonImmutableError
# 6. Reassign driver to new Discord ID → assert season 1 results still reference driver_profile_id
```

**Updated test files**:
- `tests/unit/test_season_end_service.py`: replace `assert deleted == 1` style assertions with `assert season.status == 'COMPLETED'` and `assert history entries created`.
- `tests/unit/test_season_service.py`: add tests for `has_active_or_setup_season`, `count_completed_seasons`, `complete_season`, `assert_season_mutable`.

---

## Quick Verification Checklist

After all steps are complete, run:

```bash
cd src && pytest ../tests/ -v
```

Manual smoke-test sequence (test server):

1. `/season setup game_edition:25` → succeeds, shows "Season #1 (F1 25)" in review
2. Approve and activate season → round phases complete → season completes automatically
3. Query DB: `SELECT status, game_edition FROM seasons WHERE server_id = <id>` → `COMPLETED | 25`
4. Query DB: `SELECT * FROM divisions WHERE season_id = <id>` → rows still present
5. `/season setup game_edition:26` → succeeds, shows "Season #2 (F1 26)"
6. `/season setup game_edition:27` → rejected (SETUP season already exists)
7. `/driver reassign` on a driver from Season 1 → their Season 1 results still appear correctly
