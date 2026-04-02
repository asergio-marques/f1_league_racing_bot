# Implementation Plan: Results Resubmission & Weather Phase Configurability

**Branch**: `029-results-weather-improvements` | **Date**: 2026-04-02 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `specs/029-results-weather-improvements/spec.md`

## Summary

Two independent, targeted extensions to existing modules:

1. **Results hotfix resubmission** — a new "🔄 Resubmit Initial Results" danger button added to `PenaltyReviewView`. When pressed, it discards any staged (uncommitted) penalties, supersedes existing `DriverSessionResult` rows for the round, and restarts the session collection flow. On re-completion, `enter_penalty_state` is called with `is_resubmission=True`, which posts the updated provisional results with a `"Provisional Results (amended)"` label. Two audit log entries are written (staged-penalty discard + result replacement).

2. **Configurable weather phase deadlines** — a new `WeatherPipelineConfig` entity (table `weather_pipeline_config`) stores per-server phase horizons defaulting to 5d / 2d / 2h. Three new slash commands under a new `/weather config` group allow tier-2 admins to update these values outside an active season, subject to the ordering invariant `P1×24 > P2×24 > P3`. `SchedulerService.schedule_round()` is updated to accept phase deadline kwargs (defaulting to the stored or default values).

## Technical Context

**Language/Version**: Python 3.13.2  
**Primary Dependencies**: discord.py 2.7.1 (app_commands groups, persistent Views, Modals), aiosqlite ≥ 0.19, APScheduler ≥ 3.10  
**Storage**: SQLite via aiosqlite; sequential SQL migration files applied on startup (migration 028 added)  
**Testing**: pytest; `python -m pytest tests/ -v` from repo root  
**Target Platform**: Raspberry Pi (Linux), developed on Windows  
**Project Type**: Discord bot (single-process async service)  
**Performance Goals**: All commands must acknowledge within 3 seconds (deferred response used for any operation that may exceed this)  
**Constraints**: No new external dependencies; changes must be backward-compatible with existing DB rows (defaults handle absent `weather_pipeline_config` rows)  
**Scale/Scope**: Small-to-medium Discord servers; no bulk computation paths affected

## Constitution Check

*Evaluated against constitution v2.8.0 — re-evaluated post-design below.*

| Principle | Status | Notes |
|---|---|---|
| I — Trusted Configuration Authority | ✅ PASS | All new commands gated behind tier-2 admin (league manager) role. Resubmit button uses same LM gate as existing `PenaltyReviewView` buttons. |
| II — Multi-Division Isolation | ✅ PASS | No cross-division reads or writes introduced. Resubmit is scoped to a single round/division. `weather_pipeline_config` is per-server (not per-division). |
| III — Resilient Schedule Management | ✅ PASS | `schedule_round()` now accepts configurable horizons; already uses `replace_existing=True`. No amendment invalidation semantics affected. |
| IV — Three-Phase Weather Pipeline | ⚠️ NOTE | Principle IV currently hardcodes T−5d/T−2d/T−2h as "NON-NEGOTIABLE". This feature makes the horizons configurable but keeps the sequential three-phase structure non-negotiable. **Constitution amendment required** (MINOR): make horizons configurable with mandatory defaults; codify ordering invariant; restrict changes to non-ACTIVE periods. See checklist. |
| V — Observability & Change Audit Trail | ✅ PASS | All four new audit change types logged (staged-penalty discard, result replacement, three deadline changes). Actor, old value, new value recorded on all. |
| VI — Incremental Scope Expansion | ✅ PASS | Both changes fall within ratified domains: Results & Standings (item 8–9) and Weather generation (item 1). No new domain introduced. |
| VII — Output Channel Discipline | ✅ PASS | Resubmit posts only to the existing transient submission channel. Weather config commands are ephemeral. No new channel categories introduced. |
| VIII — Driver Profile Integrity | ✅ PASS | No driver state machine involvement. |
| IX — Team & Division Structural Integrity | ✅ PASS | No team/division structural changes. |
| X — Modular Feature Architecture | ✅ PASS | New `/weather config` commands check the weather module-enabled flag before executing (Principle X, rule 5). Phase deadline config is owned by the weather module. |
| XI — Signup Wizard Integrity | ✅ PASS | No signup module involvement. |
| XII — Race Results & Championship Integrity | ⚠️ NOTE | Principle XII covers amendment and penalty but does not explicitly name in-wizard hotfix resubmission. **Constitution amendment required** (PATCH): name resubmission as a permitted in-wizard action; mandate staged-penalty discard; require "(amended)" marker. |

**Post-design re-evaluation**: No new violations introduced by the design. The two noted amendments (Principle IV + XII) are governance clarifications of existing ratified domains — neither changes the fundamental rules. Both are recorded and flagged for ratification.

**Complexity Tracking**: No constitution violations that require justification. No over-engineered patterns. Both features are strictly additive.

## Project Structure

### Documentation (this feature)

```text
specs/029-results-weather-improvements/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
├── contracts/
│   └── commands.md      ← Phase 1 output
└── tasks.md             ← Phase 2 output (speckit.tasks)
```

### Source Code

```text
src/
├── cogs/
│   ├── weather_cog.py                   ← NEW
│   └── (results_cog.py unchanged)
├── models/
│   └── weather_config.py                ← NEW
├── services/
│   ├── penalty_wizard.py                ← MODIFIED (resubmit button)
│   ├── result_submission_service.py     ← MODIFIED (resubmit flow, is_resubmission param)
│   ├── scheduler_service.py             ← MODIFIED (configurable horizons in schedule_round)
│   └── weather_config_service.py        ← NEW
├── db/
│   └── migrations/
│       └── 028_weather_pipeline_config.sql  ← NEW
└── bot.py                               ← MODIFIED (register WeatherCog; pass horizons)

tests/
├── unit/
│   ├── test_penalty_wizard.py           ← EXTENDED (pw_resubmit button contract)
│   ├── test_result_submission_service.py ← EXTENDED (resubmit flow)
│   ├── test_weather_config_service.py   ← NEW
│   └── test_scheduler_service.py        ← NEW or EXTENDED (configurable horizons)
└── integration/
    ├── test_penalty_flow.py             ← EXTENDED (resubmit integration path)
    └── test_weather_config_flow.py      ← NEW (deadline commands + active-season gate)
```

**Structure Decision**: Single-project layout; no structural change to existing `src/` tree. All new files follow established module patterns (cogs, models, services, migration files).

## Phase 0: Research

See [research.md](research.md) for full findings. Key decisions:

| Decision | Rationale | Alternatives Considered |
|---|---|---|
| `WeatherPipelineConfig` as a separate table (not added to `server_configs`) | Keeps weather module config isolated; easier to clear on future module restructures; mirrors signup_module_config pattern | Adding columns to server_configs — rejected: pollutes the core config table with module-specific data |
| `schedule_round()` accepts kwargs (not does a DB lookup internally) | Keeps the method synchronous and easily unit-testable; consistent with existing calling pattern | schedule_round does its own DB lookup — rejected: breaks sync signature, requires db_path propagation |
| New `WeatherCog` (not adding to existing cog) | No existing `/weather` group exists; weather commands are a distinct domain from module enable/disable | Adding to `module_cog.py` — rejected: mixes module lifecycle commands with configuration commands, violating command grouping principle |
| Resubmit as in-wizard button (not a separate slash command) | Spec requires the fix to happen without leaving the submission channel; avoids disambiguation issues about which session/round to resubmit | `/results resubmit` command — rejected: requires round ID parameter, breaks wizard isolation |
| Supersede (`is_superseded = 1`) existing DriverSessionResult rows, do not hard-delete | Preserves audit trail of the original submission; consistent with existing supersession pattern used in amendment flow | Hard-delete — rejected: loses the original result data from the audit trail |

## Phase 1: Design

- [data-model.md](data-model.md) — `WeatherPipelineConfig` entity; `schedule_round` signature change; `PenaltyReviewView` button addition; label-suffix mechanism
- [contracts/commands.md](contracts/commands.md) — `pw_resubmit` button spec; `/weather config phase-1-deadline`, `phase-2-deadline`, `phase-3-deadline` command contracts; audit change type names
- [quickstart.md](quickstart.md) — implementation sequence, file map, key invariants, test run commands
