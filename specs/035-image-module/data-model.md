# Data Model: Image Module — Initial Setup & Configuration

**Feature**: 035-image-module | **Date**: 2026-08-10 | **Migration**: `039_image_module.sql`

Three tables. All server-scoped; none is per-division (spec Assumptions). Column defaults carry the
spec's packaged defaults, so a freshly inserted row is a fully valid default configuration without
any application-side default logic.

---

## Table: `image_config`

One row per server, created on first `/module enable images`.

| Column | Type | Default | Requirement | Notes |
|--------|------|---------|-------------|-------|
| `server_id` | INTEGER PK | — | FR-003 | `REFERENCES server_configs(server_id) ON DELETE CASCADE` |
| `module_enabled` | INTEGER NOT NULL | `0` | FR-001 | Default-off per Principle X.1 |
| `template_directory` | TEXT NOT NULL | `'resources/templates'` | FR-010 | Project-root-relative |
| `calendar_template` | TEXT NOT NULL | `'calendar_template.svg'` | FR-012 | |
| `lineup_template` | TEXT NOT NULL | `'lineup_template.svg'` | FR-012 | |
| `results_qualifying_template` | TEXT NOT NULL | `'results_qualifying_template.svg'` | FR-012 | |
| `results_race_template` | TEXT NOT NULL | `'results_race_template.svg'` | FR-012 | |
| `standings_drivers_template` | TEXT NOT NULL | `'standings_drivers_template.svg'` | FR-012 | |
| `standings_constructors_template` | TEXT NOT NULL | `'standings_constructors_template.svg'` | FR-012 | |
| `attendance_template` | TEXT NOT NULL | `'attendance_template.svg'` | FR-012 | |
| `rsvp_template` | TEXT NOT NULL | `'rsvp_template.svg'` | FR-012 | |
| `weather_p1_template` | TEXT NOT NULL | `'weather_p1_template.svg'` | FR-012 | |
| `weather_p2_template` | TEXT NOT NULL | `'weather_p2_template.svg'` | FR-012 | Non-sprint |
| `weather_p3_template` | TEXT NOT NULL | `'weather_p3_template.svg'` | FR-012 | Non-sprint |
| `weather_p2_sprint_template` | TEXT NOT NULL | `'weather_p2_sprint_template.svg'` | FR-012 | |
| `weather_p3_sprint_template` | TEXT NOT NULL | `'weather_p3_sprint_template.svg'` | FR-012 | |
| `weather_mystery_template` | TEXT NOT NULL | `'weather_mystery_template.svg'` | FR-012 | |
| `verdicts_template` | TEXT NOT NULL | `'verdicts_template.svg'` | FR-012 | |
| `track_image_directory` | TEXT NOT NULL | `'resources/tracks'` | FR-016 | |
| `team_image_directory` | TEXT NOT NULL | `'resources/teams'` | FR-016 | |
| `flag_directory` | TEXT NOT NULL | `'resources/flags'` | FR-016 | |
| `driver_image_directory` | TEXT NOT NULL | `'resources/drivers'` | FR-016 | |
| `marker_directory` | TEXT NOT NULL | `'resources/markers'` | FR-016 | |
| `weather_icon_directory` | TEXT NOT NULL | `'resources/weather'` | FR-016 | |
| `tyre_directory` | TEXT NOT NULL | `'resources/tyres'` | FR-016 | |
| `time_zone` | TEXT NOT NULL | `'UTC'` | FR-021 | IANA name; offset resolved per displayed date |
| `time_format` | TEXT NOT NULL | `'24H'` | FR-022 | `12H` \| `24H` |
| `date_format` | TEXT NOT NULL | `'DDD_DD_MON_YYYY'` | FR-023 | Carries weekday; see below |
| `fastest_lap_colour` | TEXT NOT NULL | `'#A020F0'` | FR-024 | `#RRGGBB` |

**27 settable columns on this table**: 1 template directory + 15 template filenames + 7 asset
directories + 4 preferences. `module_enabled` is state rather than configuration and is not
settable through the configuration commands. With the 8 toggles below, the module carries **35
configuration values** in total.

### Date format values (FR-023)

At least one must carry the weekday. Stored as a token, formatted at render time:

| Token | Renders as | Weekday |
|-------|-----------|---------|
| `DDD_DD_MON_YYYY` | `Sun 14 Jun 2026` | yes — **default** |
| `DD_MM_YYYY` | `14/06/2026` | no |
| `MM_DD_YYYY` | `06/14/2026` | no |
| `YYYY_MM_DD` | `2026-06-14` | no |
| `DD_MON_YYYY` | `14 Jun 2026` | no |

The weekday-carrying format is the default deliberately: a season run on the same weekday every
second week makes the weekday the part of a date a driver reads for.

---

## Table: `image_aspect_toggles`

One row per server per aspect — eight rows, all inserted at enable time with `enabled = 0`.

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `server_id` | INTEGER NOT NULL | — | `REFERENCES image_config(server_id) ON DELETE CASCADE` |
| `aspect` | TEXT NOT NULL | — | See enumeration below |
| `enabled` | INTEGER NOT NULL | `0` | FR-017 — all disabled by default |

`PRIMARY KEY (server_id, aspect)`.

### Aspect enumeration and template mapping

The mapping is a code constant, not data — no command addresses an individual template's toggle.

| Aspect | Source module | Templates | Test kinds |
|--------|---------------|-----------|------------|
| `calendar` | — (foundational) | `calendar_template` | `calendar` |
| `lineup` | — (foundational) | `lineup_template` | `lineup` |
| `results` | results | `results_qualifying_template`, `results_race_template` | `results` |
| `standings` | results | `standings_drivers_template`, `standings_constructors_template` | `standings` |
| `attendance` | attendance | `attendance_template` | `attendance` |
| `rsvp` | attendance | `rsvp_template` | `rsvp` |
| `weather` | weather | `weather_p1`, `weather_p2`, `weather_p3`, `weather_p2_sprint`, `weather_p3_sprint`, `weather_mystery` | `weather-p1`, `weather-p2`, `weather-p3`, `weather-mystery` |
| `verdicts` | results | `verdicts_template` | `verdicts` |

15 templates, 11 test kinds. `weather-p2` and `weather-p3` each cover two templates (sprint and
non-sprint), which is why FR-040 requires both variants back from one test.

`source_module` drives FR-031's third state: an aspect enabled while its source module is disabled
reports **enabled but invalid**.

---

## Table: `image_render_notices`

Append-only. The Principle XIV.4 audit record for non-fatal degradations.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK AUTOINCREMENT | |
| `server_id` | INTEGER NOT NULL | `REFERENCES server_configs(server_id) ON DELETE CASCADE` |
| `image_type` | TEXT NOT NULL | Template key, not aspect — a notice is per render |
| `rendered_at` | TEXT NOT NULL | UTC ISO 8601 |
| `notice_kind` | TEXT NOT NULL | `FONT_SUBSTITUTED` \| `WRAP_TRUNCATED` \| `INLINE_SIZE_TRUNCATED` |
| `field_id` | TEXT | Template `@id`; NULL for render-wide notices |
| `detail` | TEXT NOT NULL | Human-readable, posted to the log channel |

Index on `(server_id, rendered_at)` for retrieval.

**Render problems are deliberately not stored here**, per Constitution v2.11.0: a problem aborts the
render, falls back to text, and is recorded in the existing audit log alongside the source module's
own output entry.

---

## Retention across disable — the Principle X.6 exception

Principle X.6 (as amended at v2.12.0) requires a module claiming the exception to **enumerate the
qualifying values**. A value qualifies only if it names nothing the bot owns or schedules.

| Value group | Count | Qualifies | Why |
|-------------|-------|-----------|-----|
| Template directory | 1 | yes | Filesystem path |
| Template filenames | 15 | yes | Filenames |
| Asset directories | 7 | yes | Filesystem paths |
| Time zone, time format, date format | 3 | yes | Display preferences |
| Fastest-lap colour | 1 | yes | A colour |
| Aspect toggles | 8 | yes | Intent, naming no server object |
| **Total** | **35 rows across 2 tables** | **all qualify** | |

**No image configuration value names a Discord channel, role, message or scheduled job.** The module
therefore retains everything on disable (FR-004a), `/module disable images` sets
`module_enabled = 0` and nothing else, and no `--preserve-config` flag is offered (FR-004b).

This is why `image_render_notices` is untouched by a disable too — it is history, governed by
Principle X.3, which retains historical data in all cases.

---

## Derived types (not persisted)

Dataclasses in `src/models/image_module.py`, returned by services and rendered by the cogs.

### `ValidityReport`

Produced per template by `ImageValidityService`. The shape Principle XIV.9's "stable surface"
invariant protects — adding a layer must not change these fields.

| Field | Type | Notes |
|-------|------|-------|
| `template_key` | `str` | e.g. `weather_p3_sprint_template` |
| `resolved_path` | `Path \| None` | Full path searched — required in the invalid message (FR-028) |
| `valid` | `bool` | Passed every *currently implemented* layer |
| `depth_checked` | `int` | Highest layer applied. `1` throughout this increment (FR-028b) |
| `failed_layer` | `int \| None` | Which layer rejected it |
| `reason` | `str \| None` | Distinguishes missing file from unparseable from no-canvas (FR-028c) |

### `AspectStatus`

Produced per aspect for `/images config view` and the `/season review` addendum.

| Field | Type | Notes |
|-------|------|-------|
| `aspect` | `str` | |
| `state` | `ENABLED \| DISABLED \| ENABLED_INVALID` | The three states of FR-031 |
| `template_reports` | `list[ValidityReport]` | 1, 2 or 6 entries |
| `blocking_reasons` | `list[str]` | Names the specific template, the disabled source module, or the absent converter (FR-032) |

`state` is computed, never stored: `ENABLED_INVALID` when the toggle is on **and** any of — a
backing template invalid, the source module disabled, or the SVG-to-PNG converter absent.

### `RenderOutcome`

Returned by `ImageRenderService`. Carries the XIV.4 split explicitly so a caller cannot mistake a
degraded render for a clean one.

| Field | Type | Notes |
|-------|------|-------|
| `png_paths` | `list[Path]` | Empty when a problem occurred; more than one for multi-variant kinds |
| `problem` | `str \| None` | Non-null means the render aborted; caller falls back to text |
| `notices` | `list[RenderNotice]` | May be non-empty on success |

---

## Migration `039_image_module.sql`

Forward-only, matching the house style of `030_attendance_module.sql`:

- `CREATE TABLE IF NOT EXISTS` for all three tables, with the defaults above inline.
- No backfill. Servers that have never enabled the module get no row; the row is created by
  `/module enable images`, which also inserts the eight toggle rows in the same transaction
  (FR-003, atomicity per Principle X.2).
- No `DROP`, no data migration — nothing pre-existing is touched.
