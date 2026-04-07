-- Migration 033: Add last_notice and distribution message ID columns to rsvp_embed_messages
-- Allows the RSVP notice cleanup to also delete the last-notice and distribution
-- announcement messages from previous rounds.

ALTER TABLE rsvp_embed_messages ADD COLUMN last_notice_msg_id TEXT;
ALTER TABLE rsvp_embed_messages ADD COLUMN distribution_msg_id TEXT;
