# Quickstart: Message Retry Queue (017)

## Prerequisites

| Requirement | Version / Notes |
|---|---|
| Python | 3.11+ |
| discord.py (py-cord) | 2.x |
| aiosqlite | 0.22+ |
| APScheduler | 3.11+ (unchanged) |
| pytest + pytest-asyncio | For test runner |

```bash
pip install -r requirements.txt
```

## Environment Setup

No new environment variables are required. The existing `.env` at the repo root is sufficient:

```env
BOT_TOKEN=your_bot_token_here
DB_PATH=bot.db
```

## Running the Bot Locally

```bash
python src/bot.py
```

On startup the bot will apply migration `015_pending_messages.sql`, creating the `pending_messages` table if it does not yet exist. The `RetryCog` will start its 5-minute retry loop once the bot is ready.

## Verifying the Migration Applied

```bash
sqlite3 bot.db ".tables"
# Expected: ... pending_messages ...

sqlite3 bot.db "PRAGMA table_info(pending_messages);"
# Expected columns: id, server_id, channel_id, content, failure_reason, enqueued_at, retry_count, last_attempted_at
```

## Simulating a Failed Message (Manual Test)

1. Temporarily disconnect the bot from a Discord guild, or set the forecast channel ID in the DB to a non-existent channel snowflake.
2. Trigger a weather phase (or use test-mode `/test phase1`) so `OutputRouter.post_forecast` is called.
3. Verify a row appears in `pending_messages`:

```bash
sqlite3 bot.db "SELECT id, channel_id, retry_count, failure_reason FROM pending_messages;"
```

4. Restore the channel (or correct the channel ID). Within 5 minutes the retry worker will attempt delivery. After success, the row is removed:

```bash
sqlite3 bot.db "SELECT COUNT(*) FROM pending_messages;"
# Expected: 0
```

5. Check the calculation log channel in Discord for a delivery notification (FR-007).

## Verifying the Warning Threshold

Insert a test row with `retry_count = 12` and wait for the next retry cycle. A warning should appear in the calculation log channel without removing the row:

```bash
sqlite3 bot.db \
  "INSERT INTO pending_messages (server_id, channel_id, content, failure_reason, enqueued_at, retry_count)
   VALUES (YOUR_SERVER_ID, YOUR_CHANNEL_ID, 'test message', 'manual test insert', datetime('now'), 12);"
```

## Running the Tests

```bash
pytest tests/unit/test_retry_service.py -v
pytest tests/integration/test_retry_worker.py -v
```
