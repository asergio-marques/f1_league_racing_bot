-- Migration 039: Image Module
-- Creates image_config (server-level), image_aspect_toggles (per server per aspect)
-- and image_render_notices (append-only degradation audit, Constitution XIV.4).
--
-- Every column default carries the packaged default from the feature spec, so a freshly
-- inserted row is a fully valid default configuration with no application-side defaulting.

CREATE TABLE IF NOT EXISTS image_config (
    server_id                        INTEGER PRIMARY KEY
                                         REFERENCES server_configs(server_id)
                                         ON DELETE CASCADE,
    module_enabled                   INTEGER NOT NULL DEFAULT 0,

    -- Template location
    template_directory               TEXT NOT NULL DEFAULT 'resources/templates',
    calendar_template                TEXT NOT NULL DEFAULT 'calendar_template.svg',
    lineup_template                  TEXT NOT NULL DEFAULT 'lineup_template.svg',
    results_qualifying_template      TEXT NOT NULL DEFAULT 'results_qualifying_template.svg',
    results_race_template            TEXT NOT NULL DEFAULT 'results_race_template.svg',
    standings_drivers_template       TEXT NOT NULL DEFAULT 'standings_drivers_template.svg',
    standings_constructors_template  TEXT NOT NULL DEFAULT 'standings_constructors_template.svg',
    attendance_template              TEXT NOT NULL DEFAULT 'attendance_template.svg',
    rsvp_template                    TEXT NOT NULL DEFAULT 'rsvp_template.svg',
    weather_p1_template              TEXT NOT NULL DEFAULT 'weather_p1_template.svg',
    weather_p2_template              TEXT NOT NULL DEFAULT 'weather_p2_template.svg',
    weather_p3_template              TEXT NOT NULL DEFAULT 'weather_p3_template.svg',
    weather_p2_sprint_template       TEXT NOT NULL DEFAULT 'weather_p2_sprint_template.svg',
    weather_p3_sprint_template       TEXT NOT NULL DEFAULT 'weather_p3_sprint_template.svg',
    weather_mystery_template         TEXT NOT NULL DEFAULT 'weather_mystery_template.svg',
    verdicts_template                TEXT NOT NULL DEFAULT 'verdicts_template.svg',

    -- Asset location
    track_image_directory            TEXT NOT NULL DEFAULT 'resources/tracks',
    team_image_directory             TEXT NOT NULL DEFAULT 'resources/teams',
    flag_directory                   TEXT NOT NULL DEFAULT 'resources/flags',
    driver_image_directory           TEXT NOT NULL DEFAULT 'resources/drivers',
    marker_directory                 TEXT NOT NULL DEFAULT 'resources/markers',
    weather_icon_directory           TEXT NOT NULL DEFAULT 'resources/weather',
    tyre_directory                   TEXT NOT NULL DEFAULT 'resources/tyres',

    -- Presentation preferences
    time_zone                        TEXT NOT NULL DEFAULT 'UTC',
    time_format                      TEXT NOT NULL DEFAULT '24H',
    date_format                      TEXT NOT NULL DEFAULT 'DDD_DD_MON_YYYY',
    fastest_lap_colour               TEXT NOT NULL DEFAULT '#A020F0'
);

CREATE TABLE IF NOT EXISTS image_aspect_toggles (
    server_id  INTEGER NOT NULL
                   REFERENCES image_config(server_id)
                   ON DELETE CASCADE,
    aspect     TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (server_id, aspect)
);

CREATE TABLE IF NOT EXISTS image_render_notices (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id    INTEGER NOT NULL
                     REFERENCES server_configs(server_id)
                     ON DELETE CASCADE,
    image_type   TEXT NOT NULL,
    rendered_at  TEXT NOT NULL,
    notice_kind  TEXT NOT NULL,
    field_id     TEXT,
    detail       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_image_render_notices_server_time
    ON image_render_notices (server_id, rendered_at);
