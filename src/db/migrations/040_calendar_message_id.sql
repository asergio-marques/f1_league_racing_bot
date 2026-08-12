-- Migration 040: Calendar message id
--
-- The textual calendar has been posted once at season approval and never replaced, so
-- no message id was ever held for it. An attachment cannot be introduced into a message
-- already posted (Constitution XIV.8), so the image flow replaces the calendar message
-- rather than editing it, and must know which message to delete.
--
-- Written by every calendar posting, graphic or textual, so the two flows never disagree
-- about which message is the calendar. Sits beside lineup_message_id, added at v2.8.0.

ALTER TABLE divisions ADD COLUMN calendar_message_id TEXT;
