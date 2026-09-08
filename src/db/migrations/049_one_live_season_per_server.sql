-- Migration 049: a server holds at most one live season.
--
-- A season is "live" while it is SETUP or ACTIVE — the league is either building it or
-- running it. COMPLETED and CANCELLED seasons are archive and a server accumulates as many
-- as it has history for.
--
-- `/season setup` already refuses to start a second season in either state, so the rule was
-- intended from the beginning. Nothing enforced it below that one command, and a good deal
-- of code assumes it: `_get_active_season_id` in test_roster_service.py selects
-- `WHERE status IN ('ACTIVE','SETUP')` with no ORDER BY and takes the first row, and
-- `get_setup_or_active_season` does the same. With two live seasons those return an
-- arbitrary one of them, so `/test-mode roster add` seated drivers in one season's
-- divisions while `/season review` drew another's — a full roster listing beside an empty
-- lineup, neither command reporting a fault. Making the state impossible is what fixes that
-- class of bug at its root, rather than teaching each reader its own precedence.
--
-- Any pre-existing violation is resolved before the index is built. An ACTIVE season is
-- kept over a SETUP one — a season the league is *running* outranks a draft of the next,
-- and the draft can be rebuilt with `/season setup` where the running one cannot be
-- recovered — and the newest id breaks a tie within one status. Cancelling rather than
-- deleting preserves whatever hangs off the losing rows: CANCELLED is already the
-- immutable "this one did not run" state, and deleting a season with divisions would
-- orphan rounds, teams and assignments behind foreign keys that do not cascade.

-- 1. Cancel every live season but the one each server keeps.
UPDATE seasons
SET status = 'CANCELLED'
WHERE status IN ('SETUP', 'ACTIVE')
  AND id NOT IN (
      -- One row per server: ACTIVE ahead of SETUP, then the highest id.
      SELECT id FROM seasons AS keeper
      WHERE keeper.status IN ('SETUP', 'ACTIVE')
        AND keeper.id = (
            SELECT s2.id FROM seasons AS s2
            WHERE s2.server_id = keeper.server_id
              AND s2.status IN ('SETUP', 'ACTIVE')
            ORDER BY CASE s2.status WHEN 'ACTIVE' THEN 0 ELSE 1 END, s2.id DESC
            LIMIT 1
        )
  );

-- 2. Enforce it from here on. A partial index counts only the live rows, so archived
--    seasons stay unconstrained and a server keeps its whole history.
CREATE UNIQUE INDEX IF NOT EXISTS idx_seasons_one_live_per_server
    ON seasons(server_id)
    WHERE status IN ('SETUP', 'ACTIVE');
