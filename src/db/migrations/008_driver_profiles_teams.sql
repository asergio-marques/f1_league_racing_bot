-- Migration 008: Driver Profiles, Teams & Season Enhancements
-- Feature: 012-driver-profiles-teams

-- -----------------------------------------------------------------------
-- New tables
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS driver_profiles (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id         INTEGER NOT NULL,
    discord_user_id   TEXT    NOT NULL,
    current_state     TEXT    NOT NULL,
    former_driver     INTEGER NOT NULL DEFAULT 0,
    race_ban_count    INTEGER NOT NULL DEFAULT 0,
    season_ban_count  INTEGER NOT NULL DEFAULT 0,
    league_ban_count  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(server_id, discord_user_id)
);

CREATE TABLE IF NOT EXISTS driver_season_assignments (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    driver_profile_id    INTEGER NOT NULL REFERENCES driver_profiles(id),
    season_id            INTEGER NOT NULL REFERENCES seasons(id),
    division_id          INTEGER NOT NULL REFERENCES divisions(id),
    current_position     INTEGER NOT NULL DEFAULT 0,
    current_points       INTEGER NOT NULL DEFAULT 0,
    points_gap_to_first  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS driver_history_entries (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    driver_profile_id    INTEGER NOT NULL REFERENCES driver_profiles(id),
    season_number        INTEGER NOT NULL,
    division_name        TEXT    NOT NULL,
    division_tier        INTEGER NOT NULL DEFAULT 0,
    final_position       INTEGER NOT NULL DEFAULT 0,
    final_points         INTEGER NOT NULL DEFAULT 0,
    points_gap_to_winner INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS default_teams (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id  INTEGER NOT NULL,
    name       TEXT    NOT NULL,
    max_seats  INTEGER NOT NULL DEFAULT 2,
    is_reserve INTEGER NOT NULL DEFAULT 0,
    UNIQUE(server_id, name)
);

CREATE TABLE IF NOT EXISTS team_instances (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    division_id INTEGER NOT NULL REFERENCES divisions(id),
    name        TEXT    NOT NULL,
    max_seats   INTEGER NOT NULL DEFAULT 2,
    is_reserve  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(division_id, name)
);

CREATE TABLE IF NOT EXISTS team_seats (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    team_instance_id  INTEGER NOT NULL REFERENCES team_instances(id),
    seat_number       INTEGER NOT NULL,
    driver_profile_id INTEGER REFERENCES driver_profiles(id),
    UNIQUE(team_instance_id, seat_number)
);

-- -----------------------------------------------------------------------
-- Modified tables
-- -----------------------------------------------------------------------

ALTER TABLE server_configs ADD COLUMN previous_season_number INTEGER NOT NULL DEFAULT 0;
ALTER TABLE seasons        ADD COLUMN season_number          INTEGER NOT NULL DEFAULT 0;
ALTER TABLE divisions      ADD COLUMN tier                   INTEGER NOT NULL DEFAULT 0;
