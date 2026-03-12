-- ==================================================================
-- 015_pending_messages.sql
-- Adds pending_messages table to store failed channel posts for retry.
-- Rows are created by OutputRouter on any send failure and deleted by
-- RetryService once delivery succeeds. Survives bot restarts.
-- ==================================================================

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
