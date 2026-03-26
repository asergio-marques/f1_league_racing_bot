-- Migration 020: Season Archive & Driver Profile Identity
-- Adds game_edition to seasons; adds driver_profile_id FK to result and standings tables.

-- 1. seasons: add game_edition (positive integer; 0 = legacy/unset)
ALTER TABLE seasons ADD COLUMN game_edition INTEGER NOT NULL DEFAULT 0;

-- 2. driver_session_results: add stable driver profile FK
ALTER TABLE driver_session_results
    ADD COLUMN driver_profile_id INTEGER REFERENCES driver_profiles(id);

-- 3. driver_standings_snapshots: add stable driver profile FK
ALTER TABLE driver_standings_snapshots
    ADD COLUMN driver_profile_id INTEGER REFERENCES driver_profiles(id);

-- 4. Backfill driver_profile_id on driver_session_results
UPDATE driver_session_results
SET driver_profile_id = (
    SELECT dp.id
    FROM driver_profiles dp
    WHERE CAST(dp.discord_user_id AS INTEGER) = driver_session_results.driver_user_id
      AND dp.server_id = (
          SELECT s.server_id
          FROM session_results sr
          JOIN rounds r    ON r.id  = sr.round_id
          JOIN divisions d ON d.id  = r.division_id
          JOIN seasons s   ON s.id  = d.season_id
          WHERE sr.id = driver_session_results.session_result_id
      )
    LIMIT 1
)
WHERE driver_profile_id IS NULL;

-- 5. Backfill driver_profile_id on driver_standings_snapshots
UPDATE driver_standings_snapshots
SET driver_profile_id = (
    SELECT dp.id
    FROM driver_profiles dp
    WHERE CAST(dp.discord_user_id AS INTEGER) = driver_standings_snapshots.driver_user_id
      AND dp.server_id = (
          SELECT s.server_id
          FROM rounds r
          JOIN divisions d ON d.id = r.division_id
          JOIN seasons s   ON s.id = d.season_id
          WHERE r.id = driver_standings_snapshots.round_id
      )
    LIMIT 1
)
WHERE driver_profile_id IS NULL;

-- 6. Indexes for FK columns
CREATE INDEX IF NOT EXISTS idx_dsr_driver_profile
    ON driver_session_results(session_result_id, driver_profile_id);

CREATE INDEX IF NOT EXISTS idx_dss_driver_profile
    ON driver_standings_snapshots(division_id, driver_profile_id);
