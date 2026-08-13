---

description: "Task list for results image generation (039)"
---

# Tasks: Results Image Generation

**Input**: Design documents from `/specs/039-results-image-generation/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: included. Not because the spec asks for them but because `CLAUDE.md` makes the suite a
standing obligation — `pytest tests/ -q` is run before and after a change and compared, and the
suite is expected to pass in full. Test tasks here follow the naming already in `tests/unit/`.

**Organization**: grouped by user story. Phase 2 is genuinely blocking: the shared rendering layer
and the two catalogues are what every story stands on.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: the user story the task serves (US1–US4); Setup, Foundational and Polish carry none

---

## Phase 1: Setup

**Purpose**: establish the baseline the change is measured against.

- [X] T001 Run `pytest tests/ -q` from the repo root and record the counts; the baseline as of 2026-08-12 is 1135 passed, 1 skipped, 0 failed. Any pre-existing failure is investigated on a clean tree before proceeding, never written off
- [X] T002 [P] Confirm `resources/tyres/fallback.svg`, `resources/flags/fallback.svg` and `resources/teams/fallback.svg` are present, since the absent-tyre behaviour and the asset outcomes depend on them
- [X] T003 [P] Confirm `resources/templates/results_qualifying_template.svg` and `resources/templates/results_race_template.svg` resolve, parse and declare a root canvas, and note how many rows each declares

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the single derivation of every shared value, the two catalogues, and the one fill-pipeline
addition. **No user story can begin until this phase is complete.**

**⚠️ Order matters within `results_formatter.py` and within `image_catalogues.py`** — the tasks
touching one file are sequential and carry no `[P]`.

### The shared rendering layer (R3, [contracts/shared-rendering.md](./contracts/shared-rendering.md))

- [X] T004 Add the frozen `QualifyingRow` and `RaceRow` dataclasses to `src/utils/results_formatter.py`, one field per drawable cell, every cell already a string or `None` for "does not apply", carrying no Discord reference of any kind
- [X] T005 Correct `_pen_col` in `src/utils/results_formatter.py` to the wip-spec's precision — signed seconds, no decimal part for a whole number, three decimal places for a fraction, never rounded, so five seconds is `+5s` and five and a half `+5.500s` (R5)
- [X] T006 Implement `build_qualifying_rows` in `src/utils/results_formatter.py`, owning the reference-lap search, the gap rendering, the outcome-literal displacement of a best lap, and the points
- [X] T007 Implement `build_race_rows` in `src/utils/results_formatter.py`, owning the total-time and interval rules, the fall-back to every entry's own total time where the first-placed entry records none, the laps-behind wording singular and plural, the fastest lap, the three penalty columns and the points
- [X] T008 Refactor `format_qualifying_table` and `format_race_table` in `src/utils/results_formatter.py` to call the builders and join their cells, computing no value of their own and rendering `None` as `—`
- [X] T009 [P] Extend `tests/unit/test_results_formatter.py`: the two builders across every case the wip-spec's § "Test data" enumerates; the penalty precision in both outputs; and that the textual tables are byte-identical to their previous output save for that precision

### The two catalogues (R1, R2, [contracts/results-catalogue.md](./contracts/results-catalogue.md))

- [X] T010 Add `fallback_when_absent: frozenset[str]` to `RowSpec` in `src/models/image_catalogues.py`, naming the field suffixes whose absent datum draws the class fallback (R6)
- [X] T011 Declare the shared results field sets and both `RESULTS_QUALIFYING_CATALOGUE` and `RESULTS_RACE_CATALOGUE` in `src/models/image_catalogues.py`, composing the common part once, and register them in `CATALOGUES` under `results_qualifying_template` and `results_race_template`
- [X] T012 Add `sibling_row_fields(template_key)` to `src/models/image_catalogues.py`, deriving the sibling relation from `ASPECT_TEMPLATES` rather than hard-coding it for results, and returning the row suffixes belonging to a sibling's catalogue and not to this one
- [X] T013 [P] Add `tests/unit/test_image_results_catalogue.py`: both catalogues' classifications; that the row capacity is counted from the template; that a gap and a rowless template each raise `CapacityError`; and that `sibling_row_fields` names `gap`/`best_lap`/`tyre` against the race key and `time`/`fastest_lap`/`ingame_penalty` against the qualifying key

### The absent datum (R6)

- [X] T014 Teach the `image_data` branch of `fill` in `src/utils/svg_fill.py` to read `fallback_when_absent` from the spec's catalogue: an absent datum on a listed field draws the class's `fallback.svg` and raises **no** notice, and where the class holds no fallback the field is removed and still nothing is reported
- [X] T015 [P] Extend `tests/unit/test_svg_fill.py`: the silent fallback for an absent datum on a declared field; the silent removal where the class holds no fallback; and that an absent datum on an **undeclared** field still behaves as it did

**Checkpoint**: the shared derivation exists and is the only one; both catalogues are registered; the pipeline can draw an absence. User stories may now begin.

---

## Phase 3: User Story 1 — Preview both graphics (Priority: P1) 🎯 MVP

**Goal**: `/images test results` returns two PNGs built from fabricated data, with no season, division, round or submitted result in existence.

**Independent Test**: enable the module, name both templates, run `/images test results`, and confirm two PNGs matching the wip-spec's § "Test data" — every case in [quickstart.md](./quickstart.md) § 2 visible in the rasterised output.

### Tests for User Story 1

- [X] T016 [P] [US1] Add `tests/unit/test_image_results_service.py` covering `resolve_drawing` for both session kinds: the session name across the sprint and non-sprint formats, the lifecycle label and the two phase closures from `result_status`, the team name behind a role id with its role-name fallback, and the fastest-lap block present and absent
- [X] T017 [P] [US1] Add `tests/unit/test_image_results_fill.py` covering `build_fill_spec`: rows filled to the entry count, unused `row_<x>_group` removed with its fields marked off-canvas, the position filled from the ordinal, the sanction fields emptied quietly for an open phase, the recolour applied to exactly one row, and the two column groups and the block group removed on their conditions
- [X] T018 [P] [US1] Extend `tests/unit/test_image_calendar_fill.py`'s sibling — add sample-data coverage asserting the fabricated classification exhibits every enumerated case where the row count allows, and drops the excess cases where it does not

### Implementation for User Story 1

- [X] T019 [US1] Add `src/services/image_results_service.py` with the `ResultsDrawing`, `ResultsEntry` and `FastestLapBlock` dataclasses of [data-model.md](./data-model.md), and a `ResultsDataError` raised for a fatal disagreement before anything is drawn
- [X] T020 [US1] Implement `resolve_drawing` in `src/services/image_results_service.py`: call `build_qualifying_rows` / `build_race_rows` for every cell, call `format_session_label` and `_label_from_status` for the two labels, resolve each entry's driver name through the person-name convention and its team name through the division's teams with the role-name fallback, and derive the two phase closures from `result_status`
- [X] T021 [US1] Implement `build_fill_spec` in `src/services/image_results_service.py`: count the capacity from the template, fill text through a `put` that empties rather than dashes, route determined-empty cells to `empty_quietly`, place `team_image`, `driver_flag` and `tyre` into `image_data`, set `recolour` for the entry holding the fastest-lap bonus, remove the unused rows' groups and mark their fields off-canvas, remove the two column groups and the block group on their conditions, and report the entry count through `row_count`
- [X] T022 [US1] Add `build_results_drawing` to `src/services/image_sample_data.py` fabricating one entry fewer than the rows the template declares — or exactly one where it declares a single row — drawn from the server's team configuration, exhibiting the enumerated cases of the wip-spec's § "Test data" in order, with a race points configuration conferring the fastest-lap bonus with no position limit
- [X] T023 [US1] Extend `build_spec` in `src/services/image_sample_data.py` to dispatch both results template keys through `image_results_service.build_fill_spec`, resolving the packaged `resources/flags`, `resources/teams` and `resources/tyres` directories as the calendar and lineup branches already resolve theirs
- [X] T024 [US1] Extend the team guard in `src/cogs/image_cog.py` so `/images test results` is rejected with a clear error where the server holds no team beyond the reserve team, alongside the existing lineup guard
- [X] T025 [US1] Verify both PNGs against [quickstart.md](./quickstart.md) § 2 — opened as rasterised images, never as SVG in a browser (Constitution XIV.14)

**Checkpoint**: both graphics can be produced and inspected without any league data. Every later story reuses this path.

---

## Phase 4: User Story 2 — Results posted through their lifecycle (Priority: P2)

**Goal**: with the `results` toggle on, each session posts as a PNG under its heading and lifecycle label, redrawn and replaced on each of the six occasions.

**Independent Test**: enable the toggle, post a session, close the penalty phase, then the appeal phase; confirm one message each time, the PNG replacing the table, the previous message removed only after the replacement appeared, and the sanction columns resolving in turn.

### Tests for User Story 2

- [X] T026 [P] [US2] Extend `tests/unit/test_results_post_service.py`: the image branch taken when the toggle is on and skipped when off; a cancelled session keeping its textual notice whatever the toggle says; and the submission channel staying textual
- [X] T027 [P] [US2] Add posting-order coverage to `tests/unit/test_results_post_service.py`: the replacement produced before the old message is deleted, the new id persisted into `session_results.results_message_id`, and a failed render leaving the previously posted message in place
- [X] T028 [P] [US2] Extend `tests/integration/test_image_module_flow.py` with the three lifecycle stages, asserting both sanction columns empty at provisional, the penalty column resolved at post-race penalty, and both resolved at final

### Implementation for User Story 2

- [X] T029 [US2] Add `src/services/image_results_post.py` with `results_enabled`, `render_png` and `try_post`, following the shape of `src/services/image_lineup_post.py`: render first, post the replacement, delete the previous message only once the replacement exists, then persist its id
- [X] T030 [US2] Resolve the `flag`, `team` and `tyre` directories from the server's image config inside `render_png` in `src/services/image_results_post.py`, and call `bot.image_render_service.render_for_posting` with the posting origin
- [X] T031 [US2] Hook the image branch into `post_session_results` in `src/services/results_post_service.py`, before the textual send — the single funnel through which `post_round_results`, `repost_results_for_division` and `delete_and_repost_final_results` all reach, so all six redraw occasions are covered by one branch (R7)
- [X] T032 [US2] Keep the heading and the lifecycle label as message text in `src/services/results_post_service.py`, giving only the table to the graphic, and leave a cancelled session's textual notice untouched
- [X] T033 [US2] Confirm the round's results submission channel remains textual in its entirety, the image path being reached only for the division's results channel

**Checkpoint**: a league can switch the aspect on and read its results as graphics through a full round.

---

## Phase 5: User Story 3 — A template's faults, before a round depends on it (Priority: P3)

**Goal**: both templates are checked at all three moments, reported separately, and refuse what they cannot draw.

**Independent Test**: name templates carrying each fault of [quickstart.md](./quickstart.md) § 5 and confirm each is rejected with the configuration left as it stood; run `/season review` against a faulty template and confirm approval is refused.

### Tests for User Story 3

- [X] T034 [P] [US3] Extend `tests/unit/test_image_validity_layers.py`: a missing mandatory whole-graphic field, a template declaring no row, a gap in the row numbering, a row missing a mandatory field, and a sibling's field — each fatal at the moment the template is named
- [X] T035 [P] [US3] Add to `tests/unit/test_image_validity_layers.py` the converse: a sound template accepted with no classification in view, and an identifier belonging to no catalogue ignored rather than reported
- [X] T036 [P] [US3] Extend `tests/unit/test_image_results_catalogue.py` with the pre-render tier — the entry count checked against the counted capacity, and a mandatory value that cannot be determined failing that render alone

### Implementation for User Story 3

- [X] T037 [US3] Add the sibling-field check to `CatalogueLayer` in `src/services/image_validity_service.py`, reading `sibling_row_fields` and reporting the offending field with the catalogue it belongs to (R2)
- [X] T038 [US3] Confirm `build_aspect_statuses` in `src/services/image_validity_service.py` names each of the two results templates separately in the `results` aspect's blocking reasons, and that `TEMPLATE_LABELS` carries a distinguishable label for each
- [X] T039 [US3] Confirm `/season review` and `/images config view` both report which of the qualifying and race templates is invalid, and that approval of a season is refused while either stands invalid
- [X] T040 [US3] Confirm `/images template results-qualifying` and `/images template results-race` reject a faulty file and leave the configuration as it stood

**Checkpoint**: a league learns its templates are unusable at the moment it configures them, not at the moment a round posts.

---

## Phase 6: User Story 4 — Degradations to staff, never to drivers (Priority: P4)

**Goal**: every notice reaches the logging channel naming the session it pertains to; a fatal error falls back to text for an uncommanded posting and rejects a commanded one; one failure never spreads.

**Independent Test**: draw a session whose driver has no nationality and one whose template is missing, and confirm the reporting and fallback of [quickstart.md](./quickstart.md) § 4.

### Tests for User Story 4

- [X] T041 [P] [US4] Add reporting coverage to `tests/unit/test_image_results_post.py`: a notice naming the season, division, round and session, and nothing reported into the division's results channel
- [X] T042 [P] [US4] Add fallback coverage to `tests/unit/test_image_results_post.py`: an uncommanded posting falling back to the textual table, a commanded one rejected with nothing posted, and the caller told what is at fault
- [X] T043 [P] [US4] Add isolation coverage to `tests/integration/test_image_module_flow.py`: one session's failure leaving the round's other sessions, the other divisions and the standings posting unaffected

### Implementation for User Story 4

- [X] T044 [US4] Implement `_report` in `src/services/image_results_post.py`, naming the season, the division, the round and the session in every notice and problem sent to the logging channel, and additionally alongside a triggering command's output
- [X] T045 [US4] Wire the `PostingDecision` outcomes in `src/services/image_results_post.py` so an uncommanded failure returns without posting and lets the caller's textual body run, and a commanded one returns the rejection message
- [X] T046 [US4] Ensure each session renders and fails on its own in `src/services/results_post_service.py`, so neither the round's other sessions, the other divisions, nor the standings posted alongside are prevented
- [X] T047 [US4] Ensure the **textual table** is what gets enqueued for retry where a generated image fails to post for a reason of the Discord service rather than of the generation, in `src/services/image_results_post.py`
- [X] T048 [US4] Confirm the fatal-error path of `/images test results` reports to its caller and posts nothing, having no textual counterpart to fall back to

**Checkpoint**: the aspect is safe to leave switched on unattended.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T049 Run `pytest tests/ -q` and compare against the T001 baseline; every delta is accounted for, and the only expected change to existing expectations is the penalty precision of T005
- [X] T050 Verify every graphic of [quickstart.md](./quickstart.md) §§ 2–6 as a rasterised PNG, including the overflow case naming the drivers that would have been dropped
- [X] T051 [P] Confirm no time, gap, interval, lap-count or penalty formatting exists anywhere in `src/services/image_results_service.py` — the utility places cells and does not make them (Constitution XIV.7)
- [X] T052 [P] Update `README.md` for what a league can now see: the `results` toggle drawing session tables as images, the two template commands, and the fastest-lap colour
- [X] T053 Invoke the `close-out` skill, which is **mandatory before reporting this work complete**: it reconciles `docs/wip-specs/image_module_specification.md` and `README.md` against what was built and against every decision taken in conversation

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)** — no dependencies
- **Foundational (Phase 2)** — depends on Setup; **blocks every user story**
- **US1 (Phase 3)** — depends on Phase 2 alone
- **US2 (Phase 4)** — depends on Phase 2; reuses US1's utility, so in practice follows it
- **US3 (Phase 5)** — depends on Phase 2 alone; independent of US1 and US2
- **US4 (Phase 6)** — depends on US2, being the reporting around a posting path
- **Polish (Phase 7)** — depends on every story to be delivered

### Within each story

- Tests are written first and confirmed failing before the implementation they cover
- Dataclasses before the resolution that fills them; resolution before the projection onto a template; projection before the posting

### Parallel opportunities

- T002 and T003 together
- T009, T013 and T015 are three different test files and run together, each after the implementation it covers
- **US1 and US3 can be built in parallel by two people** once Phase 2 is done: US3 touches only `image_validity_service.py` and the validity tests, US1 only the service, the sample data and the cog
- T041, T042 and T043 together
- T051 and T052 together

### Parallel example: after Phase 2

```bash
# Two independent tracks:
Track A (US1): T016, T017, T018 → T019 → T020 → T021 → T022 → T023 → T024 → T025
Track B (US3): T034, T035, T036 → T037 → T038 → T039 → T040
```

---

## Implementation Strategy

### MVP (User Story 1 only)

1. Phase 1 — baseline recorded
2. Phase 2 — the shared derivation and both catalogues, which is the bulk of the risk
3. Phase 3 — `/images test results` returning two inspectable PNGs
4. **Stop and validate** against quickstart § 2, as PNGs
5. A league manager can author and correct both templates from here, with no season in existence

### Incremental delivery

1. Setup + Foundational → the derivation is single and the catalogues are registered
2. + US1 → both graphics previewable (MVP)
3. + US2 → a division's results posted as graphics through a full round
4. + US3 → faults caught at configuration rather than at posting
5. + US4 → safe unattended
6. + Polish → docs reconciled, suite compared, close-out run

### The three things most likely to go wrong

1. **The refactor of Phase 2 silently changing the textual table.** T009 exists for this: the text output must be byte-identical save for the penalty precision. Run the existing results-table tests before touching anything else.
2. **A second derivation creeping into the utility.** The moment `image_results_service.py` formats a time, XIV.7 is broken and nothing will catch it but T051 and review.
3. **Verifying in a browser.** The rasteriser exposes flowed text, substituted fonts and unresolvable hrefs that a browser hides. T025 and T050 both say PNG for that reason.

---

## Notes

- `[P]` means a different file and no dependency on an incomplete task
- Tasks touching `results_formatter.py` (T004–T008) and `image_catalogues.py` (T010–T012) are sequential within their file and carry no `[P]`
- No migration is written: `session_results.results_message_id` already exists
- Commit after each task or logical group; stop at any checkpoint to validate a story on its own
