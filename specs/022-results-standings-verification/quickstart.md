# Quickstart: Results & Standings — Standings Design, Sync Command, and Sort-Key Correction

**Feature branch**: `022-results-standings-verification`
**Date**: 2026-03-23

---

## What this feature delivers

1. **Sort-key correctness fix** — standings tiebreaks now work correctly when drivers or teams have achieved different sets of finishing positions. Previously, a driver with P2-only finishes could incorrectly rank above a driver with P2 *and* P3 finishes due to a Python tuple-length comparison artefact.
2. **`/results standings sync <division>`** — forces an immediate standings repost to the division's standings channel without requiring a new round submission.
3. **Ratification** — confirmed no changes needed for `/results reserves toggle` (already correct) and for reserve driver point continuity on team re-assignment (data model guarantee already holds).

---

## Workflow: how a standing tiebreak is now resolved

```
total points (DESC)
  └─ Feature Race P1 count (DESC)
       └─ Feature Race P2 count (DESC)
            └─ Feature Race P3 count (DESC)
                 └─ ... (all positions)
                      └─ earliest round of first diverging Feature Race finish (ASC)
```

All finish-count vectors are padded to the **global** maximum finishing position recorded in the division before sorting. No per-driver truncation occurs.

---

## Quick reference: affected files

| File | Change |
|------|--------|
| `src/services/standings_service.py` | Fix `_sort_key` in both `compute_driver_standings` and `compute_team_standings` — use `global_max_pos` instead of per-entity `max_pos` |
| `src/services/results_post_service.py` | Add `repost_standings_for_division(db_path, division_id, guild)` helper |
| `src/cogs/results_cog.py` | Add `standings_group` app_commands.Group and `sync` subcommand under `results_group` |
| `tests/unit/test_standings_service.py` | Add 5 tiebreak correctness tests |

---

## Trying out the sync command

Prerequisites: R&S module enabled, division created, at least one completed round with results submitted.

```
/results standings sync <division-name>
```

The bot defers ephemerally, recomputes standings from all completed rounds, and posts to the standings channel. If reserve visibility is off, reserve drivers will be absent from the post.

---

## Running the tests

```bash
cd /path/to/f1_league_racing_bot
pytest tests/unit/test_standings_service.py -v
```

The five new tiebreak tests all start with `test_tiebreak_`. To run only those:

```bash
pytest tests/unit/test_standings_service.py -v -k "tiebreak"
```

---

## Constitution re-check post-design

| Principle | Status after Phase 1 |
|-----------|----------------------|
| I — Access tiers | ✅ `@admin_only` + `@channel_guard` applied to new sync command |
| II — Multi-division isolation | ✅ Sync operates on a single `division_id`; no cross-division reads |
| V — Audit trail | ✅ No mutations introduced; no audit entry required |
| VII — Output channel discipline | ✅ Sync posts only to the registered `standings_channel_id` |
| X — Module gate | ✅ `_module_gate` called in sync handler |
| XII — Standings computation | ✅ C1 defect resolved by `global_max_pos` fix |
