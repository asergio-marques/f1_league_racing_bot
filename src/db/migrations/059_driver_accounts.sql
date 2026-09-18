-- ── 059: A driver owns every Discord account they have raced under ────────────
--
-- A driver was their Discord account: `driver_profiles.discord_user_id` was the
-- whole of their identity, and every result, standing and signup names them by it.
-- A person who changed account could keep their history only by having every record
-- rewritten onto the new one, completed seasons included (issue #243).
--
-- A profile now owns a list of accounts. `driver_profiles.discord_user_id` keeps its
-- meaning — the **current** account, the one everything is posted to — and the list
-- holds it together with every account the driver held before. Any of them identifies
-- the driver; no record is ever rewritten from one to another.
--
-- An account belongs to at most one driver in a league, which the unique key holds.
-- Another league's profile for the same person is a different driver.
--
-- Written by triggers, as the division memberships of migration 057 are, so that every
-- path creating a profile or changing its current account lists it alike. The insert
-- trigger is a plain INSERT on purpose: creating a profile on an account that is already
-- another driver's fails, rather than leaving that account with two owners. The update
-- trigger adds the account only when the profile does not list it already, so making a
-- past account current again changes nothing in the list — and taking an account another
-- driver lists still fails.

CREATE TABLE IF NOT EXISTS driver_accounts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id         INTEGER NOT NULL,
    driver_profile_id INTEGER NOT NULL REFERENCES driver_profiles(id) ON DELETE CASCADE,
    discord_user_id   TEXT    NOT NULL,
    UNIQUE (server_id, discord_user_id)
);

CREATE INDEX IF NOT EXISTS idx_driver_accounts_profile
    ON driver_accounts(driver_profile_id);

INSERT OR IGNORE INTO driver_accounts (server_id, driver_profile_id, discord_user_id)
SELECT server_id, id, discord_user_id FROM driver_profiles;

CREATE TRIGGER IF NOT EXISTS driver_account_on_profile_insert
AFTER INSERT ON driver_profiles
BEGIN
    INSERT INTO driver_accounts (server_id, driver_profile_id, discord_user_id)
    VALUES (NEW.server_id, NEW.id, NEW.discord_user_id);
END;

CREATE TRIGGER IF NOT EXISTS driver_account_on_current_change
AFTER UPDATE OF discord_user_id ON driver_profiles
WHEN NOT EXISTS (
    SELECT 1 FROM driver_accounts
    WHERE server_id = NEW.server_id
      AND discord_user_id = NEW.discord_user_id
      AND driver_profile_id = NEW.id
)
BEGIN
    INSERT INTO driver_accounts (server_id, driver_profile_id, discord_user_id)
    VALUES (NEW.server_id, NEW.id, NEW.discord_user_id);
END;
