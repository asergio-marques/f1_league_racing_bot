# Implementation Plan: Signup Module Expansion

**Branch**: `025-signup-expansion` | **Date**: 2026-03-27 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/025-signup-expansion/spec.md`

## Summary

Expand the signup module in three areas: (1) decouple channel/role configuration from `/module enable signup` into three dedicated commands; (2) add an optional auto-close timer to `/signup open`, backed by APScheduler, keeping unassigned drivers intact on close; (3) add per-division lineup announcement channels that post automatically when all unassigned drivers have been placed. All new commands and automated events emit audit log entries.

## Technical Context

**Language/Version**: Python 3.13.2 (targets 3.8+)
**Primary Dependencies**: discord.py 2.7.1, aiosqlite ≥ 0.19, APScheduler ≥ 3.10 (AsyncIOScheduler + SQLAlchemyJobStore)
**Storage**: SQLite via aiosqlite; schema versioned with sequential SQL migration files applied on startup
**Testing**: pytest with pytest-asyncio; `python -m pytest tests/ -v` from repo root
**Target Platform**: Raspberry Pi (Linux); developed on Windows
**Project Type**: Discord bot (slash-command service)
**Performance Goals**: Command acknowledgement within 3 s (Discord deferred response); auto-close fires within normal APScheduler scheduling tolerance
**Constraints**: SQLite concurrency (single-writer); all DB access through `aiosqlite` via `get_connection()` context manager; no new dependencies introduced
**Scale/Scope**: Small-to-medium Discord servers; tens to low hundreds of concurrent drivers per server

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Requirement | Status |
|---|---|---|
| I — Trusted Configuration Authority | New channel/role commands are server-admin only. Timer-blocked close message is clear and actionable. | ✅ PASS |
| II — Multi-Division Isolation | `SignupDivisionConfig` is keyed by `(server_id, division_id)`; lineup posts are per-division. | ✅ PASS |
| III — Resilient Schedule Management | No impact on round/season amendment paths. | ✅ PASS |
| IV — Three-Phase Weather Pipeline | No interaction with weather module. | ✅ PASS |
| V — Observability & Change Audit Trail | Every new command and automated action (auto-close, lineup post) emits an audit log entry. | ✅ PASS |
| VI — Incremental Scope Expansion | Signup wizard and driver onboarding are formally in-scope (point 5). Lineup channels are an extension of driver onboarding output. | ✅ PASS |
| VII — Output Channel Discipline | All new channel categories (lineup channel) are module-introduced, documented in spec, and configured via dedicated commands. | ✅ PASS |
| VIII — Driver Profile Integrity | Auto-close fires `execute_forced_close()`, which uses the established state machine transitions. `UNASSIGNED`/`ASSIGNED` drivers are never affected. | ✅ PASS |
| IX — Team & Division Structural Integrity | `SignupDivisionConfig` uses FK to `divisions(id)` with ON DELETE CASCADE. No structural mutations to teams or divisions. | ✅ PASS |
| X — Modular Feature Architecture | Enable atomicity rule (v2.6.0): enable now sets flag + arms jobs only; config is applied via dedicated commands. Disable cancels the close timer job atomically. | ✅ PASS |
| XI — Signup Wizard Integrity | Close timer semantics and lineup channel fully specified per constitution v2.6.0. Configuration snapshot unchanged; timer is at window level, not wizard level. | ✅ PASS |
| XII — Race Results & Championship Integrity | No interaction with results module. | ✅ PASS |

**Post-design re-check**: All gates remain PASS after Phase 1 design. No violations requiring justification in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/025-signup-expansion/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
├── contracts/
│   └── commands.md      ← Phase 1 output
├── tasks.md             ← Phase 2 output (speckit.tasks — not yet created)
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
src/
├── cogs/
│   ├── module_cog.py         ← modify: remove inline signup params from _enable_signup()
│   ├── signup_cog.py         ← modify: add /signup channel, /signup base-role,
│   │                            /signup complete-role; extend /signup open with
│   │                            close_time param; block /signup close when timer active
│   └── driver_cog.py         ← modify: extend assign/unassign/sack to invoke lineup check
├── models/
│   └── signup_module.py      ← modify: add close_at to SignupModuleConfig;
│                                add SignupDivisionConfig dataclass
├── services/
│   ├── signup_module_service.py  ← modify: add close_at CRUD; add SignupDivisionConfig CRUD
│   ├── scheduler_service.py      ← modify: add schedule/cancel_signup_close_timer();
│   │                                add module-level _signup_close_timer_job callable
│   └── placement_service.py      ← modify: add _maybe_post_lineup() hook
├── db/
│   └── migrations/
│       └── 024_signup_expansion.sql  ← new: add close_at column; make channel/role cols
│                                         nullable; create signup_division_config table
└── bot.py                    ← modify: on_ready() close-timer restart recovery

tests/
├── unit/
│   ├── test_signup_module_service.py   ← modify: cover close_at CRUD, division config CRUD
│   ├── test_scheduler_service.py       ← modify: cover signup close timer schedule/cancel
│   └── test_placement_service.py       ← modify: cover lineup trigger logic
└── integration/
    └── test_signup_expansion.py         ← new: end-to-end flows for all new commands
```

**Structure Decision**: Single-project layout, extending existing files. No new top-level modules or packages. All new logic follows the established cog → service → model → DB layering already present in the codebase.

## Complexity Tracking

> No Constitution Check violations. Table left empty per policy.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| — | — | — |
