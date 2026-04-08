-- Migration 030: Attendance Module
-- Creates attendance_config (server-level) and attendance_division_config (per-division) tables.

CREATE TABLE IF NOT EXISTS attendance_config (
    server_id                INTEGER PRIMARY KEY
                                 REFERENCES server_configs(server_id)
                                 ON DELETE CASCADE,
    module_enabled           INTEGER NOT NULL DEFAULT 0,
    rsvp_notice_days         INTEGER NOT NULL DEFAULT 5,
    rsvp_last_notice_hours   INTEGER NOT NULL DEFAULT 24,
    rsvp_deadline_hours      INTEGER NOT NULL DEFAULT 2,
    no_rsvp_penalty          INTEGER NOT NULL DEFAULT 1,
    no_rsvp_absent_penalty   INTEGER NOT NULL DEFAULT 1,
    rsvp_absent_penalty      INTEGER NOT NULL DEFAULT 1,
    autoreserve_threshold    INTEGER,
    autosack_threshold       INTEGER
);

CREATE TABLE IF NOT EXISTS attendance_division_config (
    division_id               INTEGER PRIMARY KEY
                                  REFERENCES divisions(id)
                                  ON DELETE CASCADE,
    server_id                 INTEGER NOT NULL,
    rsvp_channel_id           TEXT,
    attendance_channel_id     TEXT
);
