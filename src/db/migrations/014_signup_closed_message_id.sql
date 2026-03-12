-- ==================================================================
-- 014_signup_closed_message_id.sql
-- Adds signup_closed_message_id to signup_module_config so the bot
-- can track and delete the "signups are closed" status message when
-- signups are reopened.
-- ==================================================================

ALTER TABLE signup_module_config
    ADD COLUMN signup_closed_message_id INTEGER;
