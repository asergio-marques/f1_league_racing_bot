-- Add result_status to rounds
ALTER TABLE rounds ADD COLUMN result_status TEXT NOT NULL DEFAULT 'PROVISIONAL';

-- Populate result_status from existing finalized flag
UPDATE rounds SET result_status = 'FINAL' WHERE finalized = 1;

-- Add penalty_channel_id to division_results_config
ALTER TABLE division_results_config ADD COLUMN penalty_channel_id TEXT;

-- penalty_records: stores each applied penalty from the wizard
CREATE TABLE IF NOT EXISTS penalty_records (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    driver_session_result_id INTEGER NOT NULL REFERENCES driver_session_results(id),
    penalty_type             TEXT    NOT NULL,
    time_seconds             INTEGER,
    description              TEXT    NOT NULL,
    justification            TEXT    NOT NULL,
    applied_by               TEXT    NOT NULL,
    applied_at               TEXT    NOT NULL,
    announcement_channel_id  TEXT
);

-- appeal_records: stores each applied appeal correction from the appeals wizard
CREATE TABLE IF NOT EXISTS appeal_records (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    driver_session_result_id INTEGER NOT NULL REFERENCES driver_session_results(id),
    status                   TEXT    NOT NULL DEFAULT 'UPHELD',
    penalty_type             TEXT    NOT NULL,
    time_seconds             INTEGER,
    description              TEXT    NOT NULL,
    justification            TEXT    NOT NULL,
    submitted_by             TEXT    NOT NULL,
    submitted_at             TEXT    NOT NULL,
    announcement_channel_id  TEXT
);
