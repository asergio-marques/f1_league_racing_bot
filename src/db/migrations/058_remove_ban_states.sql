-- ── 058: The driver profile carries no bans ────────────────────────────────
--
-- SEASON_BANNED and LEAGUE_BANNED were driver states nothing could reach. They
-- were defined in the enum and wired into the transition table, but no command
-- was ever built to impose or lift a ban, so the four counters below were only
-- ever written as zero and no league could see either state (issue #221).
--
-- Sanctions belong to the stewarding module, which will bring whatever shape of
-- ban it needs along with the commands that issue it. race_ban_count goes with
-- the rest: the bot can issue neither a race ban nor a qualifying ban, and the
-- profile counted only one of that pair.
--
-- The UPDATE should touch nothing — no code path ever wrote either state — but
-- a row left at one would raise ValueError on the next read of the profile,
-- well away from the migration that allowed it.

UPDATE driver_profiles
   SET current_state = 'NOT_SIGNED_UP'
 WHERE current_state IN ('SEASON_BANNED', 'LEAGUE_BANNED');

ALTER TABLE driver_profiles DROP COLUMN race_ban_count;
ALTER TABLE driver_profiles DROP COLUMN season_ban_count;
ALTER TABLE driver_profiles DROP COLUMN league_ban_count;
ALTER TABLE driver_profiles DROP COLUMN ban_races_remaining;
