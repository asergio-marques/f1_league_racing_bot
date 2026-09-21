-- The schema as it stood after migration 061, squashed into one file (issue #254).
--
-- It replaced the 61 migrations 001_initial.sql to 061_one_league.sql, which git still
-- holds. It was generated from a database built through them, and proven identical to one
-- before they were deleted. Each table carries the comments SQLite kept inside its CREATE
-- statement; comments that stood above a statement live on in the history of those files.
--
-- Until go-live this file may be edited in place. From go-live on it is never edited again:
-- every schema change is a new migration numbered after it, with a test of its own.

-- server_configs
-- One row, always. `server_id` is the claim on the league's server and is NULL while the bot
-- serves none: `/bot pack` clears it and the four settings, and keeps the rest (issue #247).
CREATE TABLE server_configs (
    id                     INTEGER PRIMARY KEY CHECK (id = 1),
    server_id              INTEGER UNIQUE,
    interaction_role_id    INTEGER,
    interaction_channel_id INTEGER,
    log_channel_id         INTEGER
, test_mode_active INTEGER NOT NULL DEFAULT 0, weather_module_enabled INTEGER NOT NULL DEFAULT 0, signup_module_enabled INTEGER NOT NULL DEFAULT 0, test_mode_nationality_required INTEGER NOT NULL DEFAULT 1, league_admin_role_id INTEGER);

-- sessions
CREATE TABLE sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id            INTEGER NOT NULL,
    session_type        TEXT    NOT NULL,
    phase2_slot_type    TEXT,   -- 'rain' | 'mixed' | 'sunny'
    phase3_slots        TEXT,   -- JSON array of weather strings
    FOREIGN KEY (round_id) REFERENCES rounds(id)
);
CREATE INDEX idx_sessions_round ON sessions(round_id);

-- phase_results
CREATE TABLE phase_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id      INTEGER NOT NULL,
    phase_number  INTEGER NOT NULL,
    payload       TEXT    NOT NULL,  -- JSON blob: inputs + outputs
    status        TEXT    NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE | INVALIDATED
    created_at    TEXT    NOT NULL,
    FOREIGN KEY (round_id) REFERENCES rounds(id)
);
CREATE INDEX idx_phase_results_round ON phase_results(round_id);

-- forecast_messages
CREATE TABLE "forecast_messages" (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id     INTEGER NOT NULL REFERENCES rounds(id),
    division_id  INTEGER NOT NULL REFERENCES divisions(id),
    phase_number INTEGER NOT NULL CHECK (phase_number IN (0, 1, 2, 3)),
    message_id   INTEGER NOT NULL,
    posted_at    TEXT    NOT NULL
);
CREATE UNIQUE INDEX uq_forecast_messages_round_div_phase
    ON forecast_messages(round_id, division_id, phase_number);

-- driver_season_assignments
CREATE TABLE driver_season_assignments (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    driver_profile_id    INTEGER NOT NULL REFERENCES driver_profiles(id),
    season_id            INTEGER NOT NULL REFERENCES seasons(id),
    division_id          INTEGER NOT NULL REFERENCES divisions(id),
    current_position     INTEGER NOT NULL DEFAULT 0,
    current_points       INTEGER NOT NULL DEFAULT 0,
    points_gap_to_first  INTEGER NOT NULL DEFAULT 0
, team_seat_id INTEGER REFERENCES team_seats(id), committed INTEGER);

-- team_instances
CREATE TABLE team_instances (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    division_id INTEGER NOT NULL REFERENCES divisions(id),
    name        TEXT    NOT NULL,
    max_seats   INTEGER NOT NULL DEFAULT 2,
    is_reserve  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(division_id, name)
);

-- team_seats
CREATE TABLE team_seats (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    team_instance_id  INTEGER NOT NULL REFERENCES team_instances(id),
    seat_number       INTEGER NOT NULL,
    driver_profile_id INTEGER REFERENCES driver_profiles(id),
    UNIQUE(team_instance_id, seat_number)
);

-- division_results_config
CREATE TABLE division_results_config (
    division_id          INTEGER PRIMARY KEY
                             REFERENCES divisions(id)
                             ON DELETE CASCADE,
    results_channel_id   INTEGER,
    standings_channel_id INTEGER,
    reserves_in_standings INTEGER NOT NULL DEFAULT 1
, penalty_channel_id TEXT);

-- season_points_links
CREATE TABLE season_points_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id   INTEGER NOT NULL
                    REFERENCES seasons(id)
                    ON DELETE CASCADE,
    config_name TEXT    NOT NULL,
    UNIQUE (season_id, config_name)
);

-- points_config_entries
CREATE TABLE points_config_entries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    config_id    INTEGER NOT NULL
                     REFERENCES points_config_store(id)
                     ON DELETE CASCADE,
    session_type TEXT    NOT NULL,
    position     INTEGER NOT NULL,
    points       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (config_id, session_type, position)
);

-- points_config_fl
CREATE TABLE points_config_fl (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    config_id         INTEGER NOT NULL
                          REFERENCES points_config_store(id)
                          ON DELETE CASCADE,
    session_type      TEXT    NOT NULL,
    fl_points         INTEGER NOT NULL DEFAULT 0,
    fl_position_limit INTEGER,
    UNIQUE (config_id, session_type)
);

-- season_points_entries
CREATE TABLE season_points_entries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id    INTEGER NOT NULL
                     REFERENCES seasons(id)
                     ON DELETE CASCADE,
    config_name  TEXT    NOT NULL,
    session_type TEXT    NOT NULL,
    position     INTEGER NOT NULL,
    points       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (season_id, config_name, session_type, position)
);

-- season_points_fl
CREATE TABLE season_points_fl (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id         INTEGER NOT NULL
                          REFERENCES seasons(id)
                          ON DELETE CASCADE,
    config_name       TEXT    NOT NULL,
    session_type      TEXT    NOT NULL,
    fl_points         INTEGER NOT NULL DEFAULT 0,
    fl_position_limit INTEGER,
    UNIQUE (season_id, config_name, session_type)
);

-- season_amendment_state
CREATE TABLE season_amendment_state (
    season_id        INTEGER PRIMARY KEY
                         REFERENCES seasons(id)
                         ON DELETE CASCADE,
    amendment_active INTEGER NOT NULL DEFAULT 0,
    modified_flag    INTEGER NOT NULL DEFAULT 0
);

-- season_modification_entries
CREATE TABLE season_modification_entries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id    INTEGER NOT NULL
                     REFERENCES seasons(id)
                     ON DELETE CASCADE,
    config_name  TEXT    NOT NULL,
    session_type TEXT    NOT NULL,
    position     INTEGER NOT NULL,
    points       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (season_id, config_name, session_type, position)
);

-- season_modification_fl
CREATE TABLE season_modification_fl (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id         INTEGER NOT NULL
                          REFERENCES seasons(id)
                          ON DELETE CASCADE,
    config_name       TEXT    NOT NULL,
    session_type      TEXT    NOT NULL,
    fl_points         INTEGER NOT NULL DEFAULT 0,
    fl_position_limit INTEGER,
    UNIQUE (season_id, config_name, session_type)
);

-- session_results
CREATE TABLE session_results (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id           INTEGER NOT NULL
                           REFERENCES rounds(id)
                           ON DELETE CASCADE,
    division_id        INTEGER NOT NULL
                           REFERENCES divisions(id),
    session_type       TEXT    NOT NULL,
    status             TEXT    NOT NULL DEFAULT 'ACTIVE',
    config_name        TEXT,
    submitted_by       INTEGER,
    submitted_at       TEXT,
    results_message_id INTEGER, fl_driver_override INTEGER,
    -- results_message_ids: JSON array of *every* message the posting occupies, the
    -- anchor first. A table past 2000 characters is split across consecutive messages
    -- and only the anchor was ever recorded, leaving deletion to guess the rest by
    -- walking forward over bot-authored messages -- which mistakes an unrelated posting
    -- below it for a continuation of this one (#345). NULL on a row written before this
    -- column existed; such a row falls back to the walk.
    results_message_ids TEXT,
    UNIQUE (round_id, session_type)
);

-- driver_standings_snapshots
CREATE TABLE driver_standings_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id            INTEGER NOT NULL
                            REFERENCES rounds(id)
                            ON DELETE CASCADE,
    division_id         INTEGER NOT NULL
                            REFERENCES divisions(id),
    driver_user_id      INTEGER NOT NULL,
    standing_position   INTEGER NOT NULL,
    total_points        INTEGER NOT NULL DEFAULT 0,
    finish_counts       TEXT    NOT NULL DEFAULT '{}',
    first_finish_rounds TEXT    NOT NULL DEFAULT '{}',
    standings_message_id INTEGER, driver_profile_id INTEGER REFERENCES driver_profiles(id), constructor_standings_message_id INTEGER,
    -- The chunk lists of the two standings postings, as session_results' above.
    standings_message_ids TEXT,
    constructor_standings_message_ids TEXT,
    UNIQUE (round_id, division_id, driver_user_id)
);
CREATE INDEX idx_dss_driver_profile
    ON driver_standings_snapshots(division_id, driver_profile_id);

-- team_standings_snapshots
CREATE TABLE team_standings_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id            INTEGER NOT NULL
                            REFERENCES rounds(id)
                            ON DELETE CASCADE,
    division_id         INTEGER NOT NULL
                            REFERENCES divisions(id),
    team_role_id        INTEGER NOT NULL,
    standing_position   INTEGER NOT NULL,
    total_points        INTEGER NOT NULL DEFAULT 0,
    finish_counts       TEXT    NOT NULL DEFAULT '{}',
    first_finish_rounds TEXT    NOT NULL DEFAULT '{}',
    UNIQUE (round_id, division_id, team_role_id)
);

-- round_submission_channels
CREATE TABLE round_submission_channels (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id   INTEGER NOT NULL
                   REFERENCES rounds(id)
                   ON DELETE CASCADE,
    channel_id INTEGER NOT NULL,
    created_at TEXT    NOT NULL,
    closed     INTEGER NOT NULL DEFAULT 0, in_penalty_review INTEGER NOT NULL DEFAULT 0, results_posted   INTEGER NOT NULL DEFAULT 0, staged_penalties TEXT, prompt_message_id INTEGER, resubmitting               INTEGER NOT NULL DEFAULT 0, resubmit_prompt_message_id INTEGER,
    UNIQUE (round_id)
);

-- tracks
CREATE TABLE tracks (
    id       INTEGER PRIMARY KEY NOT NULL,
    name     TEXT    NOT NULL UNIQUE,
    gp_name  TEXT    NOT NULL,
    location TEXT    NOT NULL,
    country  TEXT    NOT NULL,
    mu       REAL    NOT NULL,
    sigma    REAL    NOT NULL
);

-- track_records
CREATE TABLE track_records (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id       INTEGER NOT NULL REFERENCES tracks(id),
    tier           INTEGER NOT NULL,
    session_type   TEXT    NOT NULL,
    game           TEXT    NOT NULL,
    season_number  INTEGER NOT NULL,
    round_number   INTEGER NOT NULL,
    lap_time       TEXT    NOT NULL,
    driver_id      INTEGER NOT NULL,
    UNIQUE (track_id, tier, session_type)
);

-- lap_records
CREATE TABLE lap_records (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id       INTEGER NOT NULL REFERENCES tracks(id),
    tier           INTEGER NOT NULL,
    session_type   TEXT    NOT NULL,
    game           TEXT    NOT NULL,
    season_number  INTEGER NOT NULL,
    round_number   INTEGER NOT NULL,
    lap_time       TEXT    NOT NULL,
    driver_id      INTEGER NOT NULL,
    UNIQUE (track_id, tier, session_type)
);

-- driver_round_attendance
CREATE TABLE driver_round_attendance (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id           INTEGER NOT NULL
                           REFERENCES rounds(id)
                           ON DELETE CASCADE,
    division_id        INTEGER NOT NULL
                           REFERENCES divisions(id),
    driver_profile_id  INTEGER NOT NULL
                           REFERENCES driver_profiles(id),
    rsvp_status        TEXT    NOT NULL DEFAULT 'NO_RSVP',
    accepted_at        TEXT,
    assigned_team_id   INTEGER REFERENCES team_instances(id),
    is_standby         INTEGER NOT NULL DEFAULT 0,
    attended           INTEGER, points_awarded   INTEGER, total_points_after INTEGER,
    UNIQUE (round_id, division_id, driver_profile_id)
);
CREATE INDEX idx_dra_round_division
    ON driver_round_attendance (round_id, division_id);

-- rsvp_embed_messages
CREATE TABLE rsvp_embed_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id    INTEGER NOT NULL
                    REFERENCES rounds(id)
                    ON DELETE CASCADE,
    division_id INTEGER NOT NULL
                    REFERENCES divisions(id),
    message_id  TEXT    NOT NULL,
    channel_id  TEXT    NOT NULL,
    posted_at   TEXT    NOT NULL, last_notice_msg_id TEXT, distribution_msg_id TEXT,
    UNIQUE (round_id, division_id)
);

-- qualifying_session_results
CREATE TABLE qualifying_session_results (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_result_id   INTEGER NOT NULL
                            REFERENCES session_results(id)
                            ON DELETE CASCADE,
    driver_user_id      INTEGER NOT NULL,
    team_role_id        INTEGER NOT NULL,
    finishing_position  INTEGER NOT NULL,
    outcome             TEXT    NOT NULL DEFAULT 'CLASSIFIED',
    tyre                TEXT,
    -- Absolute best-lap time string, e.g. "1:23.456".
    -- DNS/DNF/DSQ drivers carry their outcome literal here instead.
    best_lap            TEXT,
    points_awarded      INTEGER NOT NULL DEFAULT 0,
    driver_profile_id   INTEGER
);
CREATE INDEX idx_qsr_session
    ON qualifying_session_results(session_result_id);

-- race_session_results
CREATE TABLE race_session_results (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_result_id           INTEGER NOT NULL
                                    REFERENCES session_results(id)
                                    ON DELETE CASCADE,
    driver_user_id              INTEGER NOT NULL,
    team_role_id                INTEGER NOT NULL,
    finishing_position          INTEGER NOT NULL,
    outcome                     TEXT    NOT NULL DEFAULT 'CLASSIFIED',
    -- base_time_ms: race time in ms with ingame penalties already subtracted.
    -- NULL for lapped drivers, DNF, DNS, DSQ.
    base_time_ms                INTEGER,
    -- laps_behind: populated for "+N Laps" classified drivers; NULL otherwise.
    laps_behind                 INTEGER,
    -- ingame_time_penalties_ms: time penalty applied by the game (submitted at
    -- result entry). 0 when the submitted field was "N/A".
    ingame_time_penalties_ms    INTEGER NOT NULL DEFAULT 0,
    -- postrace_time_penalties_ms: added/removed by the steward penalty wizard.
    postrace_time_penalties_ms  INTEGER NOT NULL DEFAULT 0,
    -- appeal_time_penalties_ms: added/removed by the appeals phase.
    appeal_time_penalties_ms    INTEGER NOT NULL DEFAULT 0,
    fastest_lap                 TEXT,
    fastest_lap_bonus           INTEGER NOT NULL DEFAULT 0,
    points_awarded              INTEGER NOT NULL DEFAULT 0,
    driver_profile_id           INTEGER
);
CREATE INDEX idx_rsr_session
    ON race_session_results(session_result_id);

-- penalty_records
CREATE TABLE "penalty_records" (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    race_result_id          INTEGER REFERENCES race_session_results(id),
    qual_result_id          INTEGER REFERENCES qualifying_session_results(id),
    penalty_type            TEXT    NOT NULL,
    time_seconds            INTEGER,
    description             TEXT    NOT NULL,
    justification           TEXT    NOT NULL,
    applied_by              TEXT    NOT NULL,
    applied_at              TEXT    NOT NULL,
    announcement_channel_id TEXT,
    -- announcement_message_id: the verdict announcement itself, so an amendment can
    -- replace it rather than leaving a decision that contradicts the classification it
    -- was applied to (#189). NULL for a verdict announced before this column existed;
    -- those cannot be replaced and are re-announced fresh, which the log says (#345).
    announcement_message_id TEXT,
    -- The chunk list of that announcement, as elsewhere.
    announcement_message_ids TEXT
);

-- appeal_records
CREATE TABLE "appeal_records" (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    race_result_id          INTEGER REFERENCES race_session_results(id),
    qual_result_id          INTEGER REFERENCES qualifying_session_results(id),
    status                  TEXT    NOT NULL DEFAULT 'UPHELD',
    penalty_type            TEXT    NOT NULL,
    time_seconds            INTEGER,
    description             TEXT    NOT NULL,
    justification           TEXT    NOT NULL,
    submitted_by            TEXT    NOT NULL,
    submitted_at            TEXT    NOT NULL,
    announcement_channel_id TEXT,
    -- announcement_message_id: the verdict announcement itself, so an amendment can
    -- replace it rather than leaving a decision that contradicts the classification it
    -- was applied to (#189). NULL for a verdict announced before this column existed;
    -- those cannot be replaced and are re-announced fresh, which the log says (#345).
    announcement_message_id TEXT,
    -- The chunk list of that announcement, as elsewhere.
    announcement_message_ids TEXT
);

-- verdict_banner_messages: the banner that heads a round's run of verdict announcements.
-- A banner is a message of its own, above the cards, and belongs to no verdict record — so
-- an amendment re-announcing a round could take its verdicts down and not the banner over
-- them, leaving a header above empty space and posting a fresh one below (#345). One row per
-- banner posted; the replay deletes the round's and records what it posts in their place.
CREATE TABLE verdict_banner_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id   INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
    channel_id TEXT    NOT NULL,
    message_id TEXT    NOT NULL,
    posted_at  TEXT    NOT NULL
);
CREATE INDEX idx_verdict_banner_round ON verdict_banner_messages(round_id);

-- attendance_pardons
CREATE TABLE "attendance_pardons" (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    attendance_id  INTEGER NOT NULL
                   REFERENCES driver_round_attendance(id) ON DELETE CASCADE,
    pardon_type    TEXT    NOT NULL CHECK (pardon_type IN ('NO_RSVP', 'ABSENT', 'NO_SHOW')),
    justification  TEXT    NOT NULL,
    granted_by     INTEGER NOT NULL,
    granted_at     TEXT    NOT NULL,
    UNIQUE (attendance_id, pardon_type)
);

-- divisions
CREATE TABLE "divisions" (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id           INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    name                TEXT    NOT NULL,
    mention_role_id     INTEGER NOT NULL,
    forecast_channel_id INTEGER,
    status              TEXT    NOT NULL DEFAULT 'SETUP'
                            CHECK (status IN ('SETUP', 'ACTIVE', 'FINISHED', 'CANCELLED')),
    tier                INTEGER NOT NULL DEFAULT 1,
    lineup_channel_id   INTEGER,
    calendar_channel_id INTEGER,
    lineup_message_id   INTEGER,
    calendar_message_id TEXT
);

-- rounds
CREATE TABLE "rounds" (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    division_id     INTEGER NOT NULL,
    round_number    INTEGER NOT NULL,
    format          TEXT    NOT NULL,  -- NORMAL | SPRINT | MYSTERY | ENDURANCE
    track_name      TEXT,
    scheduled_at    TEXT    NOT NULL,  -- ISO datetime UTC, e.g. 2025-06-15T18:00:00
    phase1_done     INTEGER NOT NULL DEFAULT 0,
    phase2_done     INTEGER NOT NULL DEFAULT 0,
    phase3_done     INTEGER NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'NOT_RUN'
                        CHECK (status IN ('NOT_RUN', 'AWAITING_RESULTS',
                                          'AWAITING_REPORT_VERDICTS',
                                          'AWAITING_APPEAL_VERDICTS',
                                          'FINAL', 'CANCELLED')),
    FOREIGN KEY (division_id) REFERENCES divisions(id)
);
CREATE INDEX idx_rounds_division ON rounds(division_id);

-- season_signup_config
CREATE TABLE season_signup_config (
    season_id            INTEGER PRIMARY KEY REFERENCES seasons(id) ON DELETE CASCADE,
    nationality_required INTEGER NOT NULL,
    time_type            TEXT    NOT NULL,
    time_image_required  INTEGER NOT NULL,
    -- JSON array of {"slot_id": "Mon_19_00", "day_of_week": 1, "time_hhmm": "19:00"}
    slots_json           TEXT    NOT NULL DEFAULT '[]',
    captured_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- driver_division_memberships
CREATE TABLE driver_division_memberships (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id         INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    division_id       INTEGER NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
    driver_profile_id INTEGER NOT NULL REFERENCES driver_profiles(id) ON DELETE CASCADE,
    UNIQUE (season_id, division_id, driver_profile_id)
);

-- weather_pipeline_config
CREATE TABLE "weather_pipeline_config" (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    phase_1_days  INTEGER NOT NULL DEFAULT 5,
    phase_2_days  INTEGER NOT NULL DEFAULT 2,
    phase_3_hours INTEGER NOT NULL DEFAULT 2
);

-- attendance_config
CREATE TABLE "attendance_config" (
    id                       INTEGER PRIMARY KEY CHECK (id = 1),
    module_enabled           INTEGER NOT NULL DEFAULT 0,
    rsvp_notice_days         INTEGER NOT NULL DEFAULT 5,
    rsvp_last_notice_hours   INTEGER NOT NULL DEFAULT 24,
    rsvp_deadline_hours      INTEGER NOT NULL DEFAULT 2,
    no_rsvp_penalty          INTEGER NOT NULL DEFAULT 1,
    absent_penalty           INTEGER NOT NULL DEFAULT 1,
    no_show_penalty          INTEGER NOT NULL DEFAULT 1,
    autoreserve_threshold    INTEGER,
    autosack_threshold       INTEGER
);

-- attendance_division_config
CREATE TABLE "attendance_division_config" (
    division_id            INTEGER PRIMARY KEY
                               REFERENCES divisions(id)
                               ON DELETE CASCADE,
    rsvp_channel_id        TEXT,
    attendance_channel_id  TEXT,
    attendance_message_id  TEXT
);

-- image_aspect_toggles
CREATE TABLE "image_aspect_toggles" (
    aspect   TEXT    PRIMARY KEY,
    enabled  INTEGER NOT NULL DEFAULT 0
);

-- image_config
CREATE TABLE "image_config" (
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

-- image_tier_colour
CREATE TABLE "image_tier_colour" (
    division_slug  TEXT NOT NULL,
    slot           TEXT NOT NULL,
    colour         TEXT NOT NULL,
    PRIMARY KEY (division_slug, slot)
);

-- driver_portraits
CREATE TABLE "driver_portraits" (
    discord_user_id  TEXT PRIMARY KEY,
    avatar_key       TEXT NOT NULL,
    fetched_at       TEXT NOT NULL
);

-- results_module_config
CREATE TABLE "results_module_config" (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    module_enabled INTEGER NOT NULL DEFAULT 0
);

-- points_config_store
CREATE TABLE "points_config_store" (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    config_name TEXT    NOT NULL UNIQUE
);

-- round_amend_channels
CREATE TABLE "round_amend_channels" (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id     INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
    channel_id   INTEGER NOT NULL,
    -- session_types: the sessions this amendment re-enters, as a JSON array of session-type
    -- values in running order (#345). One amendment covers any number of a round's sessions,
    -- its reports and appeals being reviewed for them together, as a round's are.
    session_types TEXT   NOT NULL,
    created_at   TEXT    NOT NULL,
    -- pre_amendment_state: the round as it stood before stage one overwrote it, as JSON
    -- (#345). The amendment's first stage commits the corrected classification, so an
    -- amendment abandoned before its last stage is approved leaves the round scored one way
    -- and posted another. This is what the revert puts back: for each amended session, its
    -- header, its driver rows, and its verdict records whole. NULL until stage one has
    -- written, and cleared when the amendment completes.
    pre_amendment_state TEXT,
    -- expires_at: when an unapproved amendment is reverted. The stages have no timeout of
    -- their own, so without this a manager who walks away leaves the round on "Provisional
    -- Results" for ever.
    expires_at   TEXT,
    -- closed_at: set where the amendment has finished but its channel could not be deleted,
    -- the guild or the channel being out of cache (#345). The row is the only thing that names
    -- the channel, so restart recovery finds an orphan by no other route and it is kept for
    -- that — but it no longer describes an amendment in progress, and nothing treats it as one.
    closed_at   TEXT,
    -- superseded_announcements: the verdict announcements standing in the channel when the
    -- amendment's report stage was approved, as JSON (#345). That stage deletes the round's
    -- verdict records and writes the approved set back, so the message ids of the announcements
    -- to be taken down are gone by the time the final stage re-announces — noted here first.
    superseded_announcements TEXT,
    -- One amendment of a round at a time; the command allows one per division besides.
    UNIQUE (round_id)
);

-- signup_module_settings
CREATE TABLE "signup_module_settings" (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    nationality_required    INTEGER NOT NULL DEFAULT 1,
    time_type               TEXT    NOT NULL DEFAULT 'TIME_TRIAL',
    time_image_required     INTEGER NOT NULL DEFAULT 1
);

-- signup_module_config
CREATE TABLE "signup_module_config" (
    id                          INTEGER PRIMARY KEY CHECK (id = 1),
    signup_channel_id           INTEGER,
    base_role_id                INTEGER,
    signed_up_role_id           INTEGER,
    signups_open                INTEGER NOT NULL DEFAULT 0,
    signup_button_message_id    INTEGER,
    selected_tracks_json        TEXT    NOT NULL DEFAULT '[]',
    signup_closed_message_id    INTEGER,
    close_at                    TEXT
);

-- signup_division_config
CREATE TABLE "signup_division_config" (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    division_id  INTEGER NOT NULL UNIQUE
                     REFERENCES divisions(id)
                     ON DELETE CASCADE
);

-- signup_wizard_records
CREATE TABLE "signup_wizard_records" (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user_id             TEXT    NOT NULL UNIQUE,
    wizard_state                TEXT    NOT NULL DEFAULT 'UNENGAGED',
    signup_channel_id           INTEGER,
    -- Configuration snapshot captured at wizard start (JSON)
    config_snapshot_json        TEXT,
    -- Draft answers accumulated during collection (JSON object)
    draft_answers_json          TEXT    NOT NULL DEFAULT '{}',
    -- Index into the lap-time steps when collecting multi-track times
    current_lap_track_index     INTEGER NOT NULL DEFAULT 0,
    -- Timestamp of last wizard activity, used for inactivity timeout
    last_activity_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    created_at                  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_signup_wizard_channel
    ON signup_wizard_records(signup_channel_id);

-- signup_availability_slots
CREATE TABLE "signup_availability_slots" (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    day_of_week     INTEGER NOT NULL,
    time_hhmm       TEXT    NOT NULL,
    UNIQUE(day_of_week, time_hhmm)
);

-- signup_windows
CREATE TABLE "signup_windows" (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id            INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    selected_tracks_json TEXT    NOT NULL DEFAULT '[]',
    close_at             TEXT,
    opened_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    closed_at            TEXT
);
CREATE INDEX idx_signup_windows_season ON signup_windows(season_id);

-- signup_records
CREATE TABLE "signup_records" (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id             INTEGER REFERENCES seasons(id) ON DELETE CASCADE,
    window_id             INTEGER REFERENCES signup_windows(id) ON DELETE SET NULL,
    discord_user_id       TEXT    NOT NULL,
    discord_username      TEXT,
    server_display_name   TEXT,
    nationality           TEXT,
    platform              TEXT,
    platform_id           TEXT,
    availability_slot_ids TEXT,       -- JSON array of durable slot ids ("Mon_19_00")
    driver_type           TEXT,
    preferred_teams       TEXT,       -- JSON array of team names in selection order
    preferred_teammate    TEXT,
    lap_times_json        TEXT,       -- JSON object: {track_id: "M:ss.mss"}
    notes                 TEXT,
    signup_channel_id     INTEGER,
    total_lap_ms          INTEGER,
    created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    approved              INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_signup_records_account ON signup_records(discord_user_id);
CREATE INDEX idx_signup_records_season
    ON signup_records(season_id, discord_user_id);

-- driver_profiles
CREATE TABLE "driver_profiles" (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user_id    TEXT    NOT NULL UNIQUE,
    current_state      TEXT    NOT NULL,
    former_driver      INTEGER NOT NULL DEFAULT 0,
    is_test_driver     INTEGER NOT NULL DEFAULT 0,
    test_display_name  TEXT,
    test_nationality   TEXT
);

-- driver_accounts
CREATE TABLE "driver_accounts" (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    driver_profile_id INTEGER NOT NULL REFERENCES driver_profiles(id) ON DELETE CASCADE,
    discord_user_id   TEXT    NOT NULL UNIQUE
);
CREATE INDEX idx_driver_accounts_profile ON driver_accounts(driver_profile_id);

-- driver_history_entries
CREATE TABLE "driver_history_entries" (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user_id      TEXT,
    driver_profile_id    INTEGER REFERENCES driver_profiles(id) ON DELETE SET NULL,
    season_number        INTEGER NOT NULL,
    division_name        TEXT    NOT NULL,
    division_tier        INTEGER NOT NULL DEFAULT 0,
    final_position       INTEGER NOT NULL DEFAULT 0,
    final_points         INTEGER NOT NULL DEFAULT 0,
    points_gap_to_winner INTEGER NOT NULL DEFAULT 0,
    cancelled            INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX idx_driver_history_unique
    ON driver_history_entries(driver_profile_id, season_number, division_name);
CREATE INDEX idx_driver_history_identity
    ON driver_history_entries(discord_user_id);

-- default_teams
CREATE TABLE "default_teams" (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    max_seats  INTEGER NOT NULL DEFAULT 2,
    is_reserve INTEGER NOT NULL DEFAULT 0
);

-- team_role_configs
CREATE TABLE "team_role_configs" (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    team_name   TEXT    NOT NULL UNIQUE,
    role_id     INTEGER NOT NULL,
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- seasons
CREATE TABLE "seasons" (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    start_date     TEXT    NOT NULL,  -- ISO date YYYY-MM-DD
    status         TEXT    NOT NULL DEFAULT 'SETUP',  -- SETUP | ACTIVE | COMPLETED | CANCELLED
    season_number  INTEGER NOT NULL DEFAULT 0,
    game_edition   INTEGER NOT NULL DEFAULT 0,
    stage          TEXT
);
CREATE UNIQUE INDEX idx_seasons_one_live
    ON seasons((1))
    WHERE status IN ('SETUP', 'ACTIVE');

-- audit_entries
CREATE TABLE "audit_entries" (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id     INTEGER NOT NULL,
    actor_name   TEXT    NOT NULL,
    division_id  INTEGER,
    change_type  TEXT    NOT NULL,
    old_value    TEXT    NOT NULL DEFAULT '',
    new_value    TEXT    NOT NULL DEFAULT '',
    timestamp    TEXT    NOT NULL
);

-- pending_messages
CREATE TABLE "pending_messages" (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id        INTEGER NOT NULL,
    content           TEXT    NOT NULL,
    failure_reason    TEXT    NOT NULL,
    enqueued_at       TEXT    NOT NULL,
    retry_count       INTEGER NOT NULL DEFAULT 0,
    last_attempted_at TEXT
);

-- season_review_prompts
CREATE TABLE "season_review_prompts" (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    season_id   INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    reviewer_id INTEGER NOT NULL,
    posted_at   TEXT    NOT NULL
);

-- The circuits a league can schedule, seeded once.
INSERT INTO tracks (id, name, gp_name, location, country, mu, sigma) VALUES
    (1, 'Albert Park Circuit', 'Australian Grand Prix', 'Melbourne, Australia', 'Australia', 0.1, 0.05),
    (2, 'Shanghai International Circuit', 'Chinese Grand Prix', 'Shanghai, China', 'China', 0.25, 0.05),
    (3, 'Suzuka International Racing Course', 'Japanese Grand Prix', 'Suzuka, Japan', 'Japan', 0.25, 0.07),
    (4, 'Bahrain International Circuit', 'Bahrain Grand Prix', 'Sakhir, Bahrain', 'Bahrain', 0.05, 0.02),
    (5, 'Jeddah Corniche Circuit', 'Saudi Arabian Grand Prix', 'Jeddah, Saudi Arabia', 'Saudi Arabia', 0.05, 0.03),
    (6, 'Miami International Autodrome', 'Miami Grand Prix', 'Miami, Florida, United States of America', 'United States of America', 0.15, 0.07),
    (7, 'Autodromo Internazionale Enzo e Dino Ferrari', 'Emilia Romagna Grand Prix', 'Imola, Italy', 'Italy', 0.25, 0.05),
    (8, 'Circuit de Monaco', 'Monaco Grand Prix', 'Municipality of Monaco, Monaco', 'Monaco', 0.25, 0.05),
    (9, 'Circuit de Barcelona-Catalunya', 'Barcelona-Catalunya Grand Prix', 'Montmeló, Spain', 'Spain', 0.2, 0.05),
    (10, 'Circuit Gilles Villeneuve', 'Canadian Grand Prix', 'Montreal, Canada', 'Canada', 0.3, 0.05),
    (11, 'Red Bull Ring', 'Austrian Grand Prix', 'Spielberg, Austria', 'Austria', 0.25, 0.07),
    (12, 'Silverstone Circuit', 'British Grand Prix', 'Silverstone, United Kingdom', 'United Kingdom', 0.3, 0.05),
    (13, 'Circuit de Spa-Francorchamps', 'Belgian Grand Prix', 'Stavelot, Belgium', 'Belgium', 0.3, 0.08),
    (14, 'Hungaroring', 'Hungarian Grand Prix', 'Mogyoród, Hungary', 'Hungary', 0.25, 0.05),
    (15, 'Circuit Zandvoort', 'Dutch Grand Prix', 'Zandvoort, Netherlands', 'Netherlands', 0.25, 0.05),
    (16, 'Autodromo Nazionale Monza', 'Italian Grand Prix', 'Monza, Italy', 'Italy', 0.15, 0.03),
    (17, 'Circuito de Madring', 'Spanish Grand Prix', 'Madrid, Spain', 'Spain', 0.15, 0.05),
    (18, 'Baku City Circuit', 'Azerbaijan Grand Prix', 'Baku, Azerbaijan', 'Azerbaijan', 0.1, 0.03),
    (19, 'Marina Bay Street Circuit', 'Singapore Grand Prix', 'Singapore City, Singapore', 'Singapore', 0.2, 0.07),
    (20, 'Circuit of the Americas', 'United States Grand Prix', 'Austin, Texas, United States of America', 'United States of America', 0.1, 0.03),
    (21, 'Autódromo Hermanos Rodriguez', 'Mexico City Grand Prix', 'Mexico City, Mexico', 'Mexico', 0.05, 0.03),
    (22, 'Autódromo José Carlos Pace', 'São Paulo Grand Prix', 'São Paulo, Brazil', 'Brazil', 0.3, 0.08),
    (23, 'Las Vegas Strip Circuit', 'Las Vegas Grand Prix', 'Las Vegas, Nevada, United States of America', 'United States of America', 0.05, 0.02),
    (24, 'Lusail International Circuit', 'Qatar Grand Prix', 'Lusail, Qatar', 'Qatar', 0.05, 0.02),
    (25, 'Yas Marina Circuit', 'Abu Dhabi Grand Prix', 'Abu Dhabi, United Arab Emirates', 'United Arab Emirates', 0.05, 0.03),
    (26, 'Autódromo Internacional do Algarve', 'Portuguese Grand Prix', 'Portimão, Portugal', 'Portugal', 0.1, 0.03),
    (27, 'Istanbul Park', 'Turkish Grand Prix', 'Istanbul, Turkey', 'Turkey', 0.1, 0.05),
    (28, 'Circuit Paul Ricard', 'French Grand Prix', 'Le Castellet, France', 'France', 0.25, 0.05);

-- Triggers, after the seed rows so that none fires on them.
CREATE TRIGGER driver_division_membership_on_insert
AFTER INSERT ON driver_season_assignments
WHEN NEW.committed = 1
BEGIN
    INSERT OR IGNORE INTO driver_division_memberships (season_id, division_id, driver_profile_id)
    VALUES (NEW.season_id, NEW.division_id, NEW.driver_profile_id);
END;

CREATE TRIGGER driver_division_membership_on_update
AFTER UPDATE OF committed, division_id ON driver_season_assignments
WHEN NEW.committed = 1
BEGIN
    INSERT OR IGNORE INTO driver_division_memberships (season_id, division_id, driver_profile_id)
    VALUES (NEW.season_id, NEW.division_id, NEW.driver_profile_id);
END;

CREATE TRIGGER driver_account_on_profile_insert
AFTER INSERT ON driver_profiles
BEGIN
    INSERT INTO driver_accounts (driver_profile_id, discord_user_id)
    VALUES (NEW.id, NEW.discord_user_id);
END;

CREATE TRIGGER driver_account_on_current_change
AFTER UPDATE OF discord_user_id ON driver_profiles
WHEN NOT EXISTS (
    SELECT 1 FROM driver_accounts
    WHERE discord_user_id = NEW.discord_user_id
      AND driver_profile_id = NEW.id
)
BEGIN
    INSERT INTO driver_accounts (driver_profile_id, discord_user_id)
    VALUES (NEW.id, NEW.discord_user_id);
END;

CREATE TRIGGER seasons_stage_fill_on_insert
AFTER INSERT ON seasons
WHEN NEW.stage IS NULL AND NEW.status IN ('SETUP', 'ACTIVE', 'COMPLETED', 'CANCELLED')
BEGIN
    UPDATE seasons SET stage = CASE NEW.status
        WHEN 'SETUP'     THEN 'PLACEMENTS'
        WHEN 'ACTIVE'    THEN 'ONGOING'
        WHEN 'COMPLETED' THEN 'COMPLETED'
        WHEN 'CANCELLED' THEN 'CANCELLED'
    END
    WHERE id = NEW.id;
END;

CREATE TRIGGER seasons_stage_follow_status
AFTER UPDATE OF status ON seasons
WHEN NEW.status IS NOT OLD.status
BEGIN
    UPDATE seasons SET stage = CASE
        WHEN NEW.status = 'SETUP'
             AND NEW.stage IN ('CONFIGURATION', 'WAITING', 'SIGNUPS', 'PLACEMENTS')
            THEN NEW.stage
        WHEN NEW.status = 'SETUP' THEN 'PLACEMENTS'
        WHEN NEW.status = 'ACTIVE'
             AND NEW.stage IN ('ONGOING', 'ONGOING_SIGNUPS', 'ONGOING_PLACEMENTS',
                               'PENDING_COMPLETION')
            THEN NEW.stage
        WHEN NEW.status = 'ACTIVE' THEN 'ONGOING'
        ELSE NEW.status
    END
    WHERE id = NEW.id;
END;

CREATE TRIGGER seasons_stage_matches_status
BEFORE UPDATE OF stage ON seasons
WHEN NEW.stage IS NOT NULL AND NOT (
       (NEW.status = 'SETUP'
        AND NEW.stage IN ('CONFIGURATION', 'WAITING', 'SIGNUPS', 'PLACEMENTS'))
    OR (NEW.status = 'ACTIVE'
        AND NEW.stage IN ('ONGOING', 'ONGOING_SIGNUPS', 'ONGOING_PLACEMENTS',
                          'PENDING_COMPLETION'))
    OR (NEW.status IN ('COMPLETED', 'CANCELLED') AND NEW.stage = NEW.status)
)
BEGIN
    SELECT RAISE(ABORT, 'season stage does not match its status');
END;

CREATE TRIGGER seasons_stage_matches_status_on_insert
BEFORE INSERT ON seasons
WHEN NEW.stage IS NOT NULL AND NOT (
       (NEW.status = 'SETUP'
        AND NEW.stage IN ('CONFIGURATION', 'WAITING', 'SIGNUPS', 'PLACEMENTS'))
    OR (NEW.status = 'ACTIVE'
        AND NEW.stage IN ('ONGOING', 'ONGOING_SIGNUPS', 'ONGOING_PLACEMENTS',
                          'PENDING_COMPLETION'))
    OR (NEW.status IN ('COMPLETED', 'CANCELLED') AND NEW.stage = NEW.status)
)
BEGIN
    SELECT RAISE(ABORT, 'season stage does not match its status');
END;

CREATE TRIGGER driver_season_assignments_committed_default
AFTER INSERT ON driver_season_assignments
WHEN NEW.committed IS NULL
BEGIN
    UPDATE driver_season_assignments
    SET committed = CASE
        WHEN (SELECT status FROM seasons WHERE seasons.id = NEW.season_id)
             IN ('ACTIVE', 'COMPLETED', 'CANCELLED') THEN 1
        ELSE 0
    END
    WHERE id = NEW.id;
END;
