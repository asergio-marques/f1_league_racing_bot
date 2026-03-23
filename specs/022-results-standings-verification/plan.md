# Implementation Plan: Results & Standings — Standings Design, Sync Command, and Sort-Key Correction

**Branch**: `022-results-standings-verification` | **Date**: 2026-03-23 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/022-results-standings-verification/spec.md`

## Summary

Correct a standings sort-key defect (per-entity vector length causes wrong tiebreak ordering), add a `/standings sync <division>` command that reposts the latest standings on demand, and formally ratify the existing reserves-visibility toggle and reserve-driver point-continuity guarantees. No database schema changes are required; the fix is entirely in service-layer sorting logic and a new cog command.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: discord.py ≥ 2.0, aiosqlite ≥ 0.19, APScheduler ≥ 3.10  
**Storage**: SQLite via aiosqlite (`driver_standings_snapshots`, `team_standings_snapshots`, `division_results_config`)  
**Testing**: pytest ≥ 7, pytest-asyncio ≥ 0.23 (asyncio_mode = auto)  
**Target Platform**: Linux/Windows server (Discord bot process)  
**Project Type**: Discord bot — slash command service  
**Performance Goals**: N/A (single-server bot, standings computed per round event)  
**Constraints**: Discord message length limit (2000 chars); slash command interaction timeout (3 s initial response → deferred for DB work)  
**Scale/Scope**: Single Discord server per bot instance; standings computed over at most a full season (~20 rounds, ~20 drivers per division)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Requirement | Status |
|-----------|-------------|--------|
| I — Trusted Configuration Authority | `/standings sync` and `/results reserves toggle` must enforce tier-2 admin (`@admin_only`) and channel guard (`@channel_guard`). | ✅ PASS — both decorators already applied to all `ResultsCog` commands; same pattern will be used for the new sync command. |
| II — Multi-Division Isolation | Standings computation and sync operate strictly per `division_id`; no cross-division reads. | ✅ PASS — `compute_driver_standings` and `compute_team_standings` are already scoped by `division_id`. Sync command takes explicit division name and resolves to a single `division_id`. |
| V — Observability & Change Audit Trail | Sort-key fix is a pure computation correction; no mutation → no audit entry required. Sync command reposts but does not mutate result data → no audit entry required. | ✅ PASS — no new mutations introduced. |
| VII — Output Channel Discipline | Sync command posts only to the division's configured standings channel (already a registered module-introduced channel). No unregistered channel is used. | ✅ PASS — `post_standings` already targets the stored `standings_channel_id`; sync will call the same service path. |
| X — Modular Feature Architecture (rule 5) | All new commands must gate on R&S module enabled. | ✅ PASS — `_module_gate` helper already exists in `ResultsCog`; sync command will call it. |
| XII — Race Results & Championship Integrity (Standings Computation) | Sort vectors must use global max position; Feature Race only for countback; tiebreak must fall through all positions before applying first-achieved-round criterion. | ⚠️ C1 DEFECT — existing code uses per-entity `max_pos`, producing incorrect tiebreak ordering. Fixed in this feature branch (see Phase 1 design). |

**Gate result**: One active defect (C1) identified in Principle XII. Correction is the primary deliverable of this branch. No blocking violations prevent proceeding.

## Project Structure

### Documentation (this feature)

```text
specs/022-results-standings-verification/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
├── contracts/           ← Phase 1 output (Discord commands)
└── checklists/
    └── requirements.md  ← already written
```

### Source Code (affected files only)

```text
src/
├── services/
│   ├── standings_service.py      ← C1 fix: global_max_pos in both sort-key functions
│   └── results_post_service.py   ← new repost_standings_for_division() helper
└── cogs/
    └── results_cog.py            ← new /standings sync command

tests/
└── unit/
    └── test_standings_service.py ← new tiebreak correctness tests (5+ scenarios)
```

**Structure Decision**: Single-project layout. No new files required outside the three affected source files and one test file. No schema changes; no new models.

## Complexity Tracking

No constitution violations requiring justification. The sort-key defect (C1) is a bug fix, not a complexity increase.
