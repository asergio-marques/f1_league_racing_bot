-- Migration 021: add fl_driver_override column to session_results
-- Stores the Discord user ID of the manually designated fastest-lap holder.
-- NULL means automatic detection via submitted lap times.
ALTER TABLE session_results ADD COLUMN fl_driver_override INTEGER;
