-- ==================================================================
-- 011_driver_placement.sql
-- Adds total_lap_ms to signup_records, team_seat_id to
-- driver_season_assignments, and creates team_role_configs.
-- ==================================================================

PRAGMA foreign_keys = OFF;

-- 1. Seeding value (computed at approval, stored for O(1) lookup)
ALTER TABLE signup_records
    ADD COLUMN total_lap_ms INTEGER;

-- 2. Direct seat FK on season assignments
ALTER TABLE driver_season_assignments
    ADD COLUMN team_seat_id INTEGER REFERENCES team_seats(id);

-- 3. Team → Discord role mapping (server-scoped, persists across seasons)
CREATE TABLE IF NOT EXISTS team_role_configs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id   INTEGER NOT NULL
                    REFERENCES server_configs(server_id)
                    ON DELETE CASCADE,
    team_name   TEXT    NOT NULL,
    role_id     INTEGER NOT NULL,
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(server_id, team_name)
);

PRAGMA foreign_keys = ON;
