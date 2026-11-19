# Image — why the code is shaped as it is

This file says how the image module's code is laid out and why: its tables, its services and the
packages they are grouped in, its template model, its changes on the queue, its one timed job, what
it hands back for others to post, how it meets core and the modules that ask it to draw, how it
fails, and the constraints a later reader might tune away. Every path is under `src/leaguebot/`
unless it says otherwise.

It cites the rules it rests on rather than restating them, and is corrected at close-out where it
and the code part (CLAUDE.md, the `docs/design/` row): what binds every module is
`architecture.md`'s, core's side of a seam is `core.md`'s, and what a league sees is the image
module specification's (`docs/wip-specs/image_module_specification.md`), each cited by its section
heading.

---

## The database

Answers to `architecture.md`, "The database".

### Image's tables

Image owns four tables (`TABLE_OWNER` in `tests/repository/test_architecture_rules.py`), all
declared in `framework/db/migrations/001_baseline.sql`.

| Table | One row is | Held to it by |
|---|---|---|
| `image_config` | the league's whole configuration: the switch (`module_enabled`), the template directory and the sixteen template filenames, the eight asset directories, the time zone and the clock and date formats, the fastest-lap colour, the per-tier colour switch, and the four portrait settings | `CHECK (id = 1)`; every value column `NOT NULL`, its default the value the bot ships |
| `image_aspect_toggles` | one of the nine aspects a league switches on and off, and whether it is on | the aspect as primary key |
| `image_tier_colour` | one colour of one slot of a tier, keyed by the division's normalised name so that a tier's palette survives the season rollover that replaces its `divisions` row (`image_config_service`, `set_tier_colours_on`) | the primary key `(division_slug, slot)` |
| `driver_portraits` | one portrait the bot obtained: the account it is of, and its key (`avatar_key`, `CLAIMED_KEY` while the portrait is being fetched, else made by `portrait_key` of the avatar it was made from and the shape it was wrapped at) | the account as primary key |

The toggles are kept by aspect, not by template: the sixteen templates are a detail of what each
aspect draws (`image_constants`'s module docstring; image specification, "Configuration").

None of image's columns sits on core's tables, and none of its tables holds a link to one of core's
that would stop a delete: a tier's colours are keyed by the division's slug, and a portrait by the
account, so a season's end or a division's deletion is never refused for them. A column a command
may write is in the allow-lists of `image_config_service` (`SETTABLE_COLUMNS` for the text values,
and the flags apart), which a write outside raises on (`UnknownConfigField`), so that a caller
cannot reach `module_enabled` or `id` through the generic setter (the comment above
`SETTABLE_COLUMNS`).

### The switch, and what the switch-off keeps

The module's switch is `image_config.module_enabled`, read through `images_enabled_on`
(`image/services/config/module_switch_settings.py`), which the builder signs up with `ModuleService`
under the name `images` (`architecture.md`, "How modules and core fit together"). A league that has
never switched it on has no row and reads as off.

The switch-off clears the flag and nothing else: no configuration row is deleted and no toggle
reset. That is Principle X, rule 6 as every module keeps it: a disable keeps the whole of the
configuration, the portrait settings and the tier colours included, and re-enabling finds it as it
stood. The daily portrait job the configuration arms is not configuration and is not kept armed: the
switch-off removes it and the switch-on arms it again ("How a change is carried out", below).

The toggle refuses `MODULE_OFF` only where it switches an output on (`image_settings`), the
exception to the module-off rule that `image_module_specification.md`, "Configuration" makes; the
cog's gate on the module is likewise made only then.

### The portrait register

`driver_portraits` is how the bot tells a portrait it obtained from one the league put in its own
driver directory: a file with no row is the league's, and is never fetched over or removed. The
register is written around the file it records, in the order and for the reason
`architecture.md` gives ("How a change is carried out"): the claim is written with `CLAIMED_KEY`, a
key no avatar can match. `driver_portrait_service`'s module docstring holds it, pinned by
`test_a_portrait_cut_off_once_written_stays_the_bot_s`.

The register's two writes (`_record`, `_disown`) commit on their own, off the change queue: they are
the register `architecture.md` names among the off-queue exceptions ("How a change is carried
out"), for the reason given there, and the save check lists them for good (`SAVES_ALLOWED`).

### The fingerprint

A season's fingerprint asks each module for its own configuration through core's
`fingerprint_areas_read` hook (`core.md`). Image hands one area, `fingerprint_areas`
(`image/services/config/fingerprint_areas.py`): its configuration with the switch among it, its
aspect toggles and its tier colours, read whatever the switch, since a change to what the module is
set to is a change to what a review reported. The tier colours are in it so that a colour changed
between a review and its approval reads as the review changed. The artwork on disk is not read here:
the fingerprint asks the drawing for it (`ImageDrawing.artwork_signature`, a stated exception, named
under "Errors and failures").

### What is deleted, and by which hook

Image deletes only portraits. A season's end, its cancellation and the abort of a season being set
up hand core's `drivers_deleted` hook the accounts no driver is drawn under any longer, and image's
`discard_portraits` removes each one's file and then its row (`image/wiring.py`,
`sign_up_to_drivers_deleted`); `/driver reassign` asks the same of the drawing
(`ImageDrawing.discard_portraits`). Nothing else image holds is deleted by any hook.

---

## How the code is laid out

Answers to `architecture.md`, "How the code is laid out".

### Services, grouped by what they serve

A module's own services stay in its `services/` folder unless its design file groups them
(`architecture.md`, "How the code is laid out"). This file groups image's into six packages under
`image/services/`, by the part of the module each serves.

| Package | Holds |
|---|---|
| `config/` | the configuration's reads and writes (`image_config_service`), its settings on the queue (`image_settings`) and what a setting says beside its value once saved (`image_setting_advice`), the module's switch (`module_switch_settings`), and the fingerprint's area (`fingerprint_areas`) |
| `validity/` | the layered template check (`image_validity_service`) and the roll-up of its reports into each aspect's state (`aspect_status`) |
| `render/` | the render (`image_render_service`) and the one object every other part of the bot asks to draw (`image_drawing`) |
| `aspects/` | for each of the nine aspects, its resolution (`image_<aspect>_service`) and, where it has one, its post module (`image_<aspect>_post`): the drawing for a caller, or for the lineup, which `ImageDrawing` draws, the resolution of its seats |
| `preview/` | the `/images test` previews: their resolution (`image_preview_service`), the outcomes they invent (`image_preview_data`), the protocol they read other modules' data through (`preview_sources`), the body every preview shares (`preview_runner`) and the `/images config view` report (`configuration_report`) |
| `portraits/` | the driver portraits (`driver_portrait_service`) |

`aspect_status` is apart from `image_validity_service` so that the dependency runs one way: it reads
the reports the validity service makes and never makes one, and the validity service reaches for it
only when called (`aspect_status`'s module docstring).

### Models and utilities

`image/models/` holds plain data. `switch` holds `SWITCH_KEY`, the key image's switch is read by
(`"images"`). `image_module` holds the configuration as read (`ImageConfig`), the render's and the
validity check's outcomes (`RenderOutcome`, `ValidityReport`), whose shapes Principle XIV constrains
(its module docstring), with `Problem`, `AspectStatus`, the validity layers' numbers and
`DrawingRefused`, the base of the drawing's errors a league can put right (`CapacityError` and the
data error of each aspect that has one). `image_constants` holds the static maps: the nine aspects,
the sixteen templates and which aspect draws which. `image_catalogues` holds the field catalogue of
each image type. `forecast_values`, `results_drawing`, `standings_drawing`, `attendance_drawing`,
`verdict_banner_drawing` and `verdict_kind` hold models another module composes for a picture and
hands in (below, "How modules and core fit together").

`image/utils/` holds the pure work, which reads no database and touches no Discord object:
`svg_document` (parsing, the canvas, style resolution and `off_the_loop`), `svg_fill` (the six fill
operations of Principle XIV.2), `svg_palette` (the per-tier colour slots), `font_metrics`,
`asset_resolver`, `palette_import`, `colour`, `country_data` and `image_naming`. Each says in its
module docstring that it is pure, which is what lets the engine be tested without a bot.

The tyre compounds are not here: they are a results datum an image happens to draw, so they sit in
`results/utils/tyre_compound.py`, and the preview takes them through `PreviewSources`
(`tyre_compound`'s module docstring).

### The template model

A template is checked and filled through one model of what it declares. Each check and each render
parses it once (`load_svg`) and indexes its fields by id, and by layer label as the fallback
(`FieldIndex`, `svg_document`). What an image type
addresses is its catalogue (`image_catalogues`, Principle XIV.10): the fields it fills, by
operation, each mandatory or optional. A collection's members are counted from the names a template
declares, indexed once into `DeclaredNames` so that each count is a lookup. The validity check
applies three layers in order (`image_validity_service`): the template resolves and parses (Layer 1,
for all sixteen), it carries every mandatory field (Layer 2), and every bounded field can be laid
out (Layer 3), stopping at the first that fails and stating the depth it reached. The fill then
works through the six operations the constitution allows and no others (`svg_fill`).

`image_catalogues` is one file of every catalogue, one entry for each of the sixteen templates.
Declaring a type's catalogue entry is all that brings its catalogue-driven checks to life (its
module docstring).

### The cog

The `/images` group is one cog, `ImageCog` (`image/cogs/image_cog.py`), composed from one class for
each of its four subgroups, each in its own file: `config_commands`, `template_commands`,
`use_pfp_commands` and `test_commands`, split and declared as `architecture.md` gives a cog too
large for one file ("How the code is laid out"): the group and its four subgroups are declared in
`image_group.py` (`test_the_listed_order_holds_whichever_subgroup_s_file_is_imported_first`), which
also holds the division autocomplete two subgroups share. The cog answers the command and checks the
switch; the rule is a service's: the preview's body is `preview_runner`'s and the configuration
report `configuration_report`'s, each handed what it reads rather than looking it up on the bot.

### The wiring

`image/wiring.py` is image's part of the builder (`architecture.md`): it signs image's switch reader
up with `ModuleService`, its portrait discard to `drivers_deleted`, its fingerprint area to
`fingerprint_areas_read` and its kept job to `bot_packed`, registers its change types and the
handler of its one timed job, and names its one start-up step. It holds no rule of image's and no
database code.

---

## How a change is carried out

Answers to `architecture.md`, "How a change is carried out".

### The settings

Each `/images` command that changes the configuration asks one `image.setting.…` change, built by
the framework's `setting_change` from a `Setting` in `image_settings`: the check is the command's,
run at the ask and again as the change starts; the save writes the value; and the advice a reply
carries beside it (what a directory resolves to, the fastest-lap colour's contrast) is a step after
the save, planned only where the save changed something, so that a fault in it leaves the value
saved and the reply says it could not be checked. `image_settings`'s module docstring holds each
part.

A toggle has a step before the save, `picture_channels_check` (core's,
`core/services/modules/picture_channels.py`), handed the aspect as the change names it only where the
toggle switches on (`_switched_on`); the rule it enforces is in `image_module_specification.md`,
"Configuration", under `/images config toggle`, and a refusal is `_toggle_refusal`. The step runs as
the change starts, not at the ask.

### The switch on and off

`/module enable images` and `/module disable images` each ask one change built from
`module_on_images` or `module_off_images` (`module_switch_settings`).

The switch-on first runs the same step for the outputs already on (`enabled_aspects_on`, read on the
connection the step holds, with `_enable_refusal`): the configuration is kept while the module is
off, so an output left on has its channels judged before the module is, and a channel failing for
the lineup and the calendar alike, the command channel, is one line naming both. The module stays
off where any fails. It is then one save: the configuration row and every aspect toggle created where missing, the
flag raised and the audit entry written, so a fault anywhere leaves none of it. Where the kept
configuration asks for daily portraits, a step after the save arms the daily job and carries the
success line, so that the save stands whatever the arming does, and what a league is told when it
fails holds (image specification, "Configuration"; Principle X, rule 2).

The switch-off is one save, the flag and the audit entry, with the daily job removed in a step after
it (`DAILY_JOB_NOT_REMOVED_MODULE_OFF` its reply where it cannot be; image specification,
"Configuration"; Principle X, rule 3). What that reply says holds because the job reads the switch
as it fires (below).

Each switch asks the hub's panel to be refreshed once, as a step after it.

The portrait toggles arm and disarm the same job through the same two steps (`arm_daily`,
`disarm_daily` in `image_settings`).

### Off the queue

Two things image writes are not changes on the queue: the portrait register (above), and the picture
files it writes, which are not records. A rendered picture is taken down through the drawing's
`discard_attachment`, or `discard_picture` for a calendar or a lineup (image specification, "The
rendered file").

---

## Timed work and restarts

Answers to `architecture.md`, "Timed work and restarts".

Image has one timed job, the daily portrait refresh, the standing job at a time of day that
`architecture.md` allows ("Timed work and restarts"), armed by `schedule_portrait_refresh`
(`driver_portrait_service`), a plain function handed the scheduler, which arms it through the
scheduler's `arm_daily` and disarms it, with `cancel_portrait_refresh`, through `remove`. The pack
keeps the job because image's subscriber of `bot_packed` hands back its id
(`PORTRAIT_REFRESH_JOB_ID`).

The handler, `run_daily_refresh` (`driver_portrait_service`), is signed up by the wiring and handed
the queue's moment. It reads the module's switch as it fires and does nothing while the module is
off; reads the configuration and does nothing unless portraits and daily updates are on; and judges
from the moment it is handed, against `MISSED_AFTER`, whether the run was missed, and skips one that
was (image specification, "Configuration"; core specification, "When the bot stops"). It then works
through every driver assigned in the active season, a placement not yet confirmed included
(`active_season_driver_ids`), fetching for each one on the server whose portrait is stale or
missing, with no time budget, since nothing waits on it.

Image offers no step for `/test-mode advance`.

At start-up, image's one step (`portrait refresh job`) re-arms the job at the stored time where the
league has portraits and daily updates on (`rearm_daily_refresh`); a scheduler that refuses it makes
the step raise, for the start-up sweep to report.

---

## Posting to Discord

Answers to `architecture.md`, "Posting to Discord".

The image module posts nothing to a league's channels: what a drawing hands back, and who posts it,
is `architecture.md`'s ("How modules and core fit together"). On image's side, the line saying why a
picture that was due was not drawn is headed by the aspect's own name (each post module's
`*_TOGGLE_NAME`, the weather forecast's written in place), and its notices by what was drawn ("Image
render notices — …", `ImageRenderService.format_notices`). The one drawing that writes its own
notices, the lineup a command draws, is `ImageDrawing.draw_lineup_for_command`, through the log
writer the builder hands it, and it hands back the link to the message it logs, for the command to
tell its manager the notices with (`LineupPicture.log_link`). The `/images test` previews write their notices through the writer the
runner is handed (`preview_runner`), naming what was drawn.

---

## How modules and core fit together

Answers to `architecture.md`, "How modules and core fit together".

### Image is asked only to draw

How image meets the other modules is `architecture.md`'s ("How modules and core fit together"); the
contract that holds its side is `image-uses-no-other-module`. On image's side, the models it
declares in `image/models/` are `ResultsDrawing` and `StandingsDrawing`, which results composes
(`results_drawing_composer`, `standings_drawing_composer`); `ForecastValues`, which weather composes;
the attendance sheet's `DriverRecord` and `RoundHeading`, which attendance resolves into the sheet's
drawing through the same file's `resolve_drawing` (`attendance_drawing`); the verdicts' banner, which
results builds and words through `verdict_banner_drawing`; and `VerdictKind`, which names a verdict
results asks to be drawn. The aspect a results or a standings picture is switched by is declared
beside its model, for results to ask the gate about. Results reads a verdict's team name and key
from core's team readers itself. Attendance hands in a check-in call's deadline. A resolution kept
among the models is pure, as the models are, and holds the picture's own rules (the attendance
sheet's limit plate and marks); the projection onto a template stays in the aspect's service.

*Rejected:* the sheet's and the banner's shapes declared beside their aspect's resolution in
`aspects/`, which the callers then imported. No contract could keep a caller out of the rest of an
aspect's service while the shapes it needs lived there, and the verdict's team readers sat in image
with results their only caller.

*Rejected:* image composing or formatting a classification's values itself. A rule of how a
classification reads would then be held twice, and a picture could disagree with the text table it
stands for: every value comes from `results_formatter`'s row builders and is merely placed
(`results_drawing_composer`'s module docstring, Principle XIV.7), and image imports no other module
(`architecture.md`, "How modules and core fit together").

### One drawing

Every module that asks image to draw is handed one object, `ImageDrawing`
(`image/services/render/image_drawing.py`), which the builder makes once over image's services and
attaches as `bot.image_drawing`; a wiring hands it to the change types that post, and reaches it as
image's own class through `as_image_drawing`. The contract that keeps results, attendance and
weather out of image's render, configuration and aspects' services is
`results-attendance-and-weather-do-not-reach-image-s-services`. Core reaches the same
object through the protocol it declares (`core/services/modules/image_drawing.py`, `core.md`).

### The gate

Every picture drawn for a post or for a command's output, but the weather forecast's and the
`/images test` previews', asks `ImageDrawing.aspect_wanted` (`architecture.md`, "Errors and
failures"); the forecast asks `_weather_gate`. Results asks the gate for each standings template
before composing, and hands the drawing only the championships wanted. The `/images test` previews
ask the module's switch alone.

`aspect_attaches_files` answers the attachment a channel's permissions are judged by: results'
reposting, attendance's recalculation and each module's channels at a confirmation, each stating
what its channel needs of the bot and asking the drawing only whether a picture could attach a
file. `calendar_attaches_files` and `lineup_attaches_files` put the same question for core's two
channels, which core asks through its protocol. Why it does not ask template validity is its
docstring's, pinned by `test_template_validity_is_deliberately_not_consulted`.

### Commanded and scheduled

A render that fails is resolved against who asked for the posting (Principle XIV.7). The seven
aspects drawn through an `image_<aspect>_post` resolve it in
`ImageRenderService.render_for_posting`, which hands back a `PostingDecision` the caller reads and
does exactly one thing with: post the picture, fall back to text, or reject the command. Its
`posting_origin` is required, and anything but a `PostingOrigin` raises. The calendar and the lineup
make the same split in their own drawing methods (`draw_lineup`, `draw_lineup_for_command`,
`draw_review_lineup`) and in core's `render_for_command`.

*Rejected:* inferring the origin from whether an interaction is in scope, which is wrong for a
command that schedules later work and for a retry re-posting what a command originated
(`render_for_posting`'s docstring).

### The previews' sources

What a preview draws is the image specification's ("The test commands"). What another module
owns is read through a protocol image declares, `PreviewSources` (`preview/preview_sources.py`):
each module implements the part whose data it owns, results its sample sessions and
classification rows, compounds, standings, results drawing and the sanction's wording
(`results/services/publishing/preview_sources.py`), and attendance its check-in deadline
(`attendance/services/hooks/preview_sources.py`), and the builder joins the two and attaches them as
`bot.preview_sources`. A method hands back a worked answer, never a stored row or a service of the
module's.

Image has no entry in the dependency table and declares no hook; the one seam it declares for others
to fill is this protocol (`architecture.md`, "How modules and core fit together").

### Core's readers

Image reads core's tables only through core's readers, among them a division's seating as the lineup
of record shows it (`division_seating`), a round's and a division's context (`round_context`,
`division_context`), and a driver's name and nationality and whether the league collects one
(`core/services/drivers/driver_names.py`), which it asks with core's `driver_names_read` hook,
the one signup answers (`core.md`), and the channels an output posts to, through core's
`picture_channels_check`, which asks the `picture_channels_read` hook (`core.md`) that image does
not answer, the outputs' channels being the modules' and core's. The portrait roster reads every driver assigned in the active
season through `active_season_driver_ids`.

### The hooks image signs up to

| Hook | Image's subscriber |
|---|---|
| `drivers_deleted` | `discard_portraits`: the portraits of accounts no longer drawn |
| `fingerprint_areas_read` | `fingerprint_areas`: image's configuration, toggles and tier colours |
| `bot_packed` | `bot_packed_on`: hands back the daily refresh's job id under `KEPT_JOB_IDS`, so the pack keeps it |
| `starting` | the `portrait refresh job` step, under image's switch key |

---

## Errors and failures

Answers to `architecture.md`, "Errors and failures", and its catch-all places.

The places, and the stated exceptions with their reasons (the gates, the portraits, the verdict's
optional fields, the artwork signature), are `architecture.md`'s, and are not repeated here. This
section is not exhaustive: every catch-all its shape does not place is in the catch-all check's
lists (`CATCH_ALLS_ALLOWED`, `KNOWN_CATCH_ALLS_OUTSIDE_THE_PLACES`).

**The render is place 5.** `ImageRenderService.render` and its worker `_draw` hold the render's
fallback: a tier's colours that cannot be read or applied are drawn in the template's own, with a
notice; data that cannot be assembled or a template that cannot be filled comes back as a problem,
from which the caller falls back to text (image specification, "Errors and the rejection of input").
A render's problem and its notices are distinct outcomes (`RenderOutcome`, Principle XIV.4).
Of what the spec build and the fill raise, only a `DrawingRefused` is meant as a problem: an error
raised on purpose, worded for the league, which it can put right. Any other exception is a fault
in the bot. A caller with a fallback still takes it as a problem, which is the default. A caller
with none, the preview, asks `render` to raise it (`raise_faults`), so that it reaches the
command's failure path (core specification, "When a command fails" and "The record of what
changed"): a problem there would be a fallback with nothing to fall back to. The template, verify,
unresolved-field and rasteriser problems are not of this kind and come back as problems either way.

**A drawing that rides on a post is place 6.** Image's own are `ImageDrawing.attach_forecast`,
`attach_check_in_call`, `draw_attendance_sheet`, `draw_banner` and the forecast's `_weather_gate`.
Where another module posts the picture, the catch is the caller's, of place 5, the post itself
falling back to text: results' `produce_session_results`, `produce_standings` and `_send_verdict`,
and core's `LineupPoster._refresh_lineup_post` and `post_division_calendar`. The calendar a command
draws for the placements review rides on no post: core's `render_for_command` refuses on its fault,
as a commanded render does.

**Work off the event loop is place 4.** The worker's catch is `_contained`, which hands the fault
back as plain text, and `off_the_loop` raises it afresh on the loop, so that nothing parsed leaves
the thread.

**A portrait fetch.** A Discord error stops the tasks not yet started, since the CDN fetch has no
back-off (`driver_portrait_service`'s module docstring, pinned by
`test_an_http_error_abandons_the_rest_of_the_batch`).

**The rasteriser is a separate program.** The render hands the filled drawing to Inkscape, a binary
no package declaration installs, one of the three dependencies the image specification's opening
section names; its absence is fatal to the module and reported, not raised (`image_render_service`'s
module docstring). `rasterise` writes the drawing to an intermediate SVG beside the PNG, runs the
converter on it with a timeout (`_RASTERISE_TIMEOUT_SECONDS`), and removes that SVG whatever the
outcome (image specification, "The rendered file"); the render calls it in a thread, as a blocking
subprocess must not run on the event loop (the comment at its call in `ImageRenderService.render`).
A lookup of the converter's location is reused for up to a minute (`_CACHE_TTL_SECONDS`, whose
comment gives the reason: it is probed too often for a filesystem walk each time, and briefly enough
that installing it is noticed without a restart), save where a caller asks afresh: the switch-on's
reply and the previews.

---

## Constraints a later reader might tune away

Each of these is held by the docstring named, and pinned by a test where one is named there.

- **`DeclaredNames` is never rewrapped in a plain set** on its way down the member loops: that
  silently restores the quadratic scan (`DeclaredNames`,
  `test_a_template_s_names_are_indexed_once_per_enumeration`).
- **Nothing parsed leaves a worker thread** (`load_svg`, `off_the_loop`).
- **A mandatory field's ids are read before anything is removed** (`svg_fill._mandatory_ids`): a
  group removal part-way through a fill would otherwise read as a gap.
- **`FieldIndex` skips `<defs>`**, while the palette's scan walks it (`FieldIndex`, `svg_palette`).
- **A colour slot reaches a stylesheet only through `normalise_slot`**, the one gate between a
  league's typing and a CSS selector (`svg_palette`).
- **A palette is a stylesheet, not CSS custom properties**, which the rasteriser rejects
  (`svg_palette`'s module docstring).
- **Layers 2 and 3 skip an empty catalogue rather than pass it**, so no template is reported checked
  more deeply than it was (`image_catalogues`, `image_validity_service`).
- **The portrait register is written around the file**, claimed before and removed after
  (`driver_portrait_service`, `test_a_portrait_cut_off_once_written_stays_the_bot_s`).
- **A portrait is an SVG wrapping the PNG**, so that asset resolution keeps one extension
  (`driver_portrait_service`'s module docstring).
- **A setting writes a column only through the allow-lists** (`SETTABLE_COLUMNS` for text values,
  `FLAG_COLUMNS` for flags, `image_config_service`), so the generic setter cannot reach
  `module_enabled` or `id`.
- **`aspect_attaches_files` does not ask template validity** (above).
- **A picture's channels are judged as if the output were a picture**, Attach Files among the needs
  whatever the output's switch holds: the step runs while the output is still text, and the drawing
  is not asked, the step's connection being held (`picture_channels`, `core.md`).
- **`posting_origin` is required and never inferred** (`render_for_posting`).
