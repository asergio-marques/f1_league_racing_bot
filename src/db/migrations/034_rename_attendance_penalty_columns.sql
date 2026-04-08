-- Migration 034: rename attendance penalty columns and pardon type values for clarity.
--
-- Column renames (SQLite requires recreate-and-copy):
--   attendance_config.no_attend_penalty  → no_rsvp_absent_penalty
--   attendance_config.no_show_penalty    → rsvp_absent_penalty
--
-- Pardon type CHECK constraint update (also rename stored values):
--   attendance_pardons.pardon_type  NO_ATTEND → NO_RSVP_ABSENT
--                                   NO_SHOW   → RSVP_ABSENT

-- 1. Rename attendance_config columns via table rebuild.
CREATE TABLE attendance_config_new (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id                INTEGER NOT NULL UNIQUE,
    rsvp_notice_days         INTEGER NOT NULL DEFAULT 3,
    rsvp_last_notice_hours   INTEGER NOT NULL DEFAULT 0,
    rsvp_deadline_hours      INTEGER NOT NULL DEFAULT 2,
    no_rsvp_penalty          INTEGER NOT NULL DEFAULT 1,
    no_rsvp_absent_penalty   INTEGER NOT NULL DEFAULT 1,
    rsvp_absent_penalty      INTEGER NOT NULL DEFAULT 1,
    autoreserve_threshold    INTEGER,
    autosack_threshold       INTEGER
);

INSERT INTO attendance_config_new
    (id, server_id, rsvp_notice_days, rsvp_last_notice_hours, rsvp_deadline_hours,
     no_rsvp_penalty, no_rsvp_absent_penalty, rsvp_absent_penalty,
     autoreserve_threshold, autosack_threshold)
SELECT id, server_id, rsvp_notice_days, rsvp_last_notice_hours, rsvp_deadline_hours,
       no_rsvp_penalty, no_attend_penalty, no_show_penalty,
       autoreserve_threshold, autosack_threshold
FROM attendance_config;

DROP TABLE attendance_config;
ALTER TABLE attendance_config_new RENAME TO attendance_config;

-- 2. Update stored pardon_type values and rebuild attendance_pardons with new CHECK.
UPDATE attendance_pardons SET pardon_type = 'NO_RSVP_ABSENT' WHERE pardon_type = 'NO_ATTEND';
UPDATE attendance_pardons SET pardon_type = 'RSVP_ABSENT'    WHERE pardon_type = 'NO_SHOW';

CREATE TABLE attendance_pardons_new (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id          INTEGER NOT NULL,
    division_id       INTEGER NOT NULL,
    driver_profile_id INTEGER NOT NULL,
    pardon_type       TEXT    NOT NULL CHECK (pardon_type IN ('NO_RSVP', 'NO_RSVP_ABSENT', 'RSVP_ABSENT')),
    justification     TEXT    NOT NULL,
    granted_by        TEXT    NOT NULL,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (round_id, division_id, driver_profile_id, pardon_type)
);

INSERT INTO attendance_pardons_new
SELECT * FROM attendance_pardons;

DROP TABLE attendance_pardons;
ALTER TABLE attendance_pardons_new RENAME TO attendance_pardons;
