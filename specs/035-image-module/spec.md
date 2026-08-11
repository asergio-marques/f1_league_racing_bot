# Feature Specification: Image Module — Initial Setup & Configuration

**Feature Branch**: `035-image-module`
**Created**: 2026-08-10
**Status**: Draft
**Input**: User description of the Image module's basic setup and configuration surface.

## Scope of This Increment

This increment delivers the module's **configuration surface and its diagnostic**, not its live
output. Specifically:

- **In**: enabling and disabling the module; every configuration command; validity reporting;
  the `season review` addendum; `/images test`, and therefore the template-filling and
  rasterisation engine that command requires.
- **Out**: the wiring of the eight output aspects into the source modules that post them. The
  toggles specified here are **stored but inert** — setting one changes what the configuration
  view reports and nothing else. Until a later increment wires each source module, every aspect
  continues to post exactly the text it posts today.

FR-018 through FR-020 (fallback to text, logging of the fallback, preservation of the text path)
are therefore specified but not exercisable in this increment. They are stated now because they
constrain the wiring increment that follows, and because `/images test` must already distinguish a
render that failed from one that degraded.

## The Validity Contract *(keynote)*

**What makes a template valid is deliberately not settled in this specification.** It will be
defined incrementally, one image type at a time, as each type's field catalogue is written in a
later session. This increment's job is to build the surface those future definitions plug into,
and to be honest about how shallow today's check is.

Validity is therefore structured as **ordered, independently named layers**, cheapest first,
governed by Constitution Principle XIV.9:

| Layer | Check | Status in this increment |
|-------|-------|--------------------------|
| 1 — Resolution | File resolves inside the configured directory, parses as well-formed SVG, declares a root width and height | **Implemented.** Applies to all fifteen templates. |
| 2 — Catalogue conformance | Every field the image type requires is declared in the template by `@id` | **Not yet defined.** Requires the type's field catalogue. |
| 3 — Bounds declaration | Fields receiving unbounded text declare `inline-size` or `shape-inside` | **Not yet defined.** |
| 4 — Trial render | The template fills and rasterises against sample data | **Not yet defined.** |

Four properties MUST hold as layers are added, so that later sessions extend this feature rather
than rewrite it:

1. **Stable surface** — adding a layer changes neither the configuration commands, the three
   reported states, nor the structure of a validity report. Only the set of reasons grows.
2. **Specific attribution** — every layer names the individual template at fault, never the group.
3. **Declared depth** — a validity report states which layers were applied.
4. **No silent pass** — a template checked only to Layer 1 is reported as checked to Layer 1, not
   as fully valid.

This is why FR-026's contrast measurement is specified against a documented element rather than
against "the template's background": Layer 1 cannot tell the module which element that is.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Enable and Disable the Image Module (Priority: P1)

A server administrator turns the Image module on for their server. Nothing about the bot's
output changes yet — every aspect still posts as text — but the module's configuration surface
becomes reachable and the bot begins reporting whether its prerequisites are satisfied. Turning
the module off returns the server to exactly the behaviour it had before.

**Why this priority**: No other part of this feature is reachable until the module can be
enabled and disabled. It is the smallest slice that delivers value on its own: an administrator
can confirm the machine running the bot is capable of image generation at all.

**Independent Test**: Enable the module on a fresh server, confirm `/images config view` becomes
available and reports the prerequisite status, then disable it and confirm every image command
is rejected with a clear error.

**Acceptance Scenarios**:

1. **Given** a server that has never enabled the Image module, **When** an administrator runs
   `/module enable images`, **Then** the module is enabled, an image configuration record is
   created with every default value, and a confirmation is posted to the server's log channel.
2. **Given** the Image module is disabled, **When** any member runs any `/images …` command,
   **Then** the command is rejected with an error naming the module and how to enable it, and no
   configuration is created or read.
3. **Given** the Image module is enabled, **When** an administrator runs `/module disable images`,
   **Then** the module is disabled, a notice is posted to the log channel, and every aspect
   reverts to text output.
4. **Given** a server with a fully customised image configuration, **When** an administrator
   disables and then re-enables the module, **Then** every configured value — directories,
   filenames, preferences, colour and toggles — is exactly as it was before the disable.
5. **Given** the machine running the bot does not carry the SVG-to-PNG converter, **When** an
   administrator enables the module, **Then** the module is enabled but the missing converter is
   reported as a fatal configuration problem, and no image generation is attempted while it stands.

---

### User Story 2 — Locate the Templates (Priority: P2)

A server administrator points the bot at the folder holding the league's SVG templates and, where
the league has renamed one, at the individual file. Fifteen templates back the eleven kinds of
image the module can produce, and each is nameable independently of the rest.

**Why this priority**: Without a resolvable template a validity report has nothing to report on and
a render has nothing to fill. It is the second-smallest slice and everything downstream depends on it.

**Independent Test**: Change the template directory to a folder that does not exist and confirm every
template reports invalid; change it back and confirm they all report valid; rename one template and
confirm only that one reports invalid.

**Acceptance Scenarios**:

1. **Given** the Image module is enabled and no template directory has been set, **When** an
   administrator runs `/images config view`, **Then** the template directory reads `resources/templates`
   and every template filename reads its packaged default.
2. **Given** the Image module is enabled, **When** an administrator sets the template directory to a
   path relative to the project root, **Then** the value is stored and every template's validity is
   re-evaluated against the new directory.
3. **Given** a template filename is set to a file that does not exist in the template directory,
   **When** the administrator views the configuration, **Then** that template alone is reported
   invalid, with the reason and the full path that was searched.
4. **Given** a template filename is set to a file that exists but is not well-formed SVG, **When**
   the administrator views the configuration, **Then** that template is reported invalid with a
   reason distinguishing it from a missing file.

---

### User Story 3 — Review Configuration and Validity (Priority: P3)

A league manager asks the bot to show the whole image configuration and whether it holds together,
and a server administrator sees the same summary appended to the season review they already run
before approving a season.

**Why this priority**: The value of the configuration surface is that a league can tell, before a
race weekend, whether the graphics will actually be produced. This story is what makes the
preceding two observable and is the last one required for a usable increment.

**Independent Test**: Misconfigure one template of each grouped aspect (weather, results,
standings) and confirm the review names the exact template at fault rather than the group.

**Acceptance Scenarios**:

1. **Given** the Image module is enabled and fully configured, **When** a league manager runs
   `/images config view`, **Then** every configuration value is listed with its current setting and
   a validity status.
1a. **Given** only Layer 1 validity is implemented, **When** a league manager views the
   configuration, **Then** the report states the depth to which templates were checked, so that a
   template resolving and parsing is not mistaken for one known to carry every field it needs.
2. **Given** an aspect is toggled on but one of its templates is invalid, **When** the configuration
   is viewed, **Then** that aspect shows the "enabled but invalid" state rather than "enabled".
3. **Given** the weather aspect is toggled on and the phase 3 sprint template is missing, **When**
   the configuration is viewed, **Then** the report names phase 3, identifies it as the sprint
   variant, and leaves phases 1, 2, the non-sprint variants and the mystery notice reported as valid.
4. **Given** the results aspect is toggled on and only the qualifying template is invalid, **When**
   the configuration is viewed, **Then** the report names the qualifying template specifically and
   reports the race template as valid.
5. **Given** the standings aspect is toggled on and only the constructors template is invalid,
   **When** the configuration is viewed, **Then** the report names the constructors template
   specifically and reports the drivers template as valid.
6. **Given** a season is pending approval and the Image module is enabled, **When** an administrator
   runs `/season review`, **Then** the review carries an image section showing the module's enabled
   status and the same per-aspect and per-template validity summary.
7. **Given** the Image module is disabled, **When** an administrator runs `/season review`, **Then**
   the review reports the module as disabled and omits the configuration detail.

---

### User Story 4 — Choose Which Aspects Are Drawn (Priority: P4)

A league manager decides, aspect by aspect, whether the bot posts a graphic or the text it has
always posted. A league can adopt graphics for its standings while keeping its weather forecasts
as text.

**In this increment the choice is recorded, not acted on.** The toggle is stored and reported;
what each source module posts is unchanged until the wiring increment that follows. A league
uses `/images test` (User Story 7) to see what a toggle will eventually produce.

**Why this priority**: This is the league's actual choice, but it is inert until the templates are
locatable and their validity is visible — hence its position behind the first three stories.

**Independent Test**: Toggle each of the eight aspects on and off in turn and confirm the stored
state changes and is reflected in the configuration view, independently of the others.

**Acceptance Scenarios**:

1. **Given** a freshly enabled Image module, **When** a league manager views the configuration,
   **Then** all eight aspects read as disabled.
2. **Given** an aspect is disabled, **When** a league manager toggles it, **Then** it becomes enabled
   and the change is confirmed ephemerally; running the command again returns it to disabled.
3. **Given** an aspect whose source module is itself disabled is toggled on, **When** the
   configuration is viewed, **Then** the aspect is reported as enabled but invalid, with the
   disabled source module named as the reason.
4. **Given** any aspect is toggled on in this increment, **When** that aspect's source module posts,
   **Then** it posts exactly the text it posted before the toggle was set — the toggle records
   intent and changes no output until the wiring increment.
5. **Given** any aspect is toggled on *after* the wiring increment, **When** an error occurs at any
   step of generating or posting that aspect's image, **Then** the bot posts the information in its
   previous textual form instead, and the failure is recorded to the server's log channel.

---

### User Story 5 — Locate the Assets (Priority: P5)

A server administrator points the bot at the folders holding the images the templates draw into
their slots: circuits, team badges, nationality flags, driver portraits, position-change markers,
weather icons and tyre compounds.

**Why this priority**: Assets are what let a league brand its graphics, but a template renders
without them long enough for the configuration surface to be judged complete.

**Independent Test**: Change each of the seven asset directories in turn and confirm the stored
value and its validity status change independently of the other six.

**Acceptance Scenarios**:

1. **Given** a freshly enabled Image module, **When** a league manager views the configuration,
   **Then** each of the seven asset directories reads its packaged default under `resources/`.
2. **Given** an asset directory is set to a path that does not exist relative to the project root,
   **When** the configuration is viewed, **Then** that directory alone is reported invalid with the
   full path that was searched.

---

### User Story 6 — Set Presentation Preferences (Priority: P6)

A league manager sets the time zone, clock format and date format the graphics display, and the
colour that distinguishes the fastest lap of a race.

**Why this priority**: These change how a graphic reads rather than whether it is produced. A
league can run on the defaults indefinitely.

**Independent Test**: Set each preference in turn and confirm the stored value; supply a malformed
colour and confirm it is rejected; supply a low-contrast colour and confirm it is accepted with a
warning.

**Acceptance Scenarios**:

1. **Given** a freshly enabled Image module, **When** a league manager views the configuration,
   **Then** the time zone reads UTC, the clock format reads 24-hour, the date format reads the
   packaged default, and the fastest-lap colour reads `#A020F0`.
2. **Given** a league manager sets the fastest-lap colour to a value that is not a `#` followed by
   exactly six hexadecimal digits, **When** the command is run, **Then** it is rejected with an
   error stating the required form, and the stored colour is unchanged.
3. **Given** a league manager sets the fastest-lap colour to a well-formed value whose contrast
   against the background the configured race results template draws behind that field is at least
   4.5:1, **When** the command is run, **Then** the value is stored and the measured contrast ratio
   is reported.
4. **Given** the contrast falls below 4.5:1, **When** the command is run, **Then** the value is
   stored all the same, the measured ratio is reported, and a legibility warning is issued.
5. **Given** a league manager sets the date format, **When** the available formats are offered,
   **Then** at least one of them carries the day of the week.

---

### User Story 7 — Test a Render (Priority: P7)

A league manager asks the bot to draw one kind of image from known sample data, so the league can
see what its templates and assets produce before a race weekend depends on them.

**Why this priority**: It is a diagnostic. Every preceding story is judgeable from the configuration
view alone; this one turns a validity report into a picture.

**Independent Test**: Run the test command for each of the eleven kinds and confirm each returns
either an image or a clear account of why it could not be produced.

**Acceptance Scenarios**:

1. **Given** the Image module is enabled and a kind's templates are valid, **When** a league manager
   runs `/images test <kind>`, **Then** the bot returns the rendered image built from that kind's
   sample data, visible only to the invoking member.
2. **Given** a render completes but degrades — a font was substituted, or a text field was cut to
   the room it was given — **When** the test returns, **Then** the image is returned and each
   degradation is listed alongside it.
3. **Given** a render cannot complete, **When** the test is run, **Then** no image is returned and
   the specific reason is stated.
4. **Given** the SVG-to-PNG converter is absent from the machine, **When** the test is run, **Then**
   it is rejected immediately with that as the stated reason, and no render is attempted.
5. **Given** the kind requested is one the module draws from more than one template — the phase 2 or
   phase 3 weather forecast, whose sprint and non-sprint variants differ — **When** the test is run,
   **Then** both variants are returned.

---

### Edge Cases

- **Template directory absent entirely**: every template reports invalid against the same directory
  path, and the report says the directory itself is missing rather than repeating fifteen
  file-not-found lines.
- **Template path escaping the project root**: a configured directory that resolves outside the
  project root is rejected at the point of configuration, not at render time.
- **Template valid at configuration, gone at render time**: the render fails and the aspect falls
  back to its textual form; the configuration view reflects the new invalid state on next reading.
- **A grouped aspect partially valid**: the weather aspect has six templates, results two and
  standings two. An aspect is only fully valid when every template backing it is.
- **Aspect toggled on while the module is disabled**: the toggle command is rejected outright, as
  every image command is when the module is disabled.
- **Aspect toggled on while its source module is disabled**: accepted and stored, reported as
  enabled but invalid, and produces nothing until the source module is enabled.
- **Fastest-lap contrast reported against an invalid race results template**: the colour is stored
  and its form validated, but the contrast cannot be measured; this is reported rather than guessed.
- **Time zone with a daylight-saving transition inside a season**: times are displayed in the zone's
  offset in force on the date being displayed, not a single offset fixed at configuration time.
- **Module disabled and re-enabled**: see the clarification on configuration retention below.
- **Two members configuring at once**: the last write wins; the configuration view is read fresh on
  each invocation rather than cached.

## Requirements *(mandatory)*

### Functional Requirements

#### Module lifecycle

- **FR-001**: The Image module MUST be disabled by default for all servers.
- **FR-002**: `images` MUST be an accepted value of the `/module enable` and `/module disable`
  commands, alongside the existing modules.
- **FR-003**: Enabling the Image module MUST atomically create an image configuration record for
  the server with every default value if one does not exist, set the module-enabled flag, and post a
  confirmation to the server's log channel.
- **FR-004**: Disabling the Image module MUST atomically clear the module-enabled flag, post a
  notice to the server's log channel, and return every aspect to its textual output path.
- **FR-004a**: Disabling the Image module MUST NOT clear any configuration value. The template
  directory, the fifteen template filenames, the seven asset directories, the time zone, the clock
  and date formats, the fastest-lap colour and the eight aspect toggles MUST all survive a disable,
  and re-enabling MUST restore the server to the configuration it had. This is the exception
  granted by Constitution Principle X.6 for configuration that cannot go stale: none of these
  values names a Discord channel, role, message or scheduled job, so none can become a stale
  binding while the module is off.
- **FR-004b**: Because nothing is cleared, no `--preserve-config` flag is offered on
  `/module disable images`.
- **FR-005**: Every `/images …` command MUST check the module-enabled flag before executing and,
  when the module is disabled, MUST reject the invocation with an error naming the module and the
  command that enables it.
- **FR-006**: The Image module MUST be enablable independently of any other optional module. An
  aspect whose source module is disabled MUST NOT produce an image.

#### Prerequisites

- **FR-007**: The bot MUST detect at enable time, at configuration-view time and at season review
  whether the SVG-to-PNG converter binary is present on the machine running it.
- **FR-008**: The absence of that binary MUST be reported as a fatal, module-wide problem naming the
  binary and stating that it is not installed by the bot's package dependencies.
- **FR-009**: While that binary is absent, the bot MUST NOT attempt any image generation, and every
  enabled aspect MUST fall back to its textual output path.

#### Template location

- **FR-010**: A server administrator MUST be able to set the directory searched for template files.
  The value MUST be interpreted as a path relative to the project root and MUST default to
  `resources/templates`.
- **FR-011**: A configured directory that resolves outside the project root MUST be rejected at the
  point of configuration with a clear error, and the stored value left unchanged.
- **FR-012**: A server administrator MUST be able to set the filename of each of the following
  fifteen templates independently, each defaulting to the name given:

  | Command | Template | Default filename |
  |---------|----------|------------------|
  | `/images template calendar` | Division calendar | `calendar_template.svg` |
  | `/images template lineup` | Division lineup | `lineup_template.svg` |
  | `/images template results-qualifying` | Qualifying session results | `results_qualifying_template.svg` |
  | `/images template results-race` | Race session results | `results_race_template.svg` |
  | `/images template standings-drivers` | Driver standings | `standings_drivers_template.svg` |
  | `/images template standings-constructors` | Constructor standings | `standings_constructors_template.svg` |
  | `/images template attendance` | Attendance sheet | `attendance_template.svg` |
  | `/images template rsvp` | Round check-in call | `rsvp_template.svg` |
  | `/images template weather-p1` | Weather phase 1 | `weather_p1_template.svg` |
  | `/images template weather-p2` | Weather phase 2, non-sprint | `weather_p2_template.svg` |
  | `/images template weather-p3` | Weather phase 3, non-sprint | `weather_p3_template.svg` |
  | `/images template weather-p2-sprint` | Weather phase 2, sprint | `weather_p2_sprint_template.svg` |
  | `/images template weather-p3-sprint` | Weather phase 3, sprint | `weather_p3_sprint_template.svg` |
  | `/images template weather-mystery` | Mystery round notice | `weather_mystery_template.svg` |
  | `/images template verdicts` | Verdicts | `verdicts_template.svg` |

- **FR-012a**: These fifteen commands live under `/images template` rather than `/images config`
  because Discord permits at most 25 subcommands per group and forbids a third nesting level;
  `/images config` would otherwise carry 29. The requirement is that each of the fifteen is
  settable independently, which this satisfies; the grouping is a platform constraint, not a
  design choice.

- **FR-013**: A qualifying session and a race session MUST be drawn from separate templates. A
  sprint session and a feature session of the same kind MUST share a template, distinguished by the
  text placed on the session name field alone.
- **FR-014**: The driver standings and the constructor standings MUST be drawn from separate
  templates. The attendance sheet and the check-in call MUST be drawn from separate templates.
- **FR-015**: Each weather phase MUST be drawn from its own template, and phases 2 and 3 MUST each
  have a separate sprint and non-sprint variant, the sprint format holding four sessions where every
  other format holds two.

#### Asset location

- **FR-016**: A server administrator MUST be able to set each of the following seven asset
  directories independently. Each MUST be interpreted as a path relative to the project root, MUST
  be subject to the same escape rejection as FR-011, and MUST default to the directory given:

  | Command | Assets | Default directory |
  |---------|--------|-------------------|
  | `track-image-directory` | Circuit images | `resources/tracks` |
  | `team-image-directory` | Team logos, badges and cars | `resources/teams` |
  | `flag-directory` | Driver nationality flags | `resources/flags` |
  | `driver-image-directory` | Driver portraits | `resources/drivers` |
  | `marker-directory` | Standings position-change markers | `resources/markers` |
  | `weather-icon-directory` | Weather condition icons | `resources/weather` |
  | `tyre-directory` | Tyre compound icons | `resources/tyres` |

#### Output aspect toggles

- **FR-017**: A league manager MUST be able to toggle each of the following eight aspects between
  image output and the bot's existing textual output. All eight MUST be disabled by default:

  | Aspect | When enabled | When disabled |
  |--------|--------------|---------------|
  | `calendar` | Calendar posted as a generated image | Posted as text |
  | `lineup` | Lineup posted as a generated image | Posted as text |
  | `results` | Session results posted as a generated image | Posted as text |
  | `standings` | Standings posted as a generated image | Posted as text |
  | `attendance` | Attendance table posted as a generated image | Posted as text |
  | `rsvp` | Check-in call carries a generated image | Posted as an embed alone |
  | `weather` | Phases 1, 2, 3 and the mystery notice posted as generated images | Posted as text |
  | `verdicts` | Verdicts posted as a generated image | Posted as text |

- **FR-017a**: In this increment the toggles are **stored but inert**. Setting one MUST change the
  stored state and what the configuration view and season review report, and MUST NOT change what
  any source module posts. FR-018 through FR-020 govern the wiring increment that follows.
- **FR-018**: An error at any step of generating or posting an image for an enabled aspect MUST
  cause the bot to post that information in its previous textual form instead. The bot MUST NOT post
  a partial image, a placeholder, or nothing at all.
- **FR-019**: Every such fallback MUST be recorded to the server's log channel with the aspect and
  the reason.
- **FR-020**: Enabling an aspect MUST NOT alter, remove or degrade the textual output path for that
  aspect. The textual path MUST remain functional and MUST be the output whenever the module is
  disabled, the aspect is disabled, or a render fails.

#### Presentation preferences

- **FR-021**: A league manager MUST be able to set the time zone in which times are displayed on
  images. Times MUST be rendered in the offset that zone carries on the date being displayed.
- **FR-022**: A league manager MUST be able to select between a 12-hour and a 24-hour clock format.
- **FR-023**: A league manager MUST be able to select a date format from a list of common formats,
  at least one of which carries the day of the week.
- **FR-024**: A league manager MUST be able to set the colour distinguishing the fastest lap of a
  race, defaulting to `#A020F0`.
- **FR-025**: A fastest-lap colour MUST be rejected with a clear error unless it is a `#` followed by
  exactly six hexadecimal digits, of either case.
- **FR-026**: On a well-formed fastest-lap colour, the bot MUST report the contrast ratio of that
  colour against the background the configured race results template draws behind that field, and
  MUST issue a legibility warning where the ratio falls below 4.5:1. The value MUST be stored
  regardless; the warning does not block it.
- **FR-026a**: The background element MUST be located by a single documented `@id` in the race
  results template. Layer 1 validity cannot establish that this element exists, so its absence MUST
  be handled as FR-027 requires rather than treated as a template validity failure.
- **FR-027**: Where the contrast cannot be measured — because the race results template is invalid,
  or because the documented background element is absent from it — the bot MUST store the colour
  and say the contrast could not be measured, and why, rather than reporting a guessed or omitted
  ratio.

#### Validity reporting

- **FR-028**: The bot MUST report each configured template as valid or invalid against the layers
  currently implemented (see The Validity Contract), and MUST state the reason and the full path
  searched when invalid.
- **FR-028a**: Validity MUST be computed by an ordered set of independently named layers. Adding a
  layer MUST NOT require any change to the configuration commands, the three reported states, or
  the structure of a validity report.
- **FR-028b**: A validity report MUST state the depth to which each template was checked. A
  template that has passed only Layer 1 MUST NOT be presented as though it had passed a deeper
  check.
- **FR-028c**: In this increment only Layer 1 is implemented: the file resolves inside the
  configured directory, parses as well-formed SVG, and declares a root width and height. A
  template failing any of these three MUST be reported invalid with the failure distinguished from
  the other two.
- **FR-029**: The bot MUST report each configured asset directory as valid or invalid on the same
  terms.
- **FR-030**: A league manager MUST be able to view the entire image configuration and its validity
  in one command.
- **FR-031**: Each of the eight aspects MUST be reported in exactly one of three states: **enabled**
  (a checkmark), **disabled** (a cross), or **enabled but invalid** (a warning sign). An aspect is
  enabled but invalid when it is toggled on and any template backing it is invalid, or its source
  module is disabled, or the SVG-to-PNG converter is absent.
- **FR-032**: An "enabled but invalid" report MUST name the specific template at fault, not the
  group. For weather it MUST identify the phase, whether the sprint or non-sprint variant, and
  whether it is the mystery notice; for results, whether the qualifying or race template; for
  standings, whether the drivers or constructors template.
- **FR-033**: The `/season review` command MUST be augmented with a section showing the Image
  module's enabled status and, when enabled, the same configuration and validity summary.
- **FR-034**: When the Image module is disabled, `/season review` MUST report it as disabled and
  omit the configuration detail.

#### Test rendering

- **FR-035**: A league manager MUST be able to render one kind of image from sample data, choosing
  from: `calendar`, `lineup`, `results`, `standings`, `attendance`, `rsvp`, `weather-p1`,
  `weather-p2`, `weather-p3`, `weather-mystery`, `verdicts`.
- **FR-036**: A test render MUST use sample data defined for that kind, and MUST NOT read, write or
  depend on any live season, division, round, team or driver data.
- **FR-037**: A test render MUST return its output visible only to the invoking member.
- **FR-038**: A test render that completes with non-fatal degradations MUST return the image and
  list every degradation alongside it. A degradation is a substituted font, a wrapped field reduced
  to its size floor and cut, or a single-line field cut to the room it was given.
- **FR-039**: A test render that cannot complete MUST return no image and MUST state the specific
  reason.
- **FR-040**: A test render of a kind backed by more than one template — `results`, `standings`,
  `weather-p2` and `weather-p3` — MUST return every variant that kind covers.

#### Permissions

- **FR-041**: Setting the template directory, any template filename, or any asset directory MUST be
  restricted to server administrators.
- **FR-042**: Toggling an aspect, setting a presentation preference, viewing the configuration and
  running a test render MUST be available to league managers.
- **FR-043**: Every image command MUST be subject to the existing interaction-role and
  interaction-channel gates, and MUST reject an unauthorised invocation with a clear permission error.
- **FR-044**: Every configuration command response MUST be visible only to the invoking member.

### Key Entities

- **ImageConfig**: One record per server. Holds the module-enabled flag, the template directory, the
  fifteen template filenames, the seven asset directories, the time zone, the clock format, the date
  format and the fastest-lap colour. Created with defaults when the module is first enabled.
- **ImageAspectToggle**: One record per server per aspect, for the eight aspects. Holds whether that
  aspect is drawn as an image. All disabled by default.
- **RenderNotice**: An append-only record of a non-fatal degradation raised by a render — the aspect
  or test kind, the field concerned, the kind of degradation, and when it occurred.

## Dependencies

- **SVG manipulation** and **text measurement** are provided by Python packages and MUST be declared
  as project dependencies.
- **SVG-to-PNG conversion** is provided by a command-line binary (Inkscape) that the machine running
  the bot must carry. No package declaration installs it. Its absence is fatal to the module and is
  governed by FR-007 through FR-009.

## Assumptions

- **Configuration is server-wide, not per-division.** No command in this feature takes a division
  parameter, so a league that runs several divisions gets the same graphics choices across all of
  them.
- **"Server administrator" and "league manager" map to the two existing access tiers.** League
  manager is the season/config authority tier (trusted admin); server administrator is the higher
  tier already referenced by the module system for enabling and disabling modules.
- **Validity is evaluated on demand**, when a configuration is viewed, a season is reviewed, or a
  render is attempted — not cached at the moment a value is set. A template that disappears after
  being configured is therefore reported invalid the next time it is read.
- **A time zone is chosen from the standard IANA zone names**, offered through autocomplete rather
  than a fixed choice list, the number of zones exceeding what a Discord choice list holds.
- **The default time zone is UTC**, the bot already scheduling and storing in UTC.
- **`/images test` requires the module to be enabled** but does not require any aspect to be toggled
  on — it is a diagnostic run before a league commits to an aspect.
- **Provisioning the template and asset files is out of scope.** The defaults under `resources/`
  name where the bot looks; how the files get there is the operator's concern. A path that resolves
  to nothing is reported invalid with the full path searched, which is what an operator needs in
  order to fix it. The module bundles, packages and installs nothing.
- **The eight toggles and the eleven test kinds are deliberately different lists.** The weather
  toggle covers four test kinds; the test list separates them so a league can inspect one phase at a
  time.
- **The layer boundary is where later sessions plug in.** Layers 2 to 4 are named here so that the
  reporting surface, the three states and the report structure are settled before their definitions
  arrive. A later session defines a layer's checks; it does not redesign the surface.
- **A toggle set during this increment survives into the wiring increment** and takes effect the
  moment its source module is wired, without the league having to set it again.

## Clarifications

Three questions were put to the author on 2026-08-10 and answered:

1. **Scope of this increment** — the configuration surface and `/images test` only; the eight
   toggles are stored but inert until a later increment wires the source modules. See *Scope of
   This Increment* above and FR-017a.
2. **Definition of template validity** — deliberately left open, to be defined incrementally as
   each image type's field catalogue is written in later sessions, and wired into the surface
   built here. Governed by Constitution Principle XIV.9 and instantiated in *The Validity
   Contract* above and FR-028a through FR-028c.
3. **Configuration retention across a disable** — retained. Granted as an explicit exception to
   Constitution Principle X.6 for configuration that cannot go stale. See FR-004a and FR-004b.

## Out of Scope for This Increment

- Wiring the eight output aspects into the source modules that post them. The toggles are stored
  and reported here; they change no posted output until a later increment.
- Validity layers beyond Layer 1 — catalogue conformance, bounds declaration and trial render —
  each of which arrives with the field catalogue of the image type it checks.
- The field catalogue naming every addressable element of every template, and the per-template
  documentation a league needs to author its own.
- Uploading templates or assets through Discord. Both are placed on the filesystem by whoever
  operates the bot.
- Per-division or per-season overrides of any configuration value.
- Any change to the content, wording or layout of the existing textual output paths.
- Localisation of the text drawn onto images.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A server administrator can take a server from no image configuration to a fully valid
  one — module enabled, all fifteen templates and seven asset directories resolving — in under 10
  minutes, without editing any file outside the resource directories.
- **SC-002**: Every one of the fifteen templates and seven asset directories can be relocated
  independently; changing one never alters the resolution of another.
- **SC-003**: For any single misconfigured template, the validity report names that template
  specifically — including which weather phase and variant, or which of a results or standings pair
  — in 100% of cases.
- **SC-004**: With every aspect toggled on, in any configuration state, the bot's posted output is
  indistinguishable from the output of a server that never enabled the module: no aspect is lost, no
  partial image is posted, and no post is skipped.
- **SC-005**: A league manager can see what any of the eleven kinds of image will look like, from
  sample data, without a season existing on the server.
- **SC-006**: A league that runs on the defaults, having placed the packaged resources where the bot
  expects them, needs to issue no configuration command other than the module enable and the aspect
  toggles it wants.
- **SC-007**: On a machine without the SVG-to-PNG converter, the reason is stated at season review,
  at configuration view and at test render, and no image generation is attempted.
- **SC-008**: Disabling and re-enabling the module leaves all thirty-five configuration values
  byte-identical, so an administrator can toggle the module off to diagnose a problem at no cost.
- **SC-009**: A validity report never overstates what it checked: for every template it names the
  depth reached, and a later session can add a deeper layer without altering any command, any of
  the three reported states, or the structure of the report.
