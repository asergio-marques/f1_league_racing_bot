-- Migration 032: Attendance tracking — finalization columns, sheet message ID,
--                and attendance pardons table.

-- 1. Add finalization output columns to driver_round_attendance.
--    Both are nullable until the penalty-finalization pipeline runs.
ALTER TABLE driver_round_attendance ADD COLUMN points_awarded   INTEGER;
ALTER TABLE driver_round_attendance ADD COLUMN total_points_after INTEGER;

-- 2. Add attendance sheet message ID to attendance_division_config.
--    Nullable: not set until the first sheet is posted for a division.
ALTER TABLE attendance_division_config ADD COLUMN attendance_message_id TEXT;

-- 3. Create the attendance_pardons table.
--    One row per driver per round per pardon type.
--    Unique constraint prevents duplicate pardon types for the same attendance row.
CREATE TABLE IF NOT EXISTS attendance_pardons (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    attendance_id  INTEGER NOT NULL
                   REFERENCES driver_round_attendance(id) ON DELETE CASCADE,
    pardon_type    TEXT    NOT NULL CHECK (pardon_type IN ('NO_RSVP', 'NO_ATTEND', 'NO_SHOW')),
    justification  TEXT    NOT NULL,
    granted_by     INTEGER NOT NULL,
    granted_at     TEXT    NOT NULL,
    UNIQUE (attendance_id, pardon_type)
);
