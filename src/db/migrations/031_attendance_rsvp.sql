-- Migration 031: Attendance RSVP check-in tables
-- Creates driver_round_attendance and rsvp_embed_messages tables.

CREATE TABLE IF NOT EXISTS driver_round_attendance (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id           INTEGER NOT NULL
                           REFERENCES rounds(id)
                           ON DELETE CASCADE,
    division_id        INTEGER NOT NULL
                           REFERENCES divisions(id),
    driver_profile_id  INTEGER NOT NULL
                           REFERENCES driver_profiles(id),
    rsvp_status        TEXT    NOT NULL DEFAULT 'NO_RSVP',
    accepted_at        TEXT,
    assigned_team_id   INTEGER REFERENCES team_instances(id),
    is_standby         INTEGER NOT NULL DEFAULT 0,
    attended           INTEGER,
    UNIQUE (round_id, division_id, driver_profile_id)
);

CREATE INDEX IF NOT EXISTS idx_dra_round_division
    ON driver_round_attendance (round_id, division_id);

CREATE TABLE IF NOT EXISTS rsvp_embed_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id    INTEGER NOT NULL
                    REFERENCES rounds(id)
                    ON DELETE CASCADE,
    division_id INTEGER NOT NULL
                    REFERENCES divisions(id),
    message_id  TEXT    NOT NULL,
    channel_id  TEXT    NOT NULL,
    posted_at   TEXT    NOT NULL,
    UNIQUE (round_id, division_id)
);
