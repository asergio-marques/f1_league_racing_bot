# Data Model: Message Retry Queue (017)

## Schema Change

One new table. Migration file: `015_pending_messages.sql`.

---

## New Table: `pending_messages`

Stores every channel message that failed to post, pending retry.

```sql
CREATE TABLE IF NOT EXISTS pending_messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id        INTEGER NOT NULL,
    channel_id       INTEGER NOT NULL,
    content          TEXT    NOT NULL,
    failure_reason   TEXT    NOT NULL,
    enqueued_at      TEXT    NOT NULL,   -- ISO-8601 UTC
    retry_count      INTEGER NOT NULL DEFAULT 0,
    last_attempted_at TEXT             -- ISO-8601 UTC, NULL until first retry
);
```

### Column notes

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | Autoincremented; used by retry worker to reference specific entries |
| `server_id` | INTEGER | Discord guild snowflake; used to look up the log channel for delivery notifications (FR-007) |
| `channel_id` | INTEGER | Target Discord channel snowflake; the original destination of the failed message |
| `content` | TEXT | Full message text at time of failure; may contain multiple chunks but stored as the complete pre-chunked string (re-chunked on delivery) |
| `failure_reason` | TEXT | Human-readable string from the caught exception (`str(exc)`) |
| `enqueued_at` | TEXT | UTC ISO-8601 timestamp of first failure |
| `retry_count` | INTEGER | Incremented by 1 on each failed retry attempt; 0 on initial enqueue |
| `last_attempted_at` | TEXT | NULL until first retry attempt; updated each cycle |

### No foreign key constraints

`server_id` and `channel_id` are Discord snowflakes, not references to rows in this database. Foreign key constraints would prevent retrying messages for servers/channels that lose their DB row (e.g., if a `server_configs` row were to be deleted). The retry worker handles "channel not found" gracefully at delivery time via the `retry_count` / warning path.

---

## Model: `PendingMessage`

**File**: `src/models/pending_message.py`

```python
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime

@dataclass
class PendingMessage:
    id: int
    server_id: int
    channel_id: int
    content: str
    failure_reason: str
    enqueued_at: datetime
    retry_count: int
    last_attempted_at: datetime | None
```

---

## State transitions for a `pending_messages` row

```
[row created by enqueue()]
        |
        v
   retry_count = 0
   last_attempted_at = NULL
        |
    [retry worker fires]
        |
       / \
      /   \
  success  failure
     |        |
     v        v
  row DELETED  retry_count += 1
               last_attempted_at = now
               (row stays in table)
                    |
              [if retry_count > WARN_THRESHOLD]
                    |
                    v
              WARNING posted to log channel
              (row stays; retries continue)
```

---

## Existing tables: no changes

| Table | Change |
|-------|--------|
| `server_configs` | None |
| `divisions` | None |
| `rounds` | None |
| `phase_results` | None |
| `forecast_messages` | None |
| `audit_entries` | None — delivery notifications go to the log *channel*, not to `audit_entries` (delivery is infrastructure state, not a configuration mutation) |
| All others | None |

---

## Migration summary

| File | Status |
|------|--------|
| `015_pending_messages.sql` | **NEW** — creates `pending_messages` table |
| All earlier migrations (001–014) | Unchanged |
