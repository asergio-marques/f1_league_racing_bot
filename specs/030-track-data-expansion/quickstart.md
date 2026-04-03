# Developer Quickstart: Track Data Expansion (030)

**Branch**: `030-track-data-expansion`

---

## Prerequisites

- Python 3.13.2
- All dependencies installed: `pip install -r requirements.txt`
- Working Discord bot token in `.env`

---

## Running the Bot Locally

```bash
# From the repo root (Windows-friendly):
python -m src.bot
```

On first start with a fresh or existing DB, migration `029_track_data_expansion.sql` runs
automatically. Check the console output for:

```
[migrations] Applied: 029_track_data_expansion.sql
```

If it does not appear, check that `src/db/migrations/029_track_data_expansion.sql` exists
and that the migration runner lists files in lexicographic order.

---

## Verifying the Migration

After starting the bot once, inspect the DB (e.g. with a SQLite browser):

```sql
-- Confirm 28 tracks seeded
SELECT COUNT(*) FROM tracks;          -- expect: 28

-- Spot-check track 12 (Silverstone)
SELECT * FROM tracks WHERE id = 12;
-- expect: name="Silverstone Circuit", gp_name="British Grand Prix", mu=0.30, sigma=0.05

-- Confirm track_rpc_params is gone
SELECT name FROM sqlite_master WHERE type='table' AND name='track_rpc_params';
-- expect: (empty)

-- Confirm old round track names updated
SELECT DISTINCT track_name FROM rounds WHERE track_name = 'Australia';
-- expect: (empty — should now be 'Albert Park Circuit')
```

---

## Running Tests

```bash
# From repo root
python -m pytest tests/ -v
```

Key test files for this feature:

| File | What it tests |
|------|---------------|
| `tests/unit/test_track_service.py` | `get_all_tracks`, `get_track_by_name` |
| `tests/integration/test_database.py` | Migration 029 applies cleanly |

---

## Manual Slash Command Tests

Once the bot is running in a test Discord server:

### Test `/track list` (tier-2 admin only)

1. Run `/track list` as a tier-2 admin → expect ephemeral list of all 28 tracks sorted by ID.
2. Run `/track list` as a non-admin → expect permission error.
3. Confirm `/track config`, `/track reset`, `/track info` produce "Unknown command".

### Test `/division amend` (season in SETUP state)

1. Start a season setup with `/season setup`.
2. Add two divisions: `/division add name:Div1 role:@Div1 tier:1` and `/division add name:Div2 role:@Div2 tier:2`.
3. Amend Div2's tier: `/division amend name:Div2 tier:3`.
4. Attempt `/season approve` → expect rejection: tier 2 missing.
5. Amend back: `/division amend name:Div2 tier:2`.
6. Verify approval succeeds (or fails only on unrelated gates, e.g. missing channels).

### Test `/round add` autocomplete

1. Start typing a number in the `track` field of `/round add`.
2. Verify autocomplete shows the new canonical circuit names (e.g. "01 – Albert Park Circuit").

### Test Phase 1 weather

1. Schedule a round for a track (e.g. Silverstone Circuit).
2. Manually trigger Phase 1 via the test helpers or scheduler.
3. Verify the forecast message posts and the draw log shows `mu=0.30, sigma=0.05`.

---

## Key File Locations

| File | Change |
|------|--------|
| `src/db/migrations/029_track_data_expansion.sql` | New: creates tables + seeds data |
| `src/models/track.py` | Replaces static dicts with `Track` dataclass |
| `src/services/track_service.py` | Replaces `track_rpc_params` CRUD with `get_all_tracks`, `get_track_by_name` |
| `src/services/phase1_service.py` | Updates `(mu, sigma)` resolution to query `tracks` |
| `src/cogs/track_cog.py` | Removes old commands; adds `/track list` |
| `src/cogs/season_cog.py` | Adds `/division amend`; updates round autocomplete/validation |
| `tests/unit/test_track_service.py` | Rewritten for new service functions |
