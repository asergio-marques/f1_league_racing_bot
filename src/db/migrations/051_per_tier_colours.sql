-- Migration 051: a tier's own colours, and the toggle that turns them on.
--
-- A league has had two ways to tell its tiers apart on a graphic and both are coarse: author a
-- separate template per tier, which means maintaining fifteen files for each of them, or drop a
-- per-division logo in (048), which adds a crest but cannot touch the palette the sheet is drawn
-- in. This adds the third: one template, marked by the league with the slots it wants recoloured,
-- and a colour per slot per tier. The template stays a valid picture that shows its own colours;
-- the palette is injected at render as a stylesheet whose rules win on document order.
--
-- Four decisions were taken with it (2026-09-08), and they are recorded where each is enforced
-- rather than here.
--
-- **Keyed on the division's NAME, not its id.** Divisions are rows belonging to a season, so a
-- new season creates new ones; an id-keyed table would silently lose every league's palette at
-- each rollover, which is the one moment a league is least likely to re-check its graphics. The
-- slug is produced by `normalise()` in `utils.asset_resolver` — the same rule that makes
-- `Division 1` look for `division_1.svg` — so a tier's colours and its logo are found by one
-- spelling. The cost is accepted and is real: a division renamed after its colours were set is
-- a division with no colours, exactly as it is a division with no logo.
--
-- **A key-value table rather than a column per colour.** The set of slots is the league's to
-- invent: a slot is whatever its own template marks, so there is no fixed vocabulary to make
-- columns out of. `slot` is validated before it is ever written — it is interpolated into a CSS
-- selector, and `normalise_slot` in `utils.svg_palette` is the only gate.
--
-- **No row means the template's own colour.** There is deliberately no command to clear a slot:
-- the validation below blocks the state a clear would produce, so it is never a valid
-- destination, and a tier meant to draw in the house colour sets that colour explicitly. Rows
-- outlive a slot the template stops marking and are inert.
--
-- **Off by default.** `per_tier_colour_enabled` defaults to 0, so no existing league and no fresh
-- install changes behaviour by being upgraded, and nothing the bot ships declares a slot in any
-- case. While it is off nothing is injected and nothing is validated. While it is on, every slot
-- a configured template marks must have a colour for every division of the season — reported at
-- `/images config view`, at `/season review`, and as a blocking reason on the aspect toggle, all
-- three from the one `AspectStatus` list those surfaces already share.
--
-- A plain CREATE plus an ADD COLUMN, following 044 and 048 rather than 043's table rebuild:
-- nothing about the existing columns changes and there is no default to migrate.

CREATE TABLE IF NOT EXISTS image_tier_colour (
    server_id      INTEGER NOT NULL REFERENCES server_configs(server_id) ON DELETE CASCADE,
    division_slug  TEXT    NOT NULL,
    slot           TEXT    NOT NULL,
    colour         TEXT    NOT NULL,
    PRIMARY KEY (server_id, division_slug, slot)
);

ALTER TABLE image_config
    ADD COLUMN per_tier_colour_enabled INTEGER NOT NULL DEFAULT 0;
