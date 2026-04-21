-- 035_new_result_tables.sql
--
-- Replaces the monolithic driver_session_results table with two purpose-built
-- tables: one for qualifying sessions and one for race sessions.
--
-- Philosophy:
--   qualifying_session_results  — stores absolute best_lap for every driver
--                                 (gap is always computed on display).
--   race_session_results        — stores base_time_ms (race time minus ingame
--                                 penalties) plus three additive penalty columns
--                                 so total_time and intervals are always derived.
--
-- The old driver_session_results table is left in place for backward-compat with
-- existing rounds.  New submissions write exclusively to the new tables.
--
-- penalty_records and appeal_records gain two nullable FK columns each so that
-- new penalties reference the appropriate new table while old legacy rows keep
-- their driver_session_result_id intact.

-- ---------------------------------------------------------------------------
-- qualifying_session_results
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qualifying_session_results (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_result_id   INTEGER NOT NULL
                            REFERENCES session_results(id)
                            ON DELETE CASCADE,
    driver_user_id      INTEGER NOT NULL,
    team_role_id        INTEGER NOT NULL,
    finishing_position  INTEGER NOT NULL,
    outcome             TEXT    NOT NULL DEFAULT 'CLASSIFIED',
    tyre                TEXT,
    -- Absolute best-lap time string, e.g. "1:23.456".
    -- DNS/DNF/DSQ drivers carry their outcome literal here instead.
    best_lap            TEXT,
    points_awarded      INTEGER NOT NULL DEFAULT 0,
    driver_profile_id   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_qsr_session
    ON qualifying_session_results(session_result_id);

-- ---------------------------------------------------------------------------
-- race_session_results
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS race_session_results (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_result_id           INTEGER NOT NULL
                                    REFERENCES session_results(id)
                                    ON DELETE CASCADE,
    driver_user_id              INTEGER NOT NULL,
    team_role_id                INTEGER NOT NULL,
    finishing_position          INTEGER NOT NULL,
    outcome                     TEXT    NOT NULL DEFAULT 'CLASSIFIED',
    -- base_time_ms: race time in ms with ingame penalties already subtracted.
    -- NULL for lapped drivers, DNF, DNS, DSQ.
    base_time_ms                INTEGER,
    -- laps_behind: populated for "+N Laps" classified drivers; NULL otherwise.
    laps_behind                 INTEGER,
    -- ingame_time_penalties_ms: time penalty applied by the game (submitted at
    -- result entry). 0 when the submitted field was "N/A".
    ingame_time_penalties_ms    INTEGER NOT NULL DEFAULT 0,
    -- postrace_time_penalties_ms: added/removed by the steward penalty wizard.
    postrace_time_penalties_ms  INTEGER NOT NULL DEFAULT 0,
    -- appeal_time_penalties_ms: added/removed by the appeals phase.
    appeal_time_penalties_ms    INTEGER NOT NULL DEFAULT 0,
    fastest_lap                 TEXT,
    fastest_lap_bonus           INTEGER NOT NULL DEFAULT 0,
    points_awarded              INTEGER NOT NULL DEFAULT 0,
    driver_profile_id           INTEGER
);

CREATE INDEX IF NOT EXISTS idx_rsr_session
    ON race_session_results(session_result_id);

-- ---------------------------------------------------------------------------
-- penalty_records — add nullable FK columns for new tables
-- ---------------------------------------------------------------------------
ALTER TABLE penalty_records
    ADD COLUMN race_result_id INTEGER REFERENCES race_session_results(id);

ALTER TABLE penalty_records
    ADD COLUMN qual_result_id INTEGER REFERENCES qualifying_session_results(id);

-- ---------------------------------------------------------------------------
-- appeal_records — add nullable FK columns for new tables
-- ---------------------------------------------------------------------------
ALTER TABLE appeal_records
    ADD COLUMN race_result_id INTEGER REFERENCES race_session_results(id);

ALTER TABLE appeal_records
    ADD COLUMN qual_result_id INTEGER REFERENCES qualifying_session_results(id);
