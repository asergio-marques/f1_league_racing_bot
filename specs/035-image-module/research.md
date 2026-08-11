# Phase 0 Research: Image Module — Initial Setup & Configuration

**Feature**: 035-image-module | **Date**: 2026-08-10

The user's instruction was that technologies remain the same. They do: every finding below stays
inside the stack already declared in `requirements.txt` and already installed. Nothing here adds a
dependency.

---

## R1 — The render engine is built from Principle XIV, in three pure layers

**Decision**: Build the engine new, against Constitution Principle XIV as its only behavioural
source, split into three modules:

| Module | Nature | Responsibility |
|--------|--------|----------------|
| `src/utils/font_metrics.py` | pure | Index installed faces via `fontTools`; resolve a CSS `font-family` list to the face a renderer would land on; measure a string by summing advance widths |
| `src/utils/svg_fill.py` | pure | The six fill operations of XIV.2, the crop, the wrap and the bounds of XIV.5 |
| `src/services/image_render_service.py` | async | Rasterise via the Inkscape CLI; assemble `RenderOutcome`; persist notices |

**Rationale**: Principle XIV is unusually prescriptive for a constitution — it names the six
permitted operations, fixes recolour to a merge into inline `style`, requires the canvas be read
from the template root, and specifies the wrap's half-pixel descent and half-size floor including
the recomputation of line count at the reduced leading. That is a buildable specification on its
own. Building against it directly also keeps the engine's behaviour traceable to governance rather
than to prior art, so a later reviewer checking Principle XIV compliance reads the same document
the code was written from.

**The two layers are pure on purpose.** Neither takes a database handle nor a Discord object. That
is what makes the whole engine unit-testable without a bot, and it is the property most worth
protecting as the module grows.

**Alternatives considered**:

- *A single render module.* Rejected — font measurement is needed by the wrap logic, and folding
  both into the service would make the measurement untestable without a subprocess.
- *A third-party SVG templating library.* Rejected — the user's instruction is that technologies
  remain the same, and XIV.2's closed operation set is narrow enough that a general library would
  mostly need constraining back down.

**Consequence for tasks**: the engine is real implementation work, not a mechanical move. Each of
the six operations of XIV.2, plus the crop, the wrap and the `inline-size` bound, needs its own unit
test asserting the invariants Principle XIV states — see
[contracts/render-service.md](./contracts/render-service.md), which enumerates them.

---

## R2 — Rasterisation must not run on the event loop

**Decision**: `rasterise()` is a blocking `subprocess` call. Wrap every call in
`asyncio.to_thread(...)`. `/images test` must `defer(ephemeral=True)` before starting.

**Rationale**: The bot is a single-process asyncio application. A synchronous Inkscape invocation
takes on the order of a second per render and would stall every other coroutine — the scheduler,
the retry worker, every in-flight interaction — for its duration. Bot Behavior Standards also
require acknowledgement within 3 seconds, which a multi-variant test render (FR-040 returns two
variants for `weather-p2`) will not meet inline.

**Alternatives considered**: A worker process or queue. Rejected as premature — the only caller in
this increment is a manually invoked diagnostic. When the wiring increment adds automatic renders
on a schedule, revisit; the service API is written so that change is internal.

**This is the single most likely way to implement this feature incorrectly**, because it works
perfectly in a unit test and degrades the whole bot in production.

---

## R3 — Entity granularity: aspects, not image types

**Decision**: Store toggles per **aspect** (8 rows) as `ImageAspectToggle`, matching the spec and
the author's brief. Do not store per image type (15 rows).

**Rationale**: The command surface is `/images config toggle <aspect>` with eight values. A
per-image-type table would require the command to fan one value out to as many as six rows
(weather) and fold them back for reporting, with no user-visible benefit — no command can address
an individual weather phase's toggle.

**Divergence recorded**: Constitution v2.11.0 names this entity `ImageTypeState`, keyed per image
type with a `source_module` column. The spec, written later and from the author's explicit brief,
specifies per-aspect. The spec is authoritative on the command surface, so the implementation
follows it, and a **PATCH** constitution amendment renaming and re-graining the entity is proposed
once this plan is accepted. This is recorded in the plan's Complexity Tracking so the divergence is
deliberate and visible rather than discovered during implementation.

The mapping from aspect to template is a code-level constant, not a table:

```
calendar   → 1 template     standings  → 2 templates
lineup     → 1 template     attendance → 1 template
results    → 2 templates    rsvp       → 1 template
weather    → 6 templates    verdicts   → 1 template
                                          ── 15 total
```

---

## R4 — Persistence follows the Attendance module exactly

**Decision**: One new migration, `039_image_module.sql`, creating three tables. Config table keyed
on `server_id` with `REFERENCES server_configs(server_id) ON DELETE CASCADE`, mirroring
`030_attendance_module.sql`.

**Rationale**: Migration 038 is the highest existing number, so 039 is next. The attendance config
table is the closest structural analogue — a per-server config row with a `module_enabled` flag and
a set of scalar settings — and following it keeps `ModuleService` uniform.

**Note on `ModuleService`**: it currently reads the weather and signup flags from columns on
`server_configs`, and the results and attendance flags from their own module tables. The image
module follows the newer pattern (own table). `set_images_enabled` must use `INSERT OR REPLACE` on
first enable, as `set_results_enabled` does, rather than `UPDATE`, which would silently no-op when
no row exists.

**Alternatives considered**: Columns on `server_configs`. Rejected — 31 columns on a shared table,
and it would make the X.6 retention exception harder to reason about.

---

## R5 — Permission tiers map to existing decorators

**Decision**:

| Spec tier | Decorator | Discord permission | Commands |
|-----------|-----------|--------------------|----------|
| Server administrator | `@channel_guard` + `@server_admin_only` | Administrator | template directory, 15 template filenames, 7 asset directories (FR-041) |
| League manager | `@channel_guard` + `@admin_only` | Manage Server | 8 toggles, 4 preferences, `config view`, `test` (FR-042) |

**Rationale**: `src/utils/channel_guard.py` already defines exactly these three decorators, and
`module_cog.py` already applies `server_admin_only` to enable and disable. FR-043's
interaction-role and interaction-channel gates are what `channel_guard` itself provides, so
stacking gives all three requirements with no new code.

**Alternatives considered**: A new decorator for the image module. Rejected — no new tier exists.

---

## R6 — Time zone selection by autocomplete over IANA names

**Decision**: `zoneinfo.available_timezones()` from the standard library, filtered by the typed
prefix and truncated to Discord's 25-choice autocomplete limit. Stored as the IANA string. Default
`UTC`.

**Rationale**: There are several hundred zones — far past what a static `app_commands.Choice` list
holds. `zoneinfo` is standard library on Python 3.9+, so this adds no dependency. Storing the name
rather than an offset is what makes FR-021's daylight-saving requirement work: the offset is
resolved against the date being displayed, not fixed at configuration time.

**Alternatives considered**: A curated list of ~30 common zones. Rejected — a league outside them
would have no correct option, and autocomplete costs nothing.

---

## R7 — Contrast ratio by the WCAG formula

**Decision**: A pure function in a new `src/utils/colour.py`: parse `#RRGGBB` (case-insensitive,
exactly six digits per FR-025), compute WCAG 2.x relative luminance, return the ratio.

**Rationale**: 4.5:1 is named in FR-026 and is the WCAG AA threshold for normal-size text, so the
matching formula is the one the threshold was defined against. It is about fifteen lines of pure
arithmetic — a new utility module rather than an addition to `math_utils.py`, which is weather
formulas.

**The background is the hard half, not the ratio.** FR-026a requires the background be located by a
single documented `@id` in the race results template. Layer 1 validity cannot establish that the
element exists, so its absence is an unmeasurable contrast (FR-027), not a template validity
failure.

Resolving that element's effective fill needs a small CSS cascade: an SVG template may set the
colour as a presentation attribute, in an inline `style`, or in a `<style>` block keyed by class or
id. `svg_fill.py` needs the same resolution for the recolour operation anyway — XIV.2 requires
recolour be merged into inline style precisely *because* a presentation attribute loses to the
template's own stylesheet — so this is shared code, not a cost carried for the contrast check alone.

---

## R8 — Path containment by resolution, not string matching

**Decision**: Resolve the configured path against the project root with `Path.resolve()` and reject
unless `resolved.is_relative_to(project_root)` (FR-011, FR-016).

**Rationale**: String prefix checks are defeated by `..` segments, symlinks and, on Windows, by
case and short-name variation. `Path.resolve()` normalises all of these before the comparison.
`is_relative_to` is standard library on 3.9+.

**Note**: resolution must happen at configuration time, so a bad path is rejected by the command
rather than surfacing as a render failure later. This is what makes the edge case "template path
escaping the project root" a configuration error and not a validity state.

---

## R9 — Resource provisioning is out of scope

**Decision**: How the template and asset files arrive on a host is not this feature's concern. The
module resolves the configured paths, reports what it finds, and does nothing else about it.

**Rationale**: Directed by the author — the contents of `resources/` are not an input to this
feature's design. This is consistent with the spec's model throughout: paths are configuration,
files are the operator's to place, and uploading them through Discord is explicitly out of scope.
The default path *values* (`resources/templates` and the seven asset directories) come from the
author's brief and remain as specified; what is out of consideration is anything the files under
that tree might contain.

**Consequence for the design**: none — the behaviour was already correct. A path that resolves to
nothing is reported invalid by Layer 1 with the full path searched (FR-028), which is exactly what
an operator needs in order to fix it. No packaging step, no bundled defaults, no provisioning code.

**Consequence for validation**: the quickstart assumes the operator has placed templates and assets
where the configuration points, and says so rather than assuming a fresh clone carries them.

---

## R10 — `/images test` returns an ephemeral attachment

**Decision**: `defer(ephemeral=True)`, render off-loop, then `followup.send` with the PNG as a
`discord.File` and any notices as message text. Multi-variant kinds attach every variant to one
followup (FR-040).

**Rationale**: FR-037 requires visibility only to the invoker, which is what ephemeral means.
Rendering to a temporary file and attaching it is simpler than streaming bytes, and a file path is
the natural output of a CLI rasteriser.

**Size**: Discord's non-boosted attachment limit is 25 MB. A flat-colour PNG at the canvas sizes a
league's templates declare will not approach it, but the canvas is template-declared (XIV.1) and
therefore not something the module controls. Treat exceeding the limit as a **problem** — the
render aborts and falls back — rather than letting the Discord upload fail unhandled.

**Alternatives considered**: Posting to the log channel. Rejected — it violates FR-037 and would
put diagnostic output in a channel Principle VII reserves for audit entries.

---

## R11 — The working specification could not be read

**Finding**: `docs/wip-specs/image_module_specification.md` is deny-listed in
`.claude/settings.json` and was unreadable during both specification and planning. The 26-command
surface, the defaults and the eleven test kinds were derived from the author's brief in the
`/speckit-specify` invocation; the rendering behaviour from Constitution Principle XIV.

**Consequence**: Before implementation, reconcile against that document — specifically the field
catalogue (which Layer 2 will need), the exact `images test <type>` sample data, and the
per-image-type template inventory. Anything it specifies that this plan omits becomes a task.

**This does not block `/speckit-tasks`.** Everything planned here is derived from an explicit brief
and from code that runs; the risk is omission, not contradiction.
