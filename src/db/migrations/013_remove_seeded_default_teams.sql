-- ==================================================================
-- 013_remove_seeded_default_teams.sql
-- Removes all non-reserve rows from default_teams that were seeded
-- automatically on /bot-init. Admins now configure teams manually
-- via /team add. The Reserve team (is_reserve = 1) is kept.
-- ==================================================================

DELETE FROM default_teams WHERE is_reserve = 0;
