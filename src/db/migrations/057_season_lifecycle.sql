-- Migration 057: the season lifecycle, in which drivers sign up for each season (issue #220).
--
-- One migration in five parts, each changing one thing the lifecycle needs:
--
--   1. seasons.stage — the ten states a season passes through, kept in step with its status.
--   2. signup_windows and season_signup_config — every window a season opened, and the
--      signup configuration it was confirmed with.
--   3. signup_records per season — every completed signup kept, under its season and window.
--   4. driver_season_assignments.committed — whether a placement has been confirmed.
--   5. driver_history_entries by identifier — history that outlives a deleted profile.
--   6. driver_division_memberships — every division a driver held a confirmed seat in.

-- ─────────────────────────────────────────────────────────────────────────────────────────────
-- Part 1: the lifecycle stage of a season.
--
-- A season now passes through ten states — Configuration, Waiting, Signups, Placements,
-- Ongoing, Ongoing with signups open, Ongoing with placements, Pending completion,
-- Completed and Cancelled. They are held in `seasons.stage`.
--
-- `seasons.status` is kept, as the coarse grain the stage refines:
--
--   SETUP      CONFIGURATION, WAITING, SIGNUPS, PLACEMENTS   (placements never confirmed)
--   ACTIVE     ONGOING, ONGOING_SIGNUPS, ONGOING_PLACEMENTS, PENDING_COMPLETION
--   COMPLETED  COMPLETED
--   CANCELLED  CANCELLED
--
-- Nearly every existing reader of `status = 'ACTIVE'` means "placements confirmed and the
-- season being raced", which ACTIVE still means, so the coarse column keeps those readers
-- correct without a rewrite. The stage is what a reader consults when the finer state
-- matters.
--
-- Three triggers keep the two columns in step:
--
-- 1. A row inserted without a stage takes the stage its status implies: SETUP becomes
--    PLACEMENTS, ACTIVE becomes ONGOING. A season written with a status alone is therefore
--    one whose divisions may be built, or one being raced.
-- 2. A status changed without the stage carries the stage with it, keeping a stage already
--    inside the new status and taking the default otherwise.
-- 3. A stage written that its status does not allow is refused.

ALTER TABLE seasons ADD COLUMN stage TEXT;

UPDATE seasons SET stage = CASE status
    WHEN 'SETUP'     THEN 'PLACEMENTS'
    WHEN 'ACTIVE'    THEN 'ONGOING'
    WHEN 'COMPLETED' THEN 'COMPLETED'
    WHEN 'CANCELLED' THEN 'CANCELLED'
END;

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

-- ─────────────────────────────────────────────────────────────────────────────────────────────
-- Part 2: a season's signup windows, and the signup configuration it ran under.
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

-- ─────────────────────────────────────────────────────────────────────────────────────────────
-- Part 3: every completed signup is its own record, under its season and window.
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

-- ─────────────────────────────────────────────────────────────────────────────────────────────
-- Part 4: a placement is committed once placements are confirmed with it standing.
--
-- A driver placed while a season is in Placements, or placed mid-season in Ongoing, placements,
-- stands outside the championship until a league manager confirms placements: no roles, no
-- lineup, no check-in, no attendance points, no results or standings. `committed` records
-- which side of that line a placement is on, and every reader of the championship filters on
-- it.
--
-- A placement written without saying takes the default its season implies: committed where
-- the season's placements have been confirmed (ACTIVE, or ended), and uncommitted where they
-- have not (SETUP). The placement commands say so explicitly; the default is what keeps every
-- other writer — and every test fixture seating a driver in a running season — meaning what
-- it always meant.

ALTER TABLE driver_season_assignments ADD COLUMN committed INTEGER;

UPDATE driver_season_assignments
SET committed = CASE
    WHEN (SELECT status FROM seasons WHERE seasons.id = driver_season_assignments.season_id)
         IN ('ACTIVE', 'COMPLETED', 'CANCELLED') THEN 1
    ELSE 0
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

-- ─────────────────────────────────────────────────────────────────────────────────────────────
-- Part 5: a history entry names its driver by identifier, and outlives the profile.
--
-- A driver created by test mode keeps their history when test mode deletes them at the end of
-- their season, so that a driver created again under the same identifier in a later season
-- holds it as their own. An entry keyed only by the profile could not survive the profile.
--
-- So each entry now carries the server and the Discord identifier it belongs to, and its
-- profile reference is set null when the profile goes rather than refusing the deletion. The
-- driver pass that ends a season still deletes the entries of a real driver it deletes — the
-- archive keeps none for a driver who never raced — and does so explicitly.
--
-- A rebuild, because SQLite cannot relax a NOT NULL or change a foreign key's action in place.

PRAGMA foreign_keys = OFF;

CREATE TABLE IF NOT EXISTS driver_history_entries_new (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id            INTEGER,
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

INSERT INTO driver_history_entries_new
    (id, server_id, discord_user_id, driver_profile_id, season_number, division_name,
     division_tier, final_position, final_points, points_gap_to_winner, cancelled)
SELECT h.id, dp.server_id, dp.discord_user_id, h.driver_profile_id, h.season_number,
       h.division_name, h.division_tier, h.final_position, h.final_points,
       h.points_gap_to_winner, h.cancelled
FROM driver_history_entries h
LEFT JOIN driver_profiles dp ON dp.id = h.driver_profile_id;

DROP TABLE driver_history_entries;
ALTER TABLE driver_history_entries_new RENAME TO driver_history_entries;

CREATE UNIQUE INDEX IF NOT EXISTS idx_driver_history_unique
    ON driver_history_entries(driver_profile_id, season_number, division_name);
CREATE INDEX IF NOT EXISTS idx_driver_history_identity
    ON driver_history_entries(server_id, discord_user_id);

PRAGMA foreign_keys = ON;

-- ─────────────────────────────────────────────────────────────────────────────────────────────
-- Part 6: every division a driver held a confirmed seat in during a season.
--
-- A driver moved, released or sacked mid-season no longer holds the placement they raced
-- under, yet took part in that division all the same. The season's history lists every
-- division a driver was part of, and so a row is kept here the moment a placement is
-- committed in a division, and never removed by the placement changing afterwards.
--
-- Written by triggers, so that every path committing a placement — confirming placements,
-- a move, a placement written into a season already being raced — records it alike. A
-- driver deleted takes their rows with them (ON DELETE CASCADE), as does a division or
-- season deleted.

CREATE TABLE IF NOT EXISTS driver_division_memberships (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id         INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    division_id       INTEGER NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
    driver_profile_id INTEGER NOT NULL REFERENCES driver_profiles(id) ON DELETE CASCADE,
    UNIQUE (season_id, division_id, driver_profile_id)
);

INSERT OR IGNORE INTO driver_division_memberships (season_id, division_id, driver_profile_id)
SELECT season_id, division_id, driver_profile_id
FROM driver_season_assignments
WHERE committed = 1;

CREATE TRIGGER IF NOT EXISTS driver_division_membership_on_insert
AFTER INSERT ON driver_season_assignments
WHEN NEW.committed = 1
BEGIN
    INSERT OR IGNORE INTO driver_division_memberships (season_id, division_id, driver_profile_id)
    VALUES (NEW.season_id, NEW.division_id, NEW.driver_profile_id);
END;

CREATE TRIGGER IF NOT EXISTS driver_division_membership_on_update
AFTER UPDATE OF committed, division_id ON driver_season_assignments
WHEN NEW.committed = 1
BEGIN
    INSERT OR IGNORE INTO driver_division_memberships (season_id, division_id, driver_profile_id)
    VALUES (NEW.season_id, NEW.division_id, NEW.driver_profile_id);
END;
