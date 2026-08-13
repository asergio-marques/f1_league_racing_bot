-- Migration 041: Constructor standings message id
--
-- The textual flow posts ONE message carrying both championships, so one column named it.
-- The image flow posts TWO — the driver standings first and the constructor standings
-- after — and each must be deletable and replaceable without disturbing the other:
-- Constitution XIV.4 makes the unit of failure one graphic, and XIV.7 (v4.5.0) puts a
-- fallback at that same grain, so one championship may fall back to text while the other
-- stands as a picture. A single column cannot name two messages.
--
-- Written on the row of the top-ranked driver, exactly as standings_message_id already is,
-- and written on every posting — textual or graphic — so the two flows never disagree about
-- which message is which. The textual flow leaves this one null, which is also the state
-- every existing row is already in, so no backfill is required.
--
-- INTEGER to match the sibling column, not TEXT: a Discord snowflake fits SQLite's 64-bit
-- integer, and a column of a different type beside it would invite a comparison that fails
-- silently.

ALTER TABLE driver_standings_snapshots ADD COLUMN constructor_standings_message_id INTEGER;
