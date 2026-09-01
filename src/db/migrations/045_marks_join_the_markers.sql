-- Migration 045: withdraw the eighth asset directory; the result marks join the markers.
--
-- 044 gave the standings result chips a class and a directory of their own. They did not
-- need one. `marker` was already a closed-set class holding artwork of the module's own
-- vocabulary -- the position-change arrows -- and the chips are the same kind of thing, as
-- are the attendance sheet's limit marks added alongside this migration. All three now
-- resolve out of `marker_directory`, so a league draws its marks in one folder and
-- configures one path instead of two.
--
-- What made the two classes look incompatible was the aspect rule: `marker` is authored
-- 1:1 for the arrows, while a chip is stretched into whatever cell the template gives it.
-- That exemption has moved from the class to the slot -- a slot carrying
-- preserveAspectRatio="none" is passed over, whatever its class -- so one class now serves
-- both and the arrows beside the chips are still checked.
--
-- A league that had already repointed standings_highlight_directory loses that setting.
-- The column is one release old, its contents belong in the marker directory now, and
-- carrying the value onto marker_directory would silently move a league's arrows too.
--
-- SQLite cannot drop a column from a table this old dialect will accept everywhere, and the
-- house pattern for reshaping image_config is 043's rebuild -- followed here step for step,
-- including setting image_aspect_toggles aside: it carries a foreign key onto
-- image_config(server_id) ON DELETE CASCADE, and dropping the parent with foreign-key
-- enforcement on fires that cascade.
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

-- 2. The table as 043 left it -- that is, without the column 044 appended.
DROP TABLE IF EXISTS image_config_new;
CREATE TABLE image_config_new (
    server_id                        INTEGER PRIMARY KEY
                                         REFERENCES server_configs(server_id)
                                         ON DELETE CASCADE,
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

-- 3. Every existing row, named column by column: the shapes differ by exactly the column
--    being dropped, so SELECT * would not line up.
INSERT INTO image_config_new (
    server_id, module_enabled,
    template_directory,
    calendar_template, lineup_template,
    results_qualifying_template, results_race_template,
    standings_drivers_template, standings_constructors_template,
    attendance_template, rsvp_template,
    weather_p1_template, weather_p2_template, weather_p3_template,
    weather_p2_sprint_template, weather_p3_sprint_template, weather_mystery_template,
    verdicts_template,
    track_image_directory, team_image_directory, flag_directory,
    driver_image_directory, marker_directory, weather_icon_directory, tyre_directory,
    time_zone, time_format, date_format, fastest_lap_colour
)
SELECT
    server_id, module_enabled,
    template_directory,
    calendar_template, lineup_template,
    results_qualifying_template, results_race_template,
    standings_drivers_template, standings_constructors_template,
    attendance_template, rsvp_template,
    weather_p1_template, weather_p2_template, weather_p3_template,
    weather_p2_sprint_template, weather_p3_sprint_template, weather_mystery_template,
    verdicts_template,
    track_image_directory, team_image_directory, flag_directory,
    driver_image_directory, marker_directory, weather_icon_directory, tyre_directory,
    time_zone, time_format, date_format, fastest_lap_colour
FROM image_config;

-- 4. Swap.
DROP TABLE image_config;
ALTER TABLE image_config_new RENAME TO image_config;

-- 5. Restore the toggles the cascade in step 4 took with it.
DELETE FROM image_aspect_toggles;
INSERT INTO image_aspect_toggles (server_id, aspect, enabled)
SELECT server_id, aspect, enabled FROM image_aspect_toggles_keep;

DROP TABLE image_aspect_toggles_keep;
