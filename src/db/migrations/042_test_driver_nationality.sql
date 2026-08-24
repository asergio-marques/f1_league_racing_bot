-- Migration 042: Test-driver nationality, and the switch that governs it
--
-- A mock driver is an ordinary driver_profiles row flagged is_test_driver, and nationality
-- lives on signup_records — a table no mock driver has a row in. Every graphic therefore
-- drew one without a flag, which left the flag handling of the image module untestable
-- without a real signed-up league.
--
-- test_nationality mirrors test_display_name exactly: the column this repo already uses to
-- record what a mock driver has in place of a signup field, read by the same branch on
-- is_test_driver that the name is read by. It holds the canonical Title-Case adjective
-- signup_records.nationality holds — the value NATIONALITY_LOOKUP maps to — so the country
-- a flag is resolved from is derived from it in exactly the same way. Nullable, because a
-- mock driver may still be created without one, and every mock driver that exists today was.
--
-- test_mode_nationality_required sits beside test_mode_active (migration 002), where the
-- test-mode state of a server lives. It is the test-mode counterpart of
-- signup_module_settings.nationality_required, and while test mode is active it stands in
-- for it: a maintainer may preview a league that collects no nationality at all without
-- disturbing the setting their real signups run on. DEFAULT 1 to match the setting it
-- parallels, so both switches default the same way — on.
--
-- No backfill: ADD COLUMN with a non-null default fills the existing rows in place, and a
-- server already configured comes up with the switch on.

ALTER TABLE driver_profiles ADD COLUMN test_nationality TEXT;

ALTER TABLE server_configs ADD COLUMN test_mode_nationality_required INTEGER NOT NULL DEFAULT 1;
