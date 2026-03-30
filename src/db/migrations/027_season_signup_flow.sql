-- ── 027: Season-signup flow alignment ───────────────────────────────────────
--
-- Changes:
--   1. Add lineup_channel_id, calendar_channel_id, lineup_message_id to
--      divisions (moving channel ownership from signup_division_config to the
--      division row itself, consistent with results_channel_id etc.).
--   2. Migrate existing lineup_channel_id values from signup_division_config
--      into divisions.
--   3. Recreate signup_division_config without the lineup_channel_id column
--      (SQLite does not support ALTER TABLE … DROP COLUMN on all target builds;
--       rename/create/copy/drop is the standard SQLite column-drop pattern).

PRAGMA foreign_keys = OFF;

-- ── 1. Extend divisions ──────────────────────────────────────────────────────

ALTER TABLE divisions ADD COLUMN lineup_channel_id   INTEGER;
ALTER TABLE divisions ADD COLUMN calendar_channel_id  INTEGER;
ALTER TABLE divisions ADD COLUMN lineup_message_id    INTEGER;

-- ── 2. Migrate lineup_channel_id data ────────────────────────────────────────

UPDATE divisions
SET lineup_channel_id = (
    SELECT sdc.lineup_channel_id
    FROM signup_division_config sdc
    WHERE sdc.division_id = divisions.id
)
WHERE EXISTS (
    SELECT 1 FROM signup_division_config sdc
    WHERE sdc.division_id = divisions.id
      AND sdc.lineup_channel_id IS NOT NULL
);

-- ── 3. Recreate signup_division_config without lineup_channel_id ─────────────

CREATE TABLE signup_division_config_new (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id           INTEGER NOT NULL
                            REFERENCES server_configs(server_id)
                            ON DELETE CASCADE,
    division_id         INTEGER NOT NULL
                            REFERENCES divisions(id)
                            ON DELETE CASCADE,
    UNIQUE(server_id, division_id)
);

INSERT INTO signup_division_config_new (id, server_id, division_id)
SELECT id, server_id, division_id
FROM signup_division_config;

DROP TABLE signup_division_config;

ALTER TABLE signup_division_config_new RENAME TO signup_division_config;

PRAGMA foreign_keys = ON;
