-- Migration 018: Add test-driver columns to driver_profiles
-- is_test_driver: marks synthetic fake drivers so they can be cleaned up independently.
-- test_display_name: stores the human-readable name supplied at roster-add time,
--   used in cheat sheets since fake users are not real Discord members.

ALTER TABLE driver_profiles ADD COLUMN is_test_driver INTEGER NOT NULL DEFAULT 0;
ALTER TABLE driver_profiles ADD COLUMN test_display_name TEXT;
