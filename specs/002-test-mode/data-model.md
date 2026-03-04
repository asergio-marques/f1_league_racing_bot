# Data Model: Test Mode for System Verification

**Feature**: `002-test-mode`  
**Phase**: 1 — Design & Contracts  
**Date**: 2026-03-04

---

## Schema Changes

### `server_configs` table — one new column

```sql
-- Migration: 002_test_mode.sql
ALTER TABLE server_configs ADD COLUMN test_mode_active INTEGER NOT NULL DEFAULT 0;
```

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `test_mode_active` | `INTEGER` | `0` | Boolean flag: `1` = test mode active, `0` = inactive |

No other tables are modified. The `phase_results`, `rounds`, `sessions`, and `divisions`
tables that already exist provide all data needed to compute the phase advancement queue.

---

## Updated Model: `ServerConfig`

The `ServerConfig` dataclass gains one field:

```python
@dataclass
class ServerConfig:
    server_id: int
    interaction_role_id: int
    interaction_channel_id: int
    log_channel_id: int
    test_mode_active: bool = False   # ← new
```

The `get_server_config` function in `config_service.py` must be updated to read this column.
The `save_server_config` function must be updated to persist it.

---

## Conceptual Entity: Phase Queue Entry

Not persisted — computed on demand by `test_mode_service.get_next_pending_phase()`.

| Field | Type | Description |
|-------|------|-------------|
| `division_id` | `int` | Division this phase belongs to |
| `round_id` | `int` | Round this phase belongs to |
| `phase_number` | `int` | 1, 2, or 3 |
| `scheduled_at` | `datetime` | The round's original scheduled start time (used for ordering) |
| `division_name` | `str` | Human-readable label for review output |
| `track_name` | `str` | Human-readable label for review output |

### Ordering rule (SQL)

```sql
SELECT
    r.id           AS round_id,
    r.division_id,
    r.scheduled_at,
    r.track_name,
    r.phase1_done,
    r.phase2_done,
    r.phase3_done,
    d.name         AS division_name
FROM rounds r
JOIN divisions d ON d.id = r.division_id
JOIN seasons s   ON s.id = d.season_id
WHERE s.server_id = ?
  AND s.status    = 'ACTIVE'
  AND r.format   != 'MYSTERY'
ORDER BY r.scheduled_at ASC, d.id ASC;
```

Phase entries are then synthesised per round in Python as:

1. If `phase1_done = 0` → first pending phase is `(round_id, 1)`
2. Else if `phase2_done = 0` → first pending phase is `(round_id, 2)`
3. Else if `phase3_done = 0` → first pending phase is `(round_id, 3)`
4. Else → round fully complete, skip

The first qualifying entry across the ordered result set is "the next pending phase".

---

## State Transitions

```
test_mode_active = 0  ──[/test-mode toggle]──►  test_mode_active = 1
test_mode_active = 1  ──[/test-mode toggle]──►  test_mode_active = 0
```

When transitioning `0 → 1`: no other state changes. The scheduler continues running.  
When transitioning `1 → 0`: no rollback of already-executed phases. Pending phases revert to
scheduler control.

---

## Validation Rules

| Rule | Enforcement |
|------|-------------|
| `test_mode_active` must be `0` or `1` | Python `bool` coercion on read; `int(bool_val)` on write |
| `advance` and `review` commands require `test_mode_active = 1` | Service-layer guard; silent ignore if `0` |
| Cannot advance past the last phase of the last round | Service returns `None`; cog posts ephemeral "all phases complete" message |
| Cannot advance a Mystery round phase | Query excludes `format = 'MYSTERY'` |
| Cannot advance if no active season exists | Query filters `status = 'ACTIVE'`; service returns `None` |
