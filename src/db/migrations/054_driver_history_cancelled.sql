-- Migration 054: a driver's history records whether the division was cancelled.
--
-- A cancelled division is league history. It happened, drivers raced in it, and their standing
-- when it was called off is a real record — it simply should not be read as a division that ran
-- to its end. Until now nothing distinguished the two, and a season that was cancelled outright
-- left no trace in anybody's history at all, because `/season cancel` never wrote an entry.
--
-- The flag hangs off the *division*, not the season, because cancellation only ever reaches a
-- driver through one. Cancelling a season cancels each of its divisions, and cancelling a
-- division cancels each of its rounds not yet run; so "was this driver's division cancelled" is
-- the whole question, however the cancellation was ordered.
--
-- DEFAULT 0 needs no backfill. `execute_season_end` is the only thing that has ever written to
-- this table and `/season complete` is its only caller — and that command could never be got
-- past, because it gated on `rounds.finalized`, a column nothing ever set (issue #154). The
-- table is therefore empty on every database in existence, and 0 is right for the rows a future
-- completion writes.
ALTER TABLE driver_history_entries ADD COLUMN cancelled INTEGER NOT NULL DEFAULT 0;

-- A driver gains one entry per division per season, and writing it twice is a fault rather than
-- a second fact. Ending a season is a run of separate writes — roles revoked, history written,
-- classifications posted, the season's own row flipped last — and nothing makes them atomic. A
-- process that dies part-way therefore leaves a season still ACTIVE with its history already
-- written, and the retry the league is told to run would append a duplicate set. Nothing
-- afterwards would notice: the archive is never edited, and no reader could tell which of the
-- two rows to believe.
--
-- The index makes the duplicate impossible rather than asking every writer to remember, and it
-- is what lets `INSERT OR IGNORE` in `_write_driver_history_entries` stand in for a transaction
-- spanning the whole sequence. `division_name` is part of the key because a driver moved between
-- divisions mid-season legitimately earns an entry for each; only the same division twice is the
-- error. Pinned by `test_writing_the_history_twice_adds_nothing`.
CREATE UNIQUE INDEX IF NOT EXISTS idx_driver_history_unique
    ON driver_history_entries(driver_profile_id, season_number, division_name);
