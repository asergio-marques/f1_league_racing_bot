-- Migration 004: Add forecast_messages table for per-phase Discord message ID tracking.
-- Stores the Discord message snowflake of each phase forecast posted to a division's
-- forecast channel, keyed by (round_id, division_id, phase_number). Used by
-- forecast_cleanup_service to delete superseded forecast messages before posting the
-- next phase output and to purge Phase 3 messages 24 hours after round start.

CREATE TABLE IF NOT EXISTS forecast_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id     INTEGER NOT NULL REFERENCES rounds(id),
    division_id  INTEGER NOT NULL REFERENCES divisions(id),
    phase_number INTEGER NOT NULL CHECK (phase_number IN (1, 2, 3)),
    message_id   INTEGER NOT NULL,
    posted_at    TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_forecast_messages_round_div_phase
    ON forecast_messages(round_id, division_id, phase_number);
