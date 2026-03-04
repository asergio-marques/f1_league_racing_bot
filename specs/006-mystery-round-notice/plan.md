# Implementation Plan: Mystery Round Notice at Phase 1 Horizon

**Branch**: `006-mystery-round-notice` | **Date**: 2026-03-04 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/006-mystery-round-notice/spec.md`

**Reuses plan**: See [`specs/001-league-weather-bot/plan.md`](../001-league-weather-bot/plan.md)
for the full tech stack, structural decisions, and data model. Everything in that plan applies
unchanged. Only the files listed in the Scope table below require edits or creation.

## Summary

Mystery rounds currently produce no output whatsoever at any phase horizon. This causes
drivers to observe unexplained silence where a Phase 1 forecast would normally appear, which
is indistinguishable from a bot failure. This change makes the silence intentional and
visible: at the Phase 1 horizon (T−5 days) the bot posts a fixed informational notice to
the division's forecast channel stating that conditions are unknown. No division role is
tagged. Nothing is posted at the Phase 2 or Phase 3 horizons.

**Technical approach**: Add a new `mystery_r{round_id}` APScheduler job fired at T−5 days
(same horizon as Phase 1, same `misfire_grace_time`, same `replace_existing` semantics).
The job invokes a new `run_mystery_notice` coroutine that posts a fixed string via the
existing `OutputRouter`. No schema changes; no new phase pipeline logic.

## Technical Context

**Language/Version**: Python 3.13.2 (targets 3.8+)
**Primary Dependencies**: discord.py 2.7.1, aiosqlite ≥ 0.19, APScheduler ≥ 3.10
**Storage**: SQLite via aiosqlite — no schema changes required
**Testing**: pytest 9.0.2 + pytest-asyncio (`asyncio_mode = auto`); `pythonpath = src`
**Target Platform**: Any host running Python 3.8+ with a Discord bot token
**Project Type**: Discord bot (event-driven, async)
**Performance Goals**: Notice delivered within the existing 5-minute misfire grace window
**Constraints**: No schema migrations; no new slash commands; no changes to existing phase services
**Scale/Scope**: One notice job per Mystery round; bounded by the same season/division scale as the core bot

## Constitution Check

*GATE — evaluated before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Requirement | Status |
|-----------|-------------|--------|
| I — Trusted Configuration Authority | No new commands added; no access tier changes | ✅ PASS |
| II — Multi-Division Isolation | Notice job is keyed by `round_id`; looks up its own division's `forecast_channel_id`; no cross-division reads | ✅ PASS |
| III — Resilient Schedule Management | Amendment-to-MYSTERY path calls `cancel_round` then re-schedules mystery notice when T−5 is still in the future (FR-009) | ✅ PASS |
| IV — Three-Phase Weather Pipeline | Principle IV states Mystery rounds MUST NOT execute Phases 1/2/3. The mystery notice is explicitly NOT a phase — it is a fixed informational post. Phase jobs are still never created for Mystery rounds. No violation. | ✅ PASS |
| V — Observability & Change Audit Trail | The mystery notice has no computation inputs or random draws; there is nothing to audit. Not posting to the log channel for a zero-computation post is not a violation of Principle V, which targets phase computation records and config mutations. | ✅ PASS |
| VI — Simplicity & Focused Scope | Change is additive and strictly bounded; no new commands, no new entities | ✅ PASS |
| VII — Output Channel Discipline | Mystery notice posts to the per-division forecast channel only — one of the two permitted categories. No role tag. No unsolicited channel usage. | ✅ PASS |

**Constitution Check result: PASS — no violations, no Complexity Tracking entries required.**

*Re-check post-design*: All principles confirmed. No violations.

## Scope

| File | Change |
|------|--------|
| `src/utils/message_builder.py` | Add `mystery_notice_message() -> str` (FR-004) |
| `src/services/mystery_notice_service.py` | **New** — `run_mystery_notice(round_id, bot)` coroutine (FR-005) |
| `src/services/scheduler_service.py` | Add `_mystery_notice_job`, `register_mystery_notice_callback`; update `schedule_round` and `cancel_round` (FR-001, FR-002, FR-006) |
| `src/services/amendment_service.py` | Update amendment-to-MYSTERY path to call `cancel_round` + conditionally `schedule_round` (FR-009) |
| `src/bot.py` | Register mystery notice callback in `on_ready` (FR-007) |
| `tests/unit/test_mystery_notice.py` | **New** — unit tests for all NFR-003 cases |

## Project Structure

### Documentation (this feature)

```text
specs/006-mystery-round-notice/
├── plan.md              ← this file
├── spec.md              ← feature specification
└── tasks.md             ← Phase 2 output (/speckit.tasks — NOT created here)
```

No `research.md`, `data-model.md`, `quickstart.md`, or `contracts/` are created — this is a
narrow amendment with no new entities, no external interfaces, and no technology unknowns.

### Source Code additions/edits

```text
src/
├── services/
│   └── mystery_notice_service.py      ← new
├── services/scheduler_service.py      ← modify: mystery notice job support
├── services/amendment_service.py      ← modify: amendment-to-MYSTERY scheduling
├── utils/message_builder.py           ← modify: add mystery_notice_message()
└── bot.py                             ← modify: register mystery notice callback

tests/
└── unit/
    └── test_mystery_notice.py         ← new
```

All additions slot into the existing single-project layout with no structural change.
