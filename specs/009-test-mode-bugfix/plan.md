# Implementation Plan: Test Mode Bug Fixes

**Branch**: `009-test-mode-bugfix` | **Date**: 2026-03-05 | **Spec**: [spec.md](spec.md)
**Input**: Bug reports from live test-mode usage; fixes to existing feature `002-test-mode`.

**Reuses plan**: See [`specs/001-league-weather-bot/plan.md`](../001-league-weather-bot/plan.md)
for the full tech stack, structural decisions, and base data model. Everything in that plan
applies unchanged. Only the files listed in the Scope table below require edits.

## Summary

Three bugs in the `002-test-mode` implementation are corrected:

1. **Mystery round "next round" leak** — `/season-status` searched all rounds (including
   Mystery) for the next incomplete round; since Mystery rounds never have phases set to
   `True`, they permanently appeared as "next round" even after all non-Mystery rounds were
   fully processed. Fix: exclude `MYSTERY` format from the pending-round predicate.

2. **Season not ending after test-mode advance exhausts all phases** — When
   `/test-mode advance` was called after all non-Mystery phases were done,
   `get_next_pending_phase` returned `None` and the command returned "nothing to advance"
   without checking whether the season was still live. If the Phase-3 runner's internal
   `execute_season_end` call failed (e.g. timing race, past-dates fast-path, Discord API
   transient error), the season remained stuck as `ACTIVE`. Fix: when the queue is empty
   and an active season still exists, call `execute_season_end` as a safety net.

3. **Test-mode commands gated to server admins instead of interaction-role holders** —
   The `test_mode` `app_commands.Group` had no `default_permissions` value,
   leaving Discord to apply any previously cached per-server restriction (which could be
   `manage_guild`). Additionally, `guild_only` was not set, allowing the group to appear
   usable in DMs. Fix: add `guild_only=True` and `default_permissions=None` to the
   Group definition so Discord resets permissions on the next tree sync, leaving
   `channel_guard` (interaction-role check) as the sole gate.

## Technical Context

**Language/Version**: Python 3.13.2 (targets 3.8+)
**Primary Dependencies**: discord.py 2.7.1, aiosqlite ≥ 0.19, APScheduler ≥ 3.10
**Storage**: SQLite via aiosqlite — no schema changes
**Testing**: pytest 9.0.2 + pytest-asyncio (`asyncio_mode = auto`); `pythonpath = src`
**Target Platform**: Any host running Python 3.8+ with a Discord bot token
**Project Type**: Discord bot (event-driven, async)
**Performance Goals**: No new hot paths introduced
**Constraints**: No new slash commands; no schema migrations; no new dependencies
**Scale/Scope**: Two files modified; no new files created

## Constitution Check

| Principle | Requirement | Status |
|-----------|-------------|--------|
| I — Trusted Configuration Authority | Bug 3 fix restores correct Tier-1 enforcement: interaction-role check in `channel_guard` gates all three `/test-mode` subcommands; no admin-only escalation | ✅ PASS |
| II — Multi-Division Isolation | No cross-division logic touched | ✅ PASS |
| III — Resilient Schedule Management | Season-end safety net follows existing `execute_season_end` atomicity; no partial-update path introduced | ✅ PASS |
| IV — Three-Phase Weather Pipeline | Mystery round exclusion aligns with the spec: Mystery rounds must never have phases executed or reported as pending | ✅ PASS |
| V — Observability & Change Audit Trail | Season-end safety net posts the existing completion message to the log channel before clearing data; no silent mutations | ✅ PASS |
| VI — Simplicity & Focused Scope | All changes are minimal targeted corrections; no scope expansion | ✅ PASS |
| VII — Output Channel Discipline | No new output channels used; season-end posts to the configured log channel only | ✅ PASS |

**Constitution Check result: PASS — no violations, no Complexity Tracking entries required.**

## Scope

| File | Change |
|------|--------|
| `src/cogs/season_cog.py` | Add `r.format != RoundFormat.MYSTERY` guard to `next_round` generator in `season_status` |
| `src/cogs/test_mode_cog.py` | Replace bare `entry is None` early-return with season-end safety net; add `guild_only=True` + `default_permissions=None` to `test_mode` Group |
| `.specify/memory/constitution.md` | Add Sync Impact Report entry documenting the three bugs and fixes |

## Project Structure

### Documentation (this feature)

```text
specs/009-test-mode-bugfix/
├── plan.md    ← this file
├── spec.md    ← bug specifications
└── tasks.md   ← task list (/speckit.tasks output)
```

No `research.md`, `data-model.md`, `quickstart.md`, or `contracts/` — this is a
targeted bug-fix with no new data model, no new API surface, and no research phase.

### Source Code edits

```text
src/
├── cogs/
│   ├── season_cog.py        ← Bug 1 fix (next_round mystery exclusion)
│   └── test_mode_cog.py     ← Bug 2 fix (advance safety net) + Bug 3 fix (permissions)
.specify/
└── memory/
    └── constitution.md      ← Sync Impact Report entry
```
