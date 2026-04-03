# Implementation Plan: Attendance Module — Initial Setup & Configuration

**Branch**: `031-attendance-module` | **Date**: 2026-04-03 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/031-attendance-module/spec.md`

## Summary

Add an Attendance module to the F1 League Racing Bot covering the full module lifecycle (enable/disable with cascading disable from R&S), per-division RSVP and attendance channel configuration, eight `attendance config` commands (three timing parameters + five penalty/threshold parameters), season review integration, and season approval gating. Two new database tables are introduced: `attendance_config` (per-server config payload) and `attendance_division_config` (per-division channel assignments). No scheduler jobs are created in this increment; RSVP automation (notices, reserve distribution, last-notice pings) is deferred to a future increment.

## Technical Context

**Language/Version**: Python 3.13.2  
**Primary Dependencies**: discord.py (app_commands), aiosqlite, APScheduler (SQLAlchemyJobStore — not used this increment)  
**Storage**: SQLite via aiosqlite; migration `030_attendance_module.sql`  
**Testing**: pytest; run as `python -m pytest tests/ -v` from repo root  
**Target Platform**: Linux server (Raspberry Pi); development on Windows  
**Project Type**: Discord bot service  
**Performance Goals**: <3 s command acknowledgement (Discord requirement); single-row DB lookups  
**Constraints**: All command responses MUST be ephemeral; APScheduler callables must be module-level picklable (not relevant this increment — no jobs created)  
**Scale/Scope**: O(dozens) drivers per server; O(tens) rounds per season

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle | Check | Status |
|---|-----------|-------|--------|
| I | Trusted Configuration Authority | Commands guarded by `@admin_only` + `@channel_guard`; no anonymous access to config | ✅ PASS |
| II | Multi-Division Isolation | `attendance_division_config` keyed per `(division_id, server_id)`; each division's channels are independent | ✅ PASS |
| III | Resilient Schedule Management | No scheduler jobs in this increment; re-arm logic deferred to RSVP increment | ✅ PASS |
| IV | Three-Phase Weather Pipeline | Not affected | ✅ PASS |
| V | Observability & Change Audit Trail | All enable/disable and config changes produce `audit_entries` rows and `post_log` calls | ✅ PASS |
| VI | Incremental Scope Expansion | Attendance management is item 11 of the in-scope domain list (v2.10.0); this is the first increment | ✅ PASS |
| VII | Output Channel Discipline | RSVP and attendance channels are module-introduced; all command responses are ephemeral | ✅ PASS |
| VIII | Driver Profile Integrity | No driver state changes in this increment | ✅ PASS |
| IX | Team & Division Structural Integrity | No team/division mutations; `attendance_division_config` is a join table | ✅ PASS |
| X | Modular Feature Architecture | Follows all 6 rules: default-off; enable atomicity (INSERT OR REPLACE config row + set flag); disable atomicity (clear division config + post log); scheduling guard (no jobs this increment); gate enforcement on all config commands; config isolation (separate tables, cleared on disable) | ✅ PASS |
| XI | Signup Wizard Integrity | Not affected | ✅ PASS |
| XII | Race Results & Championship Integrity | R&S module dependency gate enforced per FR-002 and FR-007; no results data modified | ✅ PASS |
| XIII | Attendance & Check-in Integrity | Module dependency gate (R&S must be enabled); lifecycle gate (no enable during ACTIVE season); cascading disable (auto-disabled if R&S disabled); season validation gates (RSVP + attendance channels required per-division before approval); timing invariant enforced on all config commands | ✅ PASS |

**Post-design re-check (Phase 1)**: ✅ All gates pass. No violations found. No Complexity Tracking rows required.

## Project Structure

### Documentation (this feature)

```text
specs/031-attendance-module/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (speckit.tasks command)
```

### Source Code (repository root)

**Structure Decision**: Single-project layout. All source in `src/`; tests in `tests/unit/`.

**New files:**

```text
src/
├── db/
│   └── migrations/
│       └── 030_attendance_module.sql       # attendance_config + attendance_division_config tables
├── models/
│   └── attendance.py                       # AttendanceConfig + AttendanceDivisionConfig dataclasses
├── services/
│   └── attendance_service.py               # AttendanceService (config CRUD + timing validation)
└── cogs/
    └── attendance_cog.py                   # /attendance command group (config subcommands)

tests/
└── unit/
    └── test_attendance_service.py          # Unit tests for AttendanceService
```

**Modified files:**

```text
src/
├── services/
│   └── module_service.py                   # Add is_attendance_enabled, set_attendance_enabled
├── cogs/
│   ├── module_cog.py                       # Add "attendance" to _MODULE_CHOICES; add _enable_attendance,
│   │                                       #   _disable_attendance; cascade auto-disable in _disable_results
│   └── season_cog.py                       # Add attendance status to season_review modules block;
│                                           #   add per-division rsvp/attendance channel rows in season_review;
│                                           #   add /division rsvp-channel + /division attendance-channel commands;
│                                           #   add attendance gate in _do_approve
└── bot.py                                  # Register attendance_service + attendance_cog
```
