-- Migration 063: the image module's tables lose their server (issue #244).
--
-- image_config becomes the league's one row, keyed `id = 1` as 061 and 062 keyed theirs, and
-- its cascading foreign key onto server_configs goes; reset_service deletes it by name.
-- image_aspect_toggles is keyed by the aspect alone, image_tier_colour by division and slot,
-- and driver_portraits by the Discord account.
--
-- image_aspect_toggles hung off image_config(server_id) by a cascading foreign key, and
-- dropping a parent with enforcement on performs an implicit DELETE that fires the cascade
-- (043 met the same). So the toggles are rebuilt first, onto a table with no parent, and
-- only then is image_config rebuilt: by the time the old image_config is dropped, nothing
-- hangs off it. The toggles keep no foreign key onto the new image_config; nothing deletes
-- that row but a full reset, which names both tables.
--
-- Rows are copied for the configured server only. Nothing is live, and no database holds a
-- second, but a row belonging to no configured server is nobody's. driver_portraits carried
-- no foreign key and may hold rows written before a server was configured; those are kept.

-- 1. The toggles, detached from image_config before it is touched.
DROP TABLE IF EXISTS image_aspect_toggles_new;
CREATE TABLE image_aspect_toggles_new (
    aspect   TEXT    PRIMARY KEY,
    enabled  INTEGER NOT NULL DEFAULT 0
);
INSERT INTO image_aspect_toggles_new (aspect, enabled)
SELECT aspect, enabled FROM image_aspect_toggles
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE image_aspect_toggles;
ALTER TABLE image_aspect_toggles_new RENAME TO image_aspect_toggles;

-- 2. image_config, now a parent of nothing.
DROP TABLE IF EXISTS image_config_new;
CREATE TABLE image_config_new (
    id                               INTEGER PRIMARY KEY CHECK (id = 1),
    module_enabled                   INTEGER NOT NULL DEFAULT 0,

    -- Template location. Templates have no packaged fallback tier.
    template_directory               TEXT NOT NULL DEFAULT 'resources/defaults/templates',
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
    verdict_banner_template          TEXT NOT NULL DEFAULT 'verdict_banner_template.svg',

    -- Asset location. A league's own folder; the packaged tier answers every miss.
    track_image_directory            TEXT NOT NULL DEFAULT 'resources/league/tracks',
    team_image_directory             TEXT NOT NULL DEFAULT 'resources/league/teams',
    flag_directory                   TEXT NOT NULL DEFAULT 'resources/league/flags',
    driver_image_directory           TEXT NOT NULL DEFAULT 'resources/league/drivers',
    marker_directory                 TEXT NOT NULL DEFAULT 'resources/league/markers',
    weather_icon_directory           TEXT NOT NULL DEFAULT 'resources/league/weather',
    tyre_directory                   TEXT NOT NULL DEFAULT 'resources/league/tyres',
    division_logo_directory          TEXT NOT NULL DEFAULT 'resources/league/division-logos',

    -- Presentation preferences
    time_zone                        TEXT NOT NULL DEFAULT 'UTC',
    time_format                      TEXT NOT NULL DEFAULT '24H',
    date_format                      TEXT NOT NULL DEFAULT 'DDD_DD_MON_YYYY',
    fastest_lap_colour               TEXT NOT NULL DEFAULT '#A020F0',
    per_tier_colour_enabled          INTEGER NOT NULL DEFAULT 0,

    -- Driver portraits
    use_pfp                          INTEGER NOT NULL DEFAULT 0,
    pfp_prerender                    INTEGER NOT NULL DEFAULT 1,
    pfp_daily                        INTEGER NOT NULL DEFAULT 0,
    pfp_daily_time                   TEXT    NOT NULL DEFAULT '03:00'
);
INSERT INTO image_config_new (
    id, module_enabled, template_directory, calendar_template, lineup_template,
    results_qualifying_template, results_race_template, standings_drivers_template,
    standings_constructors_template, attendance_template, rsvp_template, weather_p1_template,
    weather_p2_template, weather_p3_template, weather_p2_sprint_template,
    weather_p3_sprint_template, weather_mystery_template, verdicts_template,
    verdict_banner_template, track_image_directory, team_image_directory, flag_directory,
    driver_image_directory, marker_directory, weather_icon_directory, tyre_directory,
    division_logo_directory, time_zone, time_format, date_format, fastest_lap_colour,
    per_tier_colour_enabled, use_pfp, pfp_prerender, pfp_daily, pfp_daily_time
)
SELECT
    1, module_enabled, template_directory, calendar_template, lineup_template,
    results_qualifying_template, results_race_template, standings_drivers_template,
    standings_constructors_template, attendance_template, rsvp_template, weather_p1_template,
    weather_p2_template, weather_p3_template, weather_p2_sprint_template,
    weather_p3_sprint_template, weather_mystery_template, verdicts_template,
    verdict_banner_template, track_image_directory, team_image_directory, flag_directory,
    driver_image_directory, marker_directory, weather_icon_directory, tyre_directory,
    division_logo_directory, time_zone, time_format, date_format, fastest_lap_colour,
    per_tier_colour_enabled, use_pfp, pfp_prerender, pfp_daily, pfp_daily_time
FROM image_config
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE image_config;
ALTER TABLE image_config_new RENAME TO image_config;

-- 3. The per-tier colours, keyed by division and slot.
DROP TABLE IF EXISTS image_tier_colour_new;
CREATE TABLE image_tier_colour_new (
    division_slug  TEXT NOT NULL,
    slot           TEXT NOT NULL,
    colour         TEXT NOT NULL,
    PRIMARY KEY (division_slug, slot)
);
INSERT INTO image_tier_colour_new (division_slug, slot, colour)
SELECT division_slug, slot, colour FROM image_tier_colour
WHERE server_id = (SELECT server_id FROM server_configs LIMIT 1);
DROP TABLE image_tier_colour;
ALTER TABLE image_tier_colour_new RENAME TO image_tier_colour;

-- 4. The portraits, keyed by the account.
DROP TABLE IF EXISTS driver_portraits_new;
CREATE TABLE driver_portraits_new (
    discord_user_id  TEXT PRIMARY KEY,
    avatar_key       TEXT NOT NULL,
    fetched_at       TEXT NOT NULL
);
INSERT OR IGNORE INTO driver_portraits_new (discord_user_id, avatar_key, fetched_at)
SELECT discord_user_id, avatar_key, fetched_at FROM driver_portraits;
DROP TABLE driver_portraits;
ALTER TABLE driver_portraits_new RENAME TO driver_portraits;
