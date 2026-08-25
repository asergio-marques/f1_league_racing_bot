-- Migration 043: point the seven asset directories at resources/league/ by default.
--
-- The bot ships its own artwork under resources/defaults/ and a league puts its own under
-- resources/league/, which is gitignored and survives an update. Until now both were the
-- same path: the default configured directory *was* the packaged one, so a league had to
-- run /images config <class>-directory seven times before the folders shipped for its own
-- artwork were looked at. The two-tier resolution added at 047 makes that unnecessary --
-- a miss in the configured directory falls through to the packaged one -- so the default
-- moves to resources/league/ and dropping a file in is the whole job.
--
-- template_directory deliberately does NOT move. Templates have no packaged second tier:
-- the configured directory is the only place searched, so pointing it at an empty folder
-- would leave a fresh install unable to render anything at all.
--
-- Existing rows are copied verbatim. A server that already named a directory keeps it,
-- and so does a server still holding the old default -- changing a value a league is
-- currently rendering from is not this migration's business. Only the DEFAULT changes,
-- which is what a newly created row picks up (ImageConfigService.create_with_defaults
-- inserts server_id alone and leans entirely on these defaults).
--
-- SQLite cannot alter a column default, hence the rebuild. image_aspect_toggles carries a
-- foreign key onto image_config(server_id) ON DELETE CASCADE, and dropping a parent table
-- with foreign-key enforcement on performs an implicit DELETE FROM that fires the cascade
-- -- so the toggles are set aside first and restored after the rename, rather than relying
-- on the pragma state the runner happens to connect with.
--
-- DROP TABLE IF EXISTS guards make each step safe to re-run if an earlier attempt failed
-- partway through.

-- 1. Set the aspect toggles aside; the drop in step 4 would cascade them away.
DROP TABLE IF EXISTS image_aspect_toggles_keep;
CREATE TABLE image_aspect_toggles_keep (
    server_id  INTEGER NOT NULL,
    aspect     TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 0
);

INSERT INTO image_aspect_toggles_keep (server_id, aspect, enabled)
SELECT server_id, aspect, enabled FROM image_aspect_toggles;

-- 2. The table as 039 declared it, with the seven asset defaults repointed.
DROP TABLE IF EXISTS image_config_new;
CREATE TABLE image_config_new (
    server_id                        INTEGER PRIMARY KEY
                                         REFERENCES server_configs(server_id)
                                         ON DELETE CASCADE,
    module_enabled                   INTEGER NOT NULL DEFAULT 0,

    -- Template location. Unchanged: templates have no packaged fallback tier.
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

    -- Asset location. A league's own folder; the packaged tier answers every miss.
    track_image_directory            TEXT NOT NULL DEFAULT 'resources/league/tracks',
    team_image_directory             TEXT NOT NULL DEFAULT 'resources/league/teams',
    flag_directory                   TEXT NOT NULL DEFAULT 'resources/league/flags',
    driver_image_directory           TEXT NOT NULL DEFAULT 'resources/league/drivers',
    marker_directory                 TEXT NOT NULL DEFAULT 'resources/league/markers',
    weather_icon_directory           TEXT NOT NULL DEFAULT 'resources/league/weather',
    tyre_directory                   TEXT NOT NULL DEFAULT 'resources/league/tyres',

    -- Presentation preferences
    time_zone                        TEXT NOT NULL DEFAULT 'UTC',
    time_format                      TEXT NOT NULL DEFAULT '24H',
    date_format                      TEXT NOT NULL DEFAULT 'DDD_DD_MON_YYYY',
    fastest_lap_colour               TEXT NOT NULL DEFAULT '#A020F0'
);

-- 3. Every existing row, verbatim. Column order matches 039 exactly.
INSERT INTO image_config_new SELECT * FROM image_config;

-- 4. Swap.
DROP TABLE image_config;
ALTER TABLE image_config_new RENAME TO image_config;

-- 5. Restore the toggles the cascade in step 4 took with it.
DELETE FROM image_aspect_toggles;
INSERT INTO image_aspect_toggles (server_id, aspect, enabled)
SELECT server_id, aspect, enabled FROM image_aspect_toggles_keep;

DROP TABLE image_aspect_toggles_keep;
