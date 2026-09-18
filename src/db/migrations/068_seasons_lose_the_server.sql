-- Migration 068: a season loses its server (issue #244).
--
-- The league holds its seasons, and at most one of them is live — in setup or active — at
-- a time. That rule was a partial unique index on the server; with no server to key it, the
-- index is on a constant, which admits one live row across the table.
--
-- seasons is the parent of twelve tables, by NO ACTION and CASCADE keys alike, so it cannot
-- be dropped with enforcement on: the drop would be refused, or would delete every season's
-- divisions, rounds and results. The rebuild runs with enforcement off, as 009, 053, 057
-- and 066 did; ids are copied verbatim, so every reference still resolves, and
-- tests/unit/test_migration_068_seasons.py runs a foreign_key_check to hold that. The four
-- triggers that keep a season's stage in step with its status go with the table and are
-- recreated exactly as 053 and 057 left them.
--
-- The foreign key onto server_configs goes with the column. It had no action: deleting the
-- configuration was refused while a season existed, and reset_service deletes the seasons
-- first on a full reset, as it always has.
--
-- Rows are copied for the configured server only. Nothing is live, and no database holds a
-- second, but a season belonging to no configured server is nobody's.

PRAGMA foreign_keys = OFF;

-- A trigger on another table reads seasons, and RENAME checks every trigger in the schema:
-- between the drop and the rename below there is no seasons for it to name, and the rename
-- would fail. It is set aside here and recreated, unchanged, once seasons stands again.
DROP TRIGGER IF EXISTS driver_season_assignments_committed_default;

DROP TABLE IF EXISTS seasons_new;
CREATE TABLE seasons_new (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    start_date     TEXT    NOT NULL,  -- ISO date YYYY-MM-DD
    status         TEXT    NOT NULL DEFAULT 'SETUP',  -- SETUP | ACTIVE | COMPLETED | CANCELLED
    season_number  INTEGER NOT NULL DEFAULT 0,
    game_edition   INTEGER NOT NULL DEFAULT 0,
    stage          TEXT
);
INSERT INTO seasons_new (id, start_date, status, season_number, game_edition, stage)
SELECT id, start_date, status, season_number, game_edition, stage FROM seasons
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE seasons;
ALTER TABLE seasons_new RENAME TO seasons;

-- At most one live season in the league.
CREATE UNIQUE INDEX IF NOT EXISTS idx_seasons_one_live
    ON seasons((1))
    WHERE status IN ('SETUP', 'ACTIVE');

CREATE TRIGGER IF NOT EXISTS seasons_stage_fill_on_insert
AFTER INSERT ON seasons
WHEN NEW.stage IS NULL AND NEW.status IN ('SETUP', 'ACTIVE', 'COMPLETED', 'CANCELLED')
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

PRAGMA foreign_keys = ON;
