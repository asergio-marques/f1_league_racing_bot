-- Migration 060: a placement is committed once placements are confirmed with it standing (#220).
--
-- A driver placed while a season is in Placements, or placed mid-season in Ongoing, placements,
-- stands outside the championship until a league manager confirms placements: no roles, no
-- lineup, no check-in, no attendance points, no results or standings. `committed` records
-- which side of that line a placement is on, and every reader of the championship filters on
-- it.
--
-- A placement written without saying takes the default its season implies: committed where
-- the season's placements have been confirmed (ACTIVE, or ended), and uncommitted where they
-- have not (SETUP). The placement commands say so explicitly; the default is what keeps every
-- other writer — and every test fixture seating a driver in a running season — meaning what
-- it always meant.

ALTER TABLE driver_season_assignments ADD COLUMN committed INTEGER;

UPDATE driver_season_assignments
SET committed = CASE
    WHEN (SELECT status FROM seasons WHERE seasons.id = driver_season_assignments.season_id)
         IN ('ACTIVE', 'COMPLETED', 'CANCELLED') THEN 1
    ELSE 0
END;

CREATE TRIGGER IF NOT EXISTS driver_season_assignments_committed_default
AFTER INSERT ON driver_season_assignments
WHEN NEW.committed IS NULL
BEGIN
    UPDATE driver_season_assignments
    SET committed = CASE
        WHEN (SELECT status FROM seasons WHERE seasons.id = NEW.season_id)
             IN ('ACTIVE', 'COMPLETED', 'CANCELLED') THEN 1
        ELSE 0
    END
    WHERE id = NEW.id;
END;
