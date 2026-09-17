-- Migration 061: a history entry names its driver by identifier, and outlives the profile (#220).
--
-- A driver created by test mode keeps their history when test mode deletes them at the end of
-- their season, so that a driver created again under the same identifier in a later season
-- holds it as their own. An entry keyed only by the profile could not survive the profile.
--
-- So each entry now carries the server and the Discord identifier it belongs to, and its
-- profile reference is set null when the profile goes rather than refusing the deletion. The
-- driver pass that ends a season still deletes the entries of a real driver it deletes — the
-- archive keeps none for a driver who never raced — and does so explicitly.
--
-- A rebuild, because SQLite cannot relax a NOT NULL or change a foreign key's action in place.

PRAGMA foreign_keys = OFF;

CREATE TABLE IF NOT EXISTS driver_history_entries_new (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id            INTEGER,
    discord_user_id      TEXT,
    driver_profile_id    INTEGER REFERENCES driver_profiles(id) ON DELETE SET NULL,
    season_number        INTEGER NOT NULL,
    division_name        TEXT    NOT NULL,
    division_tier        INTEGER NOT NULL DEFAULT 0,
    final_position       INTEGER NOT NULL DEFAULT 0,
    final_points         INTEGER NOT NULL DEFAULT 0,
    points_gap_to_winner INTEGER NOT NULL DEFAULT 0,
    cancelled            INTEGER NOT NULL DEFAULT 0
);

INSERT INTO driver_history_entries_new
    (id, server_id, discord_user_id, driver_profile_id, season_number, division_name,
     division_tier, final_position, final_points, points_gap_to_winner, cancelled)
SELECT h.id, dp.server_id, dp.discord_user_id, h.driver_profile_id, h.season_number,
       h.division_name, h.division_tier, h.final_position, h.final_points,
       h.points_gap_to_winner, h.cancelled
FROM driver_history_entries h
LEFT JOIN driver_profiles dp ON dp.id = h.driver_profile_id;

DROP TABLE driver_history_entries;
ALTER TABLE driver_history_entries_new RENAME TO driver_history_entries;

CREATE UNIQUE INDEX IF NOT EXISTS idx_driver_history_unique
    ON driver_history_entries(driver_profile_id, season_number, division_name);
CREATE INDEX IF NOT EXISTS idx_driver_history_identity
    ON driver_history_entries(server_id, discord_user_id);

PRAGMA foreign_keys = ON;
