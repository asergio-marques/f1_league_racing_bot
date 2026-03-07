-- ==================================================================
-- 009_module_system.sql
-- Adds module enable/disable flags, signup module configuration,
-- signup module settings, availability time slots, and makes
-- forecast_channel_id on divisions nullable.
-- ==================================================================

PRAGMA foreign_keys = OFF;

-- ── 1. Add module state columns to server_configs ─────────────────
ALTER TABLE server_configs
    ADD COLUMN weather_module_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE server_configs
    ADD COLUMN signup_module_enabled INTEGER NOT NULL DEFAULT 0;

-- ── 2. Signup module configuration ────────────────────────────────
--   Stores structural config: channel, roles, open state, button msg
CREATE TABLE IF NOT EXISTS signup_module_config (
    server_id                   INTEGER PRIMARY KEY
                                    REFERENCES server_configs(server_id)
                                    ON DELETE CASCADE,
    signup_channel_id           INTEGER NOT NULL,
    base_role_id                INTEGER NOT NULL,
    signed_up_role_id           INTEGER NOT NULL,
    signups_open                INTEGER NOT NULL DEFAULT 0,
    signup_button_message_id    INTEGER,
    selected_tracks_json        TEXT    NOT NULL DEFAULT '[]'
);

-- ── 3. Signup module settings ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS signup_module_settings (
    server_id               INTEGER PRIMARY KEY
                                REFERENCES server_configs(server_id)
                                ON DELETE CASCADE,
    nationality_required    INTEGER NOT NULL DEFAULT 1,
    time_type               TEXT    NOT NULL DEFAULT 'TIME_TRIAL',
    time_image_required     INTEGER NOT NULL DEFAULT 1
);

-- ── 4. Availability time slots ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS signup_availability_slots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id       INTEGER NOT NULL
                        REFERENCES server_configs(server_id)
                        ON DELETE CASCADE,
    day_of_week     INTEGER NOT NULL,
    time_hhmm       TEXT    NOT NULL,
    UNIQUE(server_id, day_of_week, time_hhmm)
);

-- ── 5. Make forecast_channel_id on divisions nullable ─────────────
CREATE TABLE IF NOT EXISTS divisions_new (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id           INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    name                TEXT    NOT NULL,
    mention_role_id     INTEGER NOT NULL,
    forecast_channel_id INTEGER,
    status              TEXT    NOT NULL DEFAULT 'SETUP',
    tier                INTEGER NOT NULL DEFAULT 1
);

INSERT INTO divisions_new
    SELECT id, season_id, name, mention_role_id, forecast_channel_id, status, tier
    FROM divisions;

DROP TABLE divisions;
ALTER TABLE divisions_new RENAME TO divisions;

PRAGMA foreign_keys = ON;
