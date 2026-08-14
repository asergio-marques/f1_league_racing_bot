---

description: "Task list for 043-verdicts-image-generation"
---

# Tasks: Verdicts Image Generation

**Input**: Design documents from `/specs/043-verdicts-image-generation/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: MANDATORY. Every implementation task below is preceded by the test that covers it, and that test must **fail first and pass before the next task begins**. No coverage is parked in the polish phase.

**Live Discord is out of scope**: no task here requires a running bot, a gateway connection or a real server. Discord is stubbed throughout. The manual system-test pass is [quickstart.md](./quickstart.md), done by hand after implementation.

**Baseline**: `pytest tests/ -q` → **1876 passed, 1 skipped**. Compare against this after every task.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US5)

---

## Phase 1: Setup

**Purpose**: The constants the rest of the feature reports through.

- [X] T001 Run `pytest tests/ -q` from the repo root and record the count; every later task compares against it rather than assuming a clean tree
- [X] T002 Add the wrapping problem kinds `PROBLEM_WRAP_NO_LEADING` and `PROBLEM_WRAP_NO_EXTENT` beside the existing `PROBLEM_*` constants in `src/models/image_module.py`, and confirm `PROBLEM_UNRESOLVED_VALUE` already covers a `shape-inside` naming a missing rectangle

**Checkpoint**: Constants exist; nothing observable has changed.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The general wrapping contract, the catalogue, and the pure resolution service. Every user story depends on all three.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

**Why this precedes the P1 story**: the sample data of US1 is what exercises wrapping by eye. Built against a half-implemented contract, its interesting cases would have to be written twice. See [plan.md](./plan.md) § Phase sequencing.

### The wrapping contract — `src/utils/svg_fill.py`

Governed by [contracts/text-wrapping.md](./contracts/text-wrapping.md). Eleven of its fourteen clauses already work; these close the rest.

- [X] T003 [P] Add failing tests in `tests/unit/test_svg_fill.py` for a single word wider than its box: one wrapped field (`shape-inside`) and one single-line field (`inline-size`), each asserting the word is broken within itself rather than emitted over-wide
- [X] T004 Break an over-wide word within itself in `_wrap` and `_truncate_to_width` in `src/utils/svg_fill.py`, so T003 passes
- [X] T005 [P] Add a failing test in `tests/unit/test_svg_fill.py` asserting that a wrapped field on which no `line-height` resolves produces a **problem** naming the field, and that no leading is substituted
- [X] T006 Make a missing `line-height` fatal in `src/utils/svg_fill.py`: have `_line_height_ratio` report absence rather than return a default, raise the problem at the `_lay_out` call site, and **delete** `_DEFAULT_LINE_HEIGHT_RATIO` so no unreachable fallback remains
- [X] T007 [P] Add a failing test in `tests/unit/test_svg_fill.py` asserting that a wrapped field whose rectangle declares no usable `width` or `height` produces a problem naming the field and the rectangle — today it silently writes one unwrapped line
- [X] T008 Treat a rectangle with no usable extent as a problem in `_lay_out` in `src/utils/svg_fill.py`, so T007 passes
- [X] T009 [P] Add a test in `tests/unit/test_svg_fill.py` pinning the **errs narrow** obligation: measured width for a representative string must be greater than or equal to the width the rasteriser draws, skipped with a clear reason when the converter is unavailable. The entire line budget rests on this and it is currently assumed rather than verified

### The catalogue — `src/models/image_catalogues.py`

Governed by [contracts/verdicts-catalogue.md](./contracts/verdicts-catalogue.md).

- [X] T010 [P] Add `tests/unit/test_image_verdicts_catalogue.py` asserting the catalogue's exact mandatory set (8), optional set (6 fields + 3 groups), asset map (`driver_flag`→flag, `team_image`→team), that `rows`, `columns`, `keyed` and `singleton` are all `None`, and that `session_name` is **mandatory**
- [X] T011 Declare `VERDICTS_CATALOGUE` in `src/models/image_catalogues.py` and register it as `CATALOGUES["verdicts_template"]`, so T010 passes. Populating it enrols verdicts in validity Layer 2 through existing machinery — no change to the layer is needed here
- [X] T012 [P] Add a test in `tests/unit/test_image_verdicts_catalogue.py` asserting the shipped `resources/templates/verdicts_template.svg` declares every mandatory id and no foreign id, so the packaged template and the catalogue cannot drift apart

### Resolution and fill — `src/services/image_verdict_service.py` (new)

Governed by [data-model.md](./data-model.md) § In-memory shapes. Pure: no Discord, no database.

- [X] T013 [P] Add `tests/unit/test_image_verdicts_service.py` covering `VerdictKind` and the three fixed stage strings — "Post-Race Penalty", "Appeal", "Attendance Sanction"
- [X] T014 Create `src/services/image_verdict_service.py` with `VerdictKind` and the `VerdictDrawing` dataclass per [data-model.md](./data-model.md), so T013 passes
- [X] T015 [P] Add tests in `tests/unit/test_image_verdicts_service.py` for mention resolution inside free text: a bare `<@123>`, the `<@!123>` and `<@&123>` forms, several mentions in one string, and — the case the attendance module actually produces — `<@123> (Ada Lovelace)`, which must yield `Ada Lovelace` once and never `Ada Lovelace (Ada Lovelace)`
- [X] T016 Implement the mention resolver in `src/services/image_verdict_service.py`, taking the name resolver as a parameter and consuming a parenthesised name that duplicates the resolved one, so T015 passes
- [X] T017 [P] Add tests in `tests/unit/test_image_verdicts_fill.py` for `build_fill_spec`: all 14 fields placed for a penalty; `session_name` **emptied** with its group removed and **no notice** for an attendance sanction; `team_name` emptied and `team_image` removed for the same; `race_name` reading "Mystery GP" for a mystery round; `division_tier` emptied where none
- [X] T018 Implement `build_fill_spec(root, drawing)` in `src/services/image_verdict_service.py` returning the module's existing `FillSpec`, so T017 passes
- [X] T019 [P] Add a test in `tests/unit/test_image_verdicts_service.py` asserting the sanction text comes from `verdict_announcement_service.translate_penalty` and is not restated — a positive magnitude reads "seconds added", a negative "seconds removed", `DSQ` reads "Disqualified"
- [X] T020 Wire the drawing's sanction text to `translate_penalty` in `src/services/image_verdict_service.py`, and reuse `image_lineup_service.resolve_driver_name` for the name, so T019 passes and no private rendering exists

**Checkpoint**: The wrapping contract is whole, the catalogue is registered, and a drawing can be turned into a fill spec — all without Discord. User stories can now begin.

---

## Phase 3: User Story 1 - Preview every kind of verdict (Priority: P1) 🎯 MVP

**Goal**: `/images test verdicts` returns six PNGs from the one template, covering all three kinds and five lengths of free text.

**Independent Test**: Configure the template on a server with a track list and nothing else; run the one command; confirm six images and every notice listed beside them. No season, review or sanctioned driver required.

### Tests for User Story 1 (MANDATORY) ⚠️

- [X] T021 [P] [US1] Add `tests/unit/test_image_sample_data.py` cases asserting `build_spec("verdicts_template", root, variant=…)` yields six distinct drawings — penalty/time-added on a **sprint** round, penalty/time-removed, penalty/DSQ, appeal, autosack, autoreserve
- [X] T022 [P] [US1] Add a test in `tests/unit/test_image_sample_data.py` asserting the six fabricated free texts cover: one line; exactly full; slightly over; over by an order of magnitude; and one with neither description nor justification entered, carrying the fixed absent-value text **without** channel markup
- [X] T023 [P] [US1] Add a test in `tests/unit/test_image_sample_data.py` asserting fabricated nationalities are values the signup wizard accepts and that at least one is the value recorded for a driver who stated none

### Implementation for User Story 1

- [X] T024 [US1] Add `build_verdict_drawing(root, *, case)` to `src/services/image_sample_data.py` producing the six cases, and branch `build_spec` on `verdicts_template`, so T021–T023 pass
- [X] T025 [US1] Register the six variant names for `verdicts_template` in `_SAMPLE_VARIANTS` in `src/cogs/image_cog.py` so one template yields six images — the mechanism already exists and needs only the entry
- [X] T026 [P] [US1] Add a test in `tests/unit/test_image_config_service.py` (or the cog's existing test module) asserting `/images test verdicts` is rejected with a clear error when the server's track list is empty, there being no round for a verdict to pertain to
- [X] T027 [US1] Add `verdicts_template` to the `needs_tracks` set in `src/cogs/image_cog.py` with a subject phrase naming the verdict, so T026 passes

**Checkpoint**: US1 is complete and independently demonstrable. This is the MVP — every later story becomes cheap to check by eye against these six images.

---

## Phase 4: User Story 2 - A penalty and an appeal as a graphic (Priority: P2)

**Goal**: With the toggle enabled, penalty and appeal verdicts post as PNGs on messages carrying the driver mention and nothing else.

**Independent Test**: Approve a penalty review with several penalties staged against a division with a verdicts channel; confirm one graphic and one message per penalty, and the textual announcement nowhere in that channel.

Governed by [contracts/verdicts-posting.md](./contracts/verdicts-posting.md).

### Tests for User Story 2 (MANDATORY) ⚠️

- [X] T028 [P] [US2] Add `tests/unit/test_image_verdicts_post.py` asserting, with Discord stubbed, that an enabled toggle posts one PNG per applied penalty on a message whose content is the driver mention **and nothing besides** — in particular carrying no trailing display name, unlike the textual announcement
- [X] T029 [P] [US2] Add a test in `tests/unit/test_image_verdicts_post.py` asserting an approved appeals review posts one graphic per correction with `verdict_stage` reading "Appeal", and that the earlier penalty verdict's message is **not** edited, replaced or deleted
- [X] T030 [P] [US2] Add a test in `tests/unit/test_image_verdicts_post.py` asserting **no message id is persisted** for any verdict — no table is written and no message state is read
- [X] T031 [P] [US2] Add a test in `tests/unit/test_image_verdicts_post.py` asserting a review approved with nothing staged announces nothing and generates nothing
- [X] T032 [P] [US2] Add a test in `tests/unit/test_image_verdicts_post.py` asserting the ordering obligation: with the render forced to fail, the review is still finalised and its penalties still applied, and that verdict falls back to the textual announcement
- [X] T033 [P] [US2] Add a test in `tests/unit/test_image_verdicts_post.py` asserting the failure of one verdict prevents neither the other verdicts of the same review nor those of another division
- [X] T034 [P] [US2] Add a test in `tests/unit/test_image_verdicts_post.py` asserting a service-level posting failure enqueues the **textual** announcement for retry and never the generated image

### Implementation for User Story 2

- [X] T035 [US2] Create `src/services/image_verdict_post.py` with a per-verdict entry point that loads context, builds a `VerdictDrawing`, renders, posts and reports — writing nothing to the database — so T028–T031 pass
- [X] T036 [US2] Resolve the team name and badge slug in `src/services/image_verdict_post.py` by reusing `image_results_post._team_names`, falling back to the Discord role name where the division holds no such team
- [X] T037 [US2] Attach the image path to `post_penalty_announcements` and `post_appeal_announcements` in `src/services/verdict_announcement_service.py`, **after** the review has been finalised, with the existing textual call as the fallback, so T032–T034 pass
- [X] T038 [P] [US2] Add an integration test in `tests/integration/test_image_module_flow.py` driving an approved penalty review end to end with Discord stubbed, asserting one attachment per penalty and no textual announcement in the channel

**Checkpoint**: US1 and US2 both work independently.

---

## Phase 5: User Story 3 - An attendance sanction as a graphic (Priority: P3)

**Goal**: Autosack and autoreserve announcements post as graphics naming no session and no team, with the composed justification carrying the driver's name.

**Independent Test**: Drive a test driver past the autosack threshold on a division with the toggle enabled; confirm one graphic with no session, no team and no TEAM label, and a justification naming the driver in words.

### Tests for User Story 3 (MANDATORY) ⚠️

- [X] T039 [P] [US3] Add tests in `tests/unit/test_image_verdicts_post.py` asserting an enforced autosack and an enforced autoreserve each post one graphic, with `verdict_stage` reading "Attendance Sanction"
- [X] T040 [P] [US3] Add a test in `tests/unit/test_image_verdicts_post.py` asserting that for such a verdict `session_name` is emptied, `team_name` is emptied, `team_image` is removed, `team_name_group` is removed, and **no notice of any kind** is raised for any of them
- [X] T041 [P] [US3] Add a test in `tests/unit/test_image_verdicts_post.py` asserting the composed justification reaches the canvas with its mention resolved to a name and **no** Discord mention anywhere on the graphic
- [X] T042 [P] [US3] Add a test in `tests/unit/test_image_verdicts_post.py` asserting an attendance **pardon** posts no graphic and remains a logging-channel record, whatever the toggle says
- [X] T043 [P] [US3] Add a test in `tests/unit/test_image_verdicts_post.py` asserting the sanction is enforced and the driver's seats altered **before** any render is attempted, and that a failed render leaves that work done

### Implementation for User Story 3

- [X] T044 [US3] Attach the image path to `post_autosanction_announcement` in `src/services/verdict_announcement_service.py`, after enforcement, with the existing textual call as the fallback, so T039–T043 pass
- [X] T045 [US3] Ensure the attendance module's pardon path reaches no image code in `src/services/attendance_service.py`, so T042 passes

**Checkpoint**: All three kinds of verdict draw. US1, US2 and US3 each stand alone.

---

## Phase 6: User Story 4 - Learn a template is faulty before a steward needs it (Priority: P4)

**Goal**: A template missing a mandatory field, or carrying a broken wrapping declaration, is refused at configuration and named at season review.

**Independent Test**: Configure a template with each defect in turn and confirm each is rejected at configuration, named individually at season review, and refused at approval.

**Note on scope**: catalogue conformance comes free from T011 — Layer 2 is catalogue-driven. What is genuinely new is the **structural wrapping check**, which belongs to the reserved `LAYER_BOUNDS = 3` slot in `src/services/image_validity_service.py`. Constitution XIV.5 ratifies these checks as structural, so the layer may now be enforced.

### Tests for User Story 4 (MANDATORY) ⚠️

- [X] T046 [P] [US4] Add `tests/unit/test_image_verdicts_validity.py` asserting a template missing any mandatory field is refused when named, with the missing field reported and the configuration left as it stood
- [X] T047 [P] [US4] Add a test in `tests/unit/test_image_verdicts_validity.py` asserting a verdicts template declaring a sibling catalogue's field is refused as the wrong file for the slot
- [X] T048 [P] [US4] Add tests in `tests/unit/test_image_validity_layers.py` for the bounds layer: a `shape-inside` naming a missing rectangle, a wrapped field with no resolvable `line-height`, and a rectangle with no usable extent are each refused, each naming the field at fault distinguishably
- [X] T049 [P] [US4] Add tests in `tests/unit/test_image_validity_layers.py` asserting XIV.9's four properties still hold with the new layer: the three reported states are unchanged, the layer names the individual template, the report states the depth reached, and a type with an empty catalogue still **skips** rather than passes
- [X] T050 [P] [US4] Add a test in `tests/unit/test_image_verdicts_validity.py` asserting season review names the verdicts template individually with its own reason and that approval is refused while the fault stands

### Implementation for User Story 4

- [X] T051 [US4] Implement a `BoundsLayer` at `LAYER_BOUNDS` in `src/services/image_validity_service.py`, checking each wrapped field the template declares against the three structural conditions, and append it to the `LAYERS` registry, so T048–T049 pass
- [X] T052 [US4] Confirm through T046, T047 and T050 that catalogue conformance and season-review naming need no further code — and if any does, add it here rather than in a later phase

**Checkpoint**: A league is told at configuration, not at midnight when a steward approves a review.

---

## Phase 7: User Story 5 - Degradations reported to staff, never to drivers (Priority: P5)

**Goal**: Truncation, font substitution and asset fallback are reported to the log channel, naming season, division, round, session and driver; never to a verdicts channel.

**Independent Test**: Point the flag directory at a directory holding only `fallback.svg`, issue a verdict with a justification an order of magnitude too long, and confirm the picture posts, both notices reach the log channel, and neither reaches a verdicts channel.

### Tests for User Story 5 (MANDATORY) ⚠️

- [X] T053 [P] [US5] Add `tests/unit/test_image_verdicts_notices.py` asserting a truncated wrapping field raises `WRAP_TRUNCATED` naming the field and the verdict, and that the render still succeeds
- [X] T054 [P] [US5] Add a test in `tests/unit/test_image_verdicts_notices.py` asserting a flag or team image falling back raises its notice, and that a class with no fallback is fatal instead
- [X] T055 [P] [US5] Add a test in `tests/unit/test_image_verdicts_notices.py` asserting the nationality distinction: collection switched off at source → field removed, **no notice**; collected but this driver recorded none → field removed, notice raised
- [X] T056 [P] [US5] Add a test in `tests/unit/test_image_verdicts_notices.py` asserting every notice names season, division, round, session and driver, is routed to the server logging channel, and reaches **no** verdicts channel
- [X] T057 [P] [US5] Add a test in `tests/unit/test_image_verdicts_notices.py` asserting that where a command triggered the generation, notices are additionally reported alongside its output

### Implementation for User Story 5

- [X] T058 [US5] Implement notice gathering and log-channel reporting in `src/services/image_verdict_post.py`, with the describe-the-subject helper naming season, division, round, session and driver, so T053–T057 pass
- [X] T059 [US5] Carry the `nationality_collected` distinction from the lineup's existing handling into `VerdictDrawing` in `src/services/image_verdict_service.py`, so T055 passes

**Checkpoint**: All five stories complete.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T060 Run `pytest tests/ -q` and compare against the T001 baseline; every prior test must still pass and the new ones be present
- [X] T061 [P] Update `README.md` to document the `verdicts` toggle's behaviour, the verdict template's fields and its two wrapping fields, and the `images test verdicts` command — the README describes the bot as it is, so this lands only now that the behaviour exists
- [X] T062 [P] Update `resources/templates/README.md` to describe the verdicts template's wrapped fields and the rectangles that size them, so a league authoring its own knows what `shape-inside` and `line-height` are for
- [X] T063 Invoke the `close-out` skill, which is mandatory before reporting this work complete: reconcile `docs/wip-specs/image_module_specification.md` and `README.md` against what was actually built, and record any decision taken in conversation during implementation

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup — **blocks every user story**
- **US1 (Phase 3)**: depends on Phase 2
- **US2 (Phase 4)**: depends on Phase 2
- **US3 (Phase 5)**: depends on Phase 2; shares `image_verdict_post.py` with US2, so sequence them if one developer
- **US4 (Phase 6)**: depends on Phase 2 (T011 for the catalogue, T005–T008 for the conditions the layer checks)
- **US5 (Phase 7)**: depends on Phase 2; touches `image_verdict_post.py`, so sequence with US2/US3
- **Polish (Phase 8)**: depends on every story intended for this increment

### Within the foundational phase

Three independent tracks that may run in parallel:

- wrapping contract (T003–T009) — `src/utils/svg_fill.py`
- catalogue (T010–T012) — `src/models/image_catalogues.py`
- service (T013–T020) — `src/services/image_verdict_service.py`

T017–T018 read the catalogue's ids, so run them after T011.

### Within each story

Tests first, failing, then the implementation that makes them pass, then the next task. Never start a task on a red or absent test.

### Parallel opportunities

- T003, T005, T007, T009 — four independent wrapping tests, same file, write together and implement one at a time
- T010, T013 — catalogue and service tests, different files
- T021, T022, T023 — three sample-data tests
- T028–T034 — seven US2 posting tests, all stubbed, all independent
- T039–T043 — five US3 tests
- T046–T050 — five US4 validity tests
- T053–T057 — five US5 notice tests
- T061, T062 — two README files

Different stories can be taken by different developers once Phase 2 is done, with the caveat that US2, US3 and US5 all edit `image_verdict_post.py`.

---

## Parallel Example: Foundational phase

```bash
# Three tracks at once, three different files:
Task: "T003 word-breaking tests in tests/unit/test_svg_fill.py"
Task: "T010 catalogue shape test in tests/unit/test_image_verdicts_catalogue.py"
Task: "T013 VerdictKind tests in tests/unit/test_image_verdicts_service.py"
```

## Parallel Example: User Story 2

```bash
# All seven posting tests are independent and Discord is stubbed:
Task: "T028 message content is the mention alone"
Task: "T029 appeal posts beside the penalty, editing nothing"
Task: "T030 no message id persisted"
Task: "T031 nothing staged, nothing announced"
Task: "T032 review finalises even when the render fails"
Task: "T033 one failure costs one graphic"
Task: "T034 transport failure enqueues text"
```

---

## Implementation Strategy

### MVP first (User Story 1)

1. Phase 1: Setup — T001–T002
2. Phase 2: Foundational — T003–T020 (**critical**; blocks everything)
3. Phase 3: US1 — T021–T027
4. **Stop and validate**: run `/images test verdicts` and inspect six PNGs

This is a genuine MVP. A league manager can author and judge a verdicts template with nothing else built, and every later story is verified against these six images.

### Incremental delivery

1. Setup + Foundational → the wrapping contract is whole and the catalogue registered
2. + US1 → the template can be previewed and judged (**MVP**)
3. + US2 → stewards' decisions post as graphics
4. + US3 → the bot's own sanctions post as graphics; the toggle's promise is complete
5. + US4 → faults surface at configuration rather than at midnight
6. + US5 → degradations are visible to the people who can fix them

### Notes

- **T006 deletes a fallback rather than leaving it unreachable.** The evidence that removing `_DEFAULT_LINE_HEIGHT_RATIO` breaks nothing is in the constitution's v4.8.0 entry: of fifteen shipped templates, only the verdicts one declares `shape-inside`, and both its wrapped fields declare `line-height`.
- **T009 is the one task that may skip.** It needs the rasteriser; it must skip with a clear reason rather than silently pass when the converter is absent.
- **The duplicated penalty/appeal announcement builders are deliberately not refactored.** [research.md](./research.md) §3 records why: the text path's content is out of scope, and a refactor of two near-identical functions is not this feature's to carry.
- Commit after each task or logical group. Stop at any checkpoint to validate a story on its own.
