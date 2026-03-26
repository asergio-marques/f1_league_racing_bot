-- Migration 019: Add finalized flag to rounds table and in_penalty_review
-- flag to round_submission_channels.
-- Once a round is approved through the post-submission penalty review flow
-- the finalized flag is set to 1 to indicate the results are final.
ALTER TABLE rounds ADD COLUMN finalized INTEGER NOT NULL DEFAULT 0;

-- Track whether the submission channel has transitioned to penalty-review state
-- (all sessions submitted/cancelled, penalty prompt posted, channel still open).
ALTER TABLE round_submission_channels ADD COLUMN in_penalty_review INTEGER NOT NULL DEFAULT 0;
