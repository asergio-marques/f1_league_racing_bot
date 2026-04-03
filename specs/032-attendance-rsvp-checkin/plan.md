# Implementation Plan: Attendance RSVP Check-in & Reserve Distribution

**Branch**: `032-attendance-rsvp-checkin` | **Date**: 2026-04-03 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `specs/032-attendance-rsvp-checkin/spec.md`

## Summary

Automates the RSVP lifecycle for scheduled rounds: the bot posts a per-division embed in the
configured RSVP channel at the `rsvp_notice_days` notice window, drivers respond via persistent
Discord buttons (Accept / Tentative / Decline), the embed roster is updated in-place, a
last-notice ping is sent to non-responding full-time drivers at `rsvp_last_notice_hours`, and at
`rsvp_deadline_hours` reserve distribution is computed and an assignment announcement is posted.

**Technical approach**: Three APScheduler `DateTrigger` jobs per non-Mystery round (notice,
last-notice, deadline), module-level async callables (picklable for SQLAlchemyJobStore),
a `discord.ui.View(timeout=None)` with deterministic `custom_id`s re-armed on restart from
`rsvp_embed_messages`, and two new SQLite tables (`driver_round_attendance`,
`rsvp_embed_messages`). All patterns extend existing scheduler, service, and cog conventions.

## Technical Context

**Language/Version**: Python 3.13.2  
**Primary Dependencies**: discord.py (slash commands, `discord.ui.View`), APScheduler
(AsyncIOScheduler + SQLAlchemyJobStore + DateTrigger), aiosqlite  
**Storage**: SQLite — new migration `src/db/migrations/031_attendance_rsvp.sql`  
**Testing**: pytest — `python -m pytest tests/ -v` from repo root  
**Target Platform**: Raspberry Pi (Linux)  
**Project Type**: Discord bot (service)  
**Performance Goals**: Sub-second embed update on button press; distribution computation at most
O(reserves × teams) — tens of drivers, not thousands  
**Constraints**: APScheduler jobs must be picklable (module-level callables only); persistent
views must be re-armed by message_id on bot restart  
**Scale/Scope**: Single Discord server; ≤5 divisions; ≤30 drivers per division

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Status |
|-----------|-------|--------|
| **XIII** — Attendance & Check-in Integrity: RSVP embed, locking rules, reserve distribution, last-notice ping, DriverRoundAttendance entity | All FR-001–FR-030 align directly with Principle XIII rules. No rule is relaxed or omitted. | ✅ PASS |
| **X** — Modular Feature Architecture (rule 3): Attendance module gate enforced at every scheduling and posting site; guarded by `is_attendance_enabled()` check in `_do_approve` before scheduling jobs; no jobs scheduled if module disabled | Job scheduling is conditional; RSVP jobs extend `cancel_round` so they are removed when a round is cancelled regardless of module state | ✅ PASS |
| **X** — Modular Feature Architecture (enable atomicity): No new enable/disable commands this increment; prior 031 implementation handles all lifecycle transitions | Out of scope for this increment | ✅ PASS |
| **VII** — Output Channel Discipline: RSVP channel is a module-introduced channel (registered in `attendance_division_config.rsvp_channel_id`); embed and announcement posted only to that channel | All output targets are module-registered channels; no unregistered channel posting | ✅ PASS |
| **V** — Audit trail: Skip events (no RSVP channel configured, Mystery round bypass) MUST produce audit log entries via `output_router.post_log` | Covered by FR-008; `_rsvp_notice_job` logs skip reason | ✅ PASS |
| **XII** — Results & Standings dependency: `attended` field on `DriverRoundAttendance` populated only when results are submitted; not populated this increment | `attended` column defined NULL by default; populated in a future increment | ✅ PASS |

**Post-Phase-1 re-check**: No violations introduced by data-model or contract design.

## Project Structure

### Documentation (this feature)

```text
specs/032-attendance-rsvp-checkin/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
└── tasks.md             ← Phase 2 output (speckit.tasks — not yet created)
```

### Source Code (repository root)

```text
src/
├── db/
│   └── migrations/
│       └── 031_attendance_rsvp.sql          NEW — driver_round_attendance, rsvp_embed_messages
├── models/
│   └── attendance.py                        AMEND — add DriverRoundAttendance, RsvpEmbedMessage
├── services/
│   ├── attendance_service.py                AMEND — add CRUD for new tables
│   ├── rsvp_service.py                      NEW — notice dispatch, last-notice, deadline + distribution
│   └── scheduler_service.py                 AMEND — 3 module-level callables + schedule_attendance_round
├── cogs/
│   ├── attendance_cog.py                    AMEND — RsvpView (persistent buttons)
│   └── season_cog.py                        AMEND — _do_approve: schedule attendance jobs
└── bot.py                                   AMEND — register callbacks, add_view, restart re-arm

tests/
└── unit/
    ├── test_rsvp_service.py                 NEW — distribution algorithm + service methods
    └── test_rsvp_embed_builder.py           NEW — embed content and roster formatting
```

**Structure Decision**: Single project, extending the existing `src/` layout. No new
directories required. Two new service-side files (`rsvp_service.py`) to keep RSVP
orchestration separate from the existing config-only `attendance_service.py`.
