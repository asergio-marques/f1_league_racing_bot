-- Migration 064: the results module's tables lose their server (issue #244).
--
-- results_module_config becomes the league's one row, keyed `id = 1` as 061 keyed its own;
-- its cascading foreign key onto server_configs goes, and reset_service deletes it by name.
-- points_config_store keys a configuration by its name alone, and round_amend_channels drops
-- the server column it carried beside the round that already names its season.
--
-- points_config_store is a parent: points_config_entries and points_config_fl hang off its
-- id by cascading foreign keys, and dropping it with enforcement on performs an implicit
-- DELETE that fires them. So the children are set aside first and restored after the
-- rename, as 043 did for the image toggles. The store's ids are copied verbatim, so every
-- child row still names its configuration.
--
-- Rows are copied for the configured server only. Nothing is live, and no database holds a
-- second, but a row belonging to no configured server is nobody's.

-- 1. results_module_config, a child with no children.
DROP TABLE IF EXISTS results_module_config_new;
CREATE TABLE results_module_config_new (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    module_enabled INTEGER NOT NULL DEFAULT 0
);
INSERT INTO results_module_config_new (id, module_enabled)
SELECT 1, module_enabled FROM results_module_config
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE results_module_config;
ALTER TABLE results_module_config_new RENAME TO results_module_config;

-- 2. The points configurations' children, set aside.
DROP TABLE IF EXISTS points_config_entries_keep;
CREATE TABLE points_config_entries_keep AS SELECT * FROM points_config_entries;
DROP TABLE IF EXISTS points_config_fl_keep;
CREATE TABLE points_config_fl_keep AS SELECT * FROM points_config_fl;

-- 3. The store, keyed by name, its ids kept.
DROP TABLE IF EXISTS points_config_store_new;
CREATE TABLE points_config_store_new (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    config_name TEXT    NOT NULL UNIQUE
);
INSERT INTO points_config_store_new (id, config_name)
SELECT id, config_name FROM points_config_store
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE points_config_store;
ALTER TABLE points_config_store_new RENAME TO points_config_store;

-- 4. The children restored, for the configurations that were kept.
DELETE FROM points_config_entries;
INSERT INTO points_config_entries (id, config_id, session_type, position, points)
SELECT id, config_id, session_type, position, points FROM points_config_entries_keep
WHERE config_id IN (SELECT id FROM points_config_store);
DROP TABLE points_config_entries_keep;

DELETE FROM points_config_fl;
INSERT INTO points_config_fl (id, config_id, session_type, fl_points, fl_position_limit)
SELECT id, config_id, session_type, fl_points, fl_position_limit FROM points_config_fl_keep
WHERE config_id IN (SELECT id FROM points_config_store);
DROP TABLE points_config_fl_keep;

-- 5. The amendment channels, keyed as before by round and session.
DROP TABLE IF EXISTS round_amend_channels_new;
CREATE TABLE round_amend_channels_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id     INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
    channel_id   INTEGER NOT NULL,
    session_type TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    UNIQUE (round_id, session_type)
);
INSERT INTO round_amend_channels_new (id, round_id, channel_id, session_type, created_at)
SELECT id, round_id, channel_id, session_type, created_at FROM round_amend_channels;
DROP TABLE round_amend_channels;
ALTER TABLE round_amend_channels_new RENAME TO round_amend_channels;
