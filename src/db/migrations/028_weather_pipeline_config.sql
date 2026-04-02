CREATE TABLE IF NOT EXISTS weather_pipeline_config (
    server_id     INTEGER PRIMARY KEY
                      REFERENCES server_configs(server_id) ON DELETE CASCADE,
    phase_1_days  INTEGER NOT NULL DEFAULT 5,
    phase_2_days  INTEGER NOT NULL DEFAULT 2,
    phase_3_hours INTEGER NOT NULL DEFAULT 2
);
