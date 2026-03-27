-- ==================================================================
-- 024_signup_expansion.sql
-- Signup module expansion:
--   1. Make signup_channel_id, base_role_id, signed_up_role_id
--      nullable in signup_module_config (table recreation required;
--      SQLite does not support ALTER COLUMN).
--   2. Add close_at TEXT column to signup_module_config.
--   3. Create signup_division_config table for per-division
--      lineup announcement channel.
-- ==================================================================

PRAGMA foreign_keys = OFF;

-- ── 1 & 2. Recreate signup_module_config with nullable config fields
--           and the new close_at column ────────────────────────────

CREATE TABLE IF NOT EXISTS signup_module_config_new (
    server_id                   INTEGER PRIMARY KEY
                                    REFERENCES server_configs(server_id)
                                    ON DELETE CASCADE,
    signup_channel_id           INTEGER,
    base_role_id                INTEGER,
    signed_up_role_id           INTEGER,
    signups_open                INTEGER NOT NULL DEFAULT 0,
    signup_button_message_id    INTEGER,
    selected_tracks_json        TEXT    NOT NULL DEFAULT '[]',
    signup_closed_message_id    INTEGER,
    close_at                    TEXT
);

INSERT INTO signup_module_config_new
    (server_id, signup_channel_id, base_role_id, signed_up_role_id,
     signups_open, signup_button_message_id, selected_tracks_json,
     signup_closed_message_id)
SELECT  server_id, signup_channel_id, base_role_id, signed_up_role_id,
        signups_open, signup_button_message_id, selected_tracks_json,
        signup_closed_message_id
FROM    signup_module_config;

DROP TABLE signup_module_config;

ALTER TABLE signup_module_config_new RENAME TO signup_module_config;

PRAGMA foreign_keys = ON;

-- ── 3. Per-division lineup announcement channel ───────────────────

CREATE TABLE IF NOT EXISTS signup_division_config (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id           INTEGER NOT NULL
                            REFERENCES server_configs(server_id)
                            ON DELETE CASCADE,
    division_id         INTEGER NOT NULL
                            REFERENCES divisions(id)
                            ON DELETE CASCADE,
    lineup_channel_id   INTEGER,
    UNIQUE(server_id, division_id)
);
