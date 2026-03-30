# Implementation Plan: Season-Signup Flow Alignment

**Branch**: `028-season-signup-flow` | **Date**: 2025-07-01 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/028-season-signup-flow/spec.md`

## Summary

Correct the season setup flow to match real-world league operation: remove the active-season
gate from `/signup open`; preserve PENDING_ADMIN_APPROVAL and PENDING_DRIVER_CORRECTION
drivers when signups are force-closed; allow `/driver assign` and `/driver unassign` against
a SETUP season with roles deferred until approval; add `/division calendar-channel`; extend
`/season review` to show per-division lineups; have `/season approve` bulk-grant roles and
auto-post lineup and calendar messages; and keep lineup posts live after every assignment
change.

**Technical approach**: Two constitution amendments to Principle XI (close-timer scope and
lineup channel ownership); migration 027 adds three columns to `divisions` and migrates
`lineup_channel_id` data from `signup_division_config`; `PlacementService` gains conditional
role logic and a redesigned `_refresh_lineup_post`; `SeasonService` gains a new
`get_setup_or_active_season` helper; changes to `module_cog`, `signup_cog`, `driver_cog`,
and `season_cog` complete the command-layer wiring.

## Technical Context

**Language/Version**: Python 3.13.2  
**Primary Dependencies**: discord.py (`app_commands`), aiosqlite, APScheduler  
**Storage**: SQLite via aiosqlite; migrations in `src/db/migrations/`  
**Testing**: pytest — `python -m pytest tests/ -v` from repo root  
**Target Platform**: Raspberry Pi (Linux); Windows dev machine  
**Project Type**: Long-running Discord bot service  
**Performance Goals**: League-scale (tens of drivers); no throughput requirements  
**Constraints**: SQLite only — no `DROP COLUMN` without table recreation on older builds; all
Discord API calls are async; approval must not block on posting failures (A-006)  
**Scale/Scope**: Single Discord server per bot instance; one season active at a time

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Violations Identified

| # | Principle | Clause | Conflict |
|---|-----------|--------|----------|
| V-1 | XI (Signup Wizard Integrity) | "Signup close timer" | Constitution says all of `{PENDING_SIGNUP_COMPLETION, PENDING_ADMIN_APPROVAL, PENDING_DRIVER_CORRECTION}` transition to NOT_SIGNED_UP on forced close. FR-002/FR-003 narrow this to `{PENDING_SIGNUP_COMPLETION}` only. |
| V-2 | XI (Signup Wizard Integrity) | "Lineup announcement channel" | Constitution says lineup channel is "configured for the signup module" and lives in `signup_division_config`. A-001 moves it (and the new `calendar_channel_id`) to the `divisions` table, not scoped to the signup module. |

### Justification

**V-1** — PENDING_ADMIN_APPROVAL drivers have fully completed the signup wizard and their
record has been atomically committed (Principle XI, "Signup data persistence"). Clearing them
on signup close discards a valid submitted and reviewed record. The real-world intent of
closing signups is to stop new wizard initiations, not to purge completed submissions.
Narrowing the forced-close scope to incomplete (`PENDING_SIGNUP_COMPLETION`) signups is
strictly more conservative and matches the described league operation.

**V-2** — The calendar channel has no dependency on the signup module. Both lineup and
calendar channels are per-division output channels, consistent with `results_channel_id`,
`standings_channel_id`, and `penalty_channel_id` which already live on `divisions`. The
`signup_division_config` table is a narrow module-configuration table, not the correct
owner for division-level announcement channels.

### Gate Decision

**PROCEED WITH AMENDMENTS** — both violations are justified. Amendment of Principle XI is
required before implementation begins (re-checks post-design: ✅ no further violations
from data model or contracts).

### Post-Design Constitution Re-check

- **Principle I** (auth guard): new `/division calendar-channel` uses `@admin_only` +
  `@channel_guard` ✅
- **Principle V** (audit trail): role grants at approval and assignment changes logged via
  existing audit infrastructure ✅
- **Principle VII** (output channel discipline): `lineup_channel_id` and
  `calendar_channel_id` are division-level channels, now registered on `divisions` alongside
  existing channel columns ✅
- **Principle VIII** (driver state integrity): forced-close transition narrowed to
  `PENDING_SIGNUP_COMPLETION` only; all other states preserved ✅
- **Principle X** (modular architecture): `/division calendar-channel` not gated on signup
  module per A-004 ✅
- **Principle XI** (amended): close-timer scope and lineup channel ownership updated by this
  feature ✅

## Project Structure

### Documentation (this feature)

```text
specs/028-season-signup-flow/
├── plan.md              # This file
├── research.md          # Phase 0 — constitution gaps, code archaeology findings
├── data-model.md        # Phase 1 — migration 027, entity changes, service signatures
├── quickstart.md        # Phase 1 — end-to-end manual test walkthrough
├── contracts/
│   └── division-commands.md   # Phase 1 — new + modified command contracts
└── tasks.md             # Phase 2 — generated by /speckit.tasks
```

### Source Code (repository root)

```text
src/
├── db/
│   └── migrations/
│       └── 027_season_signup_flow.sql   NEW — 3 new columns on divisions; data migration;
│                                              signup_division_config recreation
├── models/
│   └── division.py                      MODIFY — add lineup_channel_id, calendar_channel_id,
│                                                  lineup_message_id fields
├── services/
│   ├── season_service.py                MODIFY — add get_setup_or_active_season()
│   ├── placement_service.py             MODIFY — season_state param; _refresh_lineup_post
│   └── signup_module_service.py         MODIFY — stop writing lineup_channel_id to
│                                                  signup_division_config
└── cogs/
    ├── module_cog.py                    MODIFY — execute_forced_close: narrow in_progress_states
    ├── signup_cog.py                    MODIFY — /signup open: remove active season guard
    ├── driver_cog.py                    MODIFY — assign/unassign: use get_setup_or_active_season;
    │                                             pass season.status to placement service
    └── season_cog.py                    MODIFY — /season review: add lineup section;
                                                  _do_approve: bulk role grant + posts;
                                                  /division lineup-channel: new write target;
                                                  /division calendar-channel: NEW command

tests/
├── unit/                                ADD tests for service-layer changes
└── integration/                         ADD tests for command behaviour changes
```

**Structure Decision**: Single-project layout (existing `src/` + `tests/` tree). No new
top-level directories. All changes are within the existing codebase structure.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| V-1: Principle XI close-timer narrowed | PENDING_ADMIN_APPROVAL drivers represent completed signups; clearing them discards valid records | Keeping the old behaviour would lose all approved-but-unplaced drivers every time signups close — unacceptable data loss |
| V-2: Principle XI lineup channel moved to `divisions` | Calendar channel has no signup-module dependency; `signup_division_config` is not the correct owner | Keeping `lineup_channel_id` in `signup_division_config` and adding `calendar_channel_id` there too would split related channels across two tables with no architectural benefit |
