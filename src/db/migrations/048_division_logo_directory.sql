-- Migration 048: an eighth asset directory, for a division's own logo.
--
-- A league has had no way to put its identity on a generated graphic. The only route was to
-- bake a mark into every template file at build time, which fixes it for the whole league:
-- it cannot differ between divisions, and changing it means rebuilding fifteen files. This
-- makes the logo an asset class like any other, resolved by the two tiers and keyed on the
-- division's name, so a league drops `division_1.svg` into its folder and the graphic carries
-- their crest.
--
-- Three decisions were taken with it (2026-09-02), and they are recorded where each is
-- enforced rather than here: the class is drawn only where a league's own template declares
-- a `division_logo` slot, since nothing the bot ships declares one; drawing its fallback is
-- reported to nobody, because a league that has drawn no logo is in the ordinary state rather
-- than an incomplete one (BLANK_FALLBACK_ASSET_CLASSES in `image_constants`); and the class
-- is held to no shape at all, the template deciding it and two slots on one template being
-- free to decide differently.
--
-- A plain ADD COLUMN, following 044 rather than 043's table rebuild: nothing about the
-- existing columns changes and there is no default to migrate. The default points at
-- resources/league/ per 043's rule, and a fresh install draws the packaged blank through the
-- second tier without the command being run at all.
ALTER TABLE image_config
    ADD COLUMN division_logo_directory TEXT NOT NULL
    DEFAULT 'resources/league/division-logos';
