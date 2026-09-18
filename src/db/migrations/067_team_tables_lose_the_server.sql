-- Migration 067: the league's team list and its team roles lose their server (issue #244).
--
-- A team's name is unique in the league's list, and a team name maps to one role, across
-- the whole league rather than within a server of it. Neither table is a parent, so neither
-- drop cascades anywhere.
--
-- Rows are copied for the configured server only. Nothing is live, and no database holds a
-- second, but a row belonging to no configured server is nobody's.

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
