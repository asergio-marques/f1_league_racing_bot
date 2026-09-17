-- Migration 058: a season's signup windows, and the signup configuration it ran under (#220).
--
-- A signup belongs to the season and to the window it came through, and is kept permanently.
-- For a signup kept that long to stay readable, what it answered has to be kept beside it:
--
-- signup_windows — one row per window a season opened: the tracks it asked times for and the
--   close time it carried. `signup_module_config` still holds the window standing open now,
--   its button and its notice; this is the record of every window, opened and closed.
--
-- season_signup_config — the signup configuration a season's configuration was confirmed
--   with: which questions were asked, how times were taken, and the time slots availability
--   was answered against. The module's own settings are fixed from that confirmation to the
--   season's end, so one snapshot per season is exact.
--
-- Both go with their season (ON DELETE CASCADE), which is what an aborted season asks for.

CREATE TABLE IF NOT EXISTS signup_windows (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id            INTEGER NOT NULL REFERENCES server_configs(server_id) ON DELETE CASCADE,
    season_id            INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    selected_tracks_json TEXT    NOT NULL DEFAULT '[]',
    close_at             TEXT,
    opened_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    closed_at            TEXT
);

CREATE INDEX IF NOT EXISTS idx_signup_windows_season ON signup_windows(season_id);

CREATE TABLE IF NOT EXISTS season_signup_config (
    season_id            INTEGER PRIMARY KEY REFERENCES seasons(id) ON DELETE CASCADE,
    nationality_required INTEGER NOT NULL,
    time_type            TEXT    NOT NULL,
    time_image_required  INTEGER NOT NULL,
    -- JSON array of {"slot_id": "Mon_19_00", "day_of_week": 1, "time_hhmm": "19:00"}
    slots_json           TEXT    NOT NULL DEFAULT '[]',
    captured_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);
