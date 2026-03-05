-- migration 007: add status column to divisions and rounds for cancellation support
ALTER TABLE divisions ADD COLUMN status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'CANCELLED'));
ALTER TABLE rounds    ADD COLUMN status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'CANCELLED'));
