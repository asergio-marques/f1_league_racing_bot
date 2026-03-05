# Quickstart: Forecast Channel Message Cleanup

**Feature**: `007-forecast-msg-cleanup`  
**Phase**: 1 — Design  
**Date**: 2026-03-04

---

## Overview

This feature is **fully automatic** — no new slash commands are added. Administrators and
league members do not need to do anything differently. The bot manages the forecast channel
messages on its own as the weather pipeline progresses.

---

## What happens automatically

### During a normal race weekend

| Trigger | What the bot does |
|---------|------------------|
| Phase 1 fires (T − 5 days) | Posts Phase 1 forecast. Stores the message ID. |
| Phase 2 fires (T − 2 days) | **Deletes Phase 1 message**, then posts Phase 2 forecast. Stores Phase 2 message ID. |
| Phase 3 fires (T − 2 hours) | **Deletes Phase 2 message**, then posts Phase 3 forecast. Stores Phase 3 message ID. |
| T + 24 hours | **Deletes Phase 3 message**. No new message is posted. |

At any point during the weekend, the forecast channel shows at most one message per round
per division.

### During test mode

| Trigger | What the bot does |
|---------|------------------|
| Phase 2/3 fires while test mode **active** | Posts the new phase message. **Does NOT delete** the previous phase message. |
| 24-hour expiry fires while test mode **active** | **Does NOT delete** the Phase 3 message. The message ID is retained for later. |
| Test mode **disabled** | **Immediately deletes all stored forecast messages** for that server across all rounds and divisions. |

This lets admins inspect all phase outputs simultaneously during a test session, while
guaranteeing the channel is cleaned up the moment live operation resumes.

### During an amendment

When a round amendment triggers phase invalidation (track change, postponement, format
change, cancellation), the bot:

1. Deletes any posted Phase 1, Phase 2, and/or Phase 3 forecast messages for that round
   and division as part of the existing invalidation flow.
2. Posts the invalidation notice (existing behaviour, unchanged).
3. Re-executes any phases whose horizon has already passed and posts fresh forecast
   messages (existing behaviour). Stores the new message IDs.

### Error handling

If a forecast message has been manually deleted by a server administrator before the bot
attempts cleanup, the bot logs the situation and continues without error. If the bot lacks
permission to delete a message (misconfigured channel permissions), the bot logs the failure
to the calculation log channel and still posts the next phase message.

---

## No setup required

The `forecast_messages` table is created automatically by the migration runner (`004_forecast_messages.sql`) on next bot startup. No manual database changes or configuration
commands are needed.

---

## Observability

Deletion failures are surfaced to the **calculation log channel** (configured at bot setup),
not to the forecast channel. Successful deletions are not logged — they are expected routine
behaviour.
