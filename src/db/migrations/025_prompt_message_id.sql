-- Migration 025: Store the penalty review prompt message ID so it can be
-- deleted before reposting on bot restart.
ALTER TABLE round_submission_channels ADD COLUMN prompt_message_id INTEGER;
