# Research: Test Mode for System Verification

**Feature**: `002-test-mode`  
**Phase**: 0 — Outline & Research  
**Date**: 2026-03-04

---

## 1. Persisting Test Mode State Across Restarts

**Decision**: Store `test_mode_active INTEGER DEFAULT 0` as a new column in the existing
`server_configs` table, applied via DB migration `002_test_mode.sql`.

**Rationale**: `server_configs` already represents the per-server operational state
(interaction role, channels). Test mode is an operational flag, not a season-level concern.
Placing it here avoids a new table, keeps the data shape minimal, and is loaded at bot
startup along with all other config — restoring the state with zero extra work.

**Alternatives considered**:
- *New `test_mode` table*: Adds a join and a migration table row for a single boolean.
  Over-engineered for a scalar flag.
- *Flat file / env variable*: Not atomic with DB transactions; requires file I/O alongside
  SQLite; violates the "durable storage" rule from the constitution's Data & State Management
  section.
- *In-memory flag only*: Does not survive restart — explicitly against FR-004.

---

## 2. Computing the Phase Advancement Queue

**Decision**: The "phase advancement queue" is not a persisted table. It is computed on demand
by querying rounds and sessions joined against `phase_results`, producing an ordered list of
`(division_id, round_id, phase_number)` tuples that have not yet been executed.

Ordering rule:
1. Primary sort: `rounds.scheduled_at ASC` — the earliest real-world trigger time first.
2. Secondary sort: phase number ASC (Phase 1 before Phase 2 before Phase 3 for the same round).
3. Tertiary sort: `divisions.id ASC` (insertion order) — tie-break when rounds in different
   divisions share the same `scheduled_at`.

A phase is considered "pending" when no `phase_results` row with `status = 'done'` exists for
that `(round_id, phase_number)` pair. Phase 3 depends on Phase 2 which depends on Phase 1 —
this ordering guarantee is provided by the sort above combined with the one-phase-per-command
rule (FR: advance triggers one phase per invocation).

Mystery rounds (`rounds.format = 'MYSTERY'`) are excluded from the queue entirely by a `WHERE`
clause filter.

**Rationale**: A computed queue ensures it is always derived from the ground-truth DB state.
There is no risk of the queue becoming stale or out of sync after an amendment, restart, or
scheduler-triggered execution. The sort criteria precisely match the semantics in FR-006.

**Alternatives considered**:
- *Persisted queue table*: Requires maintaining queue rows on every amendment, scheduler
  execution, and restart. Adds synchronisation complexity with no benefit given the data
  that drives the queue (rounds, phase_results) is already in the DB.

---

## 3. Executing a Phase from the Advance Command

**Decision**: The advance command calls `run_phase1(round_id, bot)`, `run_phase2(round_id, bot)`,
or `run_phase3(round_id, bot)` directly — the same async functions called by the scheduler.

**Rationale**: These functions already handle the complete execution lifecycle: DB reads,
random draws, logging, posting to forecast and log channels, setting `phaseN_done` flags,
writing `PhaseResult` rows, and handling idempotency (they check `phase1_done` etc. before
acting). Reusing them ensures SC-002 ("outputs indistinguishable from scheduled operation")
is satisfied by construction rather than by code duplication.

**Alternatives considered**:
- *New "test phase" service methods*: Duplication of identical logic — maintainability
  liability with no benefit.
- *Injecting a fake scheduler trigger*: More complex than a direct call; adds scheduler
  state side-effects.

---

## 4. Access Control for Test Mode Commands

**Decision**: All three test mode commands (`/test-mode toggle`, `/test-mode advance`,
`/test-mode review`) are decorated with the existing `@channel_guard` decorator. The `advance`
and `review` commands additionally guard on `test_mode_active` in the service layer (return
early silently if flag is off — consistent with FR-013 "silently ignored when not active").

**Rationale**: `@channel_guard` already handles interaction-role check + interaction-channel
check with silent ignore on mismatch, satisfying Constitution Principle I. No new guard
decorator is needed. The test mode active check at the service layer is a simple boolean guard,
not a permission layer, so it does not belong in the decorator.

---

## 5. Contracts (Slash Command Schemas)

**Decision**: No `contracts/` directory is created for this feature.

**Rationale**: The project is a self-contained Discord bot. Slash command schemas are
registered dynamically with Discord via discord.py's `app_commands` tree and are not consumed
by external systems. Feature 001 established no contracts/ precedent. The constitution's
scope principle (VI) confirms text-only, single-server operation — no external API consumers.

---

## 6. Ordering of the Review Command Output

**Decision**: The review command output is grouped by division (in configured order), then
by round (in `scheduled_at` order within each division). For each round, phase completion
status is shown as three indicators: `P1: ✅/⏳`, `P2: ✅/⏳`, `P3: ✅/⏳` (or `N/A` for
Mystery rounds). The message is sent as an ephemeral response to the invoking user.

**Rationale**: Division-first grouping mirrors how the season configuration is mentally
structured by the user. Ephemeral response keeps the review out of the public channel per
Constitution Principle VII (review is not a weather output — it must not appear in forecast
or log channels).

---

## Summary of All Resolved Decisions

| Topic | Decision |
|-------|----------|
| State persistence | `test_mode_active` column in `server_configs` via migration 002 |
| Queue computation | On-demand SQL query, sorted by `scheduled_at` → phase number → division id |
| Phase execution | Call existing `run_phase1/2/3` functions directly |
| Access control | `@channel_guard` + service-layer active check |
| Contracts | None — internal Discord bot |
| Review output | Ephemeral, division-grouped, per-round phase status indicators |
