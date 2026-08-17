# Tasks: Track Imagery Split

**Feature**: `044-track-imagery-split`
**Input**: [spec.md](./spec.md), [plan.md](./plan.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)
**Date**: 2026-08-17

## Format: `[ID] [P?] [Story] Description`

- **[P]** — parallelisable: different file, no dependency on an incomplete task
- **[US1]–[US5]** — the user story from spec.md the task serves
- Every implementation task carries its unit test. **No task requires a live Discord bot.**

## Path Conventions

Single project. Source under `src/{models,services,cogs,utils,db}`, tests under `tests/unit/`,
shipped artwork and templates under `resources/`. `poc/` is out of scope and untouched throughout.

---

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 Record the baseline: run `pytest tests/ -q` from the repo root and note the count (expected 1995 passed, 1 skipped). Every later phase compares against this.
- [X] T002 Confirm the Inkscape rasteriser resolves, by full path or the `INKSCAPE` environment variable, so the PNG verification tasks can run.

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ Blocks every user story. R-001 is settled: the country vocabulary is the seed's.**

- [X] T003 Create `src/utils/country_data.py` with `NATIONALITY_COUNTRIES`, a total map from each canonical nationality adjective to its country name, spelled exactly as `tracks.country` spells it (`British` → `United Kingdom`, every US nationality → `United States of America`). Include `Other` → `Other`.
- [X] T004 Create `tests/unit/test_country_data.py` covering data-model V-1 totality (every value of `NATIONALITY_LOOKUP` is a key here), V-2 consistency (every country yielded, lowercased and excepting `Other`, is a key of `NATIONALITY_LOOKUP`), V-3 track coverage (every distinct `tracks.country` in migration 029 is reachable), and V-4 slug stability (one country yields one slug whichever path asked). Depends on T003.
- [X] T005 [P] Add `ASSET_CLASS_ASPECTS` to `src/models/image_constants.py`, keyed by asset class exactly as `ASSET_CLASS_DIRECTORIES` is: `flag` 3:2, every other class 1:1.
- [X] T006 [P] Extend `tests/unit/test_asset_resolver.py` to assert `ASSET_CLASS_ASPECTS` covers every key of `ASSET_CLASS_DIRECTORIES`, so a class added later cannot silently escape the aspect check. Depends on T005.

**Checkpoint**: the vocabulary and the aspect table exist and are proven. `pytest tests/ -q` matches T001's baseline plus the new tests.

---

## Phase 3: User Story 1 — One flag directory, keyed by country (Priority: P1) 🎯 MVP

**Goal**: every flag on every graphic resolves from one directory under a country name.

**Independent test**: point the flag directory at a folder holding `united_kingdom.svg` and `other.svg`, generate a lineup for a division holding one `British` driver and one who stated none, and confirm the first draws `united_kingdom.svg`, the second `other.svg`, and neither raises a notice.

### Tests for User Story 1 (MANDATORY) ⚠️

- [X] T007 [P] [US1] Extend `tests/unit/test_image_lineup_service.py`: a `British` driver emits the flag datum `United Kingdom`; a driver recorded `Other` emits `Other`; an unmapped country draws `fallback.svg` with exactly one notice naming field and country; a league with nationality collection off emits no flag datum and raises nothing.
- [X] T008 [P] [US1] Extend `tests/unit/test_image_results_service.py` and `tests/unit/test_image_verdicts_service.py` for the same rekey, these two types drawing a driver flag but picturing no round.

### Implementation for User Story 1

- [X] T009 [US1] Re-point the driver flag datum in `src/services/image_lineup_service.py` from the nationality adjective to `NATIONALITY_COUNTRIES[nationality]`, leaving `Other` carried through unchanged. Depends on T003, T007.
- [X] T010 [P] [US1] Same re-point in `src/services/image_results_service.py` (line ~407). Depends on T003, T008.
- [X] T011 [P] [US1] Same re-point in `src/services/image_standings_service.py` (line ~314). Depends on T003.
- [X] T012 [P] [US1] Same re-point in `src/services/image_attendance_service.py` (line ~353). Depends on T003.
- [X] T013 [P] [US1] Same re-point in `src/services/image_verdict_service.py` (line ~235). Depends on T003, T008.
- [X] T014 [US1] Update the flag fixtures in `src/services/image_sample_data.py` so the test renders resolve country-named files, and extend `tests/unit/test_image_sample_data.py` to match. Depends on T009–T013.

**Checkpoint**: US1 is independently demonstrable. Every driver flag across five graphics resolves by country; no round imagery has changed yet.

---

## Phase 4: User Story 2 — A round is a flag everywhere it is a heading (Priority: P2)

**Goal**: standings, attendance and weather draw a round's country flag and no circuit map.

**Independent test**: generate a drivers' standings image for a division holding rounds at Silverstone and Interlagos; each heading carries that country's flag, nothing resolves from the track image directory, and the field ids end in `_flag`.

### Tests for User Story 2 (MANDATORY) ⚠️

- [X] T015 [P] [US2] Extend `tests/unit/test_image_standings_catalogue.py` and `tests/unit/test_image_attendance_catalogue.py`: the round heading field is `round_<z>_flag` of class `flag`, and no `track`-class field is declared by either type.
- [X] T016 [P] [US2] Extend `tests/unit/test_image_weather_catalogue.py`: `track_flag` of class `flag` replaces `track_image`, on all six weather slots.
- [X] T017 [P] [US2] Extend `tests/unit/test_image_standings_service.py` and `tests/unit/test_image_attendance_service.py`: a round heading emits a `flag`-class datum carrying the round's country, and no `track`-class datum is emitted at all.
- [X] T018 [P] [US2] Extend `tests/unit/test_image_weather_service.py` likewise, and assert a league with nationality collection off still gets flagged round headings — that switch governs drivers alone.

### Implementation for User Story 2

- [X] T019 [P] [US2] Add `country: str | None` beside `track` on `RoundHeading` in `src/services/image_standings_service.py`, populate it from the track record, and emit `("flag", country)` for the heading in place of `("track", heading.track)`. Depends on T017.
- [X] T020 [P] [US2] The same on `RoundHeading` in `src/services/image_attendance_service.py` (line ~373). Depends on T017.
- [X] T021 [P] [US2] In `src/services/image_weather_service.py` (line ~336), emit `track_flag` as `("flag", country_name)` in place of `track_image` as `("track", track_datum)`. The service already carries `country_name`. Depends on T018.
- [X] T022 [US2] Rename the catalogue fields in `src/models/image_catalogues.py` for standings drivers, standings constructors, attendance and all six weather slots — `round_<z>_image` → `round_<z>_flag`, `track_image` → `track_flag` — and change each `assets={...}` entry from `"track"` to `"flag"`. Depends on T015, T016.
- [X] T023 [US2] Drop `("track", "track_image_directory")` from the directory requirements of `src/services/image_attendance_post.py` (line ~83) and `src/services/image_weather_post.py` (line ~180); neither draws the class any longer. Extend `tests/unit/test_image_attendance_post.py` and `tests/unit/test_image_weather_post.py`. Depends on T022.
- [X] T024 [US2] Re-author the round-heading slots in `resources/templates/standings_drivers_template.svg`, `standings_constructors_template.svg` and `attendance_template.svg`: rename the ids to `round_<z>_flag` **and re-geometry each slot from 1:1 to 3:2**. Depends on T022.
- [X] T025 [US2] The same for `track_image` → `track_flag` in the six weather templates — `weather_p1_template.svg`, `weather_p2_template.svg`, `weather_p2_sprint_template.svg`, `weather_p3_template.svg`, `weather_p3_sprint_template.svg`, `weather_mystery_template.svg` — renaming and **re-geometrying 1:1 → 3:2**. Depends on T022.
- [X] T026 [US2] Update the round-imagery fixtures in `src/services/image_sample_data.py` so these types feed a country and no track datum; extend `tests/unit/test_image_sample_data.py`. Depends on T019–T021.

**Checkpoint**: four graphics draw flags at the right shape. **Verify as PNG, not as SVG in a browser** — a missed re-geometry is visible only in the raster.

---

## Phase 5: User Story 3 — A calendar pictures each round either way, or both (Priority: P3)

**Goal**: the calendar declares both fields and chooses per round.

**Independent test**: author a calendar template declaring both slots for round 1, a flag alone for round 2, a map alone for round 3 and neither for round 4; each round draws exactly what its template declared.

### Tests for User Story 3 (MANDATORY) ⚠️

- [X] T027 [P] [US3] Extend `tests/unit/test_image_calendar_catalogue.py`: `round_<x>_flag` of class `flag` joins `round_<x>_image` of class `track`, both optional, both members of the existing round collection taking its ordinal and capacity.
- [X] T028 [P] [US3] Extend `tests/unit/test_image_calendar_fill.py`: a round declaring both draws both; declaring one draws one and raises nothing for the other; a round whose circuit has a map but whose country has no flag draws the map and the **flag** directory's fallback, never the map in the flag's place.
- [X] T029 [P] [US3] Extend `tests/unit/test_image_calendar_fill.py` for the mystery round: the flag slot resolves `mystery.svg` of the flag directory and the map slot `mystery.svg` of the track directory, with no field emptied and no notice raised.

### Implementation for User Story 3

- [X] T030 [US3] Add `round_<x>_flag` to the calendar catalogue in `src/models/image_catalogues.py` (entry at line ~1018), class `flag`, optional, beside the existing `round_<x>_image`. Depends on T027.
- [X] T031 [US3] Emit the second datum per round in `src/services/image_calendar_service.py` (line ~272): `("flag", entry.country_name)` alongside the existing `("track", entry.image_datum)`. The service already carries `country_name`. For a mystery round both take `MYSTERY_DATUM`. Depends on T028, T029, T030.
- [X] T032 [US3] Add `("flag", "flag_directory")` to the calendar's directory requirements in `src/services/calendar_post_service.py` (line ~133) and extend its test. Depends on T031.

**Checkpoint**: the calendar draws both classes, per round, independently of US4.

---

## Phase 6: User Story 4 — A check-in call pictures the round either way, or both (Priority: P3)

**Goal**: the check-in graphic declares both fields.

**Independent test**: author a check-in template declaring both slots and confirm both are drawn; remove one and confirm the graphic is still produced with the other.

### Tests for User Story 4 (MANDATORY) ⚠️

- [X] T033 [P] [US4] Extend `tests/unit/test_image_rsvp_service.py`: `track_flag` and `track_flag_group` join `track_image` and `track_image_group`, both optional; a template declaring neither still produces the graphic and raises nothing.
- [X] T034 [P] [US4] Extend `tests/unit/test_image_rsvp_service.py` for the mystery round — both slots draw their class's `mystery.svg`, no mandatory field emptied — and for the removable group, so a round carrying no track leaves neither plate standing empty under a label.

### Implementation for User Story 4

- [X] T035 [US4] Add `track_flag` and `track_flag_group` to the check-in catalogue in `src/models/image_catalogues.py` (entry at line ~1419), class `flag`, optional. Depends on T033.
- [X] T036 [US4] Emit `("flag", country_name)` for `track_flag` in `src/services/image_rsvp_service.py` (line ~322) alongside the existing `track_image` datum, both taking the mystery literal for a concealed round. Depends on T034, T035.
- [X] T037 [US4] Add `("flag", "flag_directory")` to the check-in directory requirements in `src/services/image_rsvp_post.py` (line ~113) and extend `tests/unit/test_image_rsvp_post.py`. Depends on T036.

**Checkpoint**: check-in draws both classes, independently of US3.

---

## Phase 7: User Story 5 — The shipped templates show both out of the box (Priority: P4)

**Goal**: a clean clone draws both classes on the calendar and the check-in graphic, out of packaged artwork.

**Independent test**: on a clean clone with no league artwork placed, run the calendar and check-in test renders; each carries both a flag and a map, every one resolved from a packaged file.

### Tests for User Story 5 (MANDATORY) ⚠️

- [X] T038 [P] [US5] Create `tests/unit/test_packaged_resources.py`: `resources/flags/` holds `fallback.svg` and `mystery.svg`; the new file is 3:2, is plain SVG with no `clipPath`, gradient or filter, and carries **no `<text>` element**; `resources/tracks/` still holds both of its reserved files at 1:1.
- [X] T039 [P] [US5] Extend `tests/unit/test_image_calendar_catalogue.py` and `tests/unit/test_image_rsvp_service.py` to assert the **packaged** calendar and check-in templates each declare both fields, so the shipped example cannot regress to one class.

### Implementation for User Story 5

- [X] T040 [P] [US5] Author `resources/flags/mystery.svg` at 3:2 (120 × 80), plain SVG, no text, conveying concealment by shape as `resources/tracks/mystery.svg` does. Depends on T038.
- [X] T041 [US5] Add twelve `round_<x>_flag` slots to `resources/templates/calendar_template.svg`, one per round of the existing grid, each at 3:2 and each inside that round's existing group. Depends on T030.
- [X] T042 [US5] Add `track_flag` and `track_flag_group` to `resources/templates/rsvp_template.svg` at 3:2, beside the existing `track_image` pair. Depends on T035.
- [X] T043 [US5] Render the calendar and check-in test images and **verify the PNGs**: both classes drawn, neither letterboxed, mystery round drawing each class's `mystery.svg`. Depends on T040, T041, T042.

**Checkpoint**: a clean clone demonstrates the whole feature with no artwork placed and no configuration set.

---

## Phase 8: The aspect check (cross-cutting, gated on the templates)

**Why here**: this enforces Constitution XIV.6 across every template. Running it before Phases 4–7 re-author them would fail on templates the increment is about to fix. It ships in the same increment, and T047 is what proves the re-geometry of Phase 4 actually happened.

- [X] T044 [P] Extend `tests/unit/test_image_validity_layers.py`: a 3:2 flag slot passes; a square flag slot is refused with a message naming field, class, expected aspect and found aspect; a 3:2 track slot is refused; **a slot at 120.00001 × 80 passes**, which is the tolerance case a naive implementation fails.
- [X] T045 Implement the per-class aspect check inside `CatalogueLayer` (Layer 2) in `src/services/image_validity_service.py`: for each image field the catalogue names, compare the slot's declared width÷height against `ASSET_CLASS_ASPECTS` within a **1% relative tolerance**, raising a problem on mismatch. Defer to the existing fault where a slot declares no usable dimensions rather than dividing by zero. Depends on T005, T044.
- [X] T046 Extend Layer 2 to refuse a template of any type but the calendar and the check-in graphic that declares a track-class field, naming the field (FR-009). Cover in `tests/unit/test_image_validity_layers.py`. Depends on T022, T045.
- [X] T047 Assert every one of the fifteen packaged templates passes the aspect check for every image field it declares, in `tests/unit/test_packaged_resources.py`. This is what catches a missed re-geometry from T024 or T025. Depends on T024, T025, T041, T042, T045.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T048 [P] Correct the country examples in `docs/wip-specs/image_module_specification.md` per research R-001: `Great Britain` → `United Kingdom`, `united_states.svg` → `united_states_of_america.svg`. The rule is right; the illustration contradicts the seed.
- [X] T049 [P] Correct the same examples in `.specify/memory/constitution.md` Rule 13 — **via `/speckit-constitution`, never by hand**. The version does not move: v5.0.0 is unmerged, and this is the same increment.
- [X] T050 [P] Correct the same examples in `specs/044-track-imagery-split/spec.md` (SC-001 and the edge cases).
- [X] T051 Run the full `quickstart.md` validation end to end, PNG verification included.
- [X] T052 Run `pytest tests/ -q` and compare against T001's baseline. The suite is expected to pass in full.
- [X] T053 Invoke the **`close-out`** skill. This increment changes what a league sees, so `README.md` and `resources/README.md` both need bringing into step: the country-keyed naming rule, the rename callout for a league holding an adjective-keyed folder, the two `mystery.svg` files, which graphics draw which class, and the per-class aspect rule. Neither was updated earlier, deliberately — until now the behaviour was not built.

---

## Dependencies & Execution Order

```
Phase 1 (Setup)
   └─> Phase 2 (Foundational: T003–T006)   ← blocks everything
          ├─> Phase 3  US1  (P1) 🎯 MVP    ← blocks US2, and the driver half of US5
          │      └─> Phase 4  US2  (P2)
          ├─> Phase 5  US3  (P3) ─┐
          ├─> Phase 6  US4  (P3) ─┤        ← US3 and US4 are independent of each other
          │                        └─> Phase 7  US5 (P4)  ← needs US3 + US4 catalogues
          └─────────────────────────> Phase 8 (aspect check) ← needs Phases 4–7 templates
                                             └─> Phase 9 (polish, docs, close-out)
```

**Story independence**:

- **US1** stands alone and is the MVP. It delivers the single country-keyed flag directory across five graphics without touching round imagery.
- **US2** needs US1 (a round's flag resolves through the same rekeyed class).
- **US3** and **US4** are independent of each other and of US2 — either can ship alone.
- **US5** needs the US3 and US4 catalogues, being their demonstration rather than a capability of its own.
- **Phase 8** is deliberately last of the code phases: it validates every template, so it must follow the re-authoring.

## Parallel Opportunities

- **Phase 2**: T005/T006 (aspect table) run alongside T003/T004 (country map) — different files, no shared dependency.
- **Phase 3**: T010–T013 are four services re-pointed identically in four files, all parallel once T003 lands.
- **Phase 4**: T015–T018 (tests) all parallel; T019–T021 (three services) all parallel.
- **Phases 5 and 6**: entirely parallel with each other — different catalogues, different services, different templates.
- **Phase 9**: T048/T049/T050 are three documents, parallel.

⚠️ **Not parallel**: T024 and T025 both re-author templates but touch different files, so they *are* parallel with each other — however neither may start before T022 renames the catalogue fields, or the templates and the catalogue disagree mid-flight.

## Implementation Strategy

**MVP** is Phase 3 (US1) on top of Phase 2. It delivers the one thing with standalone value — a single country-keyed flag directory — and is demonstrable without any round imagery work.

**Incremental delivery**: US1 → US2 gives the whole "flags everywhere a round is a heading" outcome, which is the correction the increment exists for. US3, US4 and US5 then add the calendar and check-in capability. Phase 8 hardens all of it.

**Do not start a task on a red or absent test.** Every implementation task above either names its test or depends on one, per this project's testing standard. No task here requires a live Discord bot; the quickstart's PNG verification is the closest thing and runs entirely offline through Inkscape.
