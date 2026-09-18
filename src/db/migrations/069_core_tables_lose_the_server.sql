-- Migration 069: the record of changes, the retry queue and the standing review prompt lose
-- their server (issue #244).
--
-- After this, server_configs is the only table that names a server, and its one row is the
-- league's: the guild the bot serves, which the entry point checks every command against and
-- which a Discord call reaches the guild through. Nothing else scopes by it.
--
-- audit_entries drops its server and the foreign key onto server_configs that came with it.
-- That key had no action, so deleting the configuration was refused while any audit row
-- existed; reset_service deletes the audit rows first, as it always has. pending_messages
-- drops its server; season_review_prompts, which held one prompt per server, becomes the
-- league's one row, keyed `id = 1`. None of the three is a parent, so no drop cascades.
--
-- Rows are copied for the configured server only. Nothing is live, and no database holds a
-- second, but a row belonging to no configured server is nobody's.

DROP TABLE IF EXISTS audit_entries_new;
CREATE TABLE audit_entries_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id     INTEGER NOT NULL,
    actor_name   TEXT    NOT NULL,
    division_id  INTEGER,
    change_type  TEXT    NOT NULL,
    old_value    TEXT    NOT NULL DEFAULT '',
    new_value    TEXT    NOT NULL DEFAULT '',
    timestamp    TEXT    NOT NULL
);
INSERT INTO audit_entries_new (
    id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp
)
SELECT id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp
FROM audit_entries
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE audit_entries;
ALTER TABLE audit_entries_new RENAME TO audit_entries;

DROP TABLE IF EXISTS pending_messages_new;
CREATE TABLE pending_messages_new (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id        INTEGER NOT NULL,
    content           TEXT    NOT NULL,
    failure_reason    TEXT    NOT NULL,
    enqueued_at       TEXT    NOT NULL,
    retry_count       INTEGER NOT NULL DEFAULT 0,
    last_attempted_at TEXT
);
INSERT INTO pending_messages_new (
    id, channel_id, content, failure_reason, enqueued_at, retry_count, last_attempted_at
)
SELECT id, channel_id, content, failure_reason, enqueued_at, retry_count, last_attempted_at
FROM pending_messages
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE pending_messages;
ALTER TABLE pending_messages_new RENAME TO pending_messages;

DROP TABLE IF EXISTS season_review_prompts_new;
CREATE TABLE season_review_prompts_new (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    season_id   INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    reviewer_id INTEGER NOT NULL,
    posted_at   TEXT    NOT NULL
);
INSERT INTO season_review_prompts_new (
    id, season_id, channel_id, message_id, reviewer_id, posted_at
)
SELECT 1, season_id, channel_id, message_id, reviewer_id, posted_at
FROM season_review_prompts
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE season_review_prompts;
ALTER TABLE season_review_prompts_new RENAME TO season_review_prompts;
