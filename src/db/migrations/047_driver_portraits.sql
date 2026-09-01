-- Migration 047: driver portraits obtained from Discord, and the three settings governing it.
--
-- The lineup already keys a driver's portrait on their Discord user ID and looks for it in
-- the configured driver image directory, which ships empty -- so every portrait resolves to
-- the packaged fallback until a league draws forty faces by hand. The module can obtain them
-- itself from the server profile picture of each driver's account, and this migration carries
-- what that needs.
--
-- `driver_portraits` does two jobs, and the second is the load-bearing one.
--
--   1. `avatar_key` is Discord's own hash of the picture, carried on the Member object the
--      lineup already fetches for its display names. Comparing it costs no HTTP at all, so a
--      render that changes nothing downloads nothing.
--   2. The table is the **ownership register**. A portrait file with no row here was placed
--      by the league itself, so the bot never overwrites it and never fetches over it. That
--      is what makes writing into `resources/league/drivers/` safe: the bot only ever touches
--      filenames it created, and a league's own artwork always wins.
--
-- Deleting a row therefore does not merely forget a hash -- it disowns the file, after which
-- the file is treated as the league's. The service deletes the file and the row together.
--
-- The four settings are columns on image_config rather than a table of their own, matching
-- every other image setting. Defaults encode the feature being opt-in: `use_pfp` off, so a
-- league that upgrades has nothing obtained for it until it asks; `pfp_prerender` on, so that
-- enabling the feature alone is a working configuration; `pfp_daily` off with a time of
-- 03:00, read as UTC (the scheduler is UTC throughout, and a stored offset would drift
-- against daylight saving).
CREATE TABLE IF NOT EXISTS driver_portraits (
    server_id        INTEGER NOT NULL,
    discord_user_id  TEXT    NOT NULL,
    avatar_key       TEXT    NOT NULL,
    fetched_at       TEXT    NOT NULL,
    PRIMARY KEY (server_id, discord_user_id)
);

ALTER TABLE image_config ADD COLUMN use_pfp        INTEGER NOT NULL DEFAULT 0;
ALTER TABLE image_config ADD COLUMN pfp_prerender  INTEGER NOT NULL DEFAULT 1;
ALTER TABLE image_config ADD COLUMN pfp_daily      INTEGER NOT NULL DEFAULT 0;
ALTER TABLE image_config ADD COLUMN pfp_daily_time TEXT    NOT NULL DEFAULT '03:00';
