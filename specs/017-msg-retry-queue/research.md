# Research: Message Retry Queue (017)

All unknowns from the Technical Context were resolved by direct codebase inspection. No external research was required.

---

## Decision 1: Background worker — `discord.ext.tasks` loop, not APScheduler

**Decision**: Implement the 5-minute retry loop as a `discord.ext.tasks.loop` inside a new `RetryCog`.

**Rationale**: The retry interval itself does not need to survive bot restarts — when the bot comes back online it simply starts a new 5-minute timer and immediately processes whatever rows exist in `pending_messages`. The interval cadence is not a scheduled *event*; it is an *invariant property* of the running process. APScheduler with `SQLAlchemyJobStore` is reserved for one-shot future events that must fire at a specific UTC timestamp (weather phases, season-end). Using it for a perpetual interval would add a persistent job row that is never removed and has no date target, which works against the existing job-management patterns in `scheduler_service.py`. `discord.ext.tasks` is simpler, requires zero DB footprint for the worker itself, and is already the idiomatic choice for periodic Discord.py background work.

**Alternatives considered**:
- APScheduler `IntervalTrigger` persisted in SQLAlchemyJobStore: rejected — adds a permanent stale job to the scheduler DB; breaks the clean one-shot semantics of the existing job store.
- `asyncio.create_task` with a bare `while True` / `asyncio.sleep` loop: rejected — less structured than `discord.ext.tasks`, no built-in reconnect semantics, harder to start/stop cleanly.

---

## Decision 2: Retry worker lives in a new `RetryCog`, not in `scheduler_service.py`

**Decision**: Create `src/cogs/retry_cog.py` holding the `RetryCog` cog. The cog owns the `tasks.loop`. It is added to the bot in `bot.py` alongside the existing cogs.

**Rationale**: `scheduler_service.py` manages APScheduler date-trigger jobs and is consciously separate from cog logic. Adding a periodic loop there would violate that single-responsibility boundary. The existing cog pattern (one cog per concern, loaded in `on_ready`) is the correct home for a background task that is always active for the lifetime of the bot.

**Alternatives considered**:
- Inline the loop in `bot.py` `on_ready`: rejected — `on_ready` is already long and mixes startup wiring with behaviour; a cog is more testable and isolatable.
- Add to `SchedulerService`: rejected — breaks existing single-responsibility boundary.

---

## Decision 3: `RetryService` is a standalone service class in `src/services/retry_service.py`

**Decision**: Create `src/services/retry_service.py` with a `RetryService` class following the same pattern as `ForecastCleanupService` (module-level async functions that receive `db_path`). Public API: `enqueue`, `get_all_pending`, `mark_delivered`, `mark_failed`, `attempt_delivery`.

**Rationale**: All data access in this project uses `get_connection(db_path)` context managers. A standalone service keeps the retry DB logic co-located, testable in isolation, and consistent with the existing service layer.

**Alternatives considered**:
- Inline DB calls directly in `RetryCog`: rejected — mixes data access with Discord event handling; untestable without a live bot.
- Embed in `OutputRouter`: rejected — `OutputRouter` is a utility (no DB path); giving it persistent state would break its lightweight design.

---

## Decision 4: `OutputRouter` receives a `RetryService` reference and enqueues on `_send` failure

**Decision**: `OutputRouter.__init__` gains an optional `retry_service: RetryService | None` parameter. When `_send` catches a `discord.HTTPException` or `discord.Forbidden`, it calls `await self._retry_service.enqueue(server_id, channel_id, content, failure_reason)` — provided `retry_service` is set and the `skip_enqueue` flag is `False`.

A `_skip_enqueue: bool = False` keyword-only argument is added to `_send`. The retry worker calls `_send(..., _skip_enqueue=True)` to satisfy FR-009 (no self-referential loops). In practice, `attempt_delivery` in `RetryService` calls the channel Discord API directly (it doesn't route through `OutputRouter`), so the flag is an extra safety guard.

**Rationale**: `post_forecast` and `post_log` both funnel through `_send`. Integrating enqueue at that single point means all future channel-write paths are automatically covered.

**Alternatives considered**:
- Enqueue at the `post_forecast` / `post_log` call sites: rejected — requires modifying all call sites; fails to cover any future `_send` callers.
- Pass a callback function instead of a service reference: rejected — more complex, hides the dependency, harder to type.

---

## Decision 5: `server_id` stored in `pending_messages` for log-channel notification

**Decision**: The `pending_messages` table includes a `server_id INTEGER NOT NULL` column. `OutputRouter._send` already receives `channel_id`; the caller context (either `post_forecast` receiving a `Division` with its `server_id`, or `post_log` which is passed `server_id` directly) knows the server. Since `_send` is called from both, `server_id` is passed down as an additional parameter alongside `channel_id`.

**Rationale**: The retry worker needs to know which server's log channel to post the delivery notification to (FR-007). Storing `server_id` at enqueue time is simpler and more reliable than looking it up from the channel at delivery time (which could fail if the channel is in a guild the bot has lost).

**Alternatives considered**:
- Look up `server_id` from the channel object at delivery time: rejected — channel lookup can fail, introducing a second failure point; also requires an extra `get_server_config` call every retry cycle per entry.
- Omit `server_id`; skip the log notification: rejected — violates FR-007 and Constitution Principle V.

---

## Decision 6: Retry attempt delivery bypasses `OutputRouter` entirely

**Decision**: `RetryService.attempt_delivery` resolves the target channel via `bot.get_channel` / `bot.fetch_channel`, then calls `channel.send()` directly (using the same chunking logic from `_chunk_message`, co-located with `output_router.py`). On success it calls `mark_delivered` and posts a log notification via `bot.output_router.post_log` (without triggering enqueue, because `post_log` itself only enqueues if `output_router._retry_service` is available — and the notification is a best-effort log, not a critical payload). On failure it calls `mark_failed`.

**Rationale**: FR-009 prohibits the retry worker from enqueuing new entries on its own delivery failures. The simplest enforcement is to bypass the enqueue path entirely. Calling `channel.send()` directly gives the retry worker full control and avoids any accidental recursion.

**Alternatives considered**:
- Route through `OutputRouter._send(..., _skip_enqueue=True)`: technically safe but creates a circular dependency (`retry_service` → `output_router` → `retry_service`). Rejected.
- Allow the log notification from `attempt_delivery` to also enqueue on failure: rejected — would create an unbounded secondary retry queue for transient log-channel failures. A failed "retry success" notification is logged to application log at WARNING level and dropped.

---

## Decision 7: Warning threshold is a module-level constant, not a DB config field

**Decision**: `RETRY_WARN_THRESHOLD: int = 12` in `retry_service.py`. No admin command to change it; it is not stored in the database.

**Rationale**: The spec states "configurable threshold (default: 12 attempts)" in the context of a code-level default, not a per-server database setting. No operator has requested runtime tunability. Adding a config field would require a DB column, migration, and admin command — all out of scope for this bugfix. A named constant in the source is visible, changeable on redeploy, and sufficient.

**Alternatives considered**:
- Per-server DB config field: rejected — over-engineered for a bugfix; adds scope.
- Environment variable: rejected — still requires a deployment change; no advantage over a constant.
