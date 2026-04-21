-- 036_drop_legacy_result_table.sql
--
-- Removes the legacy driver_session_results table now that all result
-- data is stored exclusively in qualifying_session_results and
-- race_session_results.
--
-- penalty_records and appeal_records had a NOT NULL FK to
-- driver_session_results.  We recreate both tables without that column
-- (the race_result_id / qual_result_id nullable columns added in
-- migration 035 continue to exist and are the canonical FK links).

-- ---------------------------------------------------------------------------
-- Recreate penalty_records without driver_session_result_id
-- ---------------------------------------------------------------------------
CREATE TABLE penalty_records_new (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    race_result_id          INTEGER REFERENCES race_session_results(id),
    qual_result_id          INTEGER REFERENCES qualifying_session_results(id),
    penalty_type            TEXT    NOT NULL,
    time_seconds            INTEGER,
    description             TEXT    NOT NULL,
    justification           TEXT    NOT NULL,
    applied_by              TEXT    NOT NULL,
    applied_at              TEXT    NOT NULL,
    announcement_channel_id TEXT
);

INSERT INTO penalty_records_new
    (id, race_result_id, qual_result_id, penalty_type, time_seconds,
     description, justification, applied_by, applied_at, announcement_channel_id)
SELECT id, race_result_id, qual_result_id, penalty_type, time_seconds,
       description, justification, applied_by, applied_at, announcement_channel_id
FROM penalty_records;

DROP TABLE penalty_records;
ALTER TABLE penalty_records_new RENAME TO penalty_records;

-- ---------------------------------------------------------------------------
-- Recreate appeal_records without driver_session_result_id
-- ---------------------------------------------------------------------------
CREATE TABLE appeal_records_new (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    race_result_id          INTEGER REFERENCES race_session_results(id),
    qual_result_id          INTEGER REFERENCES qualifying_session_results(id),
    status                  TEXT    NOT NULL DEFAULT 'UPHELD',
    penalty_type            TEXT    NOT NULL,
    time_seconds            INTEGER,
    description             TEXT    NOT NULL,
    justification           TEXT    NOT NULL,
    submitted_by            TEXT    NOT NULL,
    submitted_at            TEXT    NOT NULL,
    announcement_channel_id TEXT
);

INSERT INTO appeal_records_new
    (id, race_result_id, qual_result_id, status, penalty_type, time_seconds,
     description, justification, submitted_by, submitted_at, announcement_channel_id)
SELECT id, race_result_id, qual_result_id, status, penalty_type, time_seconds,
       description, justification, submitted_by, submitted_at, announcement_channel_id
FROM appeal_records;

DROP TABLE appeal_records;
ALTER TABLE appeal_records_new RENAME TO appeal_records;

-- ---------------------------------------------------------------------------
-- Drop the legacy table
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS driver_session_results;
