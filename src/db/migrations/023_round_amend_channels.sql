-- Migration 023: Track in-flight results amendment channels.
--
-- round_amend_channels records each amendment channel created by
-- /round results amend so that on restart the bot can detect stale orphans,
-- notify the log channel, and delete the dangling Discord channel.
--
-- round_id and session_type uniquely identify the amendment in progress.
-- server_id is stored directly to avoid a multi-table join in recovery.

CREATE TABLE IF NOT EXISTS round_amend_channels (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id     INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
    server_id    INTEGER NOT NULL,
    channel_id   INTEGER NOT NULL,
    session_type TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    UNIQUE (round_id, session_type)
);
