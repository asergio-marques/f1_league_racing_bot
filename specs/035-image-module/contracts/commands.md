# Contract: Command Surface

**Feature**: 035-image-module | 26 new commands, 1 modified

All commands live under the `/images` group, honouring the Bot Behavior Standards rule that new
features use `/domain action` subcommand groups and never hyphenated top-level commands.

Every command in this contract:

- stacks `@channel_guard` (interaction role + interaction channel, FR-043) with its tier decorator;
- gates on `module_enabled` and rejects with a message naming `/module enable images` (FR-005);
- responds **ephemerally** (FR-044).

**Tier key**: **A** = `@server_admin_only` (Administrator, FR-041) · **M** = `@admin_only`
(Manage Server, FR-042).

---

## Modified: `/module enable|disable`

`images` is added to `_MODULE_CHOICES` in `src/cogs/module_cog.py`, alongside the existing four.

**`_enable_images`** — one transaction (FR-003, Principle X.2):

1. `INSERT OR REPLACE` the `image_config` row with column defaults if absent.
2. Insert the eight `image_aspect_toggles` rows with `enabled = 0` if absent.
3. Set `module_enabled = 1`.
4. Probe for the Inkscape binary; if absent, include the fatal notice in the response (FR-007,
   FR-008) — but still enable, so the administrator can configure ahead of installing it.
5. Post confirmation to the log channel (Principle V).

**`_disable_images`** — sets `module_enabled = 0` and **nothing else** (FR-004a). No configuration
row is deleted, no toggle reset, no notice history purged. No `--preserve-config` flag is offered
(FR-004b). Posts a notice to the log channel.

Uses `INSERT OR REPLACE` rather than `UPDATE` on first enable, as `set_results_enabled` does —
`set_attendance_enabled`'s bare `UPDATE` silently no-ops when no row exists.

---

## Template location — tier A

### `/images config template-directory`

| Parameter | Type | Required |
|-----------|------|----------|
| `directory` | string | yes |

Interpreted relative to the project root (FR-010). Rejected if `Path.resolve()` escapes the root
(FR-011, research R8) — the stored value is left unchanged. On success, re-reports the validity of
all fifteen templates against the new directory.

### The fifteen template filename commands

Identical shape: one required `filename` string, tier A, stored verbatim, validity re-reported for
that template alone.

| Command | Column | Default |
|---------|--------|---------|
| `/images config calendar-template` | `calendar_template` | `calendar_template.svg` |
| `/images config lineup-template` | `lineup_template` | `lineup_template.svg` |
| `/images config results-qualifying-template` | `results_qualifying_template` | `results_qualifying_template.svg` |
| `/images config results-race-template` | `results_race_template` | `results_race_template.svg` |
| `/images config standings-drivers-template` | `standings_drivers_template` | `standings_drivers_template.svg` |
| `/images config standings-constructors-template` | `standings_constructors_template` | `standings_constructors_template.svg` |
| `/images config attendance-template` | `attendance_template` | `attendance_template.svg` |
| `/images config rsvp-template` | `rsvp_template` | `rsvp_template.svg` |
| `/images config weather-p1-template` | `weather_p1_template` | `weather_p1_template.svg` |
| `/images config weather-p2-template` | `weather_p2_template` | `weather_p2_template.svg` |
| `/images config weather-p3-template` | `weather_p3_template` | `weather_p3_template.svg` |
| `/images config weather-p2-sprint-template` | `weather_p2_sprint_template` | `weather_p2_sprint_template.svg` |
| `/images config weather-p3-sprint-template` | `weather_p3_sprint_template` | `weather_p3_sprint_template.svg` |
| `/images config weather-mystery-template` | `weather_mystery_template` | `weather_mystery_template.svg` |
| `/images config verdicts-template` | `verdicts_template` | `verdicts_template.svg` |

A filename containing a path separator is rejected — this command names a file inside the
configured directory, not a path.

---

## Asset location — tier A

Seven commands, identical shape: one required `directory` string, project-root-relative, subject to
the same containment rejection as `template-directory` (FR-016).

| Command | Column | Default |
|---------|--------|---------|
| `/images config track-image-directory` | `track_image_directory` | `resources/tracks` |
| `/images config team-image-directory` | `team_image_directory` | `resources/teams` |
| `/images config flag-directory` | `flag_directory` | `resources/flags` |
| `/images config driver-image-directory` | `driver_image_directory` | `resources/drivers` |
| `/images config marker-directory` | `marker_directory` | `resources/markers` |
| `/images config weather-icon-directory` | `weather_icon_directory` | `resources/weather` |
| `/images config tyre-directory` | `tyre_directory` | `resources/tyres` |

---

## Output toggles — tier M

### `/images config toggle`

| Parameter | Type | Required | Choices |
|-----------|------|----------|---------|
| `aspect` | Choice | yes | `calendar`, `lineup`, `results`, `standings`, `attendance`, `rsvp`, `weather`, `verdicts` |

Flips the stored value and confirms the new state. Eight static choices fit well inside Discord's
25-choice limit.

**In this increment the toggle is inert** (FR-017a). The response must say so plainly — a league
manager who enables `standings` and sees no change in the next standings post has been misled
otherwise. Suggested wording:

> ✅ Standings image output **enabled**. Not yet in effect — image posting is wired in a later
> update. Use `/images test standings` to see what it will produce.

If the aspect's source module is disabled, the response says so and the aspect will report
**enabled but invalid** (FR-031). The toggle is still stored — the league's intent is recorded.

---

## Presentation preferences — tier M

### `/images config time-zone`

| Parameter | Type | Required |
|-----------|------|----------|
| `zone` | string, autocompleted | yes |

Autocomplete over `zoneinfo.available_timezones()`, filtered by typed prefix, truncated to 25
(research R6). Rejected if not a recognised zone. Stored as the IANA name so the offset resolves
against the displayed date, not the configuration date (FR-021).

### `/images config time-format`

| Parameter | Type | Required | Choices |
|-----------|------|----------|---------|
| `format` | Choice | yes | `12-hour`, `24-hour` |

### `/images config date-format`

| Parameter | Type | Required | Choices |
|-----------|------|----------|---------|
| `format` | Choice | yes | The five tokens in [data-model.md](../data-model.md) |

Each choice's display name shows a worked example (`Sun 14 Jun 2026`) rather than the token, so the
manager picks by appearance. At least one carries the weekday (FR-023) and it is the default.

### `/images config fastest-lap-colour`

| Parameter | Type | Required |
|-----------|------|----------|
| `colour` | string | yes |

1. **Reject** unless `#` followed by exactly six hexadecimal digits, either case (FR-025). The
   stored value is unchanged and the error states the required form.
2. **Store** the value.
3. **Report** the WCAG contrast ratio against the background the configured race results template
   draws behind that field, located by a single documented `@id` (FR-026a).
4. **Warn** where the ratio is below 4.5:1 — the value is stored regardless (FR-026).
5. Where the ratio cannot be measured — the race results template is invalid, or the documented
   background element is absent — say so **and why**, rather than omitting or guessing (FR-027).

Order matters: the value is stored before the contrast is measured, so an unmeasurable contrast
never costs the manager their input.

---

## Reporting

### `/images config view` — tier M

No parameters. Prints the whole configuration (FR-030):

- Module enabled state, and the converter's presence (FR-007).
- Template directory and all fifteen filenames, each with its validity and — when invalid — the
  reason and the **full path searched** (FR-028).
- All seven asset directories with validity (FR-029).
- All four preferences.
- The eight aspects, each in one of three states: ✅ enabled, ❌ disabled, ⚠️ enabled but invalid
  (FR-031), with the specific blocking template named, never the group (FR-032).
- **The depth to which templates were checked** (FR-028b) — in this increment, Layer 1.

### `/season review` — modified, tier M

Gains an image section (FR-033) built from the same `AspectStatus` list the view command renders, so
the two cannot drift. When the module is disabled, reports it as disabled and omits the detail
(FR-034). Slots into the existing `**Modules**` block in `src/cogs/season_cog.py`, which already
lists weather, signup, results and attendance.

---

## Diagnostic

### `/images test` — tier M

| Parameter | Type | Required | Choices |
|-----------|------|----------|---------|
| `kind` | Choice | yes | `calendar`, `lineup`, `results`, `standings`, `attendance`, `rsvp`, `weather-p1`, `weather-p2`, `weather-p3`, `weather-mystery`, `verdicts` |

Eleven choices — the eight aspects with weather split into its four phases.

**Flow**:

1. `defer(ephemeral=True)` immediately — a multi-variant render will not meet the 3-second
   acknowledgement rule (research R2).
2. Reject at once if the converter is absent, naming it as the reason and attempting no render
   (FR-009, US7 scenario 4).
3. Render from sample data only. **No live season, division, round, team or driver data may be
   read** (FR-036) — the command must work on a server with no season at all (SC-005).
4. Return every variant the kind covers: two for `results`, `standings`, `weather-p2` and
   `weather-p3`; one otherwise (FR-040).
5. On success, `followup.send` with the PNG(s) attached and every notice listed alongside (FR-038).
6. On a problem, no image and the specific reason (FR-039).

Requires the module enabled but **no aspect toggled on** — it is the diagnostic a league runs
before committing to an aspect (spec Assumptions).
