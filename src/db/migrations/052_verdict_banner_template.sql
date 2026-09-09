-- Migration 052: a sixteenth template, for the header of a batch of verdicts.
--
-- The verdicts channel gave a reader nothing to tell one round's decisions from another's.
-- Each verdict is posted on its own, and once the `verdicts` aspect is enabled the message
-- text is cut down to the driver's mention alone -- the heading naming the season, the
-- division and the round survives only in the textual fallback. This adds a graphic that
-- heads the run: one banner per batch, naming the round the decisions below it were taken
-- upon.
--
-- It is a ninth **aspect** and not a second template of the `verdicts` one, so that a league
-- may draw verdict cards without banners or banners without cards, and so that an unusable
-- banner file cannot stop a verdict being posted. The aspect toggle itself needs no
-- migration: `image_aspect_toggles` is keyed by aspect name and a row appears when one is
-- set, so a league that has never toggled the banner reads as disabled, which is what a
-- feature nobody asked for should read as.
--
-- Two decisions were taken with it (2026-09-09) and are recorded where they are enforced
-- rather than here: the banner names no session, one review closing on a whole round and
-- being free to sanction a qualifying entry and a race entry in the same breath; and its
-- message carries no text at all, the attachment's filename being what a search has to go
-- on. Both live in the field catalogue and the posting module respectively.
--
-- A plain ADD COLUMN, following 048. Nothing about the existing columns changes, and the
-- default names the file the module ships, so a fresh install resolves it without the
-- command being run at all.
ALTER TABLE image_config
    ADD COLUMN verdict_banner_template TEXT NOT NULL
    DEFAULT 'verdict_banner_template.svg';
