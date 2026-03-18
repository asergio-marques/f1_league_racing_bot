-- Migration 016: Results & Standings module — channel configuration tables
-- Adds: results_module_config, division_results_config, season_points_links

CREATE TABLE IF NOT EXISTS results_module_config (
    server_id      INTEGER PRIMARY KEY
                       REFERENCES server_configs(server_id)
                       ON DELETE CASCADE,
    module_enabled INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS division_results_config (
    division_id          INTEGER PRIMARY KEY
                             REFERENCES divisions(id)
                             ON DELETE CASCADE,
    results_channel_id   INTEGER,
    standings_channel_id INTEGER,
    reserves_in_standings INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS season_points_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id   INTEGER NOT NULL
                    REFERENCES seasons(id)
                    ON DELETE CASCADE,
    config_name TEXT    NOT NULL,
    UNIQUE (season_id, config_name)
);
