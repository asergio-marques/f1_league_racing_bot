-- Migration 057: the lifecycle stage of a season (issue #220).
--
-- A season now passes through ten states — Configuration, Waiting, Signups, Placements,
-- Ongoing, Ongoing with signups open, Ongoing with placements, Pending completion,
-- Completed and Cancelled. They are held in `seasons.stage`.
--
-- `seasons.status` is kept, as the coarse grain the stage refines:
--
--   SETUP      CONFIGURATION, WAITING, SIGNUPS, PLACEMENTS   (placements never confirmed)
--   ACTIVE     ONGOING, ONGOING_SIGNUPS, ONGOING_PLACEMENTS, PENDING_COMPLETION
--   COMPLETED  COMPLETED
--   CANCELLED  CANCELLED
--
-- Nearly every existing reader of `status = 'ACTIVE'` means "placements confirmed and the
-- season being raced", which ACTIVE still means, so the coarse column keeps those readers
-- correct without a rewrite. The stage is what a reader consults when the finer state
-- matters.
--
-- Three triggers keep the two columns in step:
--
-- 1. A row inserted without a stage takes the stage its status implies: SETUP becomes
--    PLACEMENTS, ACTIVE becomes ONGOING. A season written with a status alone is therefore
--    one whose divisions may be built, or one being raced.
-- 2. A status changed without the stage carries the stage with it, keeping a stage already
--    inside the new status and taking the default otherwise.
-- 3. A stage written that its status does not allow is refused.

ALTER TABLE seasons ADD COLUMN stage TEXT;

UPDATE seasons SET stage = CASE status
    WHEN 'SETUP'     THEN 'PLACEMENTS'
    WHEN 'ACTIVE'    THEN 'ONGOING'
    WHEN 'COMPLETED' THEN 'COMPLETED'
    WHEN 'CANCELLED' THEN 'CANCELLED'
END;

CREATE TRIGGER IF NOT EXISTS seasons_stage_fill_on_insert
AFTER INSERT ON seasons
WHEN NEW.stage IS NULL
BEGIN
    UPDATE seasons SET stage = CASE NEW.status
        WHEN 'SETUP'     THEN 'PLACEMENTS'
        WHEN 'ACTIVE'    THEN 'ONGOING'
        WHEN 'COMPLETED' THEN 'COMPLETED'
        WHEN 'CANCELLED' THEN 'CANCELLED'
    END
    WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS seasons_stage_follow_status
AFTER UPDATE OF status ON seasons
WHEN NEW.status IS NOT OLD.status
BEGIN
    UPDATE seasons SET stage = CASE
        WHEN NEW.status = 'SETUP'
             AND NEW.stage IN ('CONFIGURATION', 'WAITING', 'SIGNUPS', 'PLACEMENTS')
            THEN NEW.stage
        WHEN NEW.status = 'SETUP' THEN 'PLACEMENTS'
        WHEN NEW.status = 'ACTIVE'
             AND NEW.stage IN ('ONGOING', 'ONGOING_SIGNUPS', 'ONGOING_PLACEMENTS',
                               'PENDING_COMPLETION')
            THEN NEW.stage
        WHEN NEW.status = 'ACTIVE' THEN 'ONGOING'
        ELSE NEW.status
    END
    WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS seasons_stage_matches_status
BEFORE UPDATE OF stage ON seasons
WHEN NOT (
       (NEW.status = 'SETUP'
        AND NEW.stage IN ('CONFIGURATION', 'WAITING', 'SIGNUPS', 'PLACEMENTS'))
    OR (NEW.status = 'ACTIVE'
        AND NEW.stage IN ('ONGOING', 'ONGOING_SIGNUPS', 'ONGOING_PLACEMENTS',
                          'PENDING_COMPLETION'))
    OR (NEW.status IN ('COMPLETED', 'CANCELLED') AND NEW.stage = NEW.status)
)
BEGIN
    SELECT RAISE(ABORT, 'season stage does not match its status');
END;

CREATE TRIGGER IF NOT EXISTS seasons_stage_matches_status_on_insert
BEFORE INSERT ON seasons
WHEN NEW.stage IS NOT NULL AND NOT (
       (NEW.status = 'SETUP'
        AND NEW.stage IN ('CONFIGURATION', 'WAITING', 'SIGNUPS', 'PLACEMENTS'))
    OR (NEW.status = 'ACTIVE'
        AND NEW.stage IN ('ONGOING', 'ONGOING_SIGNUPS', 'ONGOING_PLACEMENTS',
                          'PENDING_COMPLETION'))
    OR (NEW.status IN ('COMPLETED', 'CANCELLED') AND NEW.stage = NEW.status)
)
BEGIN
    SELECT RAISE(ABORT, 'season stage does not match its status');
END;
