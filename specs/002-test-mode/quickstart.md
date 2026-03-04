# Quickstart: Test Mode for System Verification

**Feature**: `002-test-mode`  
**Phase**: 1 — Design & Contracts  
**Date**: 2026-03-04

---

## Prerequisites

1. The bot has been initialised: `/bot-init` completed successfully (server config exists).
2. A season has been configured and approved via the existing `/season-setup` → `/season-review` → `/season-approve` flow.
3. You are a member of the configured interaction role and are posting in the configured interaction channel.

---

## Command Reference

### `/test-mode toggle`

Enables or disables test mode. The state persists across bot restarts.

**Usage**:
```
/test-mode toggle
```

**Response (mode just enabled)**:
> ✅ Test mode **enabled**. Use `/test-mode advance` to step through phases, or `/test-mode review` to inspect season status.

**Response (mode just disabled)**:
> ✅ Test mode **disabled**. The scheduler will resume normal operation for any remaining pending phases.

---

### `/test-mode advance`

Identifies the next pending phase across all divisions and rounds and executes it immediately.
Posts all outputs (weather forecast + calculation log) exactly as the scheduler would at the
real trigger time.

Only available while test mode is active. Triggers **one phase per invocation**.

**Usage**:
```
/test-mode advance
```

**Response (phase triggered)**:
> ⏩ Advancing Phase **1** for **Division A** — Round **3** at **Silverstone**. Outputs posted to the configured forecast and log channels.

**Response (no phases remain)**:
> ℹ️ All phases for all rounds and divisions have been executed. There is nothing left to advance.

**Response (test mode inactive)**:
> *(silently ignored — no response)*

---

### `/test-mode review`

Shows a structured summary of all configured rounds and their phase completion status.
Only available while test mode is active. Response is ephemeral (visible only to you).

**Usage**:
```
/test-mode review
```

**Example output**:
```
Season: 2026 Championship — ACTIVE

Division A
  Round 1 · Bahrain    · 2026-04-05  P1: ✅  P2: ✅  P3: ✅
  Round 2 · Imola      · 2026-04-12  P1: ✅  P2: ⏳  P3: ⏳
  Round 3 · Silverstone· 2026-04-19  P1: ⏳  P2: ⏳  P3: ⏳
  Round 4 · Mystery    · 2026-04-26  Phases N/A (Mystery Round)

Division B
  Round 1 · Bahrain    · 2026-04-05  P1: ✅  P2: ✅  P3: ✅
  Round 2 · Imola      · 2026-04-12  P1: ⏳  P2: ⏳  P3: ⏳
```

**Response (test mode inactive)**:
> *(silently ignored — no response)*

---

## End-to-End Test Walkthrough

The following sequence demonstrates a complete test of a season with two divisions and two
rounds each (12 total phases), from test mode activation to deactivation.

```
/test-mode toggle          → enabled

/test-mode review          → confirm all phases ⏳

/test-mode advance         → Phase 1, Div A, Round 1 (weather probability posted)
/test-mode advance         → Phase 1, Div B, Round 1
/test-mode advance         → Phase 2, Div A, Round 1 (session types posted)
/test-mode advance         → Phase 2, Div B, Round 1
/test-mode advance         → Phase 3, Div A, Round 1 (final weather posted)
/test-mode advance         → Phase 3, Div B, Round 1

/test-mode advance         → Phase 1, Div A, Round 2
/test-mode advance         → Phase 1, Div B, Round 2
/test-mode advance         → Phase 2, Div A, Round 2
/test-mode advance         → Phase 2, Div B, Round 2
/test-mode advance         → Phase 3, Div A, Round 2
/test-mode advance         → Phase 3, Div B, Round 2

/test-mode advance         → "All phases complete" message

/test-mode review          → confirm all phases ✅

/test-mode toggle          → disabled
```

> **Note on ordering**: when two divisions have the same `scheduled_at` (typical in a league
> where all divisions race the same week), Phase 1 for **both** divisions is queued before
> Phase 2 for either, because Phase 1's trigger time (T−5 days) is earlier than Phase 2's
> trigger time (T−2 days) — even if both rounds are on the same calendar date.

---

## Developer Notes

- **New migration**: `src/db/migrations/002_test_mode.sql` — runs automatically on bot startup.
- **New service**: `src/services/test_mode_service.py` — `toggle_test_mode`, `get_next_pending_phase`, `build_review_summary`.
- **New cog**: `src/cogs/test_mode_cog.py` — loads in `bot.py` alongside existing cogs.
- **Modified**: `src/models/server_config.py` — adds `test_mode_active: bool = False`.
- **Modified**: `src/services/config_service.py` — reads and writes `test_mode_active`.
- The real scheduler is **not paused** when test mode is enabled. If test mode is active and
  the scheduler triggers a phase for a round that has already been advanced manually, the phase
  service's built-in idempotency guard (`phaseN_done` flag check) silently skips it.
