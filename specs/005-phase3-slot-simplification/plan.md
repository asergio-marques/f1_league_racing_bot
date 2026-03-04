# Implementation Plan: Phase 3 Slot Simplification

**Branch**: `005-phase3-slot-simplification` | **Date**: 2026-03-04 | **Spec**: [specs/001-league-weather-bot/spec.md](../001-league-weather-bot/spec.md)  
**Input**: Amendment to FR-024 in `specs/001-league-weather-bot/spec.md`

## Summary

Narrow amendment to the Phase 3 output formatting rule. When all drawn slots for a session
are the exact same weather type, the output is simplified: the forecast channel shows only
the single type label; the calculation log channel shows the simplified label followed by the
full raw draw list in parentheses. Sessions with a single drawn slot are exempt. No schema
changes, no new entities, no new commands.

**Reuses plan**: See [`specs/001-league-weather-bot/plan.md`](../001-league-weather-bot/plan.md)
for full tech stack, structure decisions, constitution check, and data model. Everything in
that plan applies unchanged. Only the two files below require edits.

## Scope

| File | Change |
|------|--------|
| `src/utils/message_builder.py` | Add `format_slots_for_forecast` and `format_slots_for_log` helpers; update `phase3_message` to call the forecast helper |
| `src/services/phase3_service.py` | Add `slots_display` field (via log helper) to each `session_draws` entry before the log payload is serialised |
| `tests/unit/test_message_builder.py` | Unit tests for both new helpers (all-same, mixed, single-slot-exempt cases) |

## Constitution Check

All principles pass unchanged — this is a pure formatting change within Phase 3 output.
No new channels, no new commands, no schema mutations, no principle violations.
