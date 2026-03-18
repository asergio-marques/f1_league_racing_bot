-- Migration 017: Results & Standings core — points config, submission, standings
-- Adds: points_config_store, points_config_entries, points_config_fl,
--        season_points_entries, season_points_fl,
--        season_amendment_state, season_modification_entries, season_modification_fl,
--        session_results, driver_session_results,
--        driver_standings_snapshots, team_standings_snapshots,
--        round_submission_channels

CREATE TABLE IF NOT EXISTS points_config_store (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id   INTEGER NOT NULL
                    REFERENCES server_configs(server_id)
                    ON DELETE CASCADE,
    config_name TEXT    NOT NULL,
    UNIQUE (server_id, config_name)
);

CREATE TABLE IF NOT EXISTS points_config_entries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    config_id    INTEGER NOT NULL
                     REFERENCES points_config_store(id)
                     ON DELETE CASCADE,
    session_type TEXT    NOT NULL,
    position     INTEGER NOT NULL,
    points       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (config_id, session_type, position)
);

CREATE TABLE IF NOT EXISTS points_config_fl (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    config_id         INTEGER NOT NULL
                          REFERENCES points_config_store(id)
                          ON DELETE CASCADE,
    session_type      TEXT    NOT NULL,
    fl_points         INTEGER NOT NULL DEFAULT 0,
    fl_position_limit INTEGER,
    UNIQUE (config_id, session_type)
);

CREATE TABLE IF NOT EXISTS season_points_entries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id    INTEGER NOT NULL
                     REFERENCES seasons(id)
                     ON DELETE CASCADE,
    config_name  TEXT    NOT NULL,
    session_type TEXT    NOT NULL,
    position     INTEGER NOT NULL,
    points       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (season_id, config_name, session_type, position)
);

CREATE TABLE IF NOT EXISTS season_points_fl (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id         INTEGER NOT NULL
                          REFERENCES seasons(id)
                          ON DELETE CASCADE,
    config_name       TEXT    NOT NULL,
    session_type      TEXT    NOT NULL,
    fl_points         INTEGER NOT NULL DEFAULT 0,
    fl_position_limit INTEGER,
    UNIQUE (season_id, config_name, session_type)
);

CREATE TABLE IF NOT EXISTS season_amendment_state (
    season_id        INTEGER PRIMARY KEY
                         REFERENCES seasons(id)
                         ON DELETE CASCADE,
    amendment_active INTEGER NOT NULL DEFAULT 0,
    modified_flag    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS season_modification_entries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id    INTEGER NOT NULL
                     REFERENCES seasons(id)
                     ON DELETE CASCADE,
    config_name  TEXT    NOT NULL,
    session_type TEXT    NOT NULL,
    position     INTEGER NOT NULL,
    points       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (season_id, config_name, session_type, position)
);

CREATE TABLE IF NOT EXISTS season_modification_fl (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id         INTEGER NOT NULL
                          REFERENCES seasons(id)
                          ON DELETE CASCADE,
    config_name       TEXT    NOT NULL,
    session_type      TEXT    NOT NULL,
    fl_points         INTEGER NOT NULL DEFAULT 0,
    fl_position_limit INTEGER,
    UNIQUE (season_id, config_name, session_type)
);

CREATE TABLE IF NOT EXISTS session_results (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id           INTEGER NOT NULL
                           REFERENCES rounds(id)
                           ON DELETE CASCADE,
    division_id        INTEGER NOT NULL
                           REFERENCES divisions(id),
    session_type       TEXT    NOT NULL,
    status             TEXT    NOT NULL DEFAULT 'ACTIVE',
    config_name        TEXT,
    submitted_by       INTEGER,
    submitted_at       TEXT,
    results_message_id INTEGER,
    UNIQUE (round_id, session_type)
);

CREATE TABLE IF NOT EXISTS driver_session_results (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    session_result_id       INTEGER NOT NULL
                                REFERENCES session_results(id)
                                ON DELETE CASCADE,
    driver_user_id          INTEGER NOT NULL,
    team_role_id            INTEGER NOT NULL,
    finishing_position      INTEGER NOT NULL,
    outcome                 TEXT    NOT NULL DEFAULT 'CLASSIFIED',
    tyre                    TEXT,
    best_lap                TEXT,
    gap                     TEXT,
    total_time              TEXT,
    fastest_lap             TEXT,
    time_penalties          TEXT,
    post_steward_total_time TEXT,
    post_race_time_penalties TEXT,
    points_awarded          INTEGER NOT NULL DEFAULT 0,
    fastest_lap_bonus       INTEGER NOT NULL DEFAULT 0,
    is_superseded           INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS driver_standings_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id            INTEGER NOT NULL
                            REFERENCES rounds(id)
                            ON DELETE CASCADE,
    division_id         INTEGER NOT NULL
                            REFERENCES divisions(id),
    driver_user_id      INTEGER NOT NULL,
    standing_position   INTEGER NOT NULL,
    total_points        INTEGER NOT NULL DEFAULT 0,
    finish_counts       TEXT    NOT NULL DEFAULT '{}',
    first_finish_rounds TEXT    NOT NULL DEFAULT '{}',
    standings_message_id INTEGER,
    UNIQUE (round_id, division_id, driver_user_id)
);

CREATE TABLE IF NOT EXISTS team_standings_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id            INTEGER NOT NULL
                            REFERENCES rounds(id)
                            ON DELETE CASCADE,
    division_id         INTEGER NOT NULL
                            REFERENCES divisions(id),
    team_role_id        INTEGER NOT NULL,
    standing_position   INTEGER NOT NULL,
    total_points        INTEGER NOT NULL DEFAULT 0,
    finish_counts       TEXT    NOT NULL DEFAULT '{}',
    first_finish_rounds TEXT    NOT NULL DEFAULT '{}',
    UNIQUE (round_id, division_id, team_role_id)
);

CREATE TABLE IF NOT EXISTS round_submission_channels (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id   INTEGER NOT NULL
                   REFERENCES rounds(id)
                   ON DELETE CASCADE,
    channel_id INTEGER NOT NULL,
    created_at TEXT    NOT NULL,
    closed     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (round_id)
);
