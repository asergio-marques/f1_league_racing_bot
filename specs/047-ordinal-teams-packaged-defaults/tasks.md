---

description: "Task list for 047 — ordinal addressing of teams, and packaged asset defaults"
---

# Tasks: Ordinal addressing of teams, and packaged asset defaults

**Input**: Design documents from `/specs/047-ordinal-teams-packaged-defaults/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/)

**Tests**: MANDATORY. Every implementation task below either names its test or depends on a test task written immediately before it. No task parks its first coverage in the polish phase, and that test must pass before the next task begins.

**Live Discord is out of scope**: no task here requires a running bot, a gateway connection or a real server. Every test runs under `pytest` with Discord stubbed.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US5, mapping to the user stories in [spec.md](spec.md)

## Path Conventions

Single project: `src/` and `tests/` at the repository root; `resources/` holds what ships.

## A note on phase order

Phases do **not** run in strict priority order, and this is deliberate. US4 (packaged relocation, P3) is a **prerequisite** of US3 (two-tier fallback, P2): the second tier has no directory to name until the relocation defines one. US4 therefore runs before US3. Priorities still express value — US4 alone delivers little — but the build order follows the dependency.

Order: **US1 → US4 → US3 → US2 → US5**.

---

## Phase 1: Setup

**Purpose**: Establish the reference the whole change is measured against.

- [X] T001 Run `pytest tests/ -q` from the repository root and record the passing count in the PR description as the pre-change baseline (expected: 2442 passed)
- [X] T002 Run `pytest tests/ -q -m rasteriser` on a host with Inkscape and record the result, so a rasteriser break introduced later is attributable

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Break the `capacity()` coupling before anything gives the lineup a rows spec.

**⚠️ CRITICAL**: T004 must land before US1 touches `LINEUP_CATALOGUE`, or the reserve block silently starts counting team slots (research **R1**).

- [X] T003 Add a failing test in `tests/unit/test_image_lineup_catalogue.py` asserting that the reserve slot count is read independently of any team-block count — build a template declaring 11 team blocks and 6 reserve slots, and assert the reserve capacity reads 6
- [X] T004 Add `FieldCatalogue.singleton_capacity(root)` to `src/models/image_catalogues.py` returning the singleton's nested count explicitly, and amend `capacity()`'s docstring to drop the claim that it serves the lineup's reserve (depends on T003)
- [X] T005 Repoint `reserve_capacity_problem` in `src/models/image_catalogues.py` and the `reserve_slots` read in `src/services/image_lineup_service.py` at `singleton_capacity(root)` (depends on T004)

**Checkpoint**: `pytest tests/unit/test_image_lineup_catalogue.py tests/unit/test_image_lineup_service.py -q` green. User story work can begin.

---

## Phase 3: User Story 1 — Draw a lineup from the template that ships (Priority: P1) 🎯 MVP

**Goal**: A league leaves the lineup template at its default and posts a lineup for a division of any composition within the shipped capacity, having authored no template.

**Independent Test**: Resolve a division of named teams against a template declaring ordinal blocks; assert team 1 fills block 1, team 2 fills block 2, each block carries the name and badge slug of the team at its ordinal, and no team name appears in any field identifier.

### Tests for User Story 1 (MANDATORY) ⚠️

> Write these first and see them fail before the implementation task each covers.

- [X] T006 [P] [US1] Rewrite `tests/unit/test_image_lineup_catalogue.py` for the ordinal collection: blocks contiguous from 1, a gap fatal, `team_<x>_name` and `team_<x>_driver_<y>_name` mandatory throughout, `team_<x>_group` optional, per-block seat counts read independently
- [X] T007 [US1] Rewrite `tests/unit/test_image_lineup_service.py` for ordinal resolution: position→ordinal mapping, a team that recruited nobody drawn with blank seats, an ordinal with no team removed, the reserve never at a `team_<x>_` ordinal
- [X] T008 [US1] Rewrite `tests/unit/test_image_lineup_fill.py` for the two removal paths — `team_<x>_group` removed whole where declared, every field of that ordinal removed one by one where it is not
- [X] T009 [US1] Add overflow tests to `tests/unit/test_image_lineup_service.py`: more teams than blocks names the **teams** dropped; more **seated drivers** than a block's slots names the drivers dropped; a team configured with more seats than the block declares but seating no more drivers than it draws **without** error
- [X] T010 [P] [US1] Add a test in `tests/unit/test_image_catalogues_nested_ceiling.py` proving the lineup's seats and the constructors grid's cars behave **identically** under FR-018 — over-declaration silent in both, fatal only where data actually drawn would be dropped in both
- [X] T011 [US1] Add a test in `tests/unit/test_image_lineup_fill.py` distinguishing the two empty cases: a slot beyond the team's configured seats is **removed**, while a slot within them that no driver occupies is **drawn unoccupied**
- [X] T012 [P] [US1] Add an ordering test to `tests/unit/test_lineup_post_service.py` — seed a division whose teams are added **out of alphabetical order**, add one whose name sorts first, assert every existing team keeps its ordinal (research **R5**; a test seeding alphabetically proves nothing). Assert the same order from all three readers FR-009 names: the posting path, the preview path and `TeamService.get_division_teams`
- [X] T013 [US1] Add test-mode and pending-approval coverage to `tests/unit/test_image_preview_testmode.py` and `tests/unit/test_image_lineup_service.py`: a mock driver drawn by its mock name at its team's ordinal, never as an unoccupied seat; a division seated wholly by mock drivers counted as having seated drivers; a season pending approval drawing what an approved season of identical composition draws

### Implementation for User Story 1

- [X] T014 [US1] Replace `keyed=KeyedSpec(...)` with `rows=RowSpec(prefix="team", nested=NestedSpec(prefix="driver", capacity_per_member=True, ...))` in `LINEUP_CATALOGUE` in `src/models/image_catalogues.py` — the seats are a **ceiling**, as the results grid's cars already are (FR-018) (depends on T006, T010)
- [X] T015 [US1] Delete `KeyedSpec`, `LineupBinding`, `FieldCatalogue.keyed` and `FieldCatalogue.divergent_members` from `src/models/image_catalogues.py`, and drop the `binding=` parameter from `all_mandatory_ids`, `all_known_ids` and `valueless_ids` (depends on T014)
- [X] T016 [US1] Remove the `binding` field from `FillSpec` in `src/utils/svg_fill.py` and the `divergent_members` call site from `src/services/image_render_service.py` (depends on T015)
- [X] T017 [US1] Rewrite `resolve_drawing` in `src/services/image_lineup_service.py`: assign each non-reserve team its 1-based ordinal, drop the three key-collision `LineupDataError` raises, remove `LineupTeam.key` in favour of `ordinal`, and delete `LineupDrawing.binding` (depends on T007, T016)
- [X] T018 [US1] Rewrite `build_fill_spec` in `src/services/image_lineup_service.py` to address `team_<x>_*`, remove an ordinal the division fields no team at by `team_<x>_group` or field by field, and raise `LineupDataError` naming the drivers where a team's seats exceed that block's slots (depends on T008, T009, T017)
- [X] T019 [US1] Set `row_count` on the lineup's `FillSpec` in `src/services/image_lineup_service.py` to the division's team count, so team overflow reports through the generic guard in `src/services/image_render_service.py` naming the teams (depends on T018)
- [X] T020 [US1] Delete `binding_from_teams` and `divergences` from `src/services/image_lineup_service.py` and `suppressed_flag_fields`' key-based field ids in favour of ordinals (depends on T017)
- [X] T021 [US1] Change the team query to `ORDER BY is_reserve ASC, id ASC` in `src/services/image_lineup_post.py`, `src/services/image_preview_service.py` and `TeamService.get_division_teams` in `src/services/team_service.py` (depends on T012)
- [X] T022 [US1] Redraw the shipped lineup template — `resources/templates/lineup_template.svg`, or `resources/defaults/templates/lineup_template.svg` where US4 has already landed: rename the 88 team ids and their `inkscape:label` attributes to ordinals, and wrap each block's eight elements in `<g id="team_<x>_group">` — the file has **no** per-team group today (research **R4**). Keep 11 blocks × 2 seats and the 6-slot reserve
- [X] T023 [US1] Add a test in `tests/unit/test_image_lineup_catalogue.py` asserting the shipped template declares 11 blocks, 2 slots per block, 6 reserve slots, a `team_<x>_group` per block, and **no** identifier or label naming any team (depends on T022)
- [X] T024 [US1] Update `tests/unit/test_image_preview_lineup.py`, `tests/unit/test_lineup_validity.py`, `tests/unit/test_image_validity_layers.py` and `tests/support/image_sample_data.py` for ordinal fields (depends on T018)

**Checkpoint**: `pytest tests/ -q` green. A lineup draws from the shipped template for a division of 1 to 11 teams.

---

## Phase 4: User Story 4 — Packaged assets live under a defaults directory (Priority: P3, built early)

**Goal**: Everything that ships sits under `resources/defaults/`, so a maintainer can tell it from what a league added.

**Independent Test**: Assert every `images config *-directory` default and the template-directory default names its `resources/defaults/` location, and that every packaged file is present at the new path.

**⚠️ Built before US3** because the second fallback tier has no directory to name until this lands.

### Tests for User Story 4 (MANDATORY) ⚠️

- [X] T025 [US4] Add a test in `tests/unit/test_paths.py` asserting all eight packaged directories resolve under `resources/defaults/` and that each of the seven asset classes holds its `fallback.svg` there
- [X] T026 [US4] Add a test asserting the closed-set files ship at the new paths — three direction markers, eight weather icons, `mystery.svg` in both the track and flag directories — in `tests/unit/test_paths.py`

### Implementation for User Story 4

- [X] T027 [US4] `git mv` the seven asset directories and `templates/` from `resources/` to `resources/defaults/`, preserving history (depends on T025, T026)
- [X] T028 [US4] Repath the eight defaults in **both** tables in `src/models/image_constants.py` (they appear twice — around lines 162 and 237) (depends on T027)
- [X] T029 [US4] Add `packaged_directory_for(asset_class)` to `src/models/image_constants.py`, derived from the same table as the defaults so the two cannot drift, with a test in `tests/unit/test_paths.py` covering every class and an unknown class (depends on T028)
- [X] T030 [US4] Repath the fifteen test files that name old `resources/` paths: `tests/integration/test_image_module_flow.py`, `tests/support/image_sample_data.py`, `tests/unit/test_calendar_post_service.py`, `test_calendar_validity.py`, `test_image_config_service.py`, `test_image_directory_faults.py`, `test_image_preview_calendar.py`, `test_image_preview_lineup.py`, `test_image_preview_render.py`, `test_image_preview_service.py`, `test_image_validity_layers.py`, `test_image_verdicts_validity.py`, `test_image_weather_validity.py`, `test_paths.py`, `test_svg_fill.py` (depends on T028)

**Checkpoint**: `pytest tests/ -q` green with everything shipped under `resources/defaults/`.

---

## Phase 5: User Story 3 — An incomplete asset set survives without a hand-placed fallback (Priority: P2)

**Goal**: A league supplying badges for some of its teams draws every graphic, the rest falling back to the packaged image with a notice, having placed no `fallback.svg` of its own.

**Independent Test**: Resolve a datum with no file against a configured directory holding no fallback, with a packaged directory that holds one; assert the packaged fallback is returned as the non-fatal fallback outcome, not the missing one.

**Depends on**: US4 (T029 supplies the packaged directory).

### Tests for User Story 3 (MANDATORY) ⚠️

- [X] T031 [US3] Extend `tests/unit/test_asset_resolver.py` to cover all four paths of [contracts/asset-resolution.md](contracts/asset-resolution.md), including the negative: a configured directory lacking the datum's file while the packaged directory holds a file of **exactly that name** must resolve to the packaged *fallback*, never to that file
- [X] T032 [US3] Add a test in `tests/unit/test_asset_resolver.py` asserting the notice reported for a packaged-tier fallback is identical to the configured-tier one, and that `from_packaged` is the only thing distinguishing them
- [X] T033 [P] [US3] Add a test in `tests/unit/test_svg_fill.py` asserting the fill pipeline passes the packaged directory for the field's asset class
- [X] T034 [P] [US3] Add a per-graphic sweep in a new `tests/unit/test_packaged_fallback_per_graphic.py` proving **each** of the seven graphics that draw a team badge — lineup, both results, both standings, attendance, verdict — draws a packaged-tier fallback for a team whose badge the configured directory lacks (FR-045; the resolver alone does not satisfy this). Include one invocation through an `/images test` preview path, so FR-051 is asserted rather than inherited from the shared call site

### Implementation for User Story 3

- [X] T035 [US3] Widen `resolve_asset(directory, datum, *, packaged=None)` in `src/utils/asset_resolver.py` with the third path, and add `from_packaged: bool` to `AssetResolution`, keeping `AssetOutcome` at three values (research **R7**) (depends on T031, T032)
- [X] T036 [US3] Widen `has_fallback(directory, *, packaged=None)` in `src/utils/asset_resolver.py` so no single-directory fallback predicate remains (depends on T035)
- [X] T037 [US3] Pass `packaged=packaged_directory_for(asset_class)` at the single `resolve_asset` call site in `src/utils/svg_fill.py` (depends on T033, T035)
- [X] T038 [US3] Verify the absent-datum branch in `src/utils/svg_fill.py` — the tyre case — reaches the packaged tier too, and add a test for a tyre-less entry with an empty configured tyre directory in `tests/unit/test_svg_fill.py` (depends on T037)
- [X] T039 [US3] Reword the stale keyed rationale in the module docstring of `src/services/image_preview_league.py` and the `REASON_NO_SERVER_TEAMS` comment in `src/services/image_preview_service.py`, keeping the refusal itself; assert the retained behaviour in `tests/unit/test_image_preview_league.py` (FR-052)

**Checkpoint**: A league with a partial badge set draws all seven graphics without placing a fallback.

---

## Phase 6: User Story 2 — Run divisions that differ in composition (Priority: P2)

**Goal**: A season whose divisions field different teams and different numbers of them passes review and draws.

**Independent Test**: Review a season whose two divisions differ in composition; assert no divergence is reported on that account, while a season whose largest division exceeds the template's blocks still fails validation naming the division and the teams at fault.

**Depends on**: US1.

### Tests for User Story 2 (MANDATORY) ⚠️

- [X] T040 [US2] Add tests in `tests/unit/test_season_approval_gates.py`: divisions differing in teams and in team count pass review; a division exceeding the template's blocks fails validation naming the division and the teams
- [X] T041 [US2] Add a test in `tests/unit/test_season_approval_gates.py` asserting the count check runs with the `lineup` toggle **off** — it reports a template that cannot draw the season and is not gated
- [X] T042 [US2] Add a test asserting `season review` attaches the lineup graphic where the season passes, and falls back to the textual lineup reporting a **failure of validation** — not a failure to render — where an excess makes it fatal, in `tests/unit/test_season_approval_gates.py`

### Implementation for User Story 2

- [X] T043 [US2] Rewrite `_lineup_problems` in `src/cogs/season_cog.py`: delete the divisions-against-each-other uniformity check and its message, recast the per-division check as a count measurement against the template, and remove the module-and-toggle gate (depends on T040, T041)
- [X] T044 [US2] Make `_post_review_lineup_image` in `src/cogs/season_cog.py` report a fatal excess as a validation failure with a textual fall back (depends on T042, T043)

**Checkpoint**: A season of unlike divisions reviews and draws.

---

## Phase 7: User Story 5 — Name a team as the league pleases (Priority: P3)

**Goal**: "2Fast Motorsport" is accepted; the four remaining refusals still bite.

**Independent Test**: Validate a set of names and assert a leading digit passes while empty, empty-when-normalised, colliding and `reserve`-normalising names are still refused.

**Independent of US1–US4** — it may be built at any point after Phase 2. Its tests deliberately avoid the per-graphic sweep file US3 creates, so nothing here waits on another story.

### Tests for User Story 5 (MANDATORY) ⚠️

- [X] T045 [P] [US5] Extend `tests/unit/test_team_name_validation.py`: a leading digit is accepted; the four remaining criteria still refuse; only the **new** name of `team rename` is validated; `team remove` validates nothing
- [X] T046 [US5] Add a test in `tests/unit/test_asset_resolver.py` asserting a team name beginning with a digit normalises to a valid badge filename (`2Fast Motorsport` → `2fast_motorsport.svg`) and resolves through the ordinary path

### Implementation for User Story 5

- [X] T047 [US5] Remove the `key[0].isalpha()` branch from `validate_team_name` in `src/services/team_service.py` and reword the remaining diagnostics from "XML identifier" to filename terms (depends on T045)
- [X] T048 [US5] Update `tests/unit/test_team_service.py` and `tests/unit/test_team_cog.py` for the relaxed rule and the reworded messages (depends on T047)
- [X] T049 [US5] Add a test in `tests/unit/test_team_name_validation.py` covering the `season review` path — `_team_name_problems` in `src/cogs/season_cog.py` still names every offending team, still runs ungated, and still leaves an approved season alone — and correct that method's docstring, which states the withdrawn "cannot become a lineup field identifier" rationale (depends on T047)

**Checkpoint**: All five stories independently functional.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T050 [P] Rewrite `resources/README.md` for the new split between what ships under `defaults/` and where a league puts its own artwork
- [X] T051 [P] Update `README.md`: the `resources/defaults/` paths, the relaxed team-name rule, that divisions may now differ, and that a league need no longer supply a `fallback.svg`
- [X] T052 [P] Update `docs/how-to/configuring-the-image-module.md` for the new default paths, the two-tier fallback, and authoring a lineup template against ordinals
- [X] T053 Re-read `docs/wip-specs/image_module_specification.md` against what was built and correct any divergence — it is the source of these rules and already carries them
- [X] T054 Record in `docs/wip-specs/known_issues.md` anything found in passing and deliberately left unfixed
- [X] T055 Run `pytest tests/ -q` and compare against the T001 baseline; the total will **fall** as the keyed tests go, which is correct — a fall in coverage percentage is not
- [X] T056 Run `coverage run -m pytest tests/ -q -m "not rasteriser" && coverage report` and confirm line coverage is at or above `MIN_COVERAGE_REQUIRED` in `.github/workflows/unit-test.yml`
- [X] T057 Run `pytest tests/ -q -m rasteriser` on a host with Inkscape and work through §7 of [quickstart.md](quickstart.md), inspecting each render **as a PNG** — never as SVG in a browser
- [X] T058 Invoke the `close-out` skill

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: blocks US1; T004 must precede T014 or the reserve block silently miscounts
- **US1 (Phase 3)**: after Phase 2. Blocks US2
- **US4 (Phase 4)**: after Phase 2. Blocks US3. Independent of US1
- **US3 (Phase 5)**: after US4 (needs `packaged_directory_for`)
- **US2 (Phase 6)**: after US1
- **US5 (Phase 7)**: after Phase 2; independent of every other story
- **Polish (Phase 8)**: after all stories

### Story Dependency Graph

```text
Setup ──> Foundational ──┬──> US1 ──> US2 ──┐
                         │                   ├──> Polish
                         ├──> US4 ──> US3 ───┤
                         │                   │
                         └──> US5 ───────────┘
```

### Parallel Opportunities

Parallelism here is modest, and deliberately so: most test tasks within a story share a file, and two tasks editing one file are not parallel however convenient the label would be. Only these carry `[P]`:

- **T006, T010, T012** (US1 tests) — catalogue, nested-ceiling and ordering, three separate files
- **T034** — the per-graphic sweep, its own new file
- **T045** — team-name validation, its own file
- **T050, T051, T052** (documentation) — three separate documents
- **Whole branches**: this is where the real concurrency is. US1 and US4 are independent after Phase 2 and may be built at the same time, as may US5 at any point. US4 → US3 and US1 → US2 are the only cross-story chains

## Parallel Example: User Story 1

```text
# After Phase 2, three US1 test tasks genuinely run together:
T006  tests/unit/test_image_lineup_catalogue.py
T010  tests/unit/test_image_catalogues_nested_ceiling.py
T012  tests/unit/test_lineup_post_service.py

# These share files and must be sequenced, not parallelised:
T007 -> T009 -> T013   (all tests/unit/test_image_lineup_service.py)
T008 -> T011           (both tests/unit/test_image_lineup_fill.py)

# Then the implementation chain, which is mostly sequential:
T014 -> T015 -> T016 -> T017 -> T018 -> T019
T021 and T022 run alongside that chain (different files)
```

## Implementation Strategy

**MVP = Phase 1 + Phase 2 + Phase 3 (US1).** That alone delivers the headline: a league draws a lineup from a shipped template without authoring one. It is also the largest and riskiest slice, and everything else is small beside it.

**Second increment: US4 + US3.** The two-tier fallback is what a league notices next, and the relocation is its prerequisite.

**Then US2 and US5,** either order, both small.

Do not defer T022 (the template redraw) to the end of US1: several of the story's tests are only meaningful against a template that declares ordinals, and the redraw is the task most likely to surface a problem with the catalogue.

## Total

**58 tasks** — 2 setup, 3 foundational, 19 US1, 6 US4, 9 US3, 5 US2, 5 US5, 9 polish.
