-- Migration 062: the attendance configuration loses its server (issue #244).
--
-- attendance_config becomes the league's one row, keyed `id = 1` as 061 keyed the weather
-- horizons, and its cascading foreign key onto server_configs goes with the column —
-- reset_service deletes it by name instead. attendance_division_config keeps its key, the
-- division, and loses the server column it carried beside it: a division already names its
-- season, and the season needed no server to say whose it was.
--
-- Neither table is a parent, so neither drop cascades anywhere.

DROP TABLE IF EXISTS attendance_config_new;
CREATE TABLE attendance_config_new (
    id                       INTEGER PRIMARY KEY CHECK (id = 1),
    module_enabled           INTEGER NOT NULL DEFAULT 0,
    rsvp_notice_days         INTEGER NOT NULL DEFAULT 5,
    rsvp_last_notice_hours   INTEGER NOT NULL DEFAULT 24,
    rsvp_deadline_hours      INTEGER NOT NULL DEFAULT 2,
    no_rsvp_penalty          INTEGER NOT NULL DEFAULT 1,
    absent_penalty           INTEGER NOT NULL DEFAULT 1,
    no_show_penalty          INTEGER NOT NULL DEFAULT 1,
    autoreserve_threshold    INTEGER,
    autosack_threshold       INTEGER
);

INSERT INTO attendance_config_new (
    id, module_enabled, rsvp_notice_days, rsvp_last_notice_hours, rsvp_deadline_hours,
    no_rsvp_penalty, absent_penalty, no_show_penalty, autoreserve_threshold,
    autosack_threshold
)
SELECT 1, module_enabled, rsvp_notice_days, rsvp_last_notice_hours, rsvp_deadline_hours,
       no_rsvp_penalty, absent_penalty, no_show_penalty, autoreserve_threshold,
       autosack_threshold
FROM attendance_config
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);

DROP TABLE attendance_config;
ALTER TABLE attendance_config_new RENAME TO attendance_config;

DROP TABLE IF EXISTS attendance_division_config_new;
CREATE TABLE attendance_division_config_new (
    division_id            INTEGER PRIMARY KEY
                               REFERENCES divisions(id)
                               ON DELETE CASCADE,
    rsvp_channel_id        TEXT,
    attendance_channel_id  TEXT,
    attendance_message_id  TEXT
);

INSERT INTO attendance_division_config_new (
    division_id, rsvp_channel_id, attendance_channel_id, attendance_message_id
)
SELECT division_id, rsvp_channel_id, attendance_channel_id, attendance_message_id
FROM attendance_division_config;

DROP TABLE attendance_division_config;
ALTER TABLE attendance_division_config_new RENAME TO attendance_division_config;
