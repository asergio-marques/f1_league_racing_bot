# Research: Forecast Channel Message Cleanup

**Feature**: `007-forecast-msg-cleanup`  
**Phase**: 0 — Outline & Research  
**Date**: 2026-03-04

All NEEDS CLARIFICATION items from the Technical Context are resolved below.

---

## R-001 — Capturing the Discord message ID after posting

**Question**: `OutputRouter.post_forecast` currently returns `None`. The phase services need
the Discord message ID immediately after posting so it can be persisted. What is the minimal
change to `OutputRouter` that satisfies this without breaking existing callers?

**Findings**:
- `discord.abc.Messageable.send()` returns a `discord.Message` object. The current
  `_send` loop discards it (`await channel.send(chunk)` result is unused).
- Phase messages are well under 2 000 characters. The chunking path
  (`_chunk_message`) only fires for anomalously long content, which never occurs in
  practice for phase outputs.
- Decision: change `_send` to capture and return the last `discord.Message` sent (or
  `None` on failure); change `post_forecast` signature to `-> discord.Message | None`.
  All existing call sites that ignore the return value continue to work unmodified.

**Decision**: Widen `_send` return type from `bool` to `discord.Message | None` — `None`
still signals failure. Update `post_forecast` to `-> discord.Message | None`. All other
`post_log` callers (which chain to `_send` but only need success/fail) retain `bool`
semantics via an internal overload — simplest approach is to have `post_log` continue
using `_send` and not surface the `Message`.

**Alternatives considered**: Separate `send_and_capture` method alongside existing `_send`
(avoided — unnecessary duplication); adding a `capture: bool` keyword arg (avoided —
awkward caller API).

---

## R-002 — Deleting a Discord message by stored ID

**Question**: Given only a channel ID and a message ID stored as a string, what is the
most efficient discord.py pattern to delete the message while correctly handling
"message already gone" and "missing permissions"?

**Findings**:
- `channel.get_partial_message(int(message_id)).delete()` creates a stub `PartialMessage`
  with no API call for the fetch step, then issues one `DELETE /channels/{id}/messages/{id}`
  call. This is cheaper than `channel.fetch_message(id).delete()` (which issues two calls).
- `discord.NotFound` (HTTP 404) is raised when the message no longer exists — maps to FR-008.
- `discord.Forbidden` (HTTP 403) is raised when the bot lacks delete permission — maps to
  FR-009.
- Both exceptions must be caught; any other `discord.HTTPException` should also be caught
  and logged.

**Decision**: Use `channel.get_partial_message(int(message_id)).delete()` inside a
`try/except (discord.NotFound, discord.Forbidden, discord.HTTPException)` block that logs
the outcome and does not re-raise. Store message IDs in the DB as `TEXT` (see R-004).

**Alternatives considered**: `channel.fetch_message(id)` then `.delete()` (two API calls,
unnecessary for deletion); bulk `channel.delete_messages(...)` (requires `manage_messages`
permission even for own messages in some configurations, not appropriate here).

---

## R-003 — 24-hour post-race cleanup scheduler job

**Question**: The existing scheduler pattern uses module-level async callables with
`_GLOBAL_SERVICE` to avoid APScheduler / SQLAlchemyJobStore pickle failures. Does a new
cleanup job need anything beyond the established pattern?

**Findings**:
- The pattern established for `_phase_job`, `_mystery_notice_job`, and `_season_end_job`
  is consistent: module-level coroutine with named kwargs, `_GLOBAL_SERVICE` sentinel,
  registered callback injected via `register_*_callback`.
- A new `_forecast_cleanup_job(round_id: int)` follows this exactly:
  - Callable registered at module level (picklable).
  - Job ID: `cleanup_r{round_id}` — unique, follows naming convention.
  - Trigger: `DateTrigger(run_date = scheduled_at + timedelta(hours=24))`.
  - `misfire_grace_time = _GRACE_SECONDS` (5 min) — consistent with phase jobs.
  - `replace_existing=True` — safe to re-schedule after a postponement amendment.
  - Added in `schedule_round` for non-Mystery rounds alongside the three phase jobs.
  - Removed in `cancel_round` alongside phase jobs and mystery notice job.
- Callback signature: `async def run_post_race_cleanup(round_id: int, bot: Bot) -> None`
  (matches phase callback shape).

**Decision**: Add `_forecast_cleanup_job`, `_forecast_cleanup_callback`, and
`register_forecast_cleanup_callback` following the exact `_mystery_notice_job` pattern.
Schedule the `cleanup_r{id}` job at `scheduled_at + timedelta(hours=24)` inside
`schedule_round` for non-Mystery rounds.

**Alternatives considered**: Scheduling the 24h job from inside `run_phase3` after Phase 3
completes (rejected — not resilient to restarts before Phase 3 fires; the scheduler must
know about the job from round-scheduling time, not phase-execution time; also violates the
clean separation between phase services and the scheduler).

---

## R-004 — Storing Discord message snowflakes in SQLite

**Question**: Discord message IDs are 64-bit unsigned integers (up to ~9.2 × 10¹⁸).
SQLite's `INTEGER` type is signed 64-bit (max ~9.2 × 10¹⁸ signed). Is `INTEGER` safe, or
should `TEXT` be used?

**Findings**:
- Discord snowflakes are generated with epoch 2015-01-01. The theoretical maximum in the
  near future fits within signed 64-bit range, but relying on this is fragile.
- The existing codebase stores all Discord IDs (channel IDs, role IDs, user IDs) as
  `INTEGER` in the schema (see `001_initial.sql`). This is consistent with Python's
  `int` type, which handles arbitrarily large integers; aiosqlite correctly round-trips
  large integers via `INTEGER`.
- Using `INTEGER` is consistent with the rest of the schema and avoids casting overhead.

**Decision**: Store `message_id` as `INTEGER NOT NULL` in `forecast_messages`, consistent
with how all other Discord snowflakes are stored in this codebase. Handle as Python `int`
throughout (no string casting needed).

**Alternatives considered**: `TEXT` (avoids overflow concern but inconsistent with rest of
schema and adds unnecessary casting); `REAL` (lossy — rejected immediately).

---

## R-005 — Test mode interaction: suppression and flush

**Question**: When test mode is active, deletions must be suppressed. When test mode is
disabled, all stored forecast messages for the server must be bulk-deleted. What is the
clearest, least-invasive hook point for these two behaviours given the existing architecture?

**Findings**:

**Suppression (FR-014)**:
- `delete_forecast_message` in `forecast_cleanup_service` is the single chokepoint for all
  deletion attempts (phase-transition and 24h expiry). Adding a `test_mode_active` check
  at the top of this function — queried via `bot.config_service.get_server_config(server_id)` —
  is sufficient. The `server_id` is reachable via `round_id → divisions → seasons.server_id`
  with one DB query.
- The APScheduler `cleanup_r{id}` job fires regardless; the suppression is applied inside
  the service, not at the scheduler level. This preserves restart-resilience: if the bot
  restarts after test mode is disabled, the job has already fired (record still in DB) and
  the flush covers it.

**Flush (FR-015)**:
- `toggle_test_mode` in `test_mode_service` already returns `bool` (new state). The cog
  (`TestModeCog.toggle`) receives this value and is the natural hook: if `new_state is False`,
  `await flush_pending_deletions(server_id, bot)` is called before sending the confirmation
  message. This keeps `test_mode_service` free of Discord API dependencies.
- `flush_pending_deletions(server_id, bot)` queries `forecast_messages` joined to seasons
  (see data-model.md), calls `channel.get_partial_message(id).delete()` for each result,
  and clears each record. FR-008/FR-009 error handling applies per-row; one failure does not
  abort the rest.

**Decision**:
- `delete_forecast_message`: add server_id lookup + `if test_mode_active: return` guard
  before any Discord API call.
- `flush_pending_deletions(server_id, bot)`: new function in `forecast_cleanup_service`;
  called from `TestModeCog.toggle` when new state is `False`.
- `test_mode_service.toggle_test_mode`: signature unchanged.

**Alternatives considered**: Storing `server_id` directly on `forecast_messages` table
(rejected — redundant, breaks normalisation); hooking into `toggle_test_mode` itself
(rejected — would require passing `bot` into the service, introducing a Discord API
dependency into a data-layer function); scheduler-level suppression (rejected — fragile
across restarts).

## Summary of Decisions

| Decision | Chosen approach |
|----------|----------------|
| Capture message ID from OutputRouter | Widen `_send`/`post_forecast` return type to `discord.Message \| None` |
| Delete message by stored ID | `channel.get_partial_message(int(id)).delete()` with `NotFound`/`Forbidden` handling |
| 24h cleanup scheduler job | Module-level `_forecast_cleanup_job` following `_mystery_notice_job` pattern |
| SQLite storage type for message IDs | `INTEGER NOT NULL` — consistent with existing Discord ID columns |
| Test mode suppression | Guard in `delete_forecast_message`; record retained in DB while test mode active |
| Flush on test mode disable | `flush_pending_deletions(server_id, bot)` called from `TestModeCog.toggle` when `new_state is False` |
