-- Migration 056: Record a resubmission in progress on round_submission_channels.
--
-- resubmitting: set to 1 while a league manager is re-entering a round's results after
--   pressing Resubmit Initial Results, and cleared when the new results replace the old,
--   when the resubmission is cancelled, or when a restart interrupts it. The round stays in
--   penalty review throughout (in_penalty_review = 1, results_posted = 1), because the
--   results it already holds stand until the new ones replace them (issue #210). The flag is
--   what lets pastes through the review channel's message guard, and what tells the restart
--   sweep that the collection in flight was lost while the earlier results were not.
--
-- resubmit_prompt_message_id: the resubmission announcement carrying the Cancel button, so
--   the restart sweep can take the button down once nothing is listening for it.

ALTER TABLE round_submission_channels ADD COLUMN resubmitting               INTEGER NOT NULL DEFAULT 0;
ALTER TABLE round_submission_channels ADD COLUMN resubmit_prompt_message_id INTEGER;
