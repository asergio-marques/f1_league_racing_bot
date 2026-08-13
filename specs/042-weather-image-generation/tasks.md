---

description: "Task list for weather image generation (042)"
---

# Tasks: Weather Image Generation

**Input**: Design documents from `/specs/042-weather-image-generation/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: included. Not because the spec asks for them but because `CLAUDE.md` makes the suite a
standing obligation — `pytest tests/ -q` is run before and after a change and compared, and the suite is
expected to pass in full. Each contract in [contracts/](./contracts/) states obligations, and the test
tasks below are what discharge them.

**Organization**: grouped by user story. Phase 2 carries **four independent tracks**, only the first of
which blocks every story. The other three are corrections and additions to code this feature is about to
attach itself to, and each can be cherry-picked and shipped alone.

**A note on Phase 2's third track.** The produce-before-destroy reordering (T014–T016) is a correction to
the **textual** weather flow: `phase2_service` and `phase3_service` delete the previous phase's message
before posting the replacement. It is a prerequisite of no story, and it is placed early because the image
branch of US2 edits the same two functions and must land inside the corrected ordering rather than beside
it. See [contracts/weather-posting.md](./contracts/weather-posting.md) § 2a.

> **`weather_module_specification.md` is stale and is not a source for any task here.** Confirmed by the
> author on 2026-08-13. Principle IV and the shipped code govern the pipeline.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: the user story the task serves (US1–US5); Setup, Foundational and Polish carry none

---

## Phase 1: Setup

**Purpose**: establish the baseline the change is measured against, and confirm what the render needs
already exists.

- [ ] T001 Run `pytest tests/ -q` from the repo root and record the counts; the baseline as of 2026-08-13 is **1707 passed, 1 skipped, 0 failed**. Any pre-existing failure is investigated on a clean tree before proceeding, never written off
- [ ] T002 [P] Confirm the six weather templates resolve, parse and declare a root canvas in `resources/templates/` — `weather_p1_template.svg`, `weather_p2_template.svg`, `weather_p2_sprint_template.svg`, `weather_p3_template.svg`, `weather_p3_sprint_template.svg`, `weather_mystery_template.svg` — and note how many sessions and slots each declares, so the floors of T006 can be checked against files that already ship
- [ ] T003 [P] Confirm `resources/weather/fallback.svg` and `resources/tracks/fallback.svg` are present, and that **no asset class, template slot, aspect, toggle or test kind is added by this feature** — all six template keys, the `weather` aspect, the weather icon directory and the four `weather-*` test kinds were delivered at 035/036 and are registered in `src/models/image_constants.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the new capacity form and the six catalogues; three shared renderings lifted out of the
message builders; the textual flow's ordering corrected; and the eight icons the module owes.

**⚠️ Order matters within `image_catalogues.py` and within `message_builder.py`** — tasks touching one
file are sequential and carry no `[P]`.

### Track A — the declaration floor and the six catalogues (R1, R2, [contracts/declaration-floor.md](./contracts/declaration-floor.md))

- [ ] T004 Add `minimum: int | None = None` to `RowSpec` and to `NestedSpec` in `src/models/image_catalogues.py`, documented as Constitution XIV.12's **third** capacity — fixed by the template slot. Record in the docstring that `capacity=None, minimum=<int>` is the only new combination and that `capacity=<int>` with a `minimum` is not admitted, a fixed capacity being already both bounds
- [ ] T005 Enforce the floor inside `declared_capacity` on **both** specs in `src/models/image_catalogues.py`, raising `CapacityError` where the template declares fewer members than `minimum`. The message MUST name the collection, the count declared and the count required — `CatalogueLayer` surfaces it verbatim and it is the entirety of what a league manager is told (FR-016)
- [ ] T006 Derive the four floors in `src/models/image_catalogues.py` from `SESSIONS_BY_FORMAT` and `MAX_SLOTS` in `src/models/session.py` — the greatest each slot's served formats can demand — and never as literals (FR-015). Expect sprint 4 sessions × 3 slots and plain 2 sessions × 4 slots; **the 3 is the Long Feature Race and the wip-spec's "two" was an arithmetic slip**, corrected on the author's instruction of 2026-08-13
- [ ] T007 Add the six `FieldCatalogue` constants to `src/models/image_catalogues.py` per [contracts/weather-catalogues.md](./contracts/weather-catalogues.md) and key them into `CATALOGUES`: the shared heading fields, phase 1 holding nothing beyond them, the mystery notice holding four fields alone, phase 2's session collection, and phase 3's session collection with its nested `slot` collection and `slot_type` reclassified optional
- [ ] T008 [P] Add `tests/unit/test_image_weather_catalogue.py` covering all six: the field lists and classifications, `rain_probability` mandatory on phase 1 and optional on phases 2–3 (FR-004), the mystery catalogue declaring nothing beyond its four (FR-006), and the sibling relation returning the other five for each of the six with **no** change to `sibling_keys` (FR-002, R4)
- [ ] T009 [P] Add to `tests/unit/test_image_weather_catalogue.py` the mechanical proof that `session_<x>_slot_type` and `session_<x>_slot_<y>_label` do not collide (FR-009, R3): assert `session_1_slot_type` does **not** match the nested slot pattern and cannot inflate the slot count, that it *does* count as a field of session 1, and that `_canonical` yields two distinct forms (same file as T008 — sequential if both are being added at once)

### Track B — the three shared renderings (R6, FR-021, FR-023a, FR-029)

- [ ] T010 Extract the rain-likelihood rendering out of `phase1_message` in `src/utils/message_builder.py` into a separately callable renderer, and **correct it to round to the nearest whole number** with the percent sign (FR-023, FR-023a — the author's ruling of 2026-08-13). `phase1_message` then composes its message from that renderer
- [ ] T011 Extract the session-weather-type rendering out of `phase2_message` in `src/utils/message_builder.py` — today `slot.capitalize()` inline — into a separately callable renderer producing "Sunny", "Mixed" or "Rain", which both paths call (FR-020, FR-026)
- [ ] T012 Give `format_slots_for_forecast` in `src/utils/message_builder.py` an **unemphasised** form returning the sequence without the italics it bakes in today, and have `phase3_message` apply the emphasis itself. The graphic MUST NOT strip markup out of a string handed to it — Constitution XIV.16 places the repair in the code that hands the value over (FR-029)
- [ ] T013 [P] Add to `tests/unit/test_message_builder.py`: the new whole-number rounding for a value that is not a whole percentage; the three weather-type strings; the unemphasised sequence alongside the emphasised one; and that `session_type_label` still strips the length qualifier so the graphic needs **no** work for FR-025. Note in the test module that nothing previously asserted on `phase1_message`'s rendering, which is what let the divergence stand

### Track C — the textual flow's ordering (R5, FR-045, [contracts/weather-posting.md](./contracts/weather-posting.md) § 2a)

- [ ] T014 Reorder `src/services/phase2_service.py` so the phase 1 message is deleted **after** the phase 2 message has been posted successfully, not before — produce, post, then delete, then persist the new id (FR-045, Constitution XIV.8)
- [ ] T015 Reorder `src/services/phase3_service.py` identically for the phase 2 message. This is the case that matters most: a failed phase 3 render would, as the code stands, delete the standing phase 2 forecast and *then* fall back, leaving the division with nothing during the window in which something has already gone wrong
- [ ] T016 [P] Add ordering coverage to `tests/unit/test_forecast_cleanup.py`: with the post forced to fail, assert the previous phase's message is **not** deleted; and assert the delete follows the post on the ordinary path. Confirm test-mode suppression is unaffected by the reordering (FR-047)

### Track D — the eight icons the module owes (R8, FR-034)

- [ ] T017 [P] Author the eight weather icons into `resources/weather/`, beside the `fallback.svg` already there: `sunny.svg`, `mixed.svg`, `rain.svg`, `clear.svg`, `light_cloud.svg`, `overcast.svg`, `wet.svg`, `very_wet.svg`. Constitution XIV.13's closed-set clause — the module defines this vocabulary and no league chose it, so the module ships it complete, as `resources/markers/` already does for the three position-change directions
- [ ] T018 [P] Confirm each of the eight is plain SVG with **no** `clipPath`, gradient or filter, authored at the aspect ratio of the slot it fills and padded with transparent margin where the subject does not fill it (FR-035, Constitution XIV.6), by reading each file in `resources/weather/` and rasterising it — never by opening it in a browser (XIV.14)

**Checkpoint**: the catalogues exist and refuse a too-small template, both paths share three renderings, the textual chain no longer destroys before it produces, and a league draws every forecast without authoring an icon.

---

## Phase 3: User Story 1 — Preview all six weather graphics (Priority: P1) 🎯 MVP

**Goal**: the four `/images test weather-*` commands return six PNGs in all, built from fabricated data, with no season, division or round in existence.

**Independent Test**: enable the module, name the six templates, run the four commands, and confirm the PNGs show every enumerated case of [quickstart.md](./quickstart.md) § 1 in the rasterised output.

### Tests for User Story 1

- [ ] T019 [P] [US1] Add `tests/unit/test_image_weather_service.py` covering `resolve_drawing` for all three phases: the fixed phase descriptions (FR-022), the stored rain probability appearing on all three (FR-023), session names for a sprint round and for every other (FR-025), the weather type read as phase 2 persisted it and the slot sequence as phase 3 persisted it (FR-026, FR-027), and sessions and slots placed in the order run and drawn (FR-030)
- [ ] T020 [P] [US1] Add `tests/unit/test_image_weather_fill.py` covering `build_fill_spec`: sessions filled to the round's count with surplus `session_<x>_group` removed and no notice (FR-036, FR-017), slots likewise (FR-038), a session's group removal taking its slots with it (FR-040), `division_tier` emptied where unset (FR-031), and every inapplicable value **emptied rather than dashed** (FR-032)
- [ ] T021 [P] [US1] Add `weather_template_key` coverage to `tests/unit/test_image_weather_service.py` (R7, FR-012): sprint rounds taking the `_sprint` key for phases 2 and 3, every other format the plain key, phase 1 one key for all formats — and assert the function reads **only** its two arguments, taking no session count, no further configuration, and no fall back to the other slot when the selected one is unconfigured
- [ ] T022 [P] [US1] Add sample-data coverage to `tests/unit/test_image_sample_data.py` asserting the six fabricated drawings exhibit every case FR-062–FR-065 enumerate, and that the sprint and endurance pair between them reach the greatest session count (4) and the greatest slot count (4) the module can produce

### Implementation for User Story 1

- [ ] T023 [US1] Add `src/services/image_weather_service.py` with the drawing dataclasses of [data-model.md](./data-model.md) and a `WeatherDataError` raised for a fatal disagreement before anything is drawn
- [ ] T024 [US1] Implement `weather_template_key(phase, round_format)` in `src/services/image_weather_service.py` as a pure function of its two arguments — the selecting datum of Constitution XIV.10 (FR-012)
- [ ] T025 [US1] Implement `resolve_drawing` in `src/services/image_weather_service.py`, parameterised by phase: the heading fields for all three phases, the sessions for phases 2 and 3, and the slots for phase 3. **One utility serves all six templates** — they draw one subject and differ only in which parts of it they carry
- [ ] T026 [US1] Call the shared renderers of T010–T012 for every value the graphic and the textual forecast both draw, in `src/services/image_weather_service.py`, restating none of them (FR-020, Constitution XIV.7). Assert in `tests/unit/test_image_weather_service.py` that both paths produce the identical string for the rain likelihood, the weather type, the session name and the slot summary
- [ ] T027 [US1] Read the phase 1 rain probability as persisted and place it on the phase 2 and phase 3 graphics too, in `src/services/image_weather_service.py` (FR-023) — Constitution XIV.7's clause admitting a value the text path published in **another message of the same flow**. Nothing is recomputed
- [ ] T028 [US1] Implement `build_fill_spec` in `src/services/image_weather_service.py`: count the session and slot capacities from the template, fill text through a `put` that empties rather than dashes, place `track_image` and both icon families into `image_data` resolved by the module's slug rule, and remove surplus session and slot groups (FR-036, FR-038)
- [ ] T029 [US1] Implement the mystery notice's resolution in `src/services/image_weather_service.py`: the four heading fields alone, no track, no session, no forecast (FR-006). It shares the heading resolution with the phases and carries none of the rest
- [ ] T030 [US1] Add `build_weather_drawing` to `src/services/image_sample_data.py` fabricating the six drawings for a division named "Test Division", of tier 1 and season number 1, at round 1 of a track of the server's track list (FR-060) — a sprint round and an endurance round for phases 2 and 3 (FR-061)
- [ ] T031 [US1] Fabricate the case coverage in `src/services/image_sample_data.py`: a rain likelihood that is **not** a whole percentage so the T010 rounding is visible (FR-062); all three weather types across the phase 2 pair (FR-063); and for phase 3 a session of a single slot, a session of one weather throughout, a session whose slots differ, a session at its type's greatest slot count, and all five concrete weathers (FR-064)
- [ ] T032 [US1] Extend `build_spec` in `src/services/image_sample_data.py` to dispatch all six template keys through the new service, resolving the packaged `resources/weather` and `resources/tracks` directories as the existing branches resolve theirs
- [ ] T033 [US1] Add the four weather guards to `/images test` in `src/cogs/image_cog.py`: reject where the server's track list is empty, and report a fatal error from any of the four to the invoking league manager with **no** textual fallback, these commands having none (FR-058)
- [ ] T034 [US1] Verify all six PNGs against [quickstart.md](./quickstart.md) § 1 — opened as rasterised images, never as SVG in a browser (Constitution XIV.14). Confirm the percentage is a **whole number**, that all eight icons appear across the set, that no summary contains an asterisk, and that no image carries a date, a time, a driver, a team or a mention

**Checkpoint**: all six graphics can be produced and inspected without any league data. Every later story reuses this path.

---

## Phase 4: User Story 2 — The three phases posted through the chain (Priority: P2)

**Goal**: with the `weather` toggle on, each phase posts as a PNG on a message carrying the division role mention and nothing besides, each posting deleting its predecessor.

**Independent Test**: enable the toggle for a division with a forecast channel, advance a round through its three horizons in test mode, and confirm three graphics in turn with exactly one weather message standing at any moment.

### Tests for User Story 2

- [ ] T035 [P] [US2] Add `tests/unit/test_image_weather_post.py` covering the toggle branch taken when on and skipped when off; nothing generated and nothing posted where no forecast channel is configured or the channel is inaccessible (FR-050, Constitution XIV.8's "no posting, no graphic")
- [ ] T036 [US2] Add the precondition gate to `tests/unit/test_image_weather_post.py`: with the render forced to fail, assert every draw is still made, every phase result still persisted, and the calculation log still written in full (FR-051, FR-033, XIV.7's precondition clause) (same file as T035 — sequential)
- [ ] T037 [US2] Add the fallback matrix to `tests/unit/test_image_weather_post.py`: a scheduled posting falling back to the textual forecast with the fault reported to the log channel, a commanded posting rejecting and posting nothing, and the **textual** forecast — never the image — enqueued on a transport failure (FR-055, FR-056, FR-057) (same file — sequential)
- [ ] T038 [P] [US2] Add the mixed-manner chain to `tests/integration/test_round_lifecycle.py`: a phase that fell back to text deleted by a following phase posted as a graphic, and the reverse — each occasion reading which message stands and never how it was drawn (FR-046)

### Implementation for User Story 2

- [ ] T039 [US2] Add `src/services/image_weather_post.py` with `weather_enabled`, `build_drawing`, `render_png` and `try_post`, following the shape of `image_results_post.py`, and reusing its `report` and `report_notices` rather than restating them
- [ ] T040 [US2] Wire the image branch into `src/services/phase1_service.py`, **after** the computation, the persistence and the log entry, so the graphic is downstream of every state change it depicts (FR-051)
- [ ] T041 [US2] Wire the image branch into `src/services/phase2_service.py` **inside** the produce-before-destroy ordering established at T014, so the graphic and the textual fallback take the same post site and the same delete site
- [ ] T042 [US2] Wire the image branch into `src/services/phase3_service.py` identically, inside T015's ordering
- [ ] T043 [US2] Post the graphic on a message carrying the division role mention and **nothing besides**, in `src/services/image_weather_post.py` — the textual forecast's heading appearing on neither the message nor the picture, `phase_description` standing in its place (FR-042)
- [ ] T044 [US2] Select the template by the format of the round at generation through `weather_template_key`, and by nothing else, in `src/services/image_weather_post.py` (FR-012)
- [ ] T045 [US2] Ensure the failure of one phase prevents neither the phases after it nor the same phase of any other division, in `src/services/phase1_service.py`, `src/services/phase2_service.py` and `src/services/phase3_service.py` (FR-049)
- [ ] T046 [US2] Verify [quickstart.md](./quickstart.md) § 3 as rasterised PNGs, including the ordering check — with a phase 3 render forced to fail, confirm the channel at no instant holds no weather message — and the mixed-manner and test-mode checks

**Checkpoint**: a league's forecasts post and replace themselves as pictures, and no draw, result or log entry depends on one.

---

## Phase 5: User Story 3 — The notice of a mystery round (Priority: P3)

**Goal**: with the toggle on, a mystery round's phase 1 horizon posts the notice as a graphic on a message carrying no role mention, and nothing at all is posted at the later horizons.

**Independent Test**: schedule a mystery round on a division with the toggle enabled, reach its phase 1 horizon, and confirm one PNG holding the heading fields alone.

### Tests for User Story 3

- [ ] T047 [P] [US3] Add mystery coverage to `tests/unit/test_image_weather_post.py`: the notice posted as a graphic at the phase 1 horizon with **no** division role mention on its message (FR-052), and nothing whatever posted at the phase 2 and phase 3 horizons on either pathway (FR-053)
- [ ] T048 [P] [US3] Assert in `tests/unit/test_image_weather_service.py` that the mystery drawing carries no track name, no grand prix name, no country, no track image, no rain likelihood, no session and no slot (FR-006)

### Implementation for User Story 3

- [ ] T049 [US3] Wire the image branch into `run_mystery_notice` in `src/services/mystery_notice_service.py`, beside the existing `post_forecast` call, leaving the existing guard against a round amended away from `MYSTERY` and the existing phase-`0` recording untouched (R10)
- [ ] T050 [US3] Post the mystery graphic with **no** role mention in `src/services/image_weather_post.py`, its textual counterpart carrying none (FR-052), and fall back to the textual notice on a fatal error, this being a posting no command triggered (FR-055)
- [ ] T051 [US3] Confirm in `src/services/scheduler_service.py` and the three phase services that no phase graphic is generated for a mystery round and that such a round reaches only the phase 1 horizon — Principle IV as corrected at constitution v4.7.0, which is what makes this posting exist for a graphic to draw at all
- [ ] T052 [US3] Verify [quickstart.md](./quickstart.md) § 4 as a rasterised PNG

**Checkpoint**: every one of the module's four weather postings is a picture when the toggle is on.

---

## Phase 6: User Story 4 — A template's faults, before a season depends on it (Priority: P4)

**Goal**: a template too small for the rounds a season holds is refused when it is named, named again at season review, and never first discovered at a horizon.

**Independent Test**: configure each of the six templates with a deliberately short declaration and confirm each is refused at configuration and named at season review.

### Tests for User Story 4

- [ ] T053 [P] [US4] Add `tests/unit/test_image_weather_validity.py` asserting the floor refuses at **all three** validity moments — the naming command, season review, and immediately before a render — for each of the four floors, and that the message names the template, the declared count and the required count (FR-016, R2)
- [ ] T054 [US4] Add over-declaration coverage to `tests/unit/test_image_weather_validity.py`: a template declaring more than its floor is **accepted**, and the surplus is removed silently at generation with no notice (FR-017) (same file as T053 — sequential)
- [ ] T055 [US4] Add numbering coverage to `tests/unit/test_image_weather_validity.py`: a gap in the session numbering, and a gap in the slot numbering of any session, each refused at every moment (FR-018) (same file — sequential)
- [ ] T056 [P] [US4] Add sibling coverage to `tests/unit/test_image_weather_catalogue.py`: a phase 2 template declaring a slot field of the phase 3 catalogue is refused as the wrong file for that slot, and the report names which of the six it belongs to (FR-002)

### Implementation for User Story 4

- [ ] T057 [US4] Confirm `CatalogueLayer` in `src/services/image_validity_service.py` needs **no change** — it already catches `CapacityError` and surfaces its message verbatim, so the T005 floor refuses at all three moments through the path that already exists (R2). Record the confirmation rather than adding a call site
- [ ] T058 [US4] Confirm through `build_aspect_statuses` in `src/services/image_validity_service.py` that `season review` names each faulty weather template individually — which phase, and whether sprint, plain or the mystery notice — through the existing per-template reporting, and that approval is refused while any stands (FR-019)
- [ ] T059 [US4] Verify [quickstart.md](./quickstart.md) § 2: each of the four floors refused at configuration with the configuration left as it stood, and an over-declaring template accepted

**Checkpoint**: a league learns its template is too small when it names it, not two hours before a race.

---

## Phase 7: User Story 5 — Degradations to staff, never to drivers (Priority: P5)

**Goal**: a substituted icon or track image is drawn, the graphic is posted, and the substitution is reported where staff read it and nowhere a driver does.

**Independent Test**: point the weather icon directory at a directory holding only `fallback.svg`, generate a phase 2 forecast, and confirm the picture posts and one notice per substituted icon reaches the log channel alone.

### Tests for User Story 5

- [ ] T060 [P] [US5] Add notice coverage to `tests/unit/test_image_weather_post.py`: a resolved fallback drawing the graphic and raising a notice naming the field and the datum; the same class holding no `fallback.svg` abandoning the render and falling back to text (FR-055)
- [ ] T061 [US5] Assert in `tests/unit/test_image_weather_post.py` that every notice names the **season, division, round and phase**, and that no notice and no problem reaches any division's forecast channel (FR-059, SC-005) (same file as T060 — sequential)

### Implementation for User Story 5

- [ ] T062 [US5] Report notices through `image_results_post.report_notices` from `src/services/image_weather_post.py`, naming season, division, round and phase, to the server's log channel and never to a forecast channel (FR-059)
- [ ] T063 [US5] Report notices additionally alongside a command's output where a command triggered the generation, in `src/services/image_weather_post.py` and `src/cogs/image_cog.py` (FR-059)
- [ ] T064 [US5] Confirm in the three phase services that the calculation log channel remains textual in its entirety, `phase_log_message` being untouched, and that no intermediate value reaches `src/services/image_weather_service.py` (FR-033)
- [ ] T065 [US5] Verify [quickstart.md](./quickstart.md) § 5 as rasterised PNGs, both with and without the class fallback present

**Checkpoint**: the feature is safe to run unattended — every degradation is visible to the people who can fix it and invisible to the people who cannot.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T066 Run `pytest tests/ -q` and compare against the T001 baseline; every delta is accounted for. The **only** intended change to textual output is the phase 1 rounding of T010 (FR-023a)
- [ ] T067 [P] Confirm no weather utility computes, derives or re-renders any value the weather module owns — the probability, the type draw and the slot draw are read as persisted, and `src/services/image_weather_service.py` holds no arithmetic over any of them (Constitution XIV.7)
- [ ] T068 [P] Confirm `weather_template_key` in `src/services/image_weather_service.py` still reads only its two arguments, and that no call site in `src/services/image_weather_post.py` or `src/services/image_sample_data.py` passes it a session count, a configuration value or a fallback slot (FR-012)
- [ ] T069 [P] Verify [quickstart.md](./quickstart.md) §§ 6–7: the toggle changing what the forecast channel receives and nothing about what is drawn, stored or logged (SC-008); and the graphic and the message agreeing on every shared value (SC-007)
- [ ] T070 [P] Update `README.md` for what a league can now see: the `weather` toggle drawing all three phases and the mystery notice, the six template commands, the sprint and plain variants and the floors each must meet, and the eight weather icons that now ship
- [ ] T071 Correct the README's "What is already there" sentence, which says no weather artwork ships — eight icons now do (shares `README.md` with T070 — sequential)
- [ ] T072 Correct the README's count of toggles that change only what `/images config view` and `/season review` report — `weather` is now wired (shares `README.md` with T070 — sequential)
- [ ] T073 Invoke the `close-out` skill, which is **mandatory before reporting this work complete**: it reconciles `docs/wip-specs/image_module_specification.md` and `README.md` against what was built and against every decision taken in conversation

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)** — no dependencies
- **Foundational (Phase 2)** — depends on Setup. **Only Track A blocks the stories.** Track B (renderings) blocks US1's T026; Track C (ordering) blocks US2's T041–T042; Track D (icons) blocks US1's PNG verification at T034 but no code
- **US1 (Phase 3)** — depends on Track A, Track B and Track D
- **US2 (Phase 4)** — depends on US1's utility and on Track C
- **US3 (Phase 5)** — depends on US1's utility; **independent of US2**
- **US4 (Phase 6)** — depends on Track A alone; independent of US1, US2 and US3
- **US5 (Phase 7)** — depends on US2 and US3, being the reporting around both posting paths
- **Polish (Phase 8)** — depends on every story to be delivered

### Within each story

- Tests are written first and confirmed failing before the implementation they cover
- Dataclasses before the resolution that fills them; resolution before the projection onto a template; projection before the posting

### Parallel opportunities

- T002 and T003 together
- **The four Phase 2 tracks are independent** and touch four separate areas between them: `image_catalogues.py` (T004–T007), `message_builder.py` (T010–T012), the two phase services (T014–T015) and `resources/weather/` (T017–T018)
- T008/T009, T013, T016, T017 and T018 are five files that run alongside the implementation tracks they follow
- T019, T020, T021 and T022 are different test files and run together
- **US2 and US3 can be built in parallel by two people** once US1 is done: US2 touches the three phase services, US3 touches `mystery_notice_service.py`
- **US4 can be built in parallel with all of US1–US3** once Track A is done
- T067, T068, T069 and T070 together (T071 and T072 also edit `README.md` and follow T070)

### Parallel example: after Phase 1

```bash
# Four independent tracks through Phase 2:
Track A (catalogues): T004 → T005 → T006 → T007 → T008 → T009
Track B (renderings): T010 → T011 → T012 → T013
Track C (ordering):   T014, T015 → T016
Track D (icons):      T017 → T018

# Then two independent tracks:
Track E (US1 → US2 → US3): T019–T034 → T035–T046 → T047–T052
Track F (US4):             T053 → T054 → T055, T056 → T057 → T058 → T059
```

---

## Implementation Strategy

### MVP (User Story 1 only)

1. Phase 1 — baseline recorded, six templates and both fallbacks confirmed
2. Phase 2 — the floor, the six catalogues, the three renderings, the ordering, the eight icons
3. Phase 3 — four test commands returning six inspectable PNGs
4. **Stop and validate** against quickstart § 1, as PNGs
5. A league manager can author and correct all six templates from here, with no season in existence

### Incremental delivery

1. Setup + Foundational → the catalogues exist and refuse a too-small template, both paths share their renderings, a long-standing ordering fault is corrected, and eight icons ship
2. + US1 → all six graphics previewable (MVP)
3. + US2 → the three phases posted and chained, with no draw or log entry depending on one
4. + US3 → the mystery notice completing the toggle's promise
5. + US4 → faults caught at configuration rather than at a horizon
6. + US5 → safe unattended
7. + Polish → docs reconciled, suite compared, close-out run

### The five things most likely to go wrong

1. **Encoding a floor as a fixed `capacity`.** It would make over-declaration fatal, contradicting FR-017, and oblige every sprint author to draw exactly four sessions and no chrome beyond. The floor is a lower bound and never an upper one; the upper bound stays the data actually drawn.
2. **Reimplementing produce-before-destroy in the image branch.** T014–T015 put the ordering in the textual flow and T041–T042 put the image branch inside it. Two orderings in one function will drift, and the fallback path — the one reached *because* something already failed — is the half that would be left deleting first. This is the identical trap 041 recorded for the attendance sheet.
3. **Stripping the summary's asterisks in the image utility.** Constitution XIV.16 says an image type stripping markup out of a string it was handed "has been given the wrong thing, and the repair is in the code that handed it over". T012 is that repair; post-processing in the utility would pass every test and violate the rule.
4. **Selecting the template by the session count.** It gives the same answer for every format the bot can schedule today and the wrong one the day a format is added. T021 asserts the function reads only the format.
5. **Verifying in a browser.** The rasteriser exposes flowed text, substituted fonts and unresolvable hrefs that a browser hides. T018, T034, T046, T052, T059, T065 and T069 all say PNG for that reason.

---

## Notes

- `[P]` means a different file and no dependency on an incomplete task
- Tasks touching `image_catalogues.py` (T004–T007), `message_builder.py` (T010–T012) and `README.md` (T070–T072) are sequential within their file and carry no `[P]`
- **No migration is written.** `forecast_messages` already keys by round, division and phase and already admits phase `0` for the mystery notice; both flows write the same rows, which is what makes the mixed-manner chain of FR-046 true with no bookkeeping
- **No sibling code changes.** `ASPECT_TEMPLATES["weather"]` already holds all six keys — the constitution's sibling clause has named "the six forecasts" since it was written
- **Two changes reach the textual weather path**, both declared in the plan's Complexity Tracking: the ordering of T014–T015, and the rounding of T010
