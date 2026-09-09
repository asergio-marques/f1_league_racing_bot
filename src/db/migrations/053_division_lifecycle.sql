-- Migration 053: a division has a life of its own, and `rounds.finalized` never had one.
--
-- Three faults are settled here, all of them in the same lifecycle.
--
-- 1. `divisions.status` lost its constraint without anybody noticing. Migration 007 added the
--    column as `CHECK (status IN ('ACTIVE','CANCELLED'))` with a default of 'ACTIVE'. Migration
--    009 rebuilt the table to make `forecast_channel_id` nullable and, in copying the definition
--    across, dropped the CHECK and changed the default to 'SETUP'. Nothing failed, so nothing
--    reported it, and the column has been unconstrained ever since.
--
-- 2. Nothing has ever written 'ACTIVE' to a division. Every insert path — `add_division`,
--    `duplicate_division`, `save_pending_snapshot` — omits the column and takes 009's 'SETUP'
--    default, and no code moves it on. So every division a league has ever run sat at 'SETUP'
--    for its whole life, and `/season cancel`'s "post a notice to each active division" loop
--    selected nothing and told nobody.
--
-- 3. `rounds.finalized` is dead. Migration 019 added it, saying the post-submission penalty
--    review would set it to 1; that was never implemented. Migration 026 read it once to seed
--    `result_status`, which became the real state machine
--    (PROVISIONAL → POST_RACE_PENALTY → FINAL). The column has been 0 on every row since, yet
--    `/season complete` gated on it — so a season could never be completed however completely it
--    was raced, and because a server holds one live season at a time, a league that finished its
--    championship could not begin the next one (issue #154).
--
-- The lifecycle these support: a round is finished when its results are FINAL or it is cancelled;
-- a division is finished when every one of its rounds is; a season may be completed when every
-- division is finished or cancelled.

PRAGMA foreign_keys = OFF;

-- ── 1. Rebuild divisions with the CHECK restored and 'FINISHED' admitted ──────────────
--
-- A rebuild rather than an ALTER because SQLite cannot add a CHECK to an existing column. The
-- column list is today's, not 009's — the lineup and calendar columns were added after it.
CREATE TABLE IF NOT EXISTS divisions_new (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id           INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    name                TEXT    NOT NULL,
    mention_role_id     INTEGER NOT NULL,
    forecast_channel_id INTEGER,
    status              TEXT    NOT NULL DEFAULT 'SETUP'
                            CHECK (status IN ('SETUP', 'ACTIVE', 'FINISHED', 'CANCELLED')),
    tier                INTEGER NOT NULL DEFAULT 1,
    lineup_channel_id   INTEGER,
    calendar_channel_id INTEGER,
    lineup_message_id   INTEGER,
    calendar_message_id TEXT
);

-- The backfill rides on the copy. Every existing row carries 'SETUP' unless it was cancelled, so
-- the season it belongs to is the only evidence of what it should say. A cancelled division stays
-- cancelled whatever became of its season — it was called off first, and that is the truer record.
INSERT INTO divisions_new
    (id, season_id, name, mention_role_id, forecast_channel_id, status, tier,
     lineup_channel_id, calendar_channel_id, lineup_message_id, calendar_message_id)
SELECT d.id, d.season_id, d.name, d.mention_role_id, d.forecast_channel_id,
       CASE
           WHEN d.status = 'CANCELLED' THEN 'CANCELLED'
           WHEN s.status = 'CANCELLED' THEN 'CANCELLED'
           WHEN s.status = 'COMPLETED' THEN 'FINISHED'
           WHEN s.status = 'ACTIVE'    THEN 'ACTIVE'
           ELSE 'SETUP'
       END,
       d.tier, d.lineup_channel_id, d.calendar_channel_id, d.lineup_message_id,
       d.calendar_message_id
FROM divisions d
LEFT JOIN seasons s ON s.id = d.season_id;

DROP TABLE divisions;
ALTER TABLE divisions_new RENAME TO divisions;

-- ── 2. Drop the column nothing ever set ───────────────────────────────────────────────
--
-- Safe to drop: no index, view, CHECK or generated column references it, and every reader has
-- moved to `result_status` in this same change. Migration 026's one-off
-- `UPDATE rounds SET result_status = 'FINAL' WHERE finalized = 1` runs long before this file and
-- is unaffected.
ALTER TABLE rounds DROP COLUMN finalized;

PRAGMA foreign_keys = ON;
