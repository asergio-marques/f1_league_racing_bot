# Implementation Plan: Results & Standings — Points Config, Submission, and Standings

**Branch**: `019-results-submission-standings` | **Date**: 2026-03-18 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/019-results-submission-standings/spec.md`

## Summary

Implement the core runtime layer of the Results & Standings optional module: a server-scoped named points configuration store with session-level customisation; season attachment and approval-time snapshotting; a sequential per-session round result submission wizard triggered by APScheduler at each round's scheduled start time; standings computation (driver and team, with Feature Race countback tiebreaking) and round-after-round snapshot persistence; public formatted output to division results and standings channels; a guided post-race penalty/disqualification wizard; full session re-entry (amendment) with cascading standings recomputation; and a mid-season scoring amendment workflow using a modification store with a modified-flag gate and server-admin approval.

Foundation infrastructure delivered in 018-results-standings (module enable/disable, division results/standings channel assignment, `season_points_links` weak-link table, season approval gates) is already merged to `main` and on this branch.

## Technical Context

**Language/Version**: Python 3.13.2 (targets 3.8+)  
**Primary Dependencies**: discord.py ≥ 2.0 (`app_commands`, `ui.View`, `ui.Button`, `TextChannel.create`), aiosqlite ≥ 0.19, APScheduler ≥ 3.10 (DateTrigger)  
**Storage**: SQLite via aiosqlite; schema versioned with sequential numbered SQL migration files applied on startup (`src/db/migrations/`)  
**Testing**: pytest with pytest-asyncio; unit tests in `tests/unit/`, integration tests in `tests/integration/`  
**Target Platform**: Linux/Windows server (self-hosted Discord bot)  
**Project Type**: Discord bot (event-driven async service)  
**Performance Goals**: All commands complete within Discord's 3-second interaction window; deferred responses used for operations that may exceed that threshold  
**Constraints**: All state persisted to SQLite (no in-memory-only mutations); command responses ephemeral where private; channel operations idempotent against Discord API retries  
**Scale/Scope**: Single-server per bot instance; multiple concurrent divisions per season; typically 10–30 drivers per division

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Requirement | Status |
|-----------|-------------|--------|
| I — Access tiers | All results commands gated on tier-2 (trusted admin); server-admin-only commands (`/results amend toggle`, `/results amend review`) use a separate gate | ✅ PASS |
| II — Multi-division isolation | All result, standings, and channel operations are scoped per division; cross-division mutation is not possible | ✅ PASS |
| III — Resilient schedule | Round cancellation blocked if submission channel is already open (constitution XII Amendment & Penalty) | ✅ PASS |
| IV — Weather pipeline | This feature does not touch the weather pipeline | ✅ PASS |
| V — Audit trail | Every result submission, amendment, and penalty produces an audit log entry via Principle V; mid-season amendment approvals also logged | ✅ PASS |
| VI — Incremental scope | Items 8 (race results recording) and 9 (championship standings) are formally in-scope since v2.3.0; no scope gate violation | ✅ PASS |
| VII — Channel discipline | Three new module-introduced channel categories (results channel, standings channel, transient submission channel) are documented in the spec and registered per Principle VII | ✅ PASS |
| VIII — Driver state machine | Driver identity is read via Discord User ID; state transitions not mutated by this feature | ✅ PASS |
| IX — Team/division integrity | Teams referenced by role ID only; no team mutation; reserve team rule honoured in standings toggle | ✅ PASS |
| X — Modular architecture | All results commands check `is_results_enabled` gate; module cannot be enabled mid-season (enforced in `module_cog` from 018); default-off policy intact | ✅ PASS |
| XI — Signup wizard | Not touched by this feature | ✅ PASS |
| XII — Results & championship | Full compliance: named config store, session-level submission, sequential per-session wizard, config snapshotting on approval, monotonic ordering gate, FL eligibility rules (DNF eligible for FL bonus, DSQ/DNS not), standings countback hierarchy using Feature Race only, amendment workflow with modification store and modified-flag gate | ✅ PASS |

**Gate result**: All principles pass. No violations to justify. Proceed to Phase 0.

*Post-design re-check (after Phase 1)*: All entities confirmed align with constitution v2.4.1 schema. Transient submission channel registered as module-introduced category. Amendment-toggle-off blocked while `modified_flag = 1` enforced in `SeasonAmendmentState`. ✅ PASS

## Project Structure

### Documentation (this feature)

```text
specs/019-results-submission-standings/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output — slash command schemas
└── tasks.md             # Phase 2 output (/speckit.tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── cogs/
│   ├── results_cog.py          # NEW — /results config|amend|reserves; /round results penalize|amend
│   └── ... (existing cogs unchanged)
├── db/
│   └── migrations/
│       └── 017_results_core.sql  # NEW — all results/standings tables
├── models/
│   ├── points_config.py        # NEW — PointsConfigStore, PointsConfigEntry, PointsConfigFastestLap
│   ├── session_result.py       # NEW — SessionResult, DriverSessionResult
│   ├── standings_snapshot.py   # NEW — DriverStandingsSnapshot, TeamStandingsSnapshot
│   ├── amendment_state.py      # NEW — SeasonAmendmentState, SeasonModificationStore
│   └── ... (existing models unchanged)
├── services/
│   ├── points_config_service.py    # NEW — server-level config CRUD
│   ├── season_points_service.py    # NEW — season snapshot, view, monotonic validation
│   ├── result_submission_service.py # NEW — submission channel, session wizard, validation
│   ├── standings_service.py        # NEW — compute + persist + post driver/team standings
│   ├── results_post_service.py     # NEW — format and post results tables
│   ├── penalty_service.py          # NEW — penalty wizard state machine
│   ├── amendment_service.py        # EXTEND — mid-season modification store workflow
│   ├── scheduler_service.py        # EXTEND — add results_r{id} job at round start
│   └── ... (existing services unchanged)
└── utils/
    └── results_formatter.py    # NEW — table-formatting helpers for results/standings output

tests/
├── unit/
│   ├── test_points_config_service.py    # NEW
│   ├── test_season_points_service.py    # NEW
│   ├── test_result_submission_service.py # NEW
│   ├── test_standings_service.py        # NEW
│   ├── test_results_formatter.py        # NEW
│   ├── test_penalty_service.py          # NEW
│   └── test_amendment_service_extension.py # NEW (extends existing amendment tests)
└── integration/
    └── test_results_flow.py             # NEW — end-to-end round submission → standings
```
