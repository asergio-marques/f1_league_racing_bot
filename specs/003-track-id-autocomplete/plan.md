# Implementation Plan: Track ID Autocomplete & Division Command Cleanup

**Branch**: `003-track-id-autocomplete` | **Date**: 2026-03-04 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/003-track-id-autocomplete/spec.md`

## Summary

Remove the redundant `race_day`/`race_time` parameters from `/division-add` (each round already carries its own `scheduled_at`), add a Discord autocomplete dropdown to the `track` parameter of `/round-add` and `/round-amend` using a numeric ID → canonical name mapping, fix a bot startup crash caused by a `bot_`-prefixed method name in `InitCog`, and expand the README with per-command parameter tables.

## Technical Context

**Language/Version**: Python 3.13.2 (targets 3.8+)  
**Primary Dependencies**: discord.py 2.7.1 (`app_commands.Choice`, `@command.autocomplete`), aiosqlite ≥ 0.19, APScheduler ≥ 3.10  
**Storage**: SQLite via aiosqlite; schema versioned with sequential SQL migration files applied on startup  
**Testing**: pytest 9.0.2 + pytest-asyncio (`asyncio_mode = auto`)  
**Target Platform**: Windows/Linux server running Python 3.8+  
**Project Type**: Discord bot (slash commands)  
**Performance Goals**: Command acknowledgement within 3 seconds (Discord hard limit); autocomplete callback ≤ 200 ms  
**Constraints**: Autocomplete limited to 25 choices per Discord API; track registry has 27 entries — handled with substring filtering  
**Scale/Scope**: Single-server bot; changes affect the season configuration flow only

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I — Trusted Configuration Authority | ✅ PASS | `/division-add`, `/round-add`, `/round-amend` remain behind `@admin_only` + `@channel_guard`. No access-tier changes. |
| II — Multi-Division Isolation | ✅ PASS | Division data model change (removing `race_day`/`race_time`) is an additive database migration; per-division isolation is unaffected. |
| III — Resilient Schedule Management | ✅ PASS | `/round-amend` gains autocomplete but its amendment logic (atomic DB update, scheduler re-arm) is unchanged. |
| IV — Three-Phase Weather Pipeline | ✅ PASS | No changes to phase services or scheduling horizons. |
| V — Observability & Change Audit Trail | ✅ PASS | No audit-log or log-channel changes. |
| VI — Simplicity & Focused Scope | ✅ PASS | Changes reduce surface area (fewer params on `/division-add`) and improve UX without adding new commands. |
| VII — Output Channel Discipline | ✅ PASS | No new output channels or message types introduced. |

**Post-Phase 1 re-check**: No violations identified in design or implementation.

## Project Structure

### Documentation (this feature)

```text
specs/003-track-id-autocomplete/
├── plan.md          ← this file
├── research.md      ← Phase 0
├── data-model.md    ← Phase 1
├── quickstart.md    ← Phase 1
└── tasks.md         ← Phase 2 (/speckit.task)
```

### Source Code changes

```text
src/
├── models/
│   ├── division.py          # removed race_day / race_time fields
│   └── track.py             # added TRACK_IDS mapping
├── services/
│   └── season_service.py    # removed race_day/race_time from add_division + SELECT
├── cogs/
│   ├── season_cog.py        # removed params from division_add; added track autocomplete to round_add
│   ├── amendment_cog.py     # added track autocomplete to round_amend
│   └── init_cog.py          # renamed bot_init → handle_bot_init (startup crash fix)
└── db/migrations/
    └── 003_remove_division_race_fields.sql  # DROP COLUMN race_day, race_time

tests/
└── unit/
    └── test_test_mode_service.py  # updated seed INSERT statements
```

**Structure Decision**: Single-project layout (`src/` + `tests/`), consistent with features 001 and 002.

## Complexity Tracking

> No Constitution violations — table omitted.

---

## Addendum — Bot Data Reset Command

### New Source Files

| File | Purpose |
|------|---------|
| `src/services/reset_service.py` | `reset_server_data(server_id, db_path, scheduler_service, full) -> dict` |
| `src/cogs/reset_cog.py` | `/bot-reset` slash command — `@admin_only`, no `@channel_guard` |
| `tests/unit/test_reset_service.py` | Unit tests for reset service |

### Modified Files

| File | Change |
|------|--------|
| `src/bot.py` | Import and register `ResetCog` |
| `README.md` | Add `/bot-reset` parameter table and usage notes |

### Constitution Check (addendum)

| Principle | Status | Notes |
|-----------|--------|-------|
| I — Access tiers | Pass | `@admin_only` (Manage Server) — higher privilege than trusted-user |
| II — Server isolation | Pass | All DELETEs scoped by `server_id` subquery |
| III — Phase integrity | Pass | APScheduler jobs cancelled before deletion |
| IV — Scheduler contract | Pass | `cancel_round()` called per round before DB writes |
| V — Audit trail | Pass | `audit_entries` deleted last (after seasons), within same tx |
| VI — Focused scope | Pass | No new tables; no changes to existing commands |
| VII — Output channels | Pass | Response is ephemeral; no new channel writes |

> No additional Constitution violations.



---

## Addendum — Correctness Fixes (2026-03-04)

### Summary

Four existing-behaviour corrections and one new behaviour (season auto-deletion):

| # | Change | Files |
|---|--------|-------|
| C-001 | `/round-add` rejects empty track for non-MYSTERY formats | `season_cog.py` |
| C-002 | `/season-setup` blocks if an active or pending season already exists | `season_cog.py`, `season_service.py` |
| C-003 | `/bot-reset` also clears in-memory pending season setups and the season-end scheduler job | `reset_cog.py`, `season_cog.py` |
| C-004 | Season auto-deletion 7 days after last round; log message posted on completion | `season_end_service.py` (new), `phase3_service.py`, `scheduler_service.py`, `season_service.py` |
| C-005 | `/test-mode advance` on the last phase triggers immediate season end | `test_mode_cog.py` |

### New Source Files

| File | Purpose |
|------|---------|
| `src/services/season_end_service.py` | `check_and_schedule_season_end()` and `execute_season_end()` |
| `tests/unit/test_season_end_service.py` | 14 unit tests covering service helpers and end-to-end season completion |

### New SeasonService Methods

| Method | Purpose |
|--------|---------|
| `has_existing_season(server_id)` | True if any `seasons` row exists for this server |
| `all_phases_complete(server_id)` | True if all non-MYSTERY rounds in the active season have all 3 phases done |
| `get_last_scheduled_at(server_id)` | MAX `scheduled_at` across all rounds for the active season |

### New SchedulerService Methods

| Method | Purpose |
|--------|---------|
| `schedule_season_end(server_id, fire_at, callback)` | One-shot DateTrigger job; `replace_existing=True` |
| `cancel_season_end(server_id)` | Remove `season_end_{server_id}` job; no-op if absent |

### Season Auto-Deletion Flow (normal mode)

```
phase3_service.run_phase3()
  └─ check_and_schedule_season_end(server_id, bot)
       ├─ all_phases_complete()? No → return
       └─ Yes → schedule_season_end at last_round.scheduled_at + 7 days
                    ↓ (when job fires)
            execute_season_end(server_id, season_id, bot)
              ├─ idempotency guard (active season exists?)
              ├─ cancel_season_end job
              ├─ post_log "Season Complete" message
              └─ reset_server_data(full=False)  ← reuses reset_service
```

### Season Auto-Deletion Flow (test mode)

```
TestModeCog.advance()
  └─ run_phase3(round_id, bot)   ← also triggers check_and_schedule above
  └─ get_next_pending_phase() → None AND phase_number == 3
       └─ cancel_season_end (cancels the just-scheduled job)
          execute_season_end(server_id, season_id, bot)
          respond with "Season complete!" message
```

### Constitution Check (addendum)

| Principle | Status | Notes |
|-----------|--------|-------|
| I — Access tiers | Pass | C-001/C-002 are setup-flow guards, no tier changes |
| II — Server isolation | Pass | All queries scoped by `server_id` |
| III — Phase integrity | Pass | Season deletion only after all phases done |
| IV — Scheduler contract | Pass | `cancel_round` + `cancel_season_end` called before data deletion |
| V — Audit trail | Pass | `audit_entries` deleted last, same as in reset_service |
| VI — Focused scope | Pass | No new slash commands; one new internal service |
| VII — Output channels | Pass | Season-completion message goes to existing log channel only |
