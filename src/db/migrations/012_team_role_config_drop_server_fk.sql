-- ==================================================================
-- 012_team_role_config_drop_server_fk.sql
-- Recreates team_role_configs without the server_configs FK so that
-- role mappings can be written before the server is fully initialised
-- (matching the pattern used by default_teams).
-- ==================================================================

PRAGMA foreign_keys = OFF;

CREATE TABLE team_role_configs_new (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id   INTEGER NOT NULL,
    team_name   TEXT    NOT NULL,
    role_id     INTEGER NOT NULL,
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(server_id, team_name)
);

INSERT INTO team_role_configs_new (id, server_id, team_name, role_id, updated_at)
    SELECT id, server_id, team_name, role_id, updated_at
    FROM team_role_configs;

DROP TABLE team_role_configs;

ALTER TABLE team_role_configs_new RENAME TO team_role_configs;

PRAGMA foreign_keys = ON;
