-- Migration 044: an eighth asset directory, for the standings result highlights.
--
-- The chips drawn beneath a race cell of either standings grid -- a podium plate, a points
-- tint, the fastest-lap mark -- were painted from `.highlight_*` rules in the template's own
-- stylesheet. They are now artwork, resolved as every other asset class is, so that a league
-- may draw the mark it wants rather than choose a flat colour for the whole cell.
--
-- The class is a **closed set**, as `marker` and `weather` are: its five data (p1, p2, p3,
-- points, fastest_lap) are the module's own vocabulary rather than names a league chose, so a
-- league whose folder lacks one is given the bot's own file rather than a generic fallback.
--
-- A plain ADD COLUMN, unlike 043's table rebuild: nothing about the existing columns changes
-- and there is no default to migrate. The default points at resources/league/ per 043's rule,
-- and a fresh install still draws every chip through the packaged second tier.
ALTER TABLE image_config
    ADD COLUMN standings_highlight_directory TEXT NOT NULL
    DEFAULT 'resources/league/standings-highlights';
