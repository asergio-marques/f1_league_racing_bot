# Implementation Plan: Test Mode for System Verification

**Branch**: `002-test-mode` | **Date**: 2026-03-04 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `specs/002-test-mode/spec.md`

## Summary

Add a test mode that allows a user with the configured interaction role to bypass the real-time
scheduler and immediately execute Phase 1, Phase 2, or Phase 3 for any pending round. Three
new slash commands are added (`/test-mode toggle`, `/test-mode advance`, `/test-mode review`).
Test mode state is persisted in the existing `server_configs` table via a new DB migration.
Phase execution reuses the existing `run_phase1/2/3` service functions directly. The feature
adds one new service (`test_mode_service.py`) and one new cog (`test_mode_cog.py`); no
existing phase logic or DB schemas are redesigned.

## Technical Context

**Language/Version**: Python 3.13.2 (targets 3.8+)  
**Primary Dependencies**: discord.py 2.7.1, aiosqlite ≥ 0.19, APScheduler ≥ 3.10  
**Storage**: SQLite via aiosqlite; existing `server_configs` table extended with `test_mode_active` column via migration `002_test_mode.sql`  
**Testing**: pytest 9.0.2 + pytest-asyncio (`asyncio_mode = auto`); `pythonpath = src`  
**Target Platform**: Any host running Python 3.8+ with a Discord bot token  
**Project Type**: Discord bot (event-driven, async, ephemeral slash commands)  
**Performance Goals**: Command response within 3 seconds (Discord timeout ceiling); phase execution time is bounded by the existing phase service runtimes (DB reads + Discord API calls)  
**Constraints**: Must not break existing scheduler behaviour; must not modify any existing phase service; test mode state must survive bot restart  
**Scale/Scope**: Single Discord server instance; season with up to ~25 rounds × 4 divisions × 3 phases = ~300 phase entries maximum

## Constitution Check

*GATE — evaluated before Phase 0 research*

| Principle | Requirement | Status |
|-----------|-------------|--------|
| I — Trusted Configuration Authority | All three new commands decorated with `@channel_guard`; uses existing two-tier access; toggle requires interaction role only (not config authority, as it is operational not config) | ✅ PASS |
| II — Multi-Division Isolation | Phase advancement queue queries all divisions; each phase execution call operates on one `round_id` scoped to its division; no cross-division reads | ✅ PASS |
| III — Resilient Schedule Management | Feature does not touch schedule mutation paths | ✅ PASS |
| IV — Three-Phase Weather Pipeline | Advance command calls `run_phase1/2/3` directly — the exact same functions used by the scheduler; pipeline is unchanged | ✅ PASS |
| V — Observability & Change Audit Trail | Phase execution via advance command goes through the same service functions, which already write to the log channel and audit table; no audit bypass | ✅ PASS |
| VI — Simplicity & Focused Scope | Feature is strictly bounded to test/ops tooling; no weather or scheduling logic is modified | ✅ PASS |
| VII — Output Channel Discipline | Cog responses to the user are ephemeral (command channel only); all weather/log output still goes through `OutputRouter` | ✅ PASS |

*Re-check post-design (after Phase 1)*: All principles confirmed. No violations.

## Project Structure

### Documentation (this feature)

```text
specs/002-test-mode/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
└── tasks.md             ← Phase 2 output (/speckit.tasks — NOT created here)
```

### Source Code additions

```text
src/
├── db/
│   └── migrations/
│       └── 002_test_mode.sql          ← new: adds test_mode_active to server_configs
├── models/
│   └── server_config.py               ← modify: add test_mode_active field
├── services/
│   └── test_mode_service.py           ← new: toggle, queue query, review summary
└── cogs/
    └── test_mode_cog.py               ← new: /test-mode toggle|advance|review

tests/
└── unit/
    └── test_test_mode_service.py      ← new: unit tests for queue ordering logic
```

No new top-level directories. All additions slot into the existing single-project layout
with the same `src/` + `tests/` structure established in feature 001.

## Complexity Tracking

> No Constitution violations — table not applicable.
