-- Migration 001: Initial Schema
-- Applied by database.py run_migrations()

PRAGMA foreign_keys = ON;

-- 1. Schema migration tracking
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

-- 2. Per-server bot configuration
CREATE TABLE IF NOT EXISTS server_configs (
    server_id              INTEGER PRIMARY KEY,
    interaction_role_id    INTEGER NOT NULL,
    interaction_channel_id INTEGER NOT NULL,
    log_channel_id         INTEGER NOT NULL
);

-- 3. Seasons (one active season per server at a time)
CREATE TABLE IF NOT EXISTS seasons (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id   INTEGER NOT NULL,
    start_date  TEXT    NOT NULL,  -- ISO date YYYY-MM-DD
    status      TEXT    NOT NULL DEFAULT 'SETUP',  -- SETUP | ACTIVE | COMPLETED
    FOREIGN KEY (server_id) REFERENCES server_configs(server_id)
);
CREATE INDEX IF NOT EXISTS idx_seasons_server ON seasons(server_id);

-- 4. Divisions within a season
CREATE TABLE IF NOT EXISTS divisions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id          INTEGER NOT NULL,
    name               TEXT    NOT NULL,
    mention_role_id    INTEGER NOT NULL,
    forecast_channel_id INTEGER NOT NULL,
    race_day           INTEGER NOT NULL,  -- 0=Monday … 6=Sunday
    race_time          TEXT    NOT NULL,  -- HH:MM UTC
    FOREIGN KEY (season_id) REFERENCES seasons(id)
);
CREATE INDEX IF NOT EXISTS idx_divisions_season ON divisions(season_id);

-- 5. Rounds within a division
CREATE TABLE IF NOT EXISTS rounds (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    division_id     INTEGER NOT NULL,
    round_number    INTEGER NOT NULL,
    format          TEXT    NOT NULL,  -- NORMAL | SPRINT | MYSTERY | ENDURANCE
    track_name      TEXT,
    scheduled_at    TEXT    NOT NULL,  -- ISO datetime UTC, e.g. 2025-06-15T18:00:00
    phase1_done     INTEGER NOT NULL DEFAULT 0,
    phase2_done     INTEGER NOT NULL DEFAULT 0,
    phase3_done     INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (division_id) REFERENCES divisions(id)
);
CREATE INDEX IF NOT EXISTS idx_rounds_division ON rounds(division_id);

-- 6. Sessions within a round
CREATE TABLE IF NOT EXISTS sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id            INTEGER NOT NULL,
    session_type        TEXT    NOT NULL,
    phase2_slot_type    TEXT,   -- 'rain' | 'mixed' | 'sunny'
    phase3_slots        TEXT,   -- JSON array of weather strings
    FOREIGN KEY (round_id) REFERENCES rounds(id)
);
CREATE INDEX IF NOT EXISTS idx_sessions_round ON sessions(round_id);

-- 7. Phase result snapshots (full audit of every phase execution)
CREATE TABLE IF NOT EXISTS phase_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id      INTEGER NOT NULL,
    phase_number  INTEGER NOT NULL,
    payload       TEXT    NOT NULL,  -- JSON blob: inputs + outputs
    status        TEXT    NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE | INVALIDATED
    created_at    TEXT    NOT NULL,
    FOREIGN KEY (round_id) REFERENCES rounds(id)
);
CREATE INDEX IF NOT EXISTS idx_phase_results_round ON phase_results(round_id);

-- 8. Audit log entries
CREATE TABLE IF NOT EXISTS audit_entries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id    INTEGER NOT NULL,
    actor_id     INTEGER NOT NULL,
    actor_name   TEXT    NOT NULL,
    division_id  INTEGER,
    change_type  TEXT    NOT NULL,
    old_value    TEXT    NOT NULL DEFAULT '',
    new_value    TEXT    NOT NULL DEFAULT '',
    timestamp    TEXT    NOT NULL,
    FOREIGN KEY (server_id) REFERENCES server_configs(server_id)
);
CREATE INDEX IF NOT EXISTS idx_audit_server ON audit_entries(server_id);
