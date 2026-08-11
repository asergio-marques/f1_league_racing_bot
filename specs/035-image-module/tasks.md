---

description: "Task list for Image Module — Initial Setup & Configuration"
---

# Tasks: Image Module — Initial Setup & Configuration

**Input**: Design documents from `/specs/035-image-module/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: Included. The design documents request them explicitly — [contracts/render-service.md](./contracts/render-service.md) enumerates nine Principle XIV invariants each requiring a unit test, and [contracts/validity-layers.md](./contracts/validity-layers.md) names four invariants that protect the extension point.

**Organization**: Tasks are grouped by user story so each can be implemented and tested independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US7)
- Exact file paths are given in every task

## Path Conventions

Single project. `src/` and `tests/` at repository root, following the existing
`src/{models,services,cogs,db,utils}` split.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: The schema and the shared constants every later phase reads.

- [X] T001 [P] Create migration `src/db/migrations/039_image_module.sql` with the three tables (`image_config`, `image_aspect_toggles`, `image_render_notices`), every column default per [data-model.md](./data-model.md), `REFERENCES server_configs(server_id) ON DELETE CASCADE`, and an index on `image_render_notices(server_id, rendered_at)`. Use `CREATE TABLE IF NOT EXISTS`; no backfill, no DROP.
- [X] T002 [P] Create `src/models/image_module.py` with dataclasses `ImageConfig`, `ImageAspectToggle`, `RenderNotice`, `ValidityReport`, `AspectStatus`, `RenderOutcome`, `FillResult` per [data-model.md](./data-model.md) "Derived types".
- [X] T003 [P] Create `src/models/image_constants.py` with the aspect→templates map (8 aspects → 15 templates), the aspect→source_module map, the test-kind→templates map (11 kinds), and the five date-format tokens with their example renderings, per [data-model.md](./data-model.md).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The config service, the path guard and the cog skeleton every command hangs off.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T004 Implement `ImageConfigService` in `src/services/image_config_service.py`: `get_config(server_id)`, `create_with_defaults(server_id)` inserting the config row **and all eight toggle rows in one transaction**, and a generic `set_field(server_id, column, value)` guarded by an allow-list of column names.
- [X] T005 [P] Implement `resolve_within_project_root(path)` in `src/utils/paths.py` using `Path.resolve()` and `is_relative_to(project_root)` per research R8. Returns the resolved path or raises a containment error carrying the rejected input.
- [X] T006 Add `is_images_enabled(server_id)` and `set_images_enabled(server_id, value)` to `src/services/module_service.py`. Use `INSERT OR REPLACE` on the enable path, matching `set_results_enabled` — a bare `UPDATE` silently no-ops when no row exists.
- [X] T007 Create `src/cogs/image_cog.py` with the `/images` command group, the `/images config` subgroup, a `_guard_module_enabled(interaction)` helper returning a clear error naming `/module enable images` (FR-005), and an ephemeral-response helper (FR-044).
- [X] T008 Register `image_cog` in `src/bot.py` alongside the existing cogs, and wire `ImageConfigService` onto the bot instance the way `attendance_service` is wired.
- [X] T009 [P] Unit test `tests/unit/test_image_config_service.py`: a created config carries all 31 defaults; `create_with_defaults` inserts exactly 8 toggle rows all disabled; `set_field` rejects a column outside the allow-list.
- [X] T010 [P] Unit test `tests/unit/test_paths.py`: `../../etc`, an absolute path outside the root, and a symlink escaping the root are all rejected; a normal relative path resolves.

**Checkpoint**: Schema, config service and command scaffold ready — user stories can begin.

---

## Phase 3: User Story 1 — Enable and Disable the Module (Priority: P1) 🎯 MVP

**Goal**: An administrator can turn the module on and off, sees whether the host can render at all, and loses no configuration by disabling it.

**Independent Test**: Enable on a fresh server, confirm `/images config view` becomes reachable and reports prerequisite status; disable and confirm every `/images` command is rejected; re-enable and confirm every configured value survived.

### Tests for User Story 1

- [X] T011 [P] [US1] Integration test `tests/integration/test_image_module_flow.py::test_enable_creates_defaults`: `/module enable images` creates the config row and eight disabled toggle rows, and posts to the log channel.
- [X] T012 [P] [US1] Integration test `tests/integration/test_image_module_flow.py::test_disable_retains_configuration`: set a custom directory, colour and toggle; disable; re-enable; assert all three are byte-identical (FR-004a, SC-008). **This is the test that distinguishes this module from every other one on the bot.**
- [X] T013 [P] [US1] Integration test `tests/integration/test_image_module_flow.py::test_commands_gated_when_disabled`: every `/images` command is rejected with an error naming `/module enable images`, and no config row is created or read (FR-005).

### Implementation for User Story 1

- [X] T014 [US1] Add `app_commands.Choice(name="images", value="images")` to `_MODULE_CHOICES` in `src/cogs/module_cog.py` and route it in both `enable` and `disable`.
- [X] T015 [US1] Implement `_enable_images` in `src/cogs/module_cog.py`: one transaction calling `create_with_defaults` if absent, setting `module_enabled = 1`, probing the converter, and posting confirmation to the log channel. Enabling succeeds even when the converter is absent, so an administrator can configure ahead of installing it.
- [X] T016 [US1] Implement `_disable_images` in `src/cogs/module_cog.py`: set `module_enabled = 0` and **nothing else** — no config deleted, no toggle reset, no notice history purged, no `--preserve-config` flag offered (FR-004a, FR-004b).
- [X] T017 [US1] Implement `converter_available()` in `src/services/image_render_service.py`: probe conventional install locations per platform and honour an `INKSCAPE` environment variable override. **Must not rely on PATH alone** — the development host has the binary installed with a broken PATH entry. Cache per process with a short TTL.
- [X] T018 [US1] Surface the converter's absence as a fatal, module-wide problem in `src/cogs/module_cog.py` (at enable time) and `src/cogs/image_cog.py` (wherever config is reported), naming the binary and stating that no package declaration installs it (FR-007, FR-008).

**Checkpoint**: The module can be enabled, disabled and re-enabled losing nothing. MVP complete.

---

## Phase 4: User Story 2 — Locate the Templates (Priority: P2)

**Goal**: An administrator points the bot at the template folder and individual files, and can see which resolve.

**Independent Test**: Point the directory at a folder that does not exist and confirm every template reports invalid against that one directory; restore it and confirm all fifteen report valid; rename one and confirm only that one is invalid.

**Note**: `/images config view` is delivered here in its value-listing form because it is the only way to observe this story. User Story 3 extends it with the aspect rollup.

### Tests for User Story 2

- [X] T019 [P] [US2] Unit test `tests/unit/test_image_validity_layers.py::test_layer1_failure_modes`: missing file, present-but-unparseable, and parseable-but-no-canvas each produce a **distinguishable** reason (FR-028c, US2 scenario 4).
- [X] T020 [P] [US2] Unit test `tests/unit/test_image_validity_layers.py::test_missing_directory_short_circuits`: when the template directory itself does not resolve, one directory-level reason is reported rather than fifteen file-not-found lines, and every template still receives a `ValidityReport`.

### Implementation for User Story 2

- [X] T021 [P] [US2] Create `src/utils/svg_document.py`: load and parse an SVG with `lxml`, and `canvas_of(root)` reading `width`/`height` off the root per Principle XIV.1. Pure — no DB, no Discord.
- [X] T022 [US2] Implement `ImageValidityService` in `src/services/image_validity_service.py`: the `ValidityLayer` protocol, the ordered `LAYERS` registry, and the evaluation loop that runs layers in order, skips those whose `applies_to` is false, stops at first failure, and records `depth_checked`. Per [contracts/validity-layers.md](./contracts/validity-layers.md).
- [X] T023 [US2] Implement `ResolutionLayer` (Layer 1) in `src/services/image_validity_service.py`: file resolves inside the configured directory, parses as well-formed SVG, root declares width and height. All three failure reasons mutually distinguishable; directory-level short-circuit per T020.
- [X] T024 [US2] Implement `/images config template-directory` in `src/cogs/image_cog.py`, tier A (`@channel_guard` + `@server_admin_only`), rejecting a path that escapes the project root via T005 and leaving the stored value unchanged on rejection (FR-010, FR-011).
- [X] T025 [US2] Implement the fifteen template filename commands in `src/cogs/image_cog.py`, tier A, per the table in [contracts/commands.md](./contracts/commands.md). Generate them from the constants in T003 rather than writing fifteen near-identical bodies. Reject a filename containing a path separator.
- [X] T026 [US2] Implement `/images config view` in `src/cogs/image_cog.py`, tier M (`@channel_guard` + `@admin_only`): module state, converter presence, template directory, all fifteen filenames with validity, and **the depth templates were checked to** (FR-028b). An invalid template shows its reason and the full path searched (FR-028).
- [X] T027 [US2] Have every template and directory setter in `src/cogs/image_cog.py` re-evaluate validity and include the result in its confirmation, so an administrator sees the effect without a second command (US2 scenarios 2 and 3).
- [X] T028 [P] [US2] Integration test in `tests/integration/test_image_module_flow.py::test_template_relocation`: changing one template's filename alters that template's validity and no other's (SC-002).

**Checkpoint**: Templates are locatable and their resolution is visible.

---

## Phase 5: User Story 3 — Review Configuration and Validity (Priority: P3)

**Goal**: A league manager sees the whole configuration and whether it holds together, and the same summary appears in the season review.

**Independent Test**: Misconfigure one template of each grouped aspect (weather, results, standings) and confirm the report names the exact template at fault, never the group.

**Depends on**: the eight toggle rows existing, which T004 creates at enable time. The command to *change* a toggle arrives in User Story 4; this story reads the stored state, and its tests set it directly.

### Tests for User Story 3

- [X] T029 [P] [US3] Unit test `tests/unit/test_image_validity_layers.py::test_specific_attribution`: with only `weather_p3_sprint_template` invalid, the report names phase 3 *and* the sprint variant, and reports the other five weather templates valid (FR-032, US3 scenario 3). Repeat for the qualifying/race and drivers/constructors pairs (scenarios 4 and 5).
- [X] T030 [P] [US3] Unit test `tests/unit/test_image_validity_layers.py::test_declared_depth`: every template reports `depth_checked == 1`, and the rendered view text contains the depth. A report omitting it fails (FR-028b, SC-009).
- [X] T031 [P] [US3] Unit tests `tests/unit/test_image_validity_layers.py::test_stable_surface` and `::test_no_silent_pass`: registering a synthetic Layer 2 in a fixture leaves the command surface and the `ValidityReport` field set unchanged; a template the synthetic layer does not apply to reports `depth_checked == 1` while others report `2`, and is not described as fully valid. These two are what stop a later session breaking the extension point.

### Implementation for User Story 3

- [X] T032 [US3] Implement `AspectStatus` computation in `src/services/image_validity_service.py`: aggregate 1, 2 or 6 `ValidityReport`s per aspect and derive the three states. `ENABLED_INVALID` when the toggle is on **and** any of — a backing template invalid, the source module disabled, or the converter absent (FR-031).
- [X] T033 [US3] Populate `blocking_reasons` in `src/services/image_validity_service.py` with one entry per cause, each naming the specific template or the specific module — never "weather is invalid" (FR-032).
- [X] T034 [US3] Extend `/images config view` in `src/cogs/image_cog.py` with the eight-aspect section using ✅ / ❌ / ⚠️ for the three states (FR-030, FR-031).
- [X] T035 [US3] Add the image section to `/season review` in `src/cogs/season_cog.py`, inside the existing `**Modules**` block that already lists weather, signup, results and attendance. Build it from the **same** `AspectStatus` list `/images config view` renders so the two cannot drift (FR-033). When the module is disabled, report it disabled and omit the detail (FR-034).

**Checkpoint**: Configuration and validity are fully reportable through both surfaces.

---

## Phase 6: User Story 4 — Choose Which Aspects Are Drawn (Priority: P4)

**Goal**: A league manager records, aspect by aspect, whether the bot should draw a graphic. In this increment the choice is stored, not acted on.

**Independent Test**: Toggle each of the eight aspects on and off in turn; confirm the stored state changes and is reflected in the view, independently of the others, and that no source module's output changes.

### Tests for User Story 4

- [X] T036 [P] [US4] Integration test `tests/integration/test_image_module_flow.py::test_toggles_are_inert`: with every aspect toggled on, output from each source module is byte-identical to a server that never enabled the module — no image, no partial image, no skipped post (FR-017a, SC-004). **This is the gate for the whole increment.**
- [X] T037 [P] [US4] Unit test `tests/unit/test_image_validity_layers.py::test_source_module_disabled`: an aspect toggled on while its source module is disabled reports `ENABLED_INVALID` with the disabled module named (US4 scenario 3).

### Implementation for User Story 4

- [X] T038 [US4] Implement `/images config toggle` in `src/cogs/image_cog.py`, tier M, with the eight static choices. Flips the stored value and confirms the new state.
- [X] T039 [US4] Make the confirmation say plainly that the toggle is **not yet in effect** and point at `/images test`, per the wording in [contracts/commands.md](./contracts/commands.md). A manager who enables an aspect and sees no change in the next post has been misled otherwise.

**Checkpoint**: All eight toggles settable, stored, reported — and provably inert.

---

## Phase 7: User Story 5 — Locate the Assets (Priority: P5)

**Goal**: An administrator points the bot at the seven asset folders.

**Independent Test**: Change each of the seven directories in turn and confirm its stored value and validity change independently of the other six.

### Tests for User Story 5

- [X] T040 [P] [US5] Integration test `tests/integration/test_image_module_flow.py::test_asset_directory_independence`: relocating one asset directory never alters the resolution of another (SC-002).

### Implementation for User Story 5

- [X] T041 [US5] Implement the seven asset directory commands in `src/cogs/image_cog.py`, tier A, per the table in [contracts/commands.md](./contracts/commands.md), each subject to the same containment rejection as the template directory (FR-016).
- [X] T042 [US5] Add asset-directory validity reporting to `src/services/image_validity_service.py` — directory resolves or does not — and surface it in `/images config view` in `src/cogs/image_cog.py` with the full path searched (FR-029).
- [X] T043 [US5] Confirm in each asset-directory setter's response in `src/cogs/image_cog.py` whether the new directory resolves, matching the pattern established in T027.

**Checkpoint**: All twenty-three path and filename settings are configurable and reportable.

---

## Phase 8: User Story 6 — Set Presentation Preferences (Priority: P6)

**Goal**: A league manager sets time zone, clock format, date format, and the fastest-lap colour, with legibility feedback on the colour.

**Independent Test**: Set each preference; supply a malformed colour and confirm rejection; supply a low-contrast colour and confirm it is accepted with a warning.

### Tests for User Story 6

- [X] T044 [P] [US6] Unit test `tests/unit/test_colour.py`: `A020F0` (no hash), `#A020F` (five digits), `#GGGGGG` (not hex) and `#A020F0A` (seven) are all rejected; upper and lower case six-digit values are accepted (FR-025).
- [X] T045 [P] [US6] Unit test `tests/unit/test_colour.py::test_contrast_ratio`: the WCAG contrast ratio of known pairs matches published values, and the 4.5:1 boundary classifies correctly either side.
- [X] T046 [P] [US6] Unit test `tests/unit/test_svg_document.py::test_computed_style_cascade`: a fill set as a presentation attribute, in an inline `style`, and in a `<style>` block keyed by class and by id each resolve to the right value, with inline winning.

### Implementation for User Story 6

- [X] T047 [P] [US6] Create `src/utils/colour.py`: parse `#RRGGBB` case-insensitively with exactly six digits, compute WCAG 2.x relative luminance, return a contrast ratio. Pure arithmetic.
- [X] T048 [US6] Extend `src/utils/svg_document.py` with the style cascade — `stylesheet(root)` and `computed_style(element)` — resolving presentation attribute, inline `style` and `<style>` block. Shared with the recolour operation in T057, which needs the same resolution.
- [X] T049 [US6] Implement `/images config fastest-lap-colour` in `src/cogs/image_cog.py`, tier M, in this order: reject malformed input leaving the stored value untouched (FR-025); **store the value**; then measure and report contrast against the background element located by its documented `@id` in the race results template (FR-026a); warn below 4.5:1 without blocking (FR-026). Storing before measuring is what stops an unmeasurable contrast costing the manager their input.
- [X] T050 [US6] Handle the unmeasurable case in `src/cogs/image_cog.py`: when the race results template is invalid or the documented background element is absent, store the colour and say the contrast could not be measured **and why**, rather than omitting or guessing a ratio (FR-027).
- [X] T051 [P] [US6] Implement `/images config time-zone` in `src/cogs/image_cog.py`, tier M, with autocomplete over `zoneinfo.available_timezones()` filtered by typed prefix and truncated to 25. Store the IANA name so the offset resolves against the displayed date, not the configuration date (FR-021).
- [X] T052 [P] [US6] Implement `/images config time-format` and `/images config date-format` in `src/cogs/image_cog.py`, tier M. Date-format choices display a worked example (`Sun 14 Jun 2026`) rather than the token, and the weekday-carrying format is the default (FR-022, FR-023).

**Checkpoint**: Every one of the 35 configuration values is settable.

---

## Phase 9: User Story 7 — Test a Render (Priority: P7)

**Goal**: A league manager can see what any of the eleven kinds of image produces, from sample data, before committing to an aspect.

**Independent Test**: Run the test command for each of the eleven kinds and confirm each returns either an image or a clear account of why it could not be produced — on a server with no season at all.

**This is the largest phase.** The engine is built here because nothing before it needs a render. Per research R1 it is built against Constitution Principle XIV directly; the nine invariants below are where a plausible implementation goes subtly wrong.

### Tests for User Story 7 — the nine Principle XIV invariants

- [X] T053 [P] [US7] Unit test `tests/unit/test_svg_fill.py` invariants 1–3 (recolour): a recolour **merges** into inline style and preserves other declarations on the same element; it is written **inline** and so beats the template's own stylesheet; and it **does not consume the field**, which must still appear in `unresolved` if not also filled (XIV.2).
- [X] T054 [P] [US7] Unit test `tests/unit/test_svg_fill.py` invariants 4–6 (canvas and crop): two templates declaring different sizes each render at their own (XIV.1); the crop rewrites both root `height` and `viewBox` rather than delegating to the rasteriser's export area; and a field below the crop point that was never addressed does **not** fail the render (XIV.2).
- [X] T055 [P] [US7] Unit test `tests/unit/test_svg_fill.py` invariants 7–9 (text bounds): the wrap descends by half a pixel until it fits; at the floor of **half** the declared size it cuts at a word boundary with an ellipsis and raises `WRAP_TRUNCATED`; line height scales with the reduced size and the admissible line count is **recomputed at the reduced leading**, so the count at the floor exceeds the count at full size; and an over-long single-line field is cut at a word boundary, ellipsised, and raises `INLINE_SIZE_TRUNCATED` (XIV.5).
- [X] T056 [P] [US7] Unit test `tests/unit/test_font_metrics.py`: a missing first-choice family measures against the substitute and raises `FONT_SUBSTITUTED` as a **notice, never a problem**; the index is built once per process; measurement of known-width strings in a known face is exact enough that a line declared to fit does fit.

### Implementation for User Story 7

- [X] T057 [P] [US7] Create `src/utils/font_metrics.py`: index installed faces via `fontTools`, resolve a CSS `font-family` list to the face a renderer would land on, measure a string by summing advance widths, cache the index for the process lifetime.
- [X] T058 [US7] Create `src/utils/svg_fill.py` with the six operations of Principle XIV.2 and **no others**: text fill, image fill, recolour (merged into inline style), group removal, vertical crop, text wrap. Returns `FillResult` with `unresolved` and `notices`; **raises nothing** for a data disagreement.
- [X] T059 [US7] Implement the wrap and bound logic in `src/utils/svg_fill.py` per XIV.5 — half-pixel descent, half-size floor, line height scaling with the reduced size, line count recomputed at the reduced leading, word-boundary cut with ellipsis, and a notice for each truncation.
- [X] T060 [US7] Implement `rasterise()` in `src/services/image_render_service.py` invoking the Inkscape CLI located by T017, writing to a temporary file and returning its path.
- [X] T061 [US7] Implement `async def render(...)` in `src/services/image_render_service.py` returning `RenderOutcome`. **Call `rasterise` via `asyncio.to_thread`** (research R2) — a blocking subprocess on the event loop stalls the scheduler, the retry worker and every in-flight interaction, passes every unit test, and degrades the whole bot in production.
- [X] T062 [US7] Enforce the problem/notice split in `RenderOutcome` in `src/services/image_render_service.py`: `png_paths` is empty whenever `problem` is set, so no caller can receive a partial image or mistake a degraded render for a clean one (XIV.4). Treat output exceeding Discord's 25 MB attachment limit as a problem rather than letting the upload fail unhandled.
- [X] T063 [US7] Persist every notice to the `image_render_notices` table from `src/services/image_render_service.py` and post it to the calculation log channel via the existing `OutputRouter.post_log` in `src/utils/output_router.py` (Principle V, XIV.4).
- [X] T064 [P] [US7] Create `src/services/image_sample_data.py` with one dataset per test kind. **Reads nothing live** — no season, division, round, team or driver query (FR-036). Include a name long enough to trip `inline-size` and prose long enough to reach the wrap floor, so the diagnostic exercises the notice path.
- [X] T065 [US7] Implement `/images test` in `src/cogs/image_cog.py`, tier M, with the eleven kind choices: `defer(ephemeral=True)` first, reject immediately when the converter is absent naming it as the reason and attempting no render (FR-009), then render and `followup.send`.
- [X] T066 [US7] Return **every variant** the kind covers from `/images test` in `src/cogs/image_cog.py` — two for `results`, `standings`, `weather-p2` and `weather-p3`; one otherwise (FR-040).
- [X] T067 [US7] In `src/cogs/image_cog.py`, on success attach the PNG(s) and list every notice alongside (FR-038); on a problem return no image and state the specific reason (FR-039).
- [X] T068 [P] [US7] Integration test `tests/integration/test_image_module_flow.py::test_render_without_season`: every one of the eleven kinds renders on a server with no season configured (FR-036, SC-005). If any test render touches live data this fails.

**Checkpoint**: All seven user stories independently functional.

---

## Phase 10: Polish & Cross-Cutting Concerns

- [X] T069 [P] Update `README.md` "Module Commands": add `images` to the module list and document the 26 new commands. **Also add the missing `attendance` entry** — that section still names only weather, signup and results, and has been stale since the Attendance module was ratified at constitution v2.10.0.
- [X] T070 [P] Propose the PATCH constitution amendment in `.specify/memory/constitution.md` renaming `ImageTypeState` to `ImageAspectToggle` and re-graining it from image type to aspect, per the plan's Complexity Tracking. The spec is authoritative on the command surface; the constitution should match what was built.
- [ ] T071 Reconcile the command surface, the sample data and the field catalogue against `docs/wip-specs/image_module_specification.md` (research R11). Blocked while `docs/wip-specs/**` is deny-listed in `.claude/settings.json`. **The divergence is known and concrete, not hypothetical** — Discord's 25-subcommand ceiling forced the fifteen template setters from the specified `/images config <name>-template` to `/images template <name>` (FR-012a), so the built surface already differs from the written spec. Specific items to check:
  - **Command surface** — the `/images template` split above, and whether any other command name, parameter or default differs from the spec.
  - **`fastest_lap_background`** — this element id was invented, not taken from the spec. If the real templates name it differently, the contrast check in `/images config fastest-lap-colour` silently reports "could not be measured" against every template (FR-026a, FR-027).
  - **Sample data** — `src/services/image_sample_data.py` fills whatever ids a template declares, rather than the per-type test data the spec defines.
  - **Field catalogue** — needed by Layer 2 of the validity contract, and the reason the depth is still declared as layer 1 only.

  Anything the spec requires that this feature omits becomes a new task.
- [X] T072 [P] Verify every `/images` command response is ephemeral (FR-044) and every mutation posts to the log channel (Principle V), by inspection across `src/cogs/image_cog.py`.
- [ ] T073 Run the full [quickstart.md](./quickstart.md) — all eight scenarios — against a scratch server, including the converter-absent case for SC-007.
- [X] T074 Verify the renders produced by the Scenario 7 runs in [quickstart.md](./quickstart.md) by inspecting the **PNG**, not the SVG. A field overflowing its `inline-size` or wrapping past its floor can look acceptable in a browser and be visibly clipped in the rasterised output.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: depends on Setup. **Blocks every user story.**
- **US1 (Phase 3)**: depends on Foundational. Nothing else depends on it except through the enabled flag.
- **US2 (Phase 4)**: depends on Foundational. Introduces `svg_document.py` and `/images config view`.
- **US3 (Phase 5)**: depends on US2 — extends the validity service and the view command.
- **US4 (Phase 6)**: depends on Foundational for the toggle rows; on US3 to observe the third state.
- **US5 (Phase 7)**: depends on US2 for the validity service and the view command.
- **US6 (Phase 8)**: depends on US2 for `svg_document.py`, which T048 extends.
- **US7 (Phase 9)**: depends on US2 (`svg_document.py`) and US6 (the style cascade in T048).
- **Polish (Phase 10)**: depends on the stories being delivered.

### Real dependency notes

Three couplings are worth stating because they are not obvious from priority order:

1. **US3 needs toggle rows, not the toggle command.** The eight rows are created at enable time by T004, so US3 can compute and report the three states before `/images config toggle` exists. Its tests set toggle state directly.
2. **US6 and US7 share the style cascade.** T048 exists for the contrast check and is reused by the recolour operation in T058 — Principle XIV.2 requires recolour be inline precisely because a presentation attribute loses to the stylesheet, so both need the same resolution.
3. **US2 is on the critical path.** US3, US5, US6 and US7 all build on `svg_document.py` or the validity service. US1 is the only story fully independent of it.

### Within Each User Story

- Tests are written first and must fail before implementation.
- Models and pure utilities before services; services before cog commands.
- Story complete and its checkpoint validated before moving to the next priority.

### Parallel Opportunities

- T001–T003 (Setup) are fully parallel.
- T005, T009, T010 within Foundational are parallel.
- T011–T013 (US1 tests) are parallel.
- T053–T056 (the nine invariant tests) are parallel and are the highest-value tests in the feature.
- US5 and US6 are independent of each other and can be built in parallel once US2 lands.

---

## Parallel Example: User Story 7 invariant tests

```bash
# The four test tasks covering the nine Principle XIV invariants, all in different files
# or different test functions with no shared fixture state:
Task: "T053 recolour invariants 1-3 in tests/unit/test_svg_fill.py"
Task: "T054 canvas and crop invariants 4-6 in tests/unit/test_svg_fill.py"
Task: "T055 text bound invariants 7-9 in tests/unit/test_svg_fill.py"
Task: "T056 font substitution and measurement in tests/unit/test_font_metrics.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1: Setup.
2. Phase 2: Foundational.
3. Phase 3: User Story 1.
4. **STOP and VALIDATE**: enable, configure nothing, disable, re-enable — nothing lost. Confirm the
   converter's presence or absence is reported honestly.

That is a genuinely useful increment on its own: an administrator can tell whether the host is
capable of image generation at all, which is the question they cannot currently answer.

### Incremental Delivery

1. Setup + Foundational → scaffold ready.
2. US1 → **MVP**: module lifecycle and prerequisite reporting.
3. US2 → templates locatable, validity visible.
4. US3 → full reporting through both surfaces.
5. US4 → the league's choice recorded.
6. US5 + US6 → assets and presentation (parallelisable).
7. US7 → the engine and the diagnostic. The largest phase; deliver it last and alone.

### The two gates that matter

- **T036** — with every aspect toggled on, output is byte-identical to a server that never enabled
  the module. If this fails, the increment has changed live output, which it was scoped not to do.
- **T053–T056** — the nine Principle XIV invariants. These are where a plausible engine is quietly
  wrong, and they are what a later session extending the module will rely on.

---

## Notes

- `[P]` = different files, no dependency on incomplete work.
- `[Story]` maps each task to its user story for traceability.
- Verify tests fail before implementing.
- Commit after each task or logical group.
- Stop at any checkpoint to validate a story independently.
- The contents of `resources/` are not an input to this work; the module resolves configured paths
  and reports what it finds.
