---

description: "Task list for standings image generation (040)"
---

# Tasks: Standings Image Generation

**Input**: Design documents from `/specs/040-standings-image-generation/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: included. Not because the spec asks for them but because `CLAUDE.md` makes the suite a
standing obligation — `pytest tests/ -q` is run before and after a change and compared, and the suite
is expected to pass in full. Test tasks here follow the naming already in `tests/unit/`.

**Organization**: grouped by user story. Phase 2 is genuinely blocking: the grid forms, the two
catalogues, the derived columns and the text-path split are what every story stands on.

**A note on US1 and US3.** The spec's US1 exercises the grid in its third scenario, but the grid is
US3's slice. US1's phase therefore delivers the preview command against a **classification-only**
template (spec US1 scenario 4), and quickstart § 3's grid checkboxes are validated at the end of
Phase 5. This is the honest reading of two stories that overlap by design.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: the user story the task serves (US1–US5); Setup, Foundational and Polish carry none

---

## Phase 1: Setup

**Purpose**: establish the baseline the change is measured against, and the assets the render needs.

- [X] T001 Run `pytest tests/ -q` from the repo root and record the counts; the baseline as of 2026-08-13 is 1399 passed, 1 skipped, 0 failed. Any pre-existing failure is investigated on a clean tree before proceeding, never written off
- [X] T002 [P] Author `resources/markers/gained.svg`, `resources/markers/lost.svg` and `resources/markers/unchanged.svg` — plain SVG, no `clipPath`, gradient, filter or text, authored at the 64 × 64 aspect `resources/README.md` records for the marker class (R10, FR-032)
- [X] T003 [P] Confirm `resources/flags/fallback.svg`, `resources/teams/fallback.svg`, `resources/tracks/fallback.svg` and `resources/markers/fallback.svg` are present, the asset outcomes of every story depending on them
- [X] T004 [P] Confirm `resources/templates/standings_drivers_template.svg` and `resources/templates/standings_constructors_template.svg` resolve, parse and declare a root canvas, and note how many rows, rounds and cars each declares

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the grid forms, the two catalogues, the single derivation of the three columns, and the
split that lets one championship fall back alone. **No user story can begin until this phase is
complete.**

**⚠️ Order matters within `image_catalogues.py`, within `standings_service.py` and within
`results_post_service.py`** — tasks touching one file are sequential and carry no `[P]`.

### The grid forms (R1, [contracts/standings-catalogue.md](./contracts/standings-catalogue.md))

- [X] T005 Add `columns: RowSpec | None` to `FieldCatalogue` in `src/models/image_catalogues.py` — a second top-level ordinal collection beside `rows`, counted against the division's calendar rather than against the classification
- [X] T006 Add `nested: NestedSpec | None` to `RowSpec` in `src/models/image_catalogues.py`, linking a row to the collection repeating within it; `NestedSpec` already takes its parent's id as a `stem`, so `field_id("row_3", 7, …)` needs no change
- [X] T007 Add `nested: NestedSpec | None` to `NestedSpec` in `src/models/image_catalogues.py` for the third level, and admit a capacity fixed **per containing member** — the count arrives through the binding, `capacity` stays `None`, and over-declaration is trimmed for that member alone (XIV.12, v4.5.0)
- [X] T008 Add `optional_unit: bool` to `RowSpec` and `NestedSpec` in `src/models/image_catalogues.py`: a template declaring no member of the collection is not faulty and every field of it and of anything nested inside is skipped, while `mandatory_fields` binds on every member a template does declare (XIV.3, v4.5.0)
- [X] T009 Declare the shared standings field sets and both `STANDINGS_DRIVERS_CATALOGUE` and `STANDINGS_CONSTRUCTORS_CATALOGUE` in `src/models/image_catalogues.py`, composing the common part once, and register them in `CATALOGUES` under `standings_drivers_template` and `standings_constructors_template`
- [X] T010 Extend `sibling_row_fields` in `src/models/image_catalogues.py` so the standings pair is a sibling relation like the results pair, returning `driver_name`/`driver_flag` against the constructors key and nothing of the drivers row against the drivers key
- [X] T011 [P] Add `tests/unit/test_image_standings_catalogue.py`: both catalogues' classifications; three-level id construction; rows, rounds and cars each counted from the template; a gap at any level raising `CapacityError`; a template declaring no round at all passing because the unit is optional; and `round_<z>_number` demanded once any round is declared

### The three derived columns (R4, [contracts/derived-columns.md](./contracts/derived-columns.md))

- [X] T012 Implement the reference-round lookup in `src/services/standings_service.py`: the most recent round of the division holding standings, strictly below the round drawn, stepping over cancelled and unrun rounds, and `None` where no earlier round holds any (R4)
- [X] T013 Implement `derive_movement` in `src/services/standings_service.py` returning per entry the gap to the leader, the previous position, the unsigned change and the direction (`gained`/`lost`/`unchanged`), or `None` for the whole record where the reference round does not exist or does not hold the entry — reading `standing_position` and never re-establishing the order
- [X] T014 Add the movement cell renderers to `src/utils/results_formatter.py` — the gap with its leading minus and empty for the leader, the unsigned change with `0` for unchanged — so the textual path can adopt the columns by calling rather than reimplementing (XIV.7)
- [X] T015 [P] Extend `tests/unit/test_standings_service.py`: the leader's empty gap; the step-back over a cancelled round and over an unrun one; the first round of a division yielding `None`; an entry absent from the reference round yielding `None`; and the three directions

### The text path learns to post one championship (R6, [contracts/standings-posting.md](./contracts/standings-posting.md))

- [X] T016 Split `post_standings` in `src/services/results_post_service.py` so section formatting and message composition are separable: `format_driver_standings` and `format_team_standings` each yield one championship's section with its sub-heading, and the joining of the two moves to the caller
- [X] T017 Add the per-championship message id read and write to `src/services/results_post_service.py` — `_get_standings_message_id` gaining a championship parameter, and both ids written on the top-ranked driver's row on every posting, textual or graphic
- [X] T018 [P] Extend `tests/unit/test_results_post_service.py` asserting the composed textual message is **byte-identical** to its previous output, the split having changed how it is assembled and not what it says

### Persistence

- [X] T019 [P] Add `src/db/migrations/041_constructor_standings_message_id.sql` adding a nullable `constructor_standings_message_id TEXT` to `driver_standings_snapshots`, with the header comment explaining why one column could not name two messages
- [X] T020 [P] Add `constructor_standings_message_id: int | None = None` to `DriverStandingsSnapshot` in `src/models/standings_snapshot.py` and read it in `from_row`, defensively as the existing column is read

**Checkpoint**: the grid forms exist, both catalogues are registered, the three columns have exactly one derivation, the text path can post one section, and the second id has somewhere to live. User stories may now begin.

---

## Phase 3: User Story 1 — Preview both graphics (Priority: P1) 🎯 MVP

**Goal**: `/images test standings` returns two PNGs built from fabricated data, with no season, division, round or submitted result in existence.

**Independent Test**: enable the module, name both templates, run `/images test standings`, and confirm two PNGs — the classification cases of [quickstart.md](./quickstart.md) § 3 visible in the rasterised output, and § 4's classification-only template drawing without fault.

### Tests for User Story 1

- [X] T021 [P] [US1] Add `tests/unit/test_image_standings_service.py` covering `resolve_drawing` for both championships: the heading fields, the lifecycle label from `result_status`, the driver name through the person-name convention, the team name through the team convention with its role-name fallback, and the drivers graphic drawing the division team seating the driver **now** rather than any round's team
- [X] T022 [P] [US1] Add `tests/unit/test_image_standings_fill.py` covering `build_fill_spec`: rows filled to the entry count, unused `row_<x>_group` removed with its fields marked off-canvas, the position filled from the ordinal, the movement block removed whole where the record is `None`, the previous position emptied in the same case, and no notice raised for either
- [X] T023 [P] [US1] Add sample-data coverage to `tests/unit/test_image_sample_data.py` asserting the fabricated classifications exhibit every enumerated case of the wip-spec's § "Test data" where the row count allows, and drop the excess cases where it does not

### Implementation for User Story 1

- [X] T024 [US1] Add `src/services/image_standings_service.py` with the `StandingsDrawing`, `StandingsEntry`, `Movement` and `RoundCells` dataclasses of [data-model.md](./data-model.md), and a `StandingsDataError` raised for a fatal disagreement before anything is drawn
- [X] T025 [US1] Implement `resolve_drawing` in `src/services/image_standings_service.py` for the classification: read position and points from the snapshot, call `derive_movement` for the three columns, resolve names through the two conventions, and compose the driver classification exactly as the textual standings do — every non-reserve driver, and a reserve only where the reserves toggle is on and they hold points or have raced (FR-011)
- [X] T026 [US1] Implement `build_fill_spec` in `src/services/image_standings_service.py` for the classification: count the row capacity from the template, fill text through a `put` that empties rather than dashes, place `team_image`, `driver_flag` and `position_change_marker` into `image_data`, remove the movement group or empty-and-remove its parts where no group is declared, remove unused rows' groups and mark their fields off-canvas, and report the entry count through `row_count`
- [X] T027 [US1] Add `build_standings_drawing` to `src/services/image_sample_data.py` fabricating one entry fewer than the rows the template declares — or exactly one where it declares a single row — drawn from the server's team configuration, labelled "Final Results" for "Test Division" of tier 1 and season 1, exhibiting the enumerated cases in the spec's order
- [X] T028 [US1] Give the fabricated drivers nationalities the signup wizard accepts, at least one being that recorded for a driver who stated none, so the flag path is exercised in both states (FR-062)
- [X] T029 [US1] Extend `build_spec` in `src/services/image_sample_data.py` to dispatch both standings template keys through `image_standings_service.build_fill_spec`, resolving the packaged `resources/flags`, `resources/teams`, `resources/tracks` and `resources/markers` directories as the existing branches resolve theirs
- [X] T030 [US1] Extend the team guard in `src/cogs/image_cog.py` so `/images test standings` is rejected with a clear error where the server holds no team beyond the reserve team (FR-063), and report a fatal error to the invoking manager without falling back to any textual output (FR-064)
- [X] T031 [US1] Verify both PNGs against [quickstart.md](./quickstart.md) § 3 (classification rows) and § 4 — opened as rasterised images, never as SVG in a browser (Constitution XIV.14). Confirm all three markers draw their own artwork and **not** the class fallback

**Checkpoint**: both graphics can be produced and inspected without any league data. Every later story reuses this path.

---

## Phase 4: User Story 2 — Standings posted through the round lifecycle (Priority: P2)

**Goal**: with the `standings` toggle on, a division's two championships post as two graphics under their headings and lifecycle labels, redrawn and replaced on each of the seven occasions.

**Independent Test**: enable the toggle, post a round's results as provisional, then close the penalty phase; confirm two messages each time, drivers first, the PNGs replacing the table, and each previous message removed only after its replacement appeared.

### Tests for User Story 2

- [X] T032 [P] [US2] Extend `tests/unit/test_results_post_service.py`: the image branch taken when the toggle is on and skipped when off; a cancelled round posting nothing whatever the toggle says (FR-050); and the standings replaced in the division's standings channel and there alone
- [X] T033 [US2] Add posting-order coverage to `tests/unit/test_results_post_service.py`: each replacement produced before its old message is deleted, both ids persisted on the top-ranked driver's row, and a failed render leaving the previously posted message in place (same file as T032 — sequential)
- [X] T034 [P] [US2] Add `tests/unit/test_image_standings_post.py` covering the per-championship failure matrix of [contracts/standings-posting.md](./contracts/standings-posting.md) — including one championship falling back to text carrying **its section alone** while the other posts as a graphic (FR-052)

### Implementation for User Story 2

- [X] T035 [US2] Add `src/services/image_standings_post.py` with `standings_enabled`, `render_png` and `try_post`, following the shape of `src/services/image_results_post.py`: render first, post the replacement, delete the previous message only once the replacement exists, then persist its id
- [X] T036 [US2] Resolve the `flag`, `team`, `track` and `marker` directories from the server's image config inside `render_png` in `src/services/image_standings_post.py`, and call `bot.image_render_service.render_for_posting` with the posting origin
- [X] T037 [US2] Implement the two-graphic posting in `src/services/image_standings_post.py`: drivers first and constructors second, each carrying its heading and lifecycle label as message text and its graphic as an attachment, and each answering for itself so one failure never prevents the other (FR-046, FR-052)
- [X] T038 [US2] Hook the image branch into `post_standings` in `src/services/results_post_service.py`, before the textual send — the single funnel all five call sites reach, so FR-049's seven redraw occasions are covered by one branch (R7)
- [X] T039 [US2] Route a Discord-side posting failure to enqueue the **textual** standings for retry rather than the image, in `src/services/image_standings_post.py` (FR-056), and let one division's failure never touch another's (FR-055)

**Checkpoint**: a league can switch the aspect on and read both championships as graphics through a full round, each falling back on its own.

---

## Phase 5: User Story 3 — Read the whole season on one graphic (Priority: P3)

**Goal**: a template declaring the round catalogue draws every round the division holds, run or not, with a cell per session — and on the constructors graphic, a cell per car of the team.

**Independent Test**: author one template with the round catalogue and one without, run `/images test standings` against each, and confirm the first draws a grid and the second a bare classification with no fault reported.

### Tests for User Story 3

- [X] T040 [P] [US3] Extend `tests/unit/test_image_standings_service.py` with cell resolution: a finishing position, the three outcome literals, a driver dropped by disqualification carrying `DSQ` and not the position the drop gave them, and the four emptying cases of FR-024
- [X] T041 [P] [US3] Extend `tests/unit/test_image_standings_fill.py` with the grid: excess rounds removing the heading group, every row's cell group and every car group from one decision (FR-039); the same by field where no group is declared; and cars beyond a row's team's seats trimmed for that row alone (FR-041)
- [X] T042 [US3] Add car-allocation coverage to `tests/unit/test_image_standings_service.py`: a seated driver on their seat's ordinal, a seated driver who drove nothing leaving that car free, a non-seated driver taking the lowest free car, and a car nobody drove having its group removed (same file as T040 — sequential)
- [X] T043 [P] [US3] Extend `tests/unit/test_result_submission_service.py` with the cross-session team check: a reserve recorded for team A in one session and team B in another of the same round rejected on the second submission, and the same reserve under team A throughout accepted (FR-065)

### Implementation for User Story 3

- [X] T044 [US3] Extend `resolve_drawing` in `src/services/image_standings_service.py` to resolve the drivers grid: every round the division holds whether run or not, a cell per session carrying the recorded position or outcome literal, and an empty string for each of FR-024's four determined-empty cases
- [X] T045 [US3] Implement the constructors car allocation in `src/services/image_standings_service.py` per FR-026 — drivers found by the team role their session result records, seated drivers on their seat ordinal, non-seated drivers on the lowest free car — treating "one driver, one team per round" as an invariant guaranteed by T047 and **not** re-adjudicating it
- [X] T046 [US3] Extend `build_fill_spec` in `src/services/image_standings_service.py` to project the grid: the round heading fields, the per-row cell groups, the per-car groups, and the removal of all three families from one capacity decision on a round ordinal (R2)
- [X] T047 [US3] Add the cross-session team check to `_validate` in `src/services/result_submission_service.py`: a submission recording a driver under a team role different from the one another ACTIVE session of the same round already records is rejected, naming the driver, the team already recorded and the conflicting session. Forward-only — no backfill, the bot not being in production (FR-065, R9)
- [X] T048 [US3] Extend `build_standings_drawing` in `src/services/image_sample_data.py` to fabricate a calendar as long as the template declares, standing after all but two rounds — or after the first where fewer than three are declared, and none where the template declares no round — with at least one run round of the sprint format and one of the normal format (FR-057, FR-058, FR-059)
- [X] T049 [US3] Verify both grid PNGs against [quickstart.md](./quickstart.md) § 3 (grid rows) and § 4, as rasterised images

**Checkpoint**: a league can draw its whole season on one graphic, and the constructors grid rests on an invariant the data guarantee.

---

## Phase 6: User Story 4 — A template's faults, before a season depends on it (Priority: P4)

**Goal**: a faulty standings template is refused at the moment it is named; a season that would overflow its drivers template fails review; the assignment that would overflow it is refused.

**Independent Test**: configure a deliberately faulty template and confirm the configuration is left as it stood with a named reason; then seat drivers up to and past a template's row count and confirm the last command is refused.

### Tests for User Story 4

- [X] T050 [P] [US4] Extend `tests/unit/test_image_validity_service.py` with the standings structural checks of FR-035 — ≥1 row contiguous from 1 with every mandatory row field, rounds contiguous each carrying its number, cars contiguous — each refusing at all three moments (XIV.9)
- [X] T051 [US4] Add sibling-field coverage to `tests/unit/test_image_validity_service.py`: a constructors row field in the drivers template refused as the wrong file for that slot, and an id belonging to neither catalogue ignored (FR-007) (same file as T050 — sequential)
- [X] T052 [P] [US4] Add row-ceiling coverage to `tests/unit/test_placement_service.py`: a driver assignment that would carry a division past the drivers template's rows refused with the assignment unapplied, and a team assignment likewise against the constructors template (FR-044)

### Implementation for User Story 4

- [X] T053 [US4] Extend `CatalogueLayer` in `src/services/image_validity_service.py` with the standings structural checks, reading the same catalogue object the fill pipeline reads (XIV.10) and naming the individual template at fault (XIV.9, specific attribution)
- [X] T054 [US4] Report the drivers and constructors templates separately at `/season review` and `/images config view`, never as one pair, in `src/cogs/season_cog.py` and `src/cogs/image_cog.py` (FR-045)
- [X] T055 [US4] Add the row-ceiling check at season review in `src/cogs/season_cog.py`: a division that would place more drivers in its classification than the drivers template has rows fails validation naming the division, and approval is refused while it stands (FR-043)
- [X] T056 [US4] Add the assignment refusal to `src/services/placement_service.py` for both championships, rejecting the command with its change unapplied and naming the template and the counts (FR-044)
- [ ] T057 [US4] Verify [quickstart.md](./quickstart.md) §§ 5–6 — every fault refused at configuration with its own reason, and the two ceiling refusals

**Checkpoint**: a league is told its templates cannot hold its season before the season runs.

---

## Phase 7: User Story 5 — Degradations to staff, never to drivers (Priority: P5)

**Goal**: every non-fatal degradation reaches the logging channel naming the championship, and none reaches a channel drivers read.

**Independent Test**: draw a standings graphic for a division with a driver whose nationality has no flag file, and confirm the graphic is produced, the fallback drawn, and the notice appears only in the logging channel.

### Tests for User Story 5

- [X] T058 [P] [US5] Extend `tests/unit/test_image_standings_post.py`: notices routed to the logging channel with season, division, round and championship in the detail (FR-053), and never to the standings channel
- [X] T059 [P] [US5] Add nationality coverage to `tests/unit/test_image_standings_service.py`: an absent nationality removing the flag with a non-fatal error, a nationality with no file drawing the fallback with a notice, and nationality collection switched off at source drawing no flags anywhere and reporting **nothing** (FR-028)

### Implementation for User Story 5

- [X] T060 [US5] Implement notice reporting in `src/services/image_standings_post.py` reusing `image_results_post.report_notices`, naming the season, the division, the round and the championship, and additionally reporting alongside a triggering command's output (FR-053)
- [X] T061 [US5] Honour the configured-absence suppression for nationality in `src/services/image_standings_service.py`: where `signup nationality toggle` is off at its source, the flags are absent by configuration and no notice is raised (FR-028, XIV.4)
- [ ] T062 [US5] Verify [quickstart.md](./quickstart.md) § 9 — all three nationality states, with every notice in the logging channel and none in the standings channel

**Checkpoint**: safe to run unattended.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T063 Run `pytest tests/ -q` and compare against the T001 baseline; every delta is accounted for, and the textual standings output is unchanged (T018)
- [X] T064 [P] Confirm no subtraction, comparison or sign decision over points or positions exists anywhere in `src/services/image_standings_service.py` — the utility receives the finished movement record and does not make it (Constitution XIV.7, [contracts/derived-columns.md](./contracts/derived-columns.md))
- [X] T065 [P] Update `README.md` for what a league can now see: the `standings` toggle drawing both championships as images, the two template commands, the optional season grid, and the row ceiling refusing an overflowing assignment
- [X] T066 Add `gained.svg`, `lost.svg` and `unchanged.svg` to the shipped reserved filenames in `README.md` and `resources/README.md`, beside `tracks/mystery.svg` — deferred from the v4.5.0 amendment until the files existed (FR-033) (shares `README.md` with T065 — sequential)
- [ ] T067 Verify every graphic of [quickstart.md](./quickstart.md) §§ 3–9 as a rasterised PNG, including the overflow cases naming what would have been dropped
- [ ] T068 Invoke the `close-out` skill, which is **mandatory before reporting this work complete**: it reconciles `docs/wip-specs/image_module_specification.md` and `README.md` against what was built and against every decision taken in conversation

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)** — no dependencies
- **Foundational (Phase 2)** — depends on Setup; **blocks every user story**
- **US1 (Phase 3)** — depends on Phase 2 alone
- **US2 (Phase 4)** — depends on Phase 2; reuses US1's utility, so in practice follows it
- **US3 (Phase 5)** — depends on US1's utility, which it extends; independent of US2
- **US4 (Phase 6)** — depends on Phase 2 alone; independent of US1, US2 and US3
- **US5 (Phase 7)** — depends on US2, being the reporting around a posting path
- **Polish (Phase 8)** — depends on every story to be delivered

### Within each story

- Tests are written first and confirmed failing before the implementation they cover
- Dataclasses before the resolution that fills them; resolution before the projection onto a template; projection before the posting

### Parallel opportunities

- T002, T003 and T004 together
- T019 and T020 run alongside the whole of Phase 2's other three tracks — neither touches a shared file
- T011, T015 and T018 are three different test files and run together, each after the implementation it covers
- **US1 and US4 can be built in parallel by two people** once Phase 2 is done: US4 touches only `image_validity_service.py`, `placement_service.py`, the cogs' reporting and their tests; US1 only the service, the sample data and the cog's guard
- T041 and T043 together (T040 and T042 share `test_image_standings_service.py` and are sequential)
- T052 alongside T050 (T051 shares `test_image_validity_service.py` with T050 and follows it)
- T034 alongside T032 (T033 shares `test_results_post_service.py` with T032 and follows it)
- T058 and T059 together
- T064 alongside T065 (T066 also edits `README.md` and follows T065)

### Parallel example: after Phase 2

```bash
# Two independent tracks:
Track A (US1 → US3): T021, T022, T023 → T024 → … → T031 → T040–T043 → T044 → … → T049
Track B (US4):       T050, T051, T052 → T053 → T054 → T055 → T056 → T057
```

---

## Implementation Strategy

### MVP (User Story 1 only)

1. Phase 1 — baseline recorded, the three markers authored
2. Phase 2 — the grid forms, both catalogues, the single derivation and the text split; the bulk of the risk
3. Phase 3 — `/images test standings` returning two inspectable PNGs from a classification-only template
4. **Stop and validate** against quickstart §§ 3–4, as PNGs
5. A league manager can author and correct both templates from here, with no season in existence

### Incremental delivery

1. Setup + Foundational → the forms exist, the derivation is single, the text path can post one section
2. + US1 → both classifications previewable (MVP)
3. + US2 → a division's standings posted as two graphics, each falling back on its own
4. + US3 → the whole season on one graphic, on a guaranteed invariant
5. + US4 → faults caught at configuration rather than at posting
6. + US5 → safe unattended
7. + Polish → docs reconciled, suite compared, close-out run

### The four things most likely to go wrong

1. **A second derivation creeping into the utility.** The moment `image_standings_service.py` subtracts two points totals, XIV.7 is broken and nothing will catch it but T064 and review. The utility receives the movement record finished.
2. **The Phase 2 split silently changing the textual standings.** T018 exists for this: the composed message must be byte-identical. Run the existing standings tests before touching anything else.
3. **Removing a column's cells by containment.** A cell belongs to its row and its round both, and a node has one parent — so a round's cells leave through T046's three-family removal, not through the heading group. Getting this wrong draws a grid with orphaned cells that no test of the heading will catch.
4. **Verifying in a browser.** The rasteriser exposes flowed text, substituted fonts and unresolvable hrefs that a browser hides. T031, T049, T057, T062 and T067 all say PNG for that reason.

---

## Notes

- `[P]` means a different file and no dependency on an incomplete task
- Tasks touching `image_catalogues.py` (T005–T010), `standings_service.py` (T012–T013) and `results_post_service.py` (T016–T017) are sequential within their file and carry no `[P]`
- One migration is written (T019); it needs no data repair, the bot not being in production
- The three marker files (T002) are shipped by the module because their vocabulary is the module's own (XIV.13, v4.5.0) — without them every row draws the fallback and raises a notice
- Commit after each task or logical group; stop at any checkpoint to validate a story on its own

## Found during implementation

Work done outside the numbered tasks, recorded so it is not re-derived:

- **T031 (PNG verification) found two real defects that no unit test caught.**
  1. The gap to the leader was nested inside the movement record, so it blanked for every entry
     the reference round did not hold. `derive_gaps` was split out of `derive_movement`, and
     `contracts/derived-columns.md` had predicted exactly this ("the gap is never in that state").
  2. Neither shipped standings template declared `inline-size` on any of its **327** name fields,
     which XIV.5 makes a MUST — a long driver name ran across the team line beneath it. Widths were
     set from each column's geometry (drivers 140/120, constructors 165/72) and truncation verified
     as a rasterised PNG.
- **`row_<x>_position` is filled from the recorded standing position, not the row ordinal.** The two
  part company when a reserve who raced is filtered out; the textual standings print the recorded
  one, and XIV.7 forbids the graphic disagreeing. The wip-spec sentence asserting they are equal was
  corrected, along with FR-008 and `contracts/standings-catalogue.md`.
- **A pre-existing XIV.2 defect in three other image types**, not fixed here: `put_optional` empties
  an optional heading without removing its `<field>_group`, so calendar, lineup and results all draw
  the label and plate around an emptied value. The standings catalogues declare those groups so this
  type does not inherit it. Worth its own increment.
- `docs/wip-specs/…` § "The room a text is given" and `README.md` both said a name field *may* or
  *should* declare `inline-size` where the constitution says MUST; both corrected.
