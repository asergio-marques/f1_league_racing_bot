-- Migration 055: a round has one lifecycle, not two crossed ones.
--
-- A round carried two state columns. `status` said whether it was on (ACTIVE or CANCELLED) and
-- `result_status` said how settled its results were (PROVISIONAL, POST_RACE_PENALTY, FINAL). Two
-- columns describe every *combination* of the two, but the combinations do not exist: a round is
-- only ever cancellable before its results are in, so CANCELLED never coexisted with a settled
-- result. The pair modelled a grid where the truth is a list.
--
-- Worse, `PROVISIONAL` conflated three genuinely different situations — a round whose date has
-- not arrived, one waiting for its results to be entered, and one whose results are posted and
-- whose reports are being judged. They are not interchangeable: the first two may be cancelled,
-- and the third may not, because once results are in the drivers have a right to lodge reports
-- and appeals and cancelling would take that from them. With all three spelled `PROVISIONAL` the
-- cancel cascade could not tell them apart, and cancelled a division's raced-but-unjudged rounds
-- while `/round cancel` refused to do the same thing to one round directly.
--
-- The six states, in order, each named for what the round is waiting on rather than for what has
-- already happened to it:
--
--   NOT_RUN                   the round's date has not arrived
--   AWAITING_RESULTS          the date has passed; the results have not been entered
--   AWAITING_REPORT_VERDICTS  results posted; reports being judged
--   AWAITING_APPEAL_VERDICTS  report verdicts posted; appeals being judged
--   FINAL                     appeal verdicts posted; the results stand      (terminal)
--   CANCELLED                 called off, directly or by a cascade           (terminal)
--
-- A rebuild rather than an ALTER, because 007's `CHECK (status IN ('ACTIVE','CANCELLED'))` would
-- reject every one of the new names.

PRAGMA foreign_keys = OFF;

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

PRAGMA foreign_keys = ON;
