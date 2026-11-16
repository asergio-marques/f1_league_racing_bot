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
declared in `core/db/migrations/001_baseline.sql`.

| Table | One row is | Held to it by |
|---|---|---|
| `image_config` | the league's whole configuration: the switch (`module_enabled`), the template directory and the sixteen template filenames, the eight asset directories, the time zone and the clock and date formats, the fastest-lap colour, the per-tier colour switch, and the four portrait settings | `CHECK (id = 1)`; every value column `NOT NULL`, its default the value the bot ships |
| `image_aspect_toggles` | one of the nine aspects a league switches on and off, and whether it is on | the aspect as primary key |
| `image_tier_colour` | one colour of one slot of one division's tier | the primary key `(division_slug, slot)` |
| `driver_portraits` | one portrait the bot obtained: the account it is of, and the key of the avatar it was made from and the shape it was wrapped at (`portrait_key`) | the account as primary key |

None of image's columns sits on core's tables, and none of its tables holds a link to one of core's
that would stop a delete: a tier's colours are keyed by the division's slug, and a portrait by the
account, so a season's end or a division's deletion is never refused for them. A column a command
may write is in the allow-lists of `image_config_service` (`SETTABLE_COLUMNS` for the text values,
and the flags apart), which a write outside raises on (`UnknownConfigField`): the column's name is
spliced into the statement, so the allow-list is what keeps a name the league did not choose from
reaching it.

### The switch, and what the switch-off keeps

The module's switch is `image_config.module_enabled`, read through `images_enabled_on`
(`image/services/config/module_switch_settings.py`), which the builder signs up with `ModuleService`
under the name `images` (`architecture.md`, "How modules and core fit together"). A league that has
never switched it on has no row and reads as off.

The switch-off clears the flag and nothing else: no configuration row is deleted and no toggle
reset. This is the exception the constitution grants for configuration that cannot go stale
(Principle X, rule 6), which the image module claims for the whole of its configuration, the
portrait settings and the tier colours included. The daily portrait job the configuration arms is
not configuration and is not kept armed: the switch-off removes it and the switch-on arms it again
("How a change is carried out", below).

### The portrait register

`driver_portraits` is how the bot tells a portrait it obtained from one the league put in its own
driver directory: a file with no row is the league's, and is never fetched over or removed. The
register is written around the file it records: the row is claimed first, with a key no avatar can
match, the file written, the real key recorded after, and the row removed only after the file is. A
fetch cut off between the file and its key so leaves a row, and the portrait stays the bot's to
fetch again or to discard; were the file written first, a cut-off would leave a file that reads as
the league's own artwork for ever. `driver_portrait_service`'s module docstring holds this, pinned
by `test_a_portrait_cut_off_once_written_stays_the_bot_s`.

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
the fingerprint asks the drawing for it (`ImageDrawing.artwork_signature`, below).

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
| `aspects/` | for each of the nine aspects, its resolution (`image_<aspect>_service`) and its drawing for a caller (`image_<aspect>_post`), the two apart where an aspect has both |
| `preview/` | the `/images test` previews: their resolution (`image_preview_service`), the outcomes they invent (`image_preview_data`), the protocol they read other modules' data through (`preview_sources`), the body every preview shares (`preview_runner`) and the `/images config view` report (`configuration_report`) |
| `portraits/` | the driver portraits (`driver_portrait_service`) |

`aspect_status` is apart from `image_validity_service` so that the dependency runs one way: it reads
the reports the validity service makes and never makes one, and the validity service reaches for it
only when called (`aspect_status`' module docstring).

### Models and utilities

`image/models/` holds plain data. `image_module` holds the configuration as read (`ImageConfig`),
the render's and the validity check's outcomes (`RenderOutcome`, `ValidityReport`), whose shapes
Principle XIV constrains (its module docstring), with `Problem`, `AspectStatus` and the validity
layers' numbers. `image_constants` holds the static maps: the nine
aspects, the sixteen templates and which aspect draws which. `image_catalogues` holds the field
catalogue of each image type. `forecast_values`, `results_drawing` and `standings_drawing` hold what
another module composes for a picture and hands in (below, "How modules and core fit together").

`image/utils/` holds the pure work, which reads no database and touches no Discord object:
`svg_document` (parsing, the canvas, style resolution and `off_the_loop`), `svg_fill` (the six fill
operations of Principle XIV.2), `svg_palette` (the per-tier colour slots), `font_metrics`,
`asset_resolver`, `palette_import`, `colour`, `country_data` and `image_naming`. Each says in its
module docstring that it is pure, which is what lets the engine be tested without a bot.

The tyre compounds are not here: they are a results datum an image happens to draw, so they sit in
`results/utils/tyre_compound.py`, and the preview takes them through `PreviewSources`
(`tyre_compound`'s module docstring).

### The template model

A template is checked and filled through one model of what it declares. A template is parsed once
(`load_svg`) and its fields indexed by id (`FieldIndex`, `svg_document`). What an image type
addresses is its catalogue (`image_catalogues`, Principle XIV.10): the fields it fills, by
operation, each mandatory or optional. A collection's members are counted from the names a template
declares, indexed once into `DeclaredNames` so that each count is a lookup. The validity check
applies three layers in order (`image_validity_service`): the template resolves and parses (Layer 1,
for all sixteen), it carries every mandatory field (Layer 2), and every bounded field can be laid
out (Layer 3), stopping at the first that fails and stating the depth it reached. The fill then
works through the six operations the constitution allows and no others (`svg_fill`).

`image_catalogues` is kept as one file of every catalogue, one entry for each of the sixteen
templates, because declaring an entry is the whole of what a new image type needs: no cog, command
or report changes with it (its module docstring, and `image_validity_service`'s "stable surface").

### The cog

The `/images` group is one cog, `ImageCog` (`image/cogs/image_cog.py`), composed from one class for
each of its four subgroups, each in its own file: `config_commands`, `template_commands`,
`use_pfp_commands` and `test_commands`, split as `architecture.md` gives a cog too large for one
file ("How the code is laid out"). The group and its four
subgroups are declared together in `image_group.py`, in the order a league sees them, so that the
order Discord is sent does not depend on which subgroup's file is imported first; it also holds the
division autocomplete two subgroups share. The cog answers the command and checks the switch; the
rule is a service's: the preview's body is `preview_runner`'s and the configuration report
`configuration_report`'s, each handed what it reads rather than looking it up on the bot.

### The wiring

`image/wiring.py` is image's part of the builder (`architecture.md`): it signs image's switch reader
up with `ModuleService`, its portrait discard to `drivers_deleted` and its fingerprint area to
`fingerprint_areas_read`, registers its change types and the handler of its one timed job, and names
its one start-up step. It holds no rule of image's and no database code.

---

## How a change is carried out

Answers to `architecture.md`, "How a change is carried out".

### The settings

Each `/images` command that changes the configuration asks one `image.setting.…` change, built by
core's `setting_change` from a `Setting` in `image_settings`: the check is the command's, run at the
ask and again as the change starts; the save writes the value; and the advice a reply carries beside
it (what a directory resolves to, the fastest-lap colour's contrast) is a step after the save,
planned only where the save changed something, so that a fault in it leaves the value saved and the
reply says it could not be checked. `image_settings`' module docstring holds each part.

### The switch on and off

`/module enable images` and `/module disable images` each ask one change built from
`module_on_images` or `module_off_images` (`module_switch_settings`).

The switch-on is one save: the configuration row and every aspect toggle created where missing, the
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

Two things image writes are not changes on the queue: the portrait register (above), and the files a
render writes, which are not records. A rendered picture is taken down through the drawing's
`discard_attachment` (image specification, "The rendered file").

---

## Timed work and restarts

Answers to `architecture.md`, "Timed work and restarts".

Image has one timed job, the daily portrait refresh, the standing job at a time of day that
`architecture.md` allows ("Timed work and restarts"): a recurring trigger
(`SchedulerService.schedule_portrait_refresh`, whose docstring says why) armed with no lateness
limit, so that a run missed while the bot was down is the handler's to judge.

The handler, `run_daily_refresh` (`driver_portrait_service`), is signed up by the wiring and handed
the queue's moment. It reads the module's switch as it fires and does nothing while the module is
off; reads the configuration and does nothing unless portraits and daily updates are on; and judges
from the moment it is handed, against `MISSED_AFTER`, whether the run was missed (image
specification, "Configuration"; core specification, "When the bot stops"). It then works through
every driver seated in the active season, one task for each, with no time budget, since nothing
waits on it.

At start-up, image's one step (`portrait refresh job`) re-arms the job at the stored time where the
league has portraits and daily updates on (`rearm_daily_refresh`). A scheduler that refuses it makes
the step raise, and the start-up sweep reports it in the log channel and goes on with the others.

---

## Posting to Discord

Answers to `architecture.md`, "Posting to Discord".

The image module posts nothing to a league's channels: what a drawing hands back, and who posts it,
is `architecture.md`'s ("How modules and core fit together"). On image's side, each line a drawing
hands back is headed by the name its aspect's toggle is offered under, and the one drawing that
writes its own notices, the lineup a command draws, is `ImageDrawing.draw_lineup_for_command`,
through the log writer the builder hands it. The `/images test` previews write their notices through
the writer the runner is handed (`preview_runner`), naming what was drawn. Every reply a cog or the
runner sends is ephemeral (image specification, "The test commands";
`test_every_image_cog_reply_is_ephemeral`).

---

## How modules and core fit together

Answers to `architecture.md`, "How modules and core fit together".

### Image is asked only to draw

How image meets the other modules is `architecture.md`'s ("How modules and core fit together"); the
contract that holds its side is `image-uses-no-other-module`. What a picture shows is decided by the
module that owns it and handed in; image places it on a template and works none of it out. So the
values a results or standings picture draws are composed in results (`results_drawing_composer`,
`standings_drawing_composer`), from results' own formatter and tables, into the plain models image
declares (`ResultsDrawing`, `StandingsDrawing`); a forecast's words are composed in weather into
`ForecastValues`; and a check-in call's deadline is worked out by attendance and handed in.

*Rejected:* image reading the results tables and formatter to compose those pictures itself. A rule
of how a classification reads would then be held twice, and a picture could disagree with the text
table it stands for (`results_drawing_composer`' module docstring: Principle XIV.7).

### One drawing

Every module that asks image to draw is handed one object, `ImageDrawing`
(`image/services/render/image_drawing.py`), which the builder makes once over image's services and
attaches as `bot.image_drawing`; a wiring hands it to the change types that post, and reaches it as
image's own class through `as_image_drawing`. The contract that keeps results, attendance and
weather out of image's render and configuration is
`results-attendance-and-weather-do-not-reach-image-s-render-or-configuration`. Core reaches the same
object through the protocol it declares (`core/services/modules/image_drawing.py`, `core.md`).

### The gate

Every picture but the weather forecast's asks one gate, `ImageDrawing.aspect_wanted`, before
anything is read for it: the module's switch and the aspect's toggle and, where a template is named,
that template's validity, each template being the unit of failure (Principle XIV.4). The forecast
asks its own, `_weather_gate`, which hands back a line saying why no picture was drawn. Results asks
it for each standings template before composing, and hands the drawing only the championships
wanted.

A second question, `aspect_attaches_files`, asks whether an aspect could ever attach a file, for the
checks of results' reposting and attendance's recalculation that a channel can take one; why it does
not ask template validity is its docstring's, pinned by
`test_template_validity_is_deliberately_not_consulted`.

### The previews' sources

A preview draws the league's own division, round, teams and drivers, and invents only the outcome a
league cannot configure in advance (`image_preview_service`' module docstring). What another module
owns is read through a protocol image declares, `PreviewSources` (`preview/preview_sources.py`):
each module implements the part whose data it owns, results its sessions, rows, compounds,
standings, results drawing and the sanction's wording
(`results/services/publishing/preview_sources.py`), and attendance its check-in deadline
(`attendance/services/hooks/preview_sources.py`), and the builder joins the two and attaches them as
`bot.preview_sources`. A method hands back a worked answer, never a row or a service of the
module's.

### Core's readers

Image reads core's tables only through core's readers, among them a division's seating as the lineup
of record shows it (`division_seating`), a round's and a division's context (`round_context`,
`division_context`), and a driver's name and nationality and whether the league collects one
(`core/services/drivers/driver_names.py`), which it is handed signup's hook to ask
(`driver_names_read`, `core.md`). The portrait roster reads every driver assigned in the active
season through `active_season_driver_ids`.

### The hooks image signs up to

| Hook | Image's subscriber |
|---|---|
| `drivers_deleted` | `discard_portraits`: the portraits of accounts no longer drawn |
| `fingerprint_areas_read` | `fingerprint_areas`: image's configuration, toggles and tier colours |
| `starting` | the `portrait refresh job` step, under image's switch key |

---

## Errors and failures

Answers to `architecture.md`, "Errors and failures", and its catch-all places.

The places and stated exceptions are `architecture.md`'s; this section names image's code in each.

**The gate.** `aspect_wanted` is the gate's stated exception, and `_weather_gate` the forecast's own
gate, of the sixth place. `aspect_attaches_files` is a stated exception for a switch nobody signed
up for (`test_a_switch_nobody_signed_up_for_answers_no_and_is_left_to_the_job`).

**The render is place 5.** `ImageRenderService.render` and its worker `_draw` hold the render's
fallback: a tier's colours that cannot be read or applied are drawn in the template's own, with a
notice; data that cannot be assembled or a template that cannot be filled comes back as a problem,
from which the caller falls back to text (image specification, "Errors and the rejection of input").
A render's problem and its notices are distinct outcomes (`RenderOutcome`, Principle XIV.4).

**A drawing that rides on a post is place 6.** Image's own are `ImageDrawing.attach_forecast`,
`attach_check_in_call`, `draw_attendance_sheet` and `draw_banner`. Where another module posts the
picture, the catch is the caller's: results' `produce_session_results`, `produce_standings` and
`_send_verdict`, and core's `LineupPoster._refresh_lineup_post` and `render_for_command`.

**Work off the event loop is place 4.** A template is parsed, painted, verified and filled in one
call to `off_the_loop` (`svg_document`); its worker's catch is `_contained`, which raises the fault
again on the loop rebuilt from plain text, so that nothing parsed leaves the thread.

**A portrait is done without.** The stated exceptions are `refresh_before_render`, `portrait_aspect`
and `discard_portraits`, each with its reason in its docstring. The fetch is place 2, one task for
each portrait, a few at a time (`refresh_portraits`); a Discord error stops the tasks not yet
started, since the CDN fetch has no back-off (`driver_portrait_service`' module docstring, pinned by
`test_an_http_error_abandons_the_rest_of_the_batch`). A fault in the daily refresh goes to the
failure path `architecture.md` names for scheduled jobs.

**An optional field is done without.** The verdict picture's readers of the stated exception are
`_round_context`, `_driver_nationality`, `team_name_for_entry`, `team_key_for_entry` and
`_mention_names` (`image_verdict_post`).

**The rasteriser is a separate program.** The render hands the filled drawing to Inkscape, a binary
no package declaration installs, one of the three dependencies the image specification's opening
section names; its absence is fatal to the module and reported, not raised (`image_render_service`'
module docstring). No reason for the choice of a separate program is recorded, so this file records
the boundary as built.

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
  (`svg_palette`' module docstring).
- **Layers 2 and 3 skip an empty catalogue rather than pass it**, so no template is reported checked
  more deeply than it was (`image_catalogues`, `image_validity_service`).
- **The portrait register is written around the file**, claimed before and removed after
  (`driver_portrait_service`, `test_a_portrait_cut_off_once_written_stays_the_bot_s`).
- **A portrait is an SVG wrapping the PNG**, so that asset resolution keeps one extension
  (`driver_portrait_service`' module docstring).
- **A column is written only from the allow-list** (`SETTABLE_COLUMNS`, `image_config_service`).
- **`aspect_attaches_files` does not ask template validity** (above).
