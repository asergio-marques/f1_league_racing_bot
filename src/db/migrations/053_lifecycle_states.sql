-- Migration 053: the lifecycle of a division, a round, and a driver's history of them.
--
-- Three things are settled here. They arrive together because they are one change — what it
-- means for a round, a division and a season to be finished — and because two of them rebuild
-- the same tables.
--
-- ── 1. `divisions.status` lost its constraint, and never held the state it needed ──
--
-- Migration 007 added the column as `CHECK (status IN ('ACTIVE','CANCELLED'))` defaulting to
-- 'ACTIVE'. Migration 009 rebuilt the table to make `forecast_channel_id` nullable and, copying
-- the definition across, dropped the CHECK and changed the default to 'SETUP'. Nothing failed,
-- so nothing reported it.
--
-- Nothing has ever written 'ACTIVE' to a division either: every insert path takes 009's default
-- and no code moves it on. So every division a league has run sat at 'SETUP' for its whole life,
-- and `/season cancel`'s "post a notice to each active division" loop selected nothing and told
-- nobody. FINISHED is added because a division needs an end of its own — a season may be
-- completed once every division is finished or cancelled.
--
-- ── 2. A round carried two state columns describing one life ──
--
-- `status` said whether it was on (ACTIVE or CANCELLED); `result_status` said how settled its
-- results were (PROVISIONAL, POST_RACE_PENALTY, FINAL). Two columns describe every combination
-- of the two, but only six were ever reachable: a round can only be cancelled before its results
-- are in, so CANCELLED never coexisted with a settled result.
--
-- `PROVISIONAL` was the worse half, covering three different situations — a round not yet due,
-- one due but unentered, and one entered but being judged. Only the first two may be cancelled,
-- because once results are in the drivers have reports and appeals to lodge against them. With
-- all three spelled alike the cancel cascade could not tell them apart, and cancelled a division's
-- raced-but-unjudged rounds while `/round cancel` refused to do the same to one round directly.
--
-- The six states, each middle one named for what the round is waiting on rather than for what
-- has already happened to it:
--
--   NOT_RUN                   the round's date has not arrived
--   AWAITING_RESULTS          the date has passed; the results have not been entered
--   AWAITING_REPORT_VERDICTS  results posted; reports being judged
--   AWAITING_APPEAL_VERDICTS  report verdicts posted; appeals being judged
--   FINAL                     appeal verdicts posted; the results stand      (terminal)
--   CANCELLED                 called off, directly or by a cascade           (terminal)
--
-- `rounds.finalized` goes with them. Migration 019 added it saying the penalty review would set
-- it to 1; that was never implemented. Migration 026 read it once to seed `result_status` and
-- nothing has read it since — yet `/season complete` gated on it, so a season could never be
-- completed however completely it was raced, and because a server holds one live season at a
-- time the next season could not be started either (issue #154). The rebuild below simply does
-- not carry it across.
--
-- ── 3. A driver's history records whether the division was cancelled ──
--
-- A cancelled division is league history: it happened, and the drivers raced in it. It should
-- not read as a division that ran to its end. The flag hangs off the division because
-- cancellation only ever reaches a driver through one — cancelling a season cancels each of its
-- divisions, and cancelling a division cancels each of its rounds not yet run.
--
-- Both rebuilds below are rebuilds rather than ALTERs because SQLite cannot add or replace a
-- CHECK on an existing column, and 007's round CHECK would reject every one of the new names.

PRAGMA foreign_keys = OFF;

-- ── Divisions ─────────────────────────────────────────────────────────────────────────
--
-- The column list is today's, not 009's — the lineup and calendar columns were added after it.
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

-- Every existing row says 'SETUP' unless it was cancelled, so the season it belongs to is the
-- only evidence of what it should say. A cancelled division stays cancelled whatever became of
-- its season — it was called off first, and that is the truer record.
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

-- ── Rounds ────────────────────────────────────────────────────────────────────────────
--
-- Neither `finalized` nor `result_status` is carried across: the first is dead, and the second
-- is folded into `status` by the CASE below.
CREATE TABLE IF NOT EXISTS rounds_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    division_id     INTEGER NOT NULL,
    round_number    INTEGER NOT NULL,
    format          TEXT    NOT NULL,  -- NORMAL | SPRINT | MYSTERY | ENDURANCE
    track_name      TEXT,
    scheduled_at    TEXT    NOT NULL,  -- ISO datetime UTC, e.g. 2025-06-15T18:00:00
    phase1_done     INTEGER NOT NULL DEFAULT 0,
    phase2_done     INTEGER NOT NULL DEFAULT 0,
    phase3_done     INTEGER NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'NOT_RUN'
                        CHECK (status IN ('NOT_RUN', 'AWAITING_RESULTS',
                                          'AWAITING_REPORT_VERDICTS',
                                          'AWAITING_APPEAL_VERDICTS',
                                          'FINAL', 'CANCELLED')),
    FOREIGN KEY (division_id) REFERENCES divisions(id)
);

-- The two old columns are read together to place each round on the single chain. Order matters:
-- the terminal states are decided first, then the settled ones, and only then is a PROVISIONAL
-- round separated into its three real cases by what actually exists — submitted results first,
-- and failing that whether its date has passed.
INSERT INTO rounds_new
    (id, division_id, round_number, format, track_name, scheduled_at,
     phase1_done, phase2_done, phase3_done, status)
SELECT r.id, r.division_id, r.round_number, r.format, r.track_name, r.scheduled_at,
       r.phase1_done, r.phase2_done, r.phase3_done,
       CASE
           WHEN r.status        = 'CANCELLED'         THEN 'CANCELLED'
           WHEN r.result_status = 'FINAL'             THEN 'FINAL'
           WHEN r.result_status = 'POST_RACE_PENALTY' THEN 'AWAITING_APPEAL_VERDICTS'
           WHEN EXISTS (
               SELECT 1 FROM session_results sr
               WHERE sr.round_id = r.id AND sr.status = 'ACTIVE'
           )                                          THEN 'AWAITING_REPORT_VERDICTS'
           WHEN r.scheduled_at <= strftime('%Y-%m-%dT%H:%M:%S', 'now')
                                                      THEN 'AWAITING_RESULTS'
           ELSE 'NOT_RUN'
       END
FROM rounds r;

DROP TABLE rounds;
ALTER TABLE rounds_new RENAME TO rounds;

-- Dropping the table dropped its index with it.
CREATE INDEX IF NOT EXISTS idx_rounds_division ON rounds(division_id);

-- ── Driver history ────────────────────────────────────────────────────────────────────
ALTER TABLE driver_history_entries ADD COLUMN cancelled INTEGER NOT NULL DEFAULT 0;

-- A driver gains one entry per division per season, and writing it twice is a fault rather than
-- a second fact. Ending a season is a run of separate writes — roles revoked, history written,
-- classifications posted, the season's own row flipped last — and nothing makes them atomic. A
-- process that dies part-way leaves a season still ACTIVE with its history already written, and
-- the retry the league is told to run would append a duplicate set that nothing could tell apart.
--
-- The index makes the duplicate impossible rather than asking every writer to remember, and is
-- what lets `INSERT OR IGNORE` in `_write_driver_history_entries` stand in for a transaction
-- spanning the whole sequence. `division_name` is part of the key because a driver moved between
-- divisions mid-season legitimately earns an entry for each; only the same division twice is the
-- error. Pinned by `test_writing_the_history_twice_adds_nothing`.
CREATE UNIQUE INDEX IF NOT EXISTS idx_driver_history_unique
    ON driver_history_entries(driver_profile_id, season_number, division_name);

PRAGMA foreign_keys = ON;
