-- Migration 061: the weather horizons become the league's one row, keyed by nothing.
--
-- One bot serves one league (issue #244), so a table keyed by server holds one row whose key
-- says nothing. The key becomes `id`, fixed at 1 by a CHECK, and the foreign key onto
-- server_configs goes with the column. That foreign key cascaded: a full reset deleted the
-- row by deleting its parent. reset_service deletes it by name from now on, pinned by
-- tests/unit/test_full_reset_scope.py.
--
-- The row copied is the one belonging to the configured server. Nothing is live, and no
-- database holds rows for two, but a row belonging to no configured server is nobody's.
--
-- A child with no children of its own, so the drop cascades nowhere.

DROP TABLE IF EXISTS weather_pipeline_config_new;
CREATE TABLE weather_pipeline_config_new (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    phase_1_days  INTEGER NOT NULL DEFAULT 5,
    phase_2_days  INTEGER NOT NULL DEFAULT 2,
    phase_3_hours INTEGER NOT NULL DEFAULT 2
);

INSERT INTO weather_pipeline_config_new (id, phase_1_days, phase_2_days, phase_3_hours)
SELECT 1, phase_1_days, phase_2_days, phase_3_hours
FROM weather_pipeline_config
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);

DROP TABLE weather_pipeline_config;
ALTER TABLE weather_pipeline_config_new RENAME TO weather_pipeline_config;
