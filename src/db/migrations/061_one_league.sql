-- Migration 061: one bot serves one league, so nothing but server_configs names a server
-- (issue #244).
--
-- The bot was built to serve several leagues from one database, one per Discord server, and
-- every table carried the server it belonged to. A league runs its own bot, so the column
-- scoped nothing. It now leaves every table but server_configs, whose one row is the
-- league's: the guild the bot serves, which the entry point checks every command against
-- and which a Discord call reaches the guild through.
--
-- Three shapes recur below. A module configuration keyed by server becomes the league's one
-- row, keyed `id` and fixed at 1 by a CHECK. A composite key loses its server part, so what
-- was unique within a server is unique in the league. And a foreign key onto server_configs
-- goes with the column. Several of those cascaded, so a full reset deleted the rows by
-- deleting their parent; reset_service deletes them by name instead, pinned by
-- tests/unit/test_full_reset_scope.py.
--
-- Every table is rebuilt the way SQLite allows: create the new shape, copy, drop, rename.
-- Rows are copied for the configured server only. Nothing is live, and no database holds a
-- second, but a row belonging to no configured server is nobody's.
--
-- Written as nine migrations, 061 to 069, and merged into this one before any database held
-- them. tests/unit/test_migration_061_one_league.py covers each part.

-- ═════════════════════════════════════════════════════════════════════════════════════════
-- Part 1: the weather horizons.
-- ═════════════════════════════════════════════════════════════════════════════════════════
-- weather_pipeline_config becomes the league's one row, and its cascading foreign key onto
-- server_configs goes. A child with no children of its own, so the drop cascades nowhere.

DROP TABLE IF EXISTS weather_pipeline_config_new;
CREATE TABLE weather_pipeline_config_new (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    phase_1_days  INTEGER NOT NULL DEFAULT 5,
    phase_2_days  INTEGER NOT NULL DEFAULT 2,
    phase_3_hours INTEGER NOT NULL DEFAULT 2
);

INSERT INTO weather_pipeline_config_new (id, phase_1_days, phase_2_days, phase_3_hours)
SELECT 1, phase_1_days, phase_2_days, phase_3_hours
FROM weather_pipeline_config
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);

DROP TABLE weather_pipeline_config;
ALTER TABLE weather_pipeline_config_new RENAME TO weather_pipeline_config;

-- ═════════════════════════════════════════════════════════════════════════════════════════
-- Part 2: the attendance configuration.
-- ═════════════════════════════════════════════════════════════════════════════════════════
-- attendance_config becomes the league's one row, keyed `id = 1` as part 1 keyed the
-- weather horizons, and its cascading foreign key onto server_configs goes with the column
-- — reset_service deletes it by name instead. attendance_division_config keeps its key, the
-- division, and loses the server column it carried beside it: a division already names its
-- season, and the season needed no server to say whose it was.
--
-- Neither table is a parent, so neither drop cascades anywhere.

DROP TABLE IF EXISTS attendance_config_new;
CREATE TABLE attendance_config_new (
    id                       INTEGER PRIMARY KEY CHECK (id = 1),
    module_enabled           INTEGER NOT NULL DEFAULT 0,
    rsvp_notice_days         INTEGER NOT NULL DEFAULT 5,
    rsvp_last_notice_hours   INTEGER NOT NULL DEFAULT 24,
    rsvp_deadline_hours      INTEGER NOT NULL DEFAULT 2,
    no_rsvp_penalty          INTEGER NOT NULL DEFAULT 1,
    absent_penalty           INTEGER NOT NULL DEFAULT 1,
    no_show_penalty          INTEGER NOT NULL DEFAULT 1,
    autoreserve_threshold    INTEGER,
    autosack_threshold       INTEGER
);

INSERT INTO attendance_config_new (
    id, module_enabled, rsvp_notice_days, rsvp_last_notice_hours, rsvp_deadline_hours,
    no_rsvp_penalty, absent_penalty, no_show_penalty, autoreserve_threshold,
    autosack_threshold
)
SELECT 1, module_enabled, rsvp_notice_days, rsvp_last_notice_hours, rsvp_deadline_hours,
       no_rsvp_penalty, absent_penalty, no_show_penalty, autoreserve_threshold,
       autosack_threshold
FROM attendance_config
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);

DROP TABLE attendance_config;
ALTER TABLE attendance_config_new RENAME TO attendance_config;

DROP TABLE IF EXISTS attendance_division_config_new;
CREATE TABLE attendance_division_config_new (
    division_id            INTEGER PRIMARY KEY
                               REFERENCES divisions(id)
                               ON DELETE CASCADE,
    rsvp_channel_id        TEXT,
    attendance_channel_id  TEXT,
    attendance_message_id  TEXT
);

INSERT INTO attendance_division_config_new (
    division_id, rsvp_channel_id, attendance_channel_id, attendance_message_id
)
SELECT division_id, rsvp_channel_id, attendance_channel_id, attendance_message_id
FROM attendance_division_config;

DROP TABLE attendance_division_config;
ALTER TABLE attendance_division_config_new RENAME TO attendance_division_config;

-- ═════════════════════════════════════════════════════════════════════════════════════════
-- Part 3: the image module's tables.
-- ═════════════════════════════════════════════════════════════════════════════════════════
-- image_config becomes the league's one row, keyed `id = 1` as parts 1 and 2 keyed theirs,
-- and its cascading foreign key onto server_configs goes; reset_service deletes it by name.
-- image_aspect_toggles is keyed by the aspect alone, image_tier_colour by division and
-- slot, and driver_portraits by the Discord account.
--
-- image_aspect_toggles hung off image_config(server_id) by a cascading foreign key, and
-- dropping a parent with enforcement on performs an implicit DELETE that fires the cascade
-- (043 met the same). So the toggles are rebuilt first, onto a table with no parent, and
-- only then is image_config rebuilt: by the time the old image_config is dropped, nothing
-- hangs off it. The toggles keep no foreign key onto the new image_config; nothing deletes
-- that row but a full reset, which names both tables.

-- 1. The toggles, detached from image_config before it is touched.
DROP TABLE IF EXISTS image_aspect_toggles_new;
CREATE TABLE image_aspect_toggles_new (
    aspect   TEXT    PRIMARY KEY,
    enabled  INTEGER NOT NULL DEFAULT 0
);
INSERT INTO image_aspect_toggles_new (aspect, enabled)
SELECT aspect, enabled FROM image_aspect_toggles
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE image_aspect_toggles;
ALTER TABLE image_aspect_toggles_new RENAME TO image_aspect_toggles;

-- 2. image_config, now a parent of nothing.
DROP TABLE IF EXISTS image_config_new;
CREATE TABLE image_config_new (
    id                               INTEGER PRIMARY KEY CHECK (id = 1),
    module_enabled                   INTEGER NOT NULL DEFAULT 0,

    -- Template location. Templates have no packaged fallback tier.
    template_directory               TEXT NOT NULL DEFAULT 'resources/defaults/templates',
    calendar_template                TEXT NOT NULL DEFAULT 'calendar_template.svg',
    lineup_template                  TEXT NOT NULL DEFAULT 'lineup_template.svg',
    results_qualifying_template      TEXT NOT NULL DEFAULT 'results_qualifying_template.svg',
    results_race_template            TEXT NOT NULL DEFAULT 'results_race_template.svg',
    standings_drivers_template       TEXT NOT NULL DEFAULT 'standings_drivers_template.svg',
    standings_constructors_template  TEXT NOT NULL DEFAULT 'standings_constructors_template.svg',
    attendance_template              TEXT NOT NULL DEFAULT 'attendance_template.svg',
    rsvp_template                    TEXT NOT NULL DEFAULT 'rsvp_template.svg',
    weather_p1_template              TEXT NOT NULL DEFAULT 'weather_p1_template.svg',
    weather_p2_template              TEXT NOT NULL DEFAULT 'weather_p2_template.svg',
    weather_p3_template              TEXT NOT NULL DEFAULT 'weather_p3_template.svg',
    weather_p2_sprint_template       TEXT NOT NULL DEFAULT 'weather_p2_sprint_template.svg',
    weather_p3_sprint_template       TEXT NOT NULL DEFAULT 'weather_p3_sprint_template.svg',
    weather_mystery_template         TEXT NOT NULL DEFAULT 'weather_mystery_template.svg',
    verdicts_template                TEXT NOT NULL DEFAULT 'verdicts_template.svg',
    verdict_banner_template          TEXT NOT NULL DEFAULT 'verdict_banner_template.svg',

    -- Asset location. A league's own folder; the packaged tier answers every miss.
    track_image_directory            TEXT NOT NULL DEFAULT 'resources/league/tracks',
    team_image_directory             TEXT NOT NULL DEFAULT 'resources/league/teams',
    flag_directory                   TEXT NOT NULL DEFAULT 'resources/league/flags',
    driver_image_directory           TEXT NOT NULL DEFAULT 'resources/league/drivers',
    marker_directory                 TEXT NOT NULL DEFAULT 'resources/league/markers',
    weather_icon_directory           TEXT NOT NULL DEFAULT 'resources/league/weather',
    tyre_directory                   TEXT NOT NULL DEFAULT 'resources/league/tyres',
    division_logo_directory          TEXT NOT NULL DEFAULT 'resources/league/division-logos',

    -- Presentation preferences
    time_zone                        TEXT NOT NULL DEFAULT 'UTC',
    time_format                      TEXT NOT NULL DEFAULT '24H',
    date_format                      TEXT NOT NULL DEFAULT 'DDD_DD_MON_YYYY',
    fastest_lap_colour               TEXT NOT NULL DEFAULT '#A020F0',
    per_tier_colour_enabled          INTEGER NOT NULL DEFAULT 0,

    -- Driver portraits
    use_pfp                          INTEGER NOT NULL DEFAULT 0,
    pfp_prerender                    INTEGER NOT NULL DEFAULT 1,
    pfp_daily                        INTEGER NOT NULL DEFAULT 0,
    pfp_daily_time                   TEXT    NOT NULL DEFAULT '03:00'
);
INSERT INTO image_config_new (
    id, module_enabled, template_directory, calendar_template, lineup_template,
    results_qualifying_template, results_race_template, standings_drivers_template,
    standings_constructors_template, attendance_template, rsvp_template, weather_p1_template,
    weather_p2_template, weather_p3_template, weather_p2_sprint_template,
    weather_p3_sprint_template, weather_mystery_template, verdicts_template,
    verdict_banner_template, track_image_directory, team_image_directory, flag_directory,
    driver_image_directory, marker_directory, weather_icon_directory, tyre_directory,
    division_logo_directory, time_zone, time_format, date_format, fastest_lap_colour,
    per_tier_colour_enabled, use_pfp, pfp_prerender, pfp_daily, pfp_daily_time
)
SELECT
    1, module_enabled, template_directory, calendar_template, lineup_template,
    results_qualifying_template, results_race_template, standings_drivers_template,
    standings_constructors_template, attendance_template, rsvp_template, weather_p1_template,
    weather_p2_template, weather_p3_template, weather_p2_sprint_template,
    weather_p3_sprint_template, weather_mystery_template, verdicts_template,
    verdict_banner_template, track_image_directory, team_image_directory, flag_directory,
    driver_image_directory, marker_directory, weather_icon_directory, tyre_directory,
    division_logo_directory, time_zone, time_format, date_format, fastest_lap_colour,
    per_tier_colour_enabled, use_pfp, pfp_prerender, pfp_daily, pfp_daily_time
FROM image_config
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE image_config;
ALTER TABLE image_config_new RENAME TO image_config;

-- 3. The per-tier colours, keyed by division and slot.
DROP TABLE IF EXISTS image_tier_colour_new;
CREATE TABLE image_tier_colour_new (
    division_slug  TEXT NOT NULL,
    slot           TEXT NOT NULL,
    colour         TEXT NOT NULL,
    PRIMARY KEY (division_slug, slot)
);
INSERT INTO image_tier_colour_new (division_slug, slot, colour)
SELECT division_slug, slot, colour FROM image_tier_colour
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE image_tier_colour;
ALTER TABLE image_tier_colour_new RENAME TO image_tier_colour;

-- 4. The portraits, keyed by the account.
DROP TABLE IF EXISTS driver_portraits_new;
CREATE TABLE driver_portraits_new (
    discord_user_id  TEXT PRIMARY KEY,
    avatar_key       TEXT NOT NULL,
    fetched_at       TEXT NOT NULL
);
INSERT OR IGNORE INTO driver_portraits_new (discord_user_id, avatar_key, fetched_at)
SELECT discord_user_id, avatar_key, fetched_at FROM driver_portraits;
DROP TABLE driver_portraits;
ALTER TABLE driver_portraits_new RENAME TO driver_portraits;

-- ═════════════════════════════════════════════════════════════════════════════════════════
-- Part 4: the results module's tables.
-- ═════════════════════════════════════════════════════════════════════════════════════════
-- results_module_config becomes the league's one row, keyed `id = 1` as part 1 keyed its
-- own; its cascading foreign key onto server_configs goes, and reset_service deletes it by
-- name. points_config_store keys a configuration by its name alone, and
-- round_amend_channels drops the server column it carried beside the round that already
-- names its season.
--
-- points_config_store is a parent: points_config_entries and points_config_fl hang off its
-- id by cascading foreign keys, and dropping it with enforcement on performs an implicit
-- DELETE that fires them. So the children are set aside first and restored after the
-- rename, as 043 did for the image toggles. The store's ids are copied verbatim, so every
-- child row still names its configuration.

-- 1. results_module_config, a child with no children.
DROP TABLE IF EXISTS results_module_config_new;
CREATE TABLE results_module_config_new (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    module_enabled INTEGER NOT NULL DEFAULT 0
);
INSERT INTO results_module_config_new (id, module_enabled)
SELECT 1, module_enabled FROM results_module_config
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE results_module_config;
ALTER TABLE results_module_config_new RENAME TO results_module_config;

-- 2. The points configurations' children, set aside.
DROP TABLE IF EXISTS points_config_entries_keep;
CREATE TABLE points_config_entries_keep AS SELECT * FROM points_config_entries;
DROP TABLE IF EXISTS points_config_fl_keep;
CREATE TABLE points_config_fl_keep AS SELECT * FROM points_config_fl;

-- 3. The store, keyed by name, its ids kept.
DROP TABLE IF EXISTS points_config_store_new;
CREATE TABLE points_config_store_new (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    config_name TEXT    NOT NULL UNIQUE
);
INSERT INTO points_config_store_new (id, config_name)
SELECT id, config_name FROM points_config_store
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE points_config_store;
ALTER TABLE points_config_store_new RENAME TO points_config_store;

-- 4. The children restored, for the configurations that were kept.
DELETE FROM points_config_entries;
INSERT INTO points_config_entries (id, config_id, session_type, position, points)
SELECT id, config_id, session_type, position, points FROM points_config_entries_keep
WHERE config_id IN (SELECT id FROM points_config_store);
DROP TABLE points_config_entries_keep;

DELETE FROM points_config_fl;
INSERT INTO points_config_fl (id, config_id, session_type, fl_points, fl_position_limit)
SELECT id, config_id, session_type, fl_points, fl_position_limit FROM points_config_fl_keep
WHERE config_id IN (SELECT id FROM points_config_store);
DROP TABLE points_config_fl_keep;

-- 5. The amendment channels, keyed as before by round and session.
DROP TABLE IF EXISTS round_amend_channels_new;
CREATE TABLE round_amend_channels_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id     INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
    channel_id   INTEGER NOT NULL,
    session_type TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    UNIQUE (round_id, session_type)
);
INSERT INTO round_amend_channels_new (id, round_id, channel_id, session_type, created_at)
SELECT id, round_id, channel_id, session_type, created_at FROM round_amend_channels;
DROP TABLE round_amend_channels;
ALTER TABLE round_amend_channels_new RENAME TO round_amend_channels;

-- ═════════════════════════════════════════════════════════════════════════════════════════
-- Part 5: the signup module's tables.
-- ═════════════════════════════════════════════════════════════════════════════════════════
-- signup_module_settings and signup_module_config become the league's one row each, keyed
-- `id = 1` as part 1 keyed its own; their cascading foreign keys onto server_configs go,
-- and reset_service deletes them by name. The other five keep their keys and lose the
-- server column, which stood beside a division, a season or an account that already said
-- whose the row was: a wizard and an availability slot are now unique to the league as a
-- whole.
--
-- signup_windows is a parent: signup_records.window_id refers to it ON DELETE SET NULL, and
-- dropping it with enforcement on performs an implicit DELETE that would clear every
-- record's window. So the records are set aside first, and restored — window ids intact —
-- once both tables have been rebuilt.

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

-- ═════════════════════════════════════════════════════════════════════════════════════════
-- Part 6: a driver, their accounts and their history.
-- ═════════════════════════════════════════════════════════════════════════════════════════
-- A driver profile is unique by its current account, and an account belongs to at most one
-- driver, across the whole league rather than within a server of it. driver_history_entries
-- keeps the account it was written under and drops the server beside it.
--
-- driver_profiles is the parent of seven tables, by NO ACTION, SET NULL and CASCADE keys
-- alike, so it cannot be dropped with enforcement on: the drop would be refused, or would
-- null or delete the rows that name it. The rebuild runs with enforcement off, as 009, 053
-- and 057 did; ids are copied verbatim, so every reference still resolves, and
-- tests/unit/test_migration_061_one_league.py runs a foreign_key_check to hold that
-- (inside a script the pragma reports and cannot fail). The two triggers that keep a
-- driver's accounts in step with the profile go with the table and are recreated without
-- the column.

PRAGMA foreign_keys = OFF;

-- 1. The profiles.
DROP TABLE IF EXISTS driver_profiles_new;
CREATE TABLE driver_profiles_new (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user_id    TEXT    NOT NULL UNIQUE,
    current_state      TEXT    NOT NULL,
    former_driver      INTEGER NOT NULL DEFAULT 0,
    is_test_driver     INTEGER NOT NULL DEFAULT 0,
    test_display_name  TEXT,
    test_nationality   TEXT
);
INSERT INTO driver_profiles_new (
    id, discord_user_id, current_state, former_driver, is_test_driver, test_display_name,
    test_nationality
)
SELECT id, discord_user_id, current_state, former_driver, is_test_driver, test_display_name,
       test_nationality
FROM driver_profiles
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE driver_profiles;
ALTER TABLE driver_profiles_new RENAME TO driver_profiles;

-- 2. Every account a driver has held, unique to the league.
DROP TABLE IF EXISTS driver_accounts_new;
CREATE TABLE driver_accounts_new (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    driver_profile_id INTEGER NOT NULL REFERENCES driver_profiles(id) ON DELETE CASCADE,
    discord_user_id   TEXT    NOT NULL UNIQUE
);
INSERT INTO driver_accounts_new (id, driver_profile_id, discord_user_id)
SELECT id, driver_profile_id, discord_user_id FROM driver_accounts
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1)
  AND driver_profile_id IN (SELECT id FROM driver_profiles);
DROP TABLE driver_accounts;
ALTER TABLE driver_accounts_new RENAME TO driver_accounts;
CREATE INDEX IF NOT EXISTS idx_driver_accounts_profile ON driver_accounts(driver_profile_id);

-- 3. The triggers that write a driver's accounts, recreated on the rebuilt profiles.
CREATE TRIGGER IF NOT EXISTS driver_account_on_profile_insert
AFTER INSERT ON driver_profiles
BEGIN
    INSERT INTO driver_accounts (driver_profile_id, discord_user_id)
    VALUES (NEW.id, NEW.discord_user_id);
END;

CREATE TRIGGER IF NOT EXISTS driver_account_on_current_change
AFTER UPDATE OF discord_user_id ON driver_profiles
WHEN NOT EXISTS (
    SELECT 1 FROM driver_accounts
    WHERE discord_user_id = NEW.discord_user_id
      AND driver_profile_id = NEW.id
)
BEGIN
    INSERT INTO driver_accounts (driver_profile_id, discord_user_id)
    VALUES (NEW.id, NEW.discord_user_id);
END;

-- 4. The history, keeping the account each entry was written under.
DROP TABLE IF EXISTS driver_history_entries_new;
CREATE TABLE driver_history_entries_new (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user_id      TEXT,
    driver_profile_id    INTEGER REFERENCES driver_profiles(id) ON DELETE SET NULL,
    season_number        INTEGER NOT NULL,
    division_name        TEXT    NOT NULL,
    division_tier        INTEGER NOT NULL DEFAULT 0,
    final_position       INTEGER NOT NULL DEFAULT 0,
    final_points         INTEGER NOT NULL DEFAULT 0,
    points_gap_to_winner INTEGER NOT NULL DEFAULT 0,
    cancelled            INTEGER NOT NULL DEFAULT 0
);
INSERT INTO driver_history_entries_new (
    id, discord_user_id, driver_profile_id, season_number, division_name, division_tier,
    final_position, final_points, points_gap_to_winner, cancelled
)
SELECT id, discord_user_id,
       CASE WHEN driver_profile_id IN (SELECT id FROM driver_profiles)
            THEN driver_profile_id END,
       season_number, division_name, division_tier, final_position, final_points,
       points_gap_to_winner, cancelled
FROM driver_history_entries
WHERE server_id IS NULL OR server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE driver_history_entries;
ALTER TABLE driver_history_entries_new RENAME TO driver_history_entries;
CREATE UNIQUE INDEX IF NOT EXISTS idx_driver_history_unique
    ON driver_history_entries(driver_profile_id, season_number, division_name);
CREATE INDEX IF NOT EXISTS idx_driver_history_identity
    ON driver_history_entries(discord_user_id);

PRAGMA foreign_keys = ON;

-- ═════════════════════════════════════════════════════════════════════════════════════════
-- Part 7: the team list and the team roles.
-- ═════════════════════════════════════════════════════════════════════════════════════════
-- A team's name is unique in the league's list, and a team name maps to one role, across
-- the whole league rather than within a server of it. Neither table is a parent, so neither
-- drop cascades anywhere.

DROP TABLE IF EXISTS default_teams_new;
CREATE TABLE default_teams_new (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    max_seats  INTEGER NOT NULL DEFAULT 2,
    is_reserve INTEGER NOT NULL DEFAULT 0
);
INSERT INTO default_teams_new (id, name, max_seats, is_reserve)
SELECT id, name, max_seats, is_reserve FROM default_teams
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE default_teams;
ALTER TABLE default_teams_new RENAME TO default_teams;

DROP TABLE IF EXISTS team_role_configs_new;
CREATE TABLE team_role_configs_new (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    team_name   TEXT    NOT NULL UNIQUE,
    role_id     INTEGER NOT NULL,
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO team_role_configs_new (id, team_name, role_id, updated_at)
SELECT id, team_name, role_id, updated_at FROM team_role_configs
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE team_role_configs;
ALTER TABLE team_role_configs_new RENAME TO team_role_configs;

-- ═════════════════════════════════════════════════════════════════════════════════════════
-- Part 8: seasons.
-- ═════════════════════════════════════════════════════════════════════════════════════════
-- The league holds its seasons, and at most one of them is live — in setup or active — at
-- a time. That rule was a partial unique index on the server; with no server to key it, the
-- index is on a constant, which admits one live row across the table.
--
-- seasons is the parent of twelve tables, by NO ACTION and CASCADE keys alike, so it cannot
-- be dropped with enforcement on: the drop would be refused, or would delete every season's
-- divisions, rounds and results. The rebuild runs with enforcement off, as 009, 053, 057
-- and part 6 did; ids are copied verbatim, so every reference still resolves, and
-- tests/unit/test_migration_061_one_league.py runs a foreign_key_check to hold that. The
-- four triggers that keep a season's stage in step with its status go with the table and
-- are recreated exactly as 053 and 057 left them.
--
-- The foreign key onto server_configs goes with the column. It had no action: deleting the
-- configuration was refused while a season existed, and reset_service deletes the seasons
-- first on a full reset, as it always has.

PRAGMA foreign_keys = OFF;

-- A trigger on another table reads seasons, and RENAME checks every trigger in the schema:
-- between the drop and the rename below there is no seasons for it to name, and the rename
-- would fail. It is set aside here and recreated, unchanged, once seasons stands again.
DROP TRIGGER IF EXISTS driver_season_assignments_committed_default;

DROP TABLE IF EXISTS seasons_new;
CREATE TABLE seasons_new (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    start_date     TEXT    NOT NULL,  -- ISO date YYYY-MM-DD
    status         TEXT    NOT NULL DEFAULT 'SETUP',  -- SETUP | ACTIVE | COMPLETED | CANCELLED
    season_number  INTEGER NOT NULL DEFAULT 0,
    game_edition   INTEGER NOT NULL DEFAULT 0,
    stage          TEXT
);
INSERT INTO seasons_new (id, start_date, status, season_number, game_edition, stage)
SELECT id, start_date, status, season_number, game_edition, stage FROM seasons
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE seasons;
ALTER TABLE seasons_new RENAME TO seasons;

-- At most one live season in the league.
CREATE UNIQUE INDEX IF NOT EXISTS idx_seasons_one_live
    ON seasons((1))
    WHERE status IN ('SETUP', 'ACTIVE');

CREATE TRIGGER IF NOT EXISTS seasons_stage_fill_on_insert
AFTER INSERT ON seasons
WHEN NEW.stage IS NULL AND NEW.status IN ('SETUP', 'ACTIVE', 'COMPLETED', 'CANCELLED')
BEGIN
    UPDATE seasons SET stage = CASE NEW.status
        WHEN 'SETUP'     THEN 'PLACEMENTS'
        WHEN 'ACTIVE'    THEN 'ONGOING'
        WHEN 'COMPLETED' THEN 'COMPLETED'
        WHEN 'CANCELLED' THEN 'CANCELLED'
    END
    WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS seasons_stage_follow_status
AFTER UPDATE OF status ON seasons
WHEN NEW.status IS NOT OLD.status
BEGIN
    UPDATE seasons SET stage = CASE
        WHEN NEW.status = 'SETUP'
             AND NEW.stage IN ('CONFIGURATION', 'WAITING', 'SIGNUPS', 'PLACEMENTS')
            THEN NEW.stage
        WHEN NEW.status = 'SETUP' THEN 'PLACEMENTS'
        WHEN NEW.status = 'ACTIVE'
             AND NEW.stage IN ('ONGOING', 'ONGOING_SIGNUPS', 'ONGOING_PLACEMENTS',
                               'PENDING_COMPLETION')
            THEN NEW.stage
        WHEN NEW.status = 'ACTIVE' THEN 'ONGOING'
        ELSE NEW.status
    END
    WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS seasons_stage_matches_status
BEFORE UPDATE OF stage ON seasons
WHEN NEW.stage IS NOT NULL AND NOT (
       (NEW.status = 'SETUP'
        AND NEW.stage IN ('CONFIGURATION', 'WAITING', 'SIGNUPS', 'PLACEMENTS'))
    OR (NEW.status = 'ACTIVE'
        AND NEW.stage IN ('ONGOING', 'ONGOING_SIGNUPS', 'ONGOING_PLACEMENTS',
                          'PENDING_COMPLETION'))
    OR (NEW.status IN ('COMPLETED', 'CANCELLED') AND NEW.stage = NEW.status)
)
BEGIN
    SELECT RAISE(ABORT, 'season stage does not match its status');
END;

CREATE TRIGGER IF NOT EXISTS seasons_stage_matches_status_on_insert
BEFORE INSERT ON seasons
WHEN NEW.stage IS NOT NULL AND NOT (
       (NEW.status = 'SETUP'
        AND NEW.stage IN ('CONFIGURATION', 'WAITING', 'SIGNUPS', 'PLACEMENTS'))
    OR (NEW.status = 'ACTIVE'
        AND NEW.stage IN ('ONGOING', 'ONGOING_SIGNUPS', 'ONGOING_PLACEMENTS',
                          'PENDING_COMPLETION'))
    OR (NEW.status IN ('COMPLETED', 'CANCELLED') AND NEW.stage = NEW.status)
)
BEGIN
    SELECT RAISE(ABORT, 'season stage does not match its status');
END;

CREATE TRIGGER IF NOT EXISTS driver_season_assignments_committed_default
AFTER INSERT ON driver_season_assignments
WHEN NEW.committed IS NULL
BEGIN
    UPDATE driver_season_assignments
    SET committed = CASE
        WHEN (SELECT status FROM seasons WHERE seasons.id = NEW.season_id)
             IN ('ACTIVE', 'COMPLETED', 'CANCELLED') THEN 1
        ELSE 0
    END
    WHERE id = NEW.id;
END;

PRAGMA foreign_keys = ON;

-- ═════════════════════════════════════════════════════════════════════════════════════════
-- Part 9: the audit log, the retry queue and the review prompt.
-- ═════════════════════════════════════════════════════════════════════════════════════════
-- audit_entries drops its server and the foreign key onto server_configs that came with it.
-- That key had no action, so deleting the configuration was refused while any audit row
-- existed; reset_service deletes the audit rows first, as it always has. pending_messages
-- drops its server; season_review_prompts, which held one prompt per server, becomes the
-- league's one row, keyed `id = 1`. None of the three is a parent, so no drop cascades.

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
