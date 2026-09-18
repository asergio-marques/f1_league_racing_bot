-- Migration 065: the signup module's tables lose their server (issue #244).
--
-- signup_module_settings and signup_module_config become the league's one row each, keyed
-- `id = 1` as 061 keyed its own; their cascading foreign keys onto server_configs go, and
-- reset_service deletes them by name. The other five keep their keys and lose the server
-- column, which stood beside a division, a season or an account that already said whose
-- the row was: a wizard and an availability slot are now unique to the league as a whole.
--
-- signup_windows is a parent: signup_records.window_id refers to it ON DELETE SET NULL, and
-- dropping it with enforcement on performs an implicit DELETE that would clear every
-- record's window. So the records are set aside first, and restored — window ids intact —
-- once both tables have been rebuilt.
--
-- Rows are copied for the configured server only. Nothing is live, and no database holds a
-- second, but a row belonging to no configured server is nobody's.

-- 1. The two single-row tables.
DROP TABLE IF EXISTS signup_module_settings_new;
CREATE TABLE signup_module_settings_new (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    nationality_required    INTEGER NOT NULL DEFAULT 1,
    time_type               TEXT    NOT NULL DEFAULT 'TIME_TRIAL',
    time_image_required     INTEGER NOT NULL DEFAULT 1
);
INSERT INTO signup_module_settings_new (id, nationality_required, time_type, time_image_required)
SELECT 1, nationality_required, time_type, time_image_required FROM signup_module_settings
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE signup_module_settings;
ALTER TABLE signup_module_settings_new RENAME TO signup_module_settings;

DROP TABLE IF EXISTS signup_module_config_new;
CREATE TABLE signup_module_config_new (
    id                          INTEGER PRIMARY KEY CHECK (id = 1),
    signup_channel_id           INTEGER,
    base_role_id                INTEGER,
    signed_up_role_id           INTEGER,
    signups_open                INTEGER NOT NULL DEFAULT 0,
    signup_button_message_id    INTEGER,
    selected_tracks_json        TEXT    NOT NULL DEFAULT '[]',
    signup_closed_message_id    INTEGER,
    close_at                    TEXT
);
INSERT INTO signup_module_config_new (
    id, signup_channel_id, base_role_id, signed_up_role_id, signups_open,
    signup_button_message_id, selected_tracks_json, signup_closed_message_id, close_at
)
SELECT 1, signup_channel_id, base_role_id, signed_up_role_id, signups_open,
       signup_button_message_id, selected_tracks_json, signup_closed_message_id, close_at
FROM signup_module_config
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE signup_module_config;
ALTER TABLE signup_module_config_new RENAME TO signup_module_config;

-- 2. Per-division signup channels, one per division.
DROP TABLE IF EXISTS signup_division_config_new;
CREATE TABLE signup_division_config_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    division_id  INTEGER NOT NULL UNIQUE
                     REFERENCES divisions(id)
                     ON DELETE CASCADE
);
INSERT INTO signup_division_config_new (id, division_id)
SELECT id, division_id FROM signup_division_config
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE signup_division_config;
ALTER TABLE signup_division_config_new RENAME TO signup_division_config;

-- 3. The wizard, one per account.
DROP TABLE IF EXISTS signup_wizard_records_new;
CREATE TABLE signup_wizard_records_new (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user_id             TEXT    NOT NULL UNIQUE,
    wizard_state                TEXT    NOT NULL DEFAULT 'UNENGAGED',
    signup_channel_id           INTEGER,
    -- Configuration snapshot captured at wizard start (JSON)
    config_snapshot_json        TEXT,
    -- Draft answers accumulated during collection (JSON object)
    draft_answers_json          TEXT    NOT NULL DEFAULT '{}',
    -- Index into the lap-time steps when collecting multi-track times
    current_lap_track_index     INTEGER NOT NULL DEFAULT 0,
    -- Timestamp of last wizard activity, used for inactivity timeout
    last_activity_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    created_at                  TEXT    NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO signup_wizard_records_new (
    id, discord_user_id, wizard_state, signup_channel_id, config_snapshot_json,
    draft_answers_json, current_lap_track_index, last_activity_at, created_at
)
SELECT id, discord_user_id, wizard_state, signup_channel_id, config_snapshot_json,
       draft_answers_json, current_lap_track_index, last_activity_at, created_at
FROM signup_wizard_records
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE signup_wizard_records;
ALTER TABLE signup_wizard_records_new RENAME TO signup_wizard_records;
CREATE INDEX IF NOT EXISTS idx_signup_wizard_channel
    ON signup_wizard_records(signup_channel_id);

-- 4. The availability slots, unique to the league by day and time.
DROP TABLE IF EXISTS signup_availability_slots_new;
CREATE TABLE signup_availability_slots_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    day_of_week     INTEGER NOT NULL,
    time_hhmm       TEXT    NOT NULL,
    UNIQUE(day_of_week, time_hhmm)
);
INSERT INTO signup_availability_slots_new (id, day_of_week, time_hhmm)
SELECT id, day_of_week, time_hhmm FROM signup_availability_slots
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE signup_availability_slots;
ALTER TABLE signup_availability_slots_new RENAME TO signup_availability_slots;

-- 5. The records set aside, before their parent is dropped.
DROP TABLE IF EXISTS signup_records_keep;
CREATE TABLE signup_records_keep AS
SELECT * FROM signup_records
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);

-- 6. The windows.
DROP TABLE IF EXISTS signup_windows_new;
CREATE TABLE signup_windows_new (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id            INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    selected_tracks_json TEXT    NOT NULL DEFAULT '[]',
    close_at             TEXT,
    opened_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    closed_at            TEXT
);
INSERT INTO signup_windows_new (id, season_id, selected_tracks_json, close_at, opened_at, closed_at)
SELECT id, season_id, selected_tracks_json, close_at, opened_at, closed_at FROM signup_windows
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE signup_windows;
ALTER TABLE signup_windows_new RENAME TO signup_windows;
CREATE INDEX IF NOT EXISTS idx_signup_windows_season ON signup_windows(season_id);

-- 7. The records, rebuilt and restored from what was set aside.
DROP TABLE IF EXISTS signup_records_new;
CREATE TABLE signup_records_new (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id             INTEGER REFERENCES seasons(id) ON DELETE CASCADE,
    window_id             INTEGER REFERENCES signup_windows(id) ON DELETE SET NULL,
    discord_user_id       TEXT    NOT NULL,
    discord_username      TEXT,
    server_display_name   TEXT,
    nationality           TEXT,
    platform              TEXT,
    platform_id           TEXT,
    availability_slot_ids TEXT,       -- JSON array of durable slot ids ("Mon_19_00")
    driver_type           TEXT,
    preferred_teams       TEXT,       -- JSON array of team names in selection order
    preferred_teammate    TEXT,
    lap_times_json        TEXT,       -- JSON object: {track_id: "M:ss.mss"}
    notes                 TEXT,
    signup_channel_id     INTEGER,
    total_lap_ms          INTEGER,
    created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    approved              INTEGER NOT NULL DEFAULT 0
);
INSERT INTO signup_records_new (
    id, season_id, window_id, discord_user_id, discord_username, server_display_name,
    nationality, platform, platform_id, availability_slot_ids, driver_type,
    preferred_teams, preferred_teammate, lap_times_json, notes, signup_channel_id,
    total_lap_ms, created_at, updated_at, approved
)
SELECT id, season_id, window_id, discord_user_id, discord_username, server_display_name,
       nationality, platform, platform_id, availability_slot_ids, driver_type,
       preferred_teams, preferred_teammate, lap_times_json, notes, signup_channel_id,
       total_lap_ms, created_at, updated_at, approved
FROM signup_records_keep;
DROP TABLE signup_records;
DROP TABLE signup_records_keep;
ALTER TABLE signup_records_new RENAME TO signup_records;
CREATE INDEX IF NOT EXISTS idx_signup_records_account ON signup_records(discord_user_id);
CREATE INDEX IF NOT EXISTS idx_signup_records_season
    ON signup_records(season_id, discord_user_id);
