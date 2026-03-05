# Data Model: Forecast Channel Message Cleanup

**Feature**: `007-forecast-msg-cleanup`  
**Phase**: 1 — Design  
**Date**: 2026-03-04

---

## New Entity: Forecast Message Record

Represents the Discord message ID of a phase forecast currently posted in a division's
weather forecast channel. At most one record exists per `(round_id, division_id,
phase_number)` combination at any time. Records are cleared after the corresponding
deletion is attempted (successful, not-found, or permission-denied).

### Table: `forecast_messages`

```sql
CREATE TABLE IF NOT EXISTS forecast_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id     INTEGER NOT NULL REFERENCES rounds(id),
    division_id  INTEGER NOT NULL REFERENCES divisions(id),
    phase_number INTEGER NOT NULL CHECK (phase_number IN (1, 2, 3)),
    message_id   INTEGER NOT NULL,   -- Discord message snowflake
    posted_at    TEXT    NOT NULL    -- ISO-8601 UTC timestamp
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_forecast_messages_round_div_phase
    ON forecast_messages(round_id, division_id, phase_number);
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | `INTEGER PK` | Surrogate key |
| `round_id` | `INTEGER FK → rounds.id` | The round this forecast belongs to |
| `division_id` | `INTEGER FK → divisions.id` | The division this forecast was posted for |
| `phase_number` | `INTEGER (1\|2\|3)` | Which phase posted the message |
| `message_id` | `INTEGER` | Discord message snowflake, stored as `INTEGER` consistent with all other Discord ID columns in this schema |
| `posted_at` | `TEXT` | ISO-8601 UTC timestamp recorded when the message was posted |

### Constraints

- `UNIQUE (round_id, division_id, phase_number)` — enforced via unique index; an
  `INSERT OR REPLACE` (or `DELETE` then `INSERT`) is used when a phase is re-run after
  amendment invalidation.
- No record for Mystery rounds — the application enforces FR-012 by never inserting for
  Mystery rounds.

### Lifecycle

```
Phase N posts message
        │
        ▼
INSERT INTO forecast_messages (round_id, division_id, phase_number, message_id, posted_at)

        │  (Phase N+1 fires, or 24h expiry fires, or amendment invalidates)
        ▼
Attempt DELETE message via Discord API
        │
        ▼
DELETE FROM forecast_messages WHERE round_id=? AND division_id=? AND phase_number=?
```

The row is removed after the deletion attempt regardless of whether the Discord API call
succeeded, failed with NotFound, or failed with Forbidden (FR-010).

**Test mode exception** (FR-014/FR-015): when test mode is active for a server, the
deletion step is skipped entirely. The row stays in `forecast_messages`. When test mode is
disabled, `flush_pending_deletions` queries all rows belonging to that server via a
three-table JOIN and processes them in one pass:

```sql
SELECT fm.id, fm.round_id, fm.division_id, fm.phase_number, fm.message_id,
       d.forecast_channel_id
FROM forecast_messages fm
JOIN rounds   r ON r.id  = fm.round_id
JOIN divisions d ON d.id = fm.division_id
JOIN seasons   s ON s.id = d.season_id
WHERE s.server_id = ?
```

The `forecast_messages` table itself does not store `server_id` — it is derived via the JOIN,
consistent with the rest of the schema's normalisation approach.

---

## Affected Entities (no schema changes)

| Entity | Table | Change |
|--------|-------|--------|
| Round | `rounds` | None — `phase1_done` / `phase2_done` / `phase3_done` flags continue unchanged |
| Phase result | `phase_results` | None — INVALIDATED status semantics unchanged |
| Session | `sessions` | None |

---

## Migration File

**`src/db/migrations/004_forecast_messages.sql`** — applied automatically by
`run_migrations()` on bot startup. No destructive changes to existing tables.

### Validation rules

- `phase_number` is constrained to `{1, 2, 3}` at the DB level via `CHECK`.
- `message_id` is `NOT NULL`; a record is only inserted after a successful `send()`.
- The unique constraint prevents duplicate records if a phase fires more than once
  (the application guards with `phaseN_done` flags, but a belt-and-suspenders DB
  constraint avoids silent double-insertion).
