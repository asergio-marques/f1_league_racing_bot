-- Migration 059: every completed signup is its own record, under its season and window (#220).
--
-- signup_records was keyed by (server_id, discord_user_id), and a second signup overwrote
-- the first: no season's signups survived the next. A signup now belongs to the season and to
-- the window it came through, and is kept permanently — so the unique key goes, and a driver
-- who signs up twice in a season holds two records.
--
-- season_id goes with the season (ON DELETE CASCADE), which is what aborting one asks for.
-- window_id is set null rather than cascading, the window being a detail of the record and
-- never the thing that decides whether it survives.
--
-- A rebuild, because SQLite cannot drop a UNIQUE constraint in place. Rows already stored carry
-- no season: the bot runs live for no league yet (confirmed 2026-09-17), so nothing is inferred.

PRAGMA foreign_keys = OFF;

CREATE TABLE IF NOT EXISTS signup_records_new (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id             INTEGER NOT NULL REFERENCES server_configs(server_id) ON DELETE CASCADE,
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
    updated_at            TEXT    NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO signup_records_new
    (id, server_id, discord_user_id, discord_username, server_display_name, nationality,
     platform, platform_id, availability_slot_ids, driver_type, preferred_teams,
     preferred_teammate, lap_times_json, notes, signup_channel_id, total_lap_ms,
     created_at, updated_at)
SELECT id, server_id, discord_user_id, discord_username, server_display_name, nationality,
       platform, platform_id, availability_slot_ids, driver_type, preferred_teams,
       preferred_teammate, lap_times_json, notes, signup_channel_id, total_lap_ms,
       created_at, updated_at
FROM signup_records;

DROP TABLE signup_records;
ALTER TABLE signup_records_new RENAME TO signup_records;

CREATE INDEX IF NOT EXISTS idx_signup_records_server
    ON signup_records(server_id, discord_user_id);
CREATE INDEX IF NOT EXISTS idx_signup_records_season
    ON signup_records(season_id, discord_user_id);

PRAGMA foreign_keys = ON;
