-- ==================================================================
-- 010_signup_wizard.sql
-- Adds stable slot_sequence_id to availability slots, signup_records,
-- signup_wizard_records tables, and ban_races_remaining to driver_profiles.
-- ==================================================================

PRAGMA foreign_keys = OFF;

-- ── 1. Stable sequence ID for availability slots ──────────────────
--   slot_sequence_id is a per-server monotonically increasing integer
--   assigned at insert time (MAX + 1). Removing a slot never renumbers
--   remaining slots (FR-010).
ALTER TABLE signup_availability_slots
    ADD COLUMN slot_sequence_id INTEGER;

--   Backfill existing rows: assign chronological rank per server so
--   any pre-existing slots get stable IDs.
UPDATE signup_availability_slots
SET slot_sequence_id = (
    SELECT COUNT(*)
    FROM signup_availability_slots AS s2
    WHERE s2.server_id = signup_availability_slots.server_id
      AND (
          s2.day_of_week < signup_availability_slots.day_of_week
          OR (
              s2.day_of_week = signup_availability_slots.day_of_week
              AND s2.time_hhmm <= signup_availability_slots.time_hhmm
          )
      )
);

-- ── 2. ban_races_remaining on driver_profiles ─────────────────────
ALTER TABLE driver_profiles
    ADD COLUMN ban_races_remaining INTEGER NOT NULL DEFAULT 0;

-- ── 3. Committed signup data per driver per server ────────────────
CREATE TABLE IF NOT EXISTS signup_records (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id                   INTEGER NOT NULL
                                    REFERENCES server_configs(server_id)
                                    ON DELETE CASCADE,
    discord_user_id             TEXT    NOT NULL,
    discord_username            TEXT,
    server_display_name         TEXT,
    nationality                 TEXT,
    platform                    TEXT,
    platform_id                 TEXT,
    availability_slot_ids       TEXT,       -- JSON array of slot_sequence_ids
    driver_type                 TEXT,
    preferred_teams             TEXT,       -- JSON array of team names in selection order
    preferred_teammate          TEXT,
    lap_times_json              TEXT,       -- JSON object: {track_id: "M:ss.mss"}
    notes                       TEXT,
    signup_channel_id           INTEGER,    -- retained until channel is pruned
    created_at                  TEXT        NOT NULL DEFAULT (datetime('now')),
    updated_at                  TEXT        NOT NULL DEFAULT (datetime('now')),
    UNIQUE(server_id, discord_user_id)
);

-- ── 4. In-progress wizard state per driver per server ─────────────
CREATE TABLE IF NOT EXISTS signup_wizard_records (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id                   INTEGER NOT NULL
                                    REFERENCES server_configs(server_id)
                                    ON DELETE CASCADE,
    discord_user_id             TEXT    NOT NULL,
    wizard_state                TEXT    NOT NULL DEFAULT 'UNENGAGED',
    signup_channel_id           INTEGER,
    -- Configuration snapshot captured at wizard start (JSON)
    config_snapshot_json        TEXT,
    -- Draft answers accumulated during collection (JSON object)
    draft_answers_json          TEXT    NOT NULL DEFAULT '{}',
    -- Index into the lap-time steps when collecting multi-track times
    current_lap_track_index     INTEGER NOT NULL DEFAULT 0,
    -- Timestamp of last wizard activity, used for inactivity timeout
    last_activity_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    created_at                  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(server_id, discord_user_id)
);

CREATE INDEX IF NOT EXISTS idx_signup_wizard_channel
    ON signup_wizard_records(signup_channel_id);

CREATE INDEX IF NOT EXISTS idx_signup_records_server
    ON signup_records(server_id, discord_user_id);

PRAGMA foreign_keys = ON;
