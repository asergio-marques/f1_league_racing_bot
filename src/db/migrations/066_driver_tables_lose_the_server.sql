-- Migration 066: a driver, their accounts and their history lose their server (issue #244).
--
-- A driver profile is unique by its current account, and an account belongs to at most one
-- driver, across the whole league rather than within a server of it. driver_history_entries
-- keeps the account it was written under and drops the server beside it.
--
-- driver_profiles is the parent of seven tables, by NO ACTION, SET NULL and CASCADE keys
-- alike, so it cannot be dropped with enforcement on: the drop would be refused, or would
-- null or delete the rows that name it. The rebuild runs with enforcement off, as 009, 053
-- and 057 did; ids are copied verbatim, so every reference still resolves, and
-- tests/unit/test_migration_066_driver_tables.py runs a foreign_key_check to hold that
-- (inside a script the pragma reports and cannot fail). The two triggers that keep a
-- driver's accounts in step with the profile go with the table and are recreated without
-- the column.
--
-- Rows are copied for the configured server only. Nothing is live, and no database holds a
-- second, but a row belonging to no configured server is nobody's. History written before
-- any server was recorded carries none, and is kept.

PRAGMA foreign_keys = OFF;

-- 1. The profiles.
DROP TABLE IF EXISTS driver_profiles_new;
CREATE TABLE driver_profiles_new (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user_id    TEXT    NOT NULL UNIQUE,
    current_state      TEXT    NOT NULL,
    former_driver      INTEGER NOT NULL DEFAULT 0,
    is_test_driver     INTEGER NOT NULL DEFAULT 0,
    test_display_name  TEXT,
    test_nationality   TEXT
);
INSERT INTO driver_profiles_new (
    id, discord_user_id, current_state, former_driver, is_test_driver, test_display_name,
    test_nationality
)
SELECT id, discord_user_id, current_state, former_driver, is_test_driver, test_display_name,
       test_nationality
FROM driver_profiles
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE driver_profiles;
ALTER TABLE driver_profiles_new RENAME TO driver_profiles;

-- 2. Every account a driver has held, unique to the league.
DROP TABLE IF EXISTS driver_accounts_new;
CREATE TABLE driver_accounts_new (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    driver_profile_id INTEGER NOT NULL REFERENCES driver_profiles(id) ON DELETE CASCADE,
    discord_user_id   TEXT    NOT NULL UNIQUE
);
INSERT INTO driver_accounts_new (id, driver_profile_id, discord_user_id)
SELECT id, driver_profile_id, discord_user_id FROM driver_accounts
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1)
  AND driver_profile_id IN (SELECT id FROM driver_profiles);
DROP TABLE driver_accounts;
ALTER TABLE driver_accounts_new RENAME TO driver_accounts;
CREATE INDEX IF NOT EXISTS idx_driver_accounts_profile ON driver_accounts(driver_profile_id);

-- 3. The triggers that write a driver's accounts, recreated on the rebuilt profiles.
CREATE TRIGGER IF NOT EXISTS driver_account_on_profile_insert
AFTER INSERT ON driver_profiles
BEGIN
    INSERT INTO driver_accounts (driver_profile_id, discord_user_id)
    VALUES (NEW.id, NEW.discord_user_id);
END;

CREATE TRIGGER IF NOT EXISTS driver_account_on_current_change
AFTER UPDATE OF discord_user_id ON driver_profiles
WHEN NOT EXISTS (
    SELECT 1 FROM driver_accounts
    WHERE discord_user_id = NEW.discord_user_id
      AND driver_profile_id = NEW.id
)
BEGIN
    INSERT INTO driver_accounts (driver_profile_id, discord_user_id)
    VALUES (NEW.id, NEW.discord_user_id);
END;

-- 4. The history, keeping the account each entry was written under.
DROP TABLE IF EXISTS driver_history_entries_new;
CREATE TABLE driver_history_entries_new (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
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
INSERT INTO driver_history_entries_new (
    id, discord_user_id, driver_profile_id, season_number, division_name, division_tier,
    final_position, final_points, points_gap_to_winner, cancelled
)
SELECT id, discord_user_id,
       CASE WHEN driver_profile_id IN (SELECT id FROM driver_profiles)
            THEN driver_profile_id END,
       season_number, division_name, division_tier, final_position, final_points,
       points_gap_to_winner, cancelled
FROM driver_history_entries
WHERE server_id IS NULL OR server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE driver_history_entries;
ALTER TABLE driver_history_entries_new RENAME TO driver_history_entries;
CREATE UNIQUE INDEX IF NOT EXISTS idx_driver_history_unique
    ON driver_history_entries(driver_profile_id, season_number, division_name);
CREATE INDEX IF NOT EXISTS idx_driver_history_identity
    ON driver_history_entries(discord_user_id);

PRAGMA foreign_keys = ON;
