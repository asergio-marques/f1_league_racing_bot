-- Migration 022: Add crash-recovery columns to round_submission_channels.
--
-- results_posted: set to 1 after interim results/standings have been posted to
--   the division channels in enter_penalty_state.  Allows recovery to skip
--   re-posting on restart when posting already succeeded.
--
-- staged_penalties: JSON snapshot of all staged penalties serialised just
--   before apply_penalties is called in finalize_round.  Non-NULL means
--   penalties were already committed to driver_session_results before the
--   last crash; recovery uses this to warn the LM and skip re-applying them.

ALTER TABLE round_submission_channels ADD COLUMN results_posted   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE round_submission_channels ADD COLUMN staged_penalties TEXT;
