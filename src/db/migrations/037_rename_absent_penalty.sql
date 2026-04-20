-- Migration 037: rename no_rsvp_absent_penalty → absent_penalty and
-- update pardon type NO_RSVP_ABSENT → ABSENT.
--
-- The absent_penalty now applies to ALL absent drivers who did not get an
-- ACCEPTED RSVP (NO_RSVP+absent, TENTATIVE+absent, DECLINED+absent).
-- ACCEPTED+absent uses rsvp_absent_penalty as before.
--
-- DROP TABLE IF EXISTS guards make each step safe to re-run if a previous
-- attempt failed partway through.

-- 1. Rename attendance_config column via table rebuild.
DROP TABLE IF EXISTS attendance_config_new;
CREATE TABLE attendance_config_new (
    server_id                INTEGER PRIMARY KEY
                                 REFERENCES server_configs(server_id)
                                 ON DELETE CASCADE,
    module_enabled           INTEGER NOT NULL DEFAULT 0,
    rsvp_notice_days         INTEGER NOT NULL DEFAULT 5,
    rsvp_last_notice_hours   INTEGER NOT NULL DEFAULT 24,
    rsvp_deadline_hours      INTEGER NOT NULL DEFAULT 2,
    no_rsvp_penalty          INTEGER NOT NULL DEFAULT 1,
    absent_penalty           INTEGER NOT NULL DEFAULT 1,
    rsvp_absent_penalty      INTEGER NOT NULL DEFAULT 1,
    autoreserve_threshold    INTEGER,
    autosack_threshold       INTEGER
);

INSERT INTO attendance_config_new
    (server_id, module_enabled, rsvp_notice_days, rsvp_last_notice_hours, rsvp_deadline_hours,
     no_rsvp_penalty, absent_penalty, rsvp_absent_penalty,
     autoreserve_threshold, autosack_threshold)
SELECT server_id, module_enabled, rsvp_notice_days, rsvp_last_notice_hours, rsvp_deadline_hours,
       no_rsvp_penalty, no_rsvp_absent_penalty, rsvp_absent_penalty,
       autoreserve_threshold, autosack_threshold
FROM attendance_config;

DROP TABLE attendance_config;
ALTER TABLE attendance_config_new RENAME TO attendance_config;

-- 2. Update stored pardon_type values before rebuilding the pardons table.
UPDATE attendance_pardons SET pardon_type = 'ABSENT' WHERE pardon_type = 'NO_RSVP_ABSENT';

-- 3. Rebuild attendance_pardons with updated CHECK constraint.
DROP TABLE IF EXISTS attendance_pardons_new;
CREATE TABLE attendance_pardons_new (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    attendance_id  INTEGER NOT NULL
                   REFERENCES driver_round_attendance(id) ON DELETE CASCADE,
    pardon_type    TEXT    NOT NULL CHECK (pardon_type IN ('NO_RSVP', 'ABSENT', 'RSVP_ABSENT')),
    justification  TEXT    NOT NULL,
    granted_by     INTEGER NOT NULL,
    granted_at     TEXT    NOT NULL,
    UNIQUE (attendance_id, pardon_type)
);

INSERT INTO attendance_pardons_new
SELECT * FROM attendance_pardons;

DROP TABLE attendance_pardons;
ALTER TABLE attendance_pardons_new RENAME TO attendance_pardons;
