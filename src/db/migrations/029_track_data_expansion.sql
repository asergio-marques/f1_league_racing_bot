-- Migration 029: Track Data Expansion
-- Creates the tracks, track_records, and lap_records tables.
-- Drops the retired track_rpc_params table.
-- Seeds 28 default circuits.
-- Renames existing rounds.track_name values from old short names to canonical circuit names.

-- 1. Create tracks table
CREATE TABLE IF NOT EXISTS tracks (
    id       INTEGER PRIMARY KEY NOT NULL,
    name     TEXT    NOT NULL UNIQUE,
    gp_name  TEXT    NOT NULL,
    location TEXT    NOT NULL,
    country  TEXT    NOT NULL,
    mu       REAL    NOT NULL,
    sigma    REAL    NOT NULL
);

-- 2. Create track_records table (structural prerequisite — populated by a future increment)
CREATE TABLE IF NOT EXISTS track_records (
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

-- 3. Create lap_records table (race sessions only — populated by a future increment)
CREATE TABLE IF NOT EXISTS lap_records (
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

-- 4. Drop retired server-override table (IF EXISTS guards clean installations)
DROP TABLE IF EXISTS track_rpc_params;

-- 5. Seed 28 default circuits (INSERT OR IGNORE — idempotent on re-run)
INSERT OR IGNORE INTO tracks (id, name, gp_name, location, country, mu, sigma) VALUES
( 1, 'Albert Park Circuit',                            'Australian Grand Prix',       'Melbourne, Australia',                                   'Australia',            0.10, 0.05),
( 2, 'Shanghai International Circuit',                 'Chinese Grand Prix',          'Shanghai, China',                                        'China',                0.25, 0.05),
( 3, 'Suzuka International Racing Course',             'Japanese Grand Prix',         'Suzuka, Japan',                                          'Japan',                0.25, 0.07),
( 4, 'Bahrain International Circuit',                  'Bahrain Grand Prix',          'Sakhir, Bahrain',                                        'Bahrain',              0.05, 0.02),
( 5, 'Jeddah Corniche Circuit',                        'Saudi Arabian Grand Prix',    'Jeddah, Saudi Arabia',                                   'Saudi Arabia',         0.05, 0.03),
( 6, 'Miami International Autodrome',                  'Miami Grand Prix',            'Miami, Florida, United States of America',               'United States of America', 0.15, 0.07),
( 7, 'Autodromo Internazionale Enzo e Dino Ferrari',   'Emilia Romagna Grand Prix',   'Imola, Italy',                                           'Italy',                0.25, 0.05),
( 8, 'Circuit de Monaco',                              'Monaco Grand Prix',           'Municipality of Monaco, Monaco',                         'Monaco',               0.25, 0.05),
( 9, 'Circuit de Barcelona-Catalunya',                 'Barcelona-Catalunya Grand Prix','Montmeló, Spain',                                      'Spain',                0.20, 0.05),
(10, 'Circuit Gilles Villeneuve',                      'Canadian Grand Prix',         'Montreal, Canada',                                       'Canada',               0.30, 0.05),
(11, 'Red Bull Ring',                                  'Austrian Grand Prix',         'Spielberg, Austria',                                     'Austria',              0.25, 0.07),
(12, 'Silverstone Circuit',                            'British Grand Prix',          'Silverstone, United Kingdom',                            'United Kingdom',       0.30, 0.05),
(13, 'Circuit de Spa-Francorchamps',                   'Belgian Grand Prix',          'Stavelot, Belgium',                                      'Belgium',              0.30, 0.08),
(14, 'Hungaroring',                                    'Hungarian Grand Prix',        'Mogyoród, Hungary',                                      'Hungary',              0.25, 0.05),
(15, 'Circuit Zandvoort',                              'Dutch Grand Prix',            'Zandvoort, Netherlands',                                 'Netherlands',          0.25, 0.05),
(16, 'Autodromo Nazionale Monza',                      'Italian Grand Prix',          'Monza, Italy',                                           'Italy',                0.15, 0.03),
(17, 'Circuito de Madring',                            'Spanish Grand Prix',          'Madrid, Spain',                                          'Spain',                0.15, 0.05),
(18, 'Baku City Circuit',                              'Azerbaijan Grand Prix',       'Baku, Azerbaijan',                                       'Azerbaijan',           0.10, 0.03),
(19, 'Marina Bay Street Circuit',                      'Singapore Grand Prix',        'Singapore City, Singapore',                              'Singapore',            0.20, 0.07),
(20, 'Circuit of the Americas',                        'United States Grand Prix',    'Austin, Texas, United States of America',                'United States of America', 0.10, 0.03),
(21, 'Autódromo Hermanos Rodriguez',                   'Mexico City Grand Prix',      'Mexico City, Mexico',                                    'Mexico',               0.05, 0.03),
(22, 'Autódromo José Carlos Pace',                     'São Paulo Grand Prix',        'São Paulo, Brazil',                                      'Brazil',               0.30, 0.08),
(23, 'Las Vegas Strip Circuit',                        'Las Vegas Grand Prix',        'Las Vegas, Nevada, United States of America',            'United States of America', 0.05, 0.02),
(24, 'Lusail International Circuit',                   'Qatar Grand Prix',            'Lusail, Qatar',                                          'Qatar',                0.05, 0.02),
(25, 'Yas Marina Circuit',                             'Abu Dhabi Grand Prix',        'Abu Dhabi, United Arab Emirates',                        'United Arab Emirates', 0.05, 0.03),
(26, 'Autódromo Internacional do Algarve',             'Portuguese Grand Prix',       'Portimão, Portugal',                                     'Portugal',             0.10, 0.03),
(27, 'Istanbul Park',                                  'Turkish Grand Prix',          'Istanbul, Turkey',                                       'Turkey',               0.10, 0.05),
(28, 'Circuit Paul Ricard',                            'French Grand Prix',           'Le Castellet, France',                                   'France',               0.25, 0.05);

-- 6. Rename existing rounds.track_name values from old short names to canonical circuit names
--    (27 mappings; any rounds already using canonical names are unaffected due to WHERE clause)
UPDATE rounds SET track_name = 'Albert Park Circuit'                          WHERE track_name = 'Australia';
UPDATE rounds SET track_name = 'Shanghai International Circuit'               WHERE track_name = 'China';
UPDATE rounds SET track_name = 'Suzuka International Racing Course'           WHERE track_name = 'Japan';
UPDATE rounds SET track_name = 'Bahrain International Circuit'                WHERE track_name = 'Bahrain';
UPDATE rounds SET track_name = 'Jeddah Corniche Circuit'                      WHERE track_name = 'Saudi Arabia';
UPDATE rounds SET track_name = 'Miami International Autodrome'                WHERE track_name = 'Miami';
UPDATE rounds SET track_name = 'Autodromo Internazionale Enzo e Dino Ferrari' WHERE track_name = 'Imola';
UPDATE rounds SET track_name = 'Circuit de Monaco'                            WHERE track_name = 'Monaco';
UPDATE rounds SET track_name = 'Circuit de Barcelona-Catalunya'               WHERE track_name = 'Barcelona';
UPDATE rounds SET track_name = 'Circuit Gilles Villeneuve'                    WHERE track_name = 'Canada';
UPDATE rounds SET track_name = 'Red Bull Ring'                                WHERE track_name = 'Austria';
UPDATE rounds SET track_name = 'Silverstone Circuit'                          WHERE track_name = 'United Kingdom';
UPDATE rounds SET track_name = 'Circuit de Spa-Francorchamps'                 WHERE track_name = 'Belgium';
UPDATE rounds SET track_name = 'Hungaroring'                                  WHERE track_name = 'Hungary';
UPDATE rounds SET track_name = 'Circuit Zandvoort'                            WHERE track_name = 'Netherlands';
UPDATE rounds SET track_name = 'Autodromo Nazionale Monza'                    WHERE track_name = 'Monza';
UPDATE rounds SET track_name = 'Circuito de Madring'                          WHERE track_name = 'Madrid';
UPDATE rounds SET track_name = 'Baku City Circuit'                            WHERE track_name = 'Azerbaijan';
UPDATE rounds SET track_name = 'Marina Bay Street Circuit'                    WHERE track_name = 'Singapore';
UPDATE rounds SET track_name = 'Circuit of the Americas'                      WHERE track_name = 'Texas';
UPDATE rounds SET track_name = 'Autódromo Hermanos Rodriguez'                 WHERE track_name = 'Mexico';
UPDATE rounds SET track_name = 'Autódromo José Carlos Pace'                   WHERE track_name = 'Brazil';
UPDATE rounds SET track_name = 'Las Vegas Strip Circuit'                      WHERE track_name = 'Las Vegas';
UPDATE rounds SET track_name = 'Lusail International Circuit'                 WHERE track_name = 'Qatar';
UPDATE rounds SET track_name = 'Yas Marina Circuit'                           WHERE track_name = 'Abu Dhabi';
UPDATE rounds SET track_name = 'Autódromo Internacional do Algarve'           WHERE track_name = 'Portugal';
UPDATE rounds SET track_name = 'Istanbul Park'                                WHERE track_name = 'Turkey';
