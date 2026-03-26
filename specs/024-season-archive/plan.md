# Implementation Plan: Season Archive & Driver Profile Identity

**Branch**: `024-season-archive` | **Date**: 2026-03-26 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/024-season-archive/spec.md`

## Summary

Retain all season data permanently when a season completes (status → COMPLETED, no deletion); add a mandatory `game_edition` integer parameter to `/season setup`; derive the new season's display number from the count of already-COMPLETED seasons; update setup gating to allow a new season when all existing seasons are COMPLETED; and migrate `driver_session_results` + `driver_standings_snapshots` to reference the driver profile's internal integer PK (`driver_profile_id`) instead of the raw Discord user ID. Implemented via a single SQLite migration, targeted changes to `season_end_service`, `season_service`, `season_cog`, the results/penalty services, and supporting model + formatter updates.

## Technical Context

**Language/Version**: Python 3.13.2 (targets 3.8+)  
**Primary Dependencies**: discord.py 2.7.1, aiosqlite ≥ 0.19, APScheduler ≥ 3.10  
**Storage**: SQLite via aiosqlite; schema versioned with sequential SQL migration files applied at startup  
**Testing**: pytest (unit + integration); existing test suite under `tests/unit/` and `tests/integration/`  
**Target Platform**: Linux/Windows server process (Discord bot)  
**Project Type**: Discord bot service  
**Performance Goals**: All season-end operations complete in a single atomic DB transaction; no new hot-path latency introduced  
**Constraints**: SQLite ALTER TABLE cannot add NOT NULL columns without a default; column rename requires table recreation; no external services; no breaking changes to existing commands beyond the new required `game_edition` parameter  
**Scale/Scope**: Single-guild bot instances; seasons contain O(10) divisions, O(20) rounds, O(100) driver assignments

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate | Status |
|-----------|------|--------|
| I — Trusted Configuration Authority | Season completion and new season setup remain gated behind `@admin_only`. Read-only archive access requires only the interaction role. | ✅ PASS |
| II — Multi-Division Isolation | All season-completion and archive reads are scoped by `server_id`; division data is co-archived under the completed season. No cross-server exposure. | ✅ PASS |
| III — Resilient Schedule Management | Completed seasons become immutable; amendment commands for completed-season data must be rejected. The immutability guard must be applied in every mutation code path. | ✅ PASS (guard required in implementation) |
| IV — Three-Phase Weather Pipeline | No change to pipeline logic. Season completion does not affect unprocessed phases on future rounds; those rounds belong to a future ACTIVE season. | ✅ PASS |
| V — Observability & Change Audit Trail | Season completion event must be logged with actor = system, season_id, and server_id. Archive immutability rejections must be logged. | ✅ PASS (log entries required) |
| VI — Incremental Scope Expansion | This feature is the concrete implementation of the Season Archive governance introduced in constitution v2.5.0. Formally in-scope under point 2 (Season and division lifecycle). | ✅ PASS |
| VII — Output Channel Discipline | Completion message posted to log channel only (existing behaviour retained). No new channel categories introduced. | ✅ PASS |
| VIII — Driver Profile Integrity | Driver identity decoupling (FR-011 to FR-016) is an extension of the driver profile integrity mandate. Internal references must use the stable PK; Discord user ID remains only a lookup key at the command boundary. | ✅ PASS (internal-ID migration required) |
| IX — Team & Division Structural Integrity | Teams and seats within a completed season are archived alongside the season; they become read-only. No new seat or team mutations are possible once a season is COMPLETED. | ✅ PASS |
| X — Modular Feature Architecture | No module enable/disable flows involved. No changes to module lifecycle. | ✅ PASS |
| XI — Signup Wizard Integrity | Signup records remain keyed by Discord user ID until profile creation; this is explicitly accepted in spec assumptions. No signup-flow mutation after season completion. | ✅ PASS |
| XII — Race Results & Championship Integrity | Results and standings for a completed season become immutable (FR-002). Driver identity migration updates `driver_session_results` and `driver_standings_snapshots` to use `driver_profile_id`. All submission paths must resolve Discord user ID → driver_profile_id at the command boundary (FR-015). | ✅ PASS (migration + guard required) |

**Season Archive section (v2.5.0)**: This feature directly implements the append-only, full-data-retention, read-only-after-completion semantics mandated for the Season Archive. No violations present.

**Post-design re-check (complete)**: data-model.md reviewed — no new violations. Adding nullable FK columns (not recreating tables) means no cascade-delete risk. All new columns have DEFAULT 0/NULL backfill, safely preserving existing rows. No new channel categories, no new modules, no state-machine bypasses. All 12 gates remain PASS.

## Project Structure

### Documentation (this feature)

```text
specs/024-season-archive/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
├── contracts/           ← Phase 1 output
│   └── season-setup-command.md
└── tasks.md             ← Phase 2 output (speckit.tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── db/
│   └── migrations/
│       └── 020_season_archive.sql          ← NEW: game_edition, driver_profile_id columns
├── models/
│   ├── season.py                           ← CHANGE: add game_edition field
│   ├── session_result.py                   ← CHANGE: add driver_profile_id field
│   └── standings_snapshot.py              ← CHANGE: add driver_profile_id field
├── services/
│   ├── season_end_service.py               ← CHANGE: archive-on-complete (no deletion)
│   ├── season_service.py                   ← CHANGE: setup gating, season number derivation, complete_season()
│   └── driver_service.py                   ← CHANGE: driver_profile_id resolution helper
└── cogs/
    ├── season_cog.py                       ← CHANGE: game_edition param, new gating
    └── results_cog.py                      ← CHANGE: resolve discord_user_id → driver_profile_id + immutability guard

tests/
├── unit/
│   ├── test_season_end_service.py          ← CHANGE: replace delete-assertions with archive-assertions
│   ├── test_season_service.py              ← NEW: season number derivation, gating, complete_season
│   ├── test_driver_service.py              ← CHANGE: add driver_profile_id resolution tests
│   └── test_results_service.py            ← CHANGE: driver_profile_id write path tests
└── integration/
    └── test_season_archive.py              ← NEW: full season lifecycle with archive verification
```

**Structure Decision**: Single project layout. All changes are targeted additions/edits to existing files; one new migration file; one new integration test module.
