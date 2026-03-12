# Implementation Plan: Message Retry Queue

**Branch**: `017-msg-retry-queue` | **Date**: 2026-03-12 | **Spec**: [spec.md](spec.md)
**Input**: When a channel send fails (e.g., 503 upstream overflow), persist the message to the database and retry delivery every 5 minutes until it succeeds.

---

## Summary

Add a persistent message retry queue to prevent failed Discord channel posts (transient HTTP errors, upstream disconnects) from permanently dropping weather phase outputs or log entries. On any send failure in `OutputRouter._send`, the message is written to a new `pending_messages` table. A `discord.ext.tasks` loop in `RetryCog` processes pending entries every 5 minutes, delivering them and posting a log-channel notification on success.

No new user-facing commands. No schema changes to existing tables. One new migration, one new model, one new service, one new cog, and targeted changes to `OutputRouter` and `bot.py`.

---

## Technical Context

| Field | Value |
|-------|-------|
| Language / Version | Python 3.11 |
| Framework | discord.py / py-cord, aiosqlite |
| Storage | SQLite — new `pending_messages` table (migration `015_pending_messages.sql`) |
| Testing | pytest + aiosqlite in-memory DB |
| Target Platform | Discord bot (background task, no guild interaction commands) |
| Project Type | Discord bot — cog / service / model architecture |
| Performance Goals | Background infrastructure; no throughput requirement |
| Constraints | Retry worker MUST NOT enqueue new entries on its own delivery failures (FR-009); entries MUST survive bot restart (handled by DB persistence + `on_ready` task start) |
| Scale / Scope | Per-server; typically 0–5 pending messages at any time |

---

## Constitution Check

*Pre-design gate (passed). Re-evaluated after Phase 1 design — still passing.*

| Principle | Rule | Assessment |
|-----------|------|------------|
| I — Two-tier access | No new commands introduced; retry is purely background infrastructure | PASS |
| II — Multi-division isolation | `pending_messages` stores `server_id` and `channel_id` from existing per-division data; no cross-division reads | PASS |
| IV — Three-phase pipeline | Feature directly supports pipeline reliability: missed forecast posts are retried until delivered | PASS (strengthens) |
| V — Observability / Audit | Retry outcomes (successful delivery, stuck-entry warning) posted to log channel (FR-007, FR-008) | PASS |
| VI — Incremental scope | Core infrastructure improvement; no new capability domain; within ratified weather + operational scope | PASS |
| VII — Output channel discipline | Retried messages post only to their original designated channel (forecast or log); no new channel categories introduced | PASS |
| IX — Team & Division structural integrity | No team or division data touched | PASS |
| X — Modular architecture | Retry queue is always-on infrastructure, not a module feature; no module gate needed | PASS |

No gate violations detected.

---

## Project Structure

```
specs/017-msg-retry-queue/
├── plan.md           <- this file
├── research.md       <- Phase 0 decisions
├── data-model.md     <- Phase 1 data model
├── quickstart.md     <- Phase 1 quickstart
└── tasks.md          <- Phase 2 (/speckit.tasks — not yet created)

src/
├── db/
│   └── migrations/
│       └── 015_pending_messages.sql   <- NEW
├── models/
│   └── pending_message.py             <- NEW: PendingMessage dataclass
├── services/
│   └── retry_service.py               <- NEW: enqueue / attempt_delivery / mark_*
├── cogs/
│   └── retry_cog.py                   <- NEW: RetryCog with 5-min tasks.loop
├── utils/
│   └── output_router.py               <- MODIFIED: enqueue on failure
└── bot.py                             <- MODIFIED: instantiate RetryService, add RetryCog

tests/
├── unit/
│   └── test_retry_service.py          <- NEW
└── integration/
    └── test_retry_worker.py           <- NEW
```

**Structure Decision**: Single-project layout (existing pattern). No new directories; new files follow the established `models/` → `services/` → `cogs/` → `utils/` hierarchy.

---

## Phase 0 — Research & Decisions

See [research.md](research.md) for full rationale. Summary:

| # | Decision |
|---|----------|
| 1 | Background worker: `discord.ext.tasks` loop (not APScheduler) — interval needs no DB persistence |
| 2 | Worker lives in new `RetryCog`, not `scheduler_service.py` |
| 3 | `RetryService` is a standalone service in `src/services/retry_service.py` |
| 4 | `OutputRouter` gains optional `retry_service` reference; `_send` calls `enqueue` on failure |
| 5 | `server_id` stored in `pending_messages` to enable log-channel notifications |
| 6 | Retry worker calls `channel.send()` directly (bypasses `OutputRouter`) — enforces FR-009 |
| 7 | Warning threshold is a module-level constant (`RETRY_WARN_THRESHOLD = 12`), not a DB config |

---

## Phase 1 — Data Model

See [data-model.md](data-model.md) for the complete schema. One new table:

```sql
-- 015_pending_messages.sql
CREATE TABLE IF NOT EXISTS pending_messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id         INTEGER NOT NULL,
    channel_id        INTEGER NOT NULL,
    content           TEXT    NOT NULL,
    failure_reason    TEXT    NOT NULL,
    enqueued_at       TEXT    NOT NULL,
    retry_count       INTEGER NOT NULL DEFAULT 0,
    last_attempted_at TEXT
);
```

No foreign keys on `server_id` / `channel_id` (Discord snowflakes, not DB references). No changes to any existing table.

---

## Phase 1 — New File: `src/models/pending_message.py`

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

## Phase 1 — New File: `src/services/retry_service.py`

### Public API

```python
RETRY_WARN_THRESHOLD: int = 12  # ~1 hour at 5-min intervals

async def enqueue(
    db_path: str,
    server_id: int,
    channel_id: int,
    content: str,
    failure_reason: str,
) -> None:
    """Persist a failed message to pending_messages."""

async def get_all_pending(db_path: str) -> list[PendingMessage]:
    """Return all rows from pending_messages, ordered by enqueued_at ASC."""

async def mark_delivered(db_path: str, entry_id: int) -> None:
    """Delete the pending_messages row — message successfully delivered."""

async def mark_failed(db_path: str, entry_id: int) -> None:
    """Increment retry_count and set last_attempted_at = now for entry_id."""

async def attempt_delivery(
    entry: PendingMessage,
    bot: "Bot",
) -> bool:
    """Attempt to post entry.content to entry.channel_id via the Discord API.

    Returns True on success (all chunks delivered), False on any failure.
    Never raises. Never calls enqueue() — satisfies FR-009.
    On success: calls mark_delivered and posts a log notification.
    On failure: calls mark_failed. If entry.retry_count >= RETRY_WARN_THRESHOLD,
    posts a warning to the log channel before incrementing.
    """
```

### Key implementation notes

- `attempt_delivery` resolves the channel via `bot.get_channel` then `bot.fetch_channel` (same logic as `OutputRouter._send`).
- Chunking is handled by importing `_chunk_message` from `utils.output_router` (or duplicating the function — prefer import to keep it DRY).
- On successful delivery: posts to `bot.output_router.post_log(entry.server_id, ...)`. This log notification is best-effort; if `post_log` itself fails it surfaces in application logs at WARNING level but does NOT re-enqueue the notification.
- On `retry_count >= RETRY_WARN_THRESHOLD` before attempting: posts a warning to `bot.output_router.post_log` first, then attempts delivery. The warning fires every cycle once the threshold is crossed (not just once), ensuring continuous visibility.

---

## Phase 1 — New File: `src/cogs/retry_cog.py`

```python
from discord.ext import commands, tasks
from services.retry_service import attempt_delivery, get_all_pending

class RetryCog(commands.Cog):
    def __init__(self, bot):
        self._bot = bot
        self.retry_loop.start()

    def cog_unload(self):
        self.retry_loop.cancel()

    @tasks.loop(minutes=5)
    async def retry_loop(self):
        """Process all pending messages every 5 minutes."""
        pending = await get_all_pending(self._bot.db_path)
        for entry in pending:
            await attempt_delivery(entry, self._bot)

    @retry_loop.before_loop
    async def before_retry_loop(self):
        await self._bot.wait_until_ready()
```

The `before_loop` hook ensures the first cycle runs only after `on_ready` completes (DB migrations applied, services wired).

---

## Phase 1 — Modified: `src/utils/output_router.py`

### Constructor change

```python
def __init__(self, bot: "Bot", retry_service_db_path: str | None = None) -> None:
    self._bot = bot
    self._retry_db_path: str | None = retry_service_db_path
```

`retry_service_db_path` is the same `DB_PATH` used by all other services. Passing `None` disables enqueuing (safe default; always set in production via `bot.py`).

### `_send` change

Add `server_id: int` parameter and `_skip_enqueue: bool = False` keyword argument. On `discord.HTTPException` or `discord.Forbidden`:

```python
if self._retry_db_path and not _skip_enqueue:
    from services.retry_service import enqueue as _enqueue
    await _enqueue(
        self._retry_db_path, server_id, channel_id,
        content, str(exc),
    )
```

### Callers updated

- `post_forecast`: passes `server_id` sourced from a `server_id` parameter added to the call (obtainable from `division` context — the `phase*_service` files already have `server_id` in scope).
- `post_log`: already has `server_id`; passes it through.

The signature of `post_forecast` gains `server_id: int` as a parameter. All existing call sites (in `phase1_service.py`, `phase2_service.py`, `phase3_service.py`, `mystery_notice_service.py`, `amendment_service.py`) already have `server_id` in scope and will be updated to pass it.

---

## Phase 1 — Modified: `src/bot.py`

Two changes:

1. `OutputRouter` is constructed with `DB_PATH`:
   ```python
   bot.output_router = OutputRouter(bot, retry_service_db_path=DB_PATH)
   ```

2. `RetryCog` is loaded after other cogs:
   ```python
   from cogs.retry_cog import RetryCog
   await bot.add_cog(RetryCog(bot))
   ```

---

## Phase 1 — `post_forecast` call site audit

Every caller of `output_router.post_forecast` must be updated to pass `server_id`. Affected files:

| File | Variable holding server_id |
|------|---------------------------|
| `src/services/phase1_service.py` | `row["server_id"]` (already in scope, line ~30) |
| `src/services/phase2_service.py` | `row["server_id"]` (pattern matches phase1) |
| `src/services/phase3_service.py` | `row["server_id"]` (pattern matches phase1) |
| `src/services/mystery_notice_service.py` | `row["server_id"]` (same DB query pattern) |
| `src/services/amendment_service.py` | `server_id` passed in as parameter |

`post_log` callers are already passing `server_id` and require no change beyond the internal wiring.

---

## Constitution Check (post-design re-evaluation)

All gates remain PASS. No new channels, no new commands, no cross-division reads, no module state changes. The audit trail (FR-007/FR-008) routes through the existing `post_log` → calculation log channel path, consistent with Principle V.
