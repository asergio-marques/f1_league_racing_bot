# Implementation Plan: Forecast Channel Message Cleanup

**Branch**: `007-forecast-msg-cleanup` | **Date**: 2026-03-04 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/007-forecast-msg-cleanup/spec.md`

**Reuses plan**: See [`specs/001-league-weather-bot/plan.md`](../001-league-weather-bot/plan.md)
for the full tech stack, structural decisions, and base data model. Everything in that plan
applies unchanged. Only the files listed in the Scope table below require edits or creation.

## Summary

When Phase 2 is posted for a round the bot deletes the Phase 1 forecast message for that
same round and division; when Phase 3 is posted it deletes Phase 2. Twenty-four hours after
a round's scheduled start time the bot deletes the Phase 3 message. **Exception**: while
test mode is active for a server, all deletions are suppressed so the admin can inspect all
phase outputs simultaneously; when test mode is disabled every stored forecast message for
that server is immediately flushed. This keeps the per-division forecast channel to at most
one active forecast message per round at any time during live operation.

**Technical approach**: Extend `OutputRouter.post_forecast` to return the posted
`discord.Message` so phase services can persist the message ID to a new `forecast_messages`
table. Each phase service loads the previous phase's stored message ID and deletes it before
posting (suppressed while test mode is active). A new `forecast_cleanup_service.py` houses
`delete_forecast_message` (test-mode guard + FR-008/FR-009 error handling),
`flush_pending_deletions` (server-scoped bulk delete called when test mode is disabled), and
`run_post_race_cleanup` (the coroutine triggered by a new 24-hour APScheduler job,
`cleanup_r{id}` pattern, module-level callable `_forecast_cleanup_job` following existing
pickling conventions). `AmendmentService.amend_round` calls `delete_forecast_message` for
all phases as part of its existing phase-invalidation step. `TestModeCog.toggle` calls
`flush_pending_deletions` when toggling off. One new DB migration adds the
`forecast_messages` table.

## Technical Context

**Language/Version**: Python 3.13.2 (targets 3.8+)
**Primary Dependencies**: discord.py 2.7.1, aiosqlite ≥ 0.19, APScheduler ≥ 3.10
**Storage**: SQLite via aiosqlite — one new migration (`004_forecast_messages.sql`)
**Testing**: pytest 9.0.2 + pytest-asyncio (`asyncio_mode = auto`); `pythonpath = src`
**Target Platform**: Any host running Python 3.8+ with a Discord bot token
**Project Type**: Discord bot (event-driven, async)
**Performance Goals**: Deletion completes before the next phase message is posted (negligible latency — single Discord API call)
**Constraints**: No new slash commands; deletion failures MUST NOT block phase posting; atomicity of amendment invalidation must be preserved
**Scale/Scope**: One cleanup job and up to three stored message IDs per active round; bounded by season/division scale

## Constitution Check

*GATE — evaluated before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Requirement | Status |
|-----------|-------------|--------|
| I — Trusted Configuration Authority | No new commands; no access tier changes | ✅ PASS |
| II — Multi-Division Isolation | Forecast message records are keyed by `(round_id, division_id, phase_number)`; deletion is scoped to a single round/division pair; no cross-division reads or writes | ✅ PASS |
| III — Resilient Schedule Management | Cleanup job follows the same `replace_existing=True` / misfire semantics as phase jobs; amendment invalidation clears stored IDs before re-running phases | ✅ PASS |
| IV — Three-Phase Weather Pipeline | Deletion happens outside the phase computation path; phase logic is unchanged; amendment invalidation clears messages atomically alongside phase results | ✅ PASS |
| V — Observability & Change Audit Trail | Deletion failures are logged to the calculation log channel (SC-005); successful deletions do not need audit trail entries (no config mutation, no computation) | ✅ PASS |
| VI — Simplicity & Focused Scope | Change is additive and strictly bounded; no new commands, no new user-facing behaviour | ✅ PASS |
| VII — Output Channel Discipline | Deletion targets only the per-division forecast channel where the message was originally posted; no posts to other channels | ✅ PASS |

**Constitution Check result: PASS — no violations, no Complexity Tracking entries required.**

*Re-check post-design*: All principles confirmed. No violations introduced by Phase 1 data model.

## Scope

| File | Change |
|------|--------|
| `src/db/migrations/004_forecast_messages.sql` | **New** — `forecast_messages` table + unique index (FR-001–FR-003, FR-010) |
| `src/utils/output_router.py` | `post_forecast` returns `discord.Message \| None`; `_send` returns last sent message object (FR-001–FR-003) |
| `src/services/forecast_cleanup_service.py` | **New** — `delete_forecast_message` (checks test_mode before deleting), `flush_pending_deletions`, `run_post_race_cleanup` (FR-004–FR-010, FR-013–FR-015) |
| `src/services/phase1_service.py` | Capture `post_forecast` return value and store message ID via `forecast_cleanup_service` (FR-001) |
| `src/services/phase2_service.py` | Delete Phase 1 message before posting; store Phase 2 message ID (FR-002, FR-004) |
| `src/services/phase3_service.py` | Delete Phase 2 message before posting; store Phase 3 message ID (FR-003, FR-005) |
| `src/services/scheduler_service.py` | Add `_forecast_cleanup_job`, `register_forecast_cleanup_callback`, `schedule_forecast_cleanup`, `cancel_forecast_cleanup`; update `schedule_round` and `cancel_round` (FR-006, FR-007) |
| `src/services/amendment_service.py` | Call `delete_forecast_message` for all three phases in the invalidation step (FR-011) |
| `src/cogs/test_mode_cog.py` | Call `flush_pending_deletions` when toggling off (FR-015) |
| `src/bot.py` | Register forecast cleanup callback in `on_ready` (FR-007) |
| `tests/unit/test_forecast_cleanup.py` | **New** — unit tests (US1–US4 acceptance scenarios) |

## Project Structure

### Documentation (this feature)

```text
specs/007-forecast-msg-cleanup/
├── plan.md              ← this file
├── spec.md              ← feature specification
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
└── tasks.md             ← Phase 2 output (/speckit.tasks — NOT created here)
```

No `contracts/` directory — this feature adds no new commands or external interfaces.

### Source Code additions/edits

```text
src/
├── db/
│   └── migrations/
│       └── 004_forecast_messages.sql     ← new
├── services/
│   ├── forecast_cleanup_service.py       ← new (delete_forecast_message checks test_mode;
│   │                                          flush_pending_deletions; run_post_race_cleanup)
│   ├── phase1_service.py                 ← modify: store message ID
│   ├── phase2_service.py                 ← modify: delete Phase 1 msg, store Phase 2 msg ID
│   ├── phase3_service.py                 ← modify: delete Phase 2 msg, store Phase 3 msg ID
│   ├── scheduler_service.py              ← modify: cleanup job support
│   └── amendment_service.py             ← modify: delete forecast msgs on invalidation
├── cogs/
│   └── test_mode_cog.py                  ← modify: flush on toggle-off
├── utils/
│   └── output_router.py                  ← modify: post_forecast returns Message | None
└── bot.py                                ← modify: register cleanup callback

tests/
└── unit/
    └── test_forecast_cleanup.py          ← new
```

---

## Phase 0: Research

See [research.md](research.md) for full findings.

---

## Phase 1: Design

See [data-model.md](data-model.md) for the `forecast_messages` schema.

No external contracts are defined — this feature adds no new slash commands or user-facing
interfaces. See [quickstart.md](quickstart.md) for an overview of the automated behaviour.

## Complexity Tracking

> No Constitution violations — table not applicable.
