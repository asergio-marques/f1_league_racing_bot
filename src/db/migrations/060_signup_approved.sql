-- ── 060: A signup records whether it was approved ──────────────────────────────
--
-- A driver's signup is read by the graphics, the placement lists and the review — "the
-- driver's signup" is one record chosen from all they have made. Since a driver owns every
-- account they have raced under (migration 059, issue #243), that choice spans accounts, and
-- the usual merge leaves a seated driver's approved signup beside a newer one on their new
-- account that the league rejected. The approved one is theirs (decided 2026-09-18), and
-- nothing recorded which that was: a signup kept no outcome.
--
-- Set when a signup is approved, and cleared when an approved driver is turned down. Rows
-- already stored read as not approved: no league runs the bot live yet, so nothing is
-- inferred.

ALTER TABLE signup_records ADD COLUMN approved INTEGER NOT NULL DEFAULT 0;
