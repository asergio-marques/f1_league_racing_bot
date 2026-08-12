---

description: "Task list for lineup image generation"
---

# Tasks: Lineup Image Generation

**Input**: Design documents from `/specs/038-lineup-image-generation/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/lineup-catalogue.md

**Tests**: Included. The template treats tests as optional, but this repo's baseline is 1135 passing
tests and the 037 calendar increment shipped five test files
(`test_image_calendar_catalogue.py`, `test_image_calendar_crop.py`, `test_image_calendar_fill.py`,
`test_calendar_validity.py`, `test_calendar_post_service.py`). CLAUDE.md requires the suite to pass
in full. Test tasks below mirror that naming.

**Organization**: Grouped by user story so each can be implemented, tested and demonstrated on its
own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete work)
- **[Story]**: US1–US5, mapping to spec.md
- Exact file paths in every description

---

## Phase 1: Setup

**Purpose**: Establish the baseline and the fixture every later phase tests against.

- [X] T001 Run `pytest tests/ -q` on the clean branch and record the result; it must match the 1135 passed / 1 skipped / 0 failed baseline in `specs/038-lineup-image-generation/research.md`
- [X] T002 [P] Add a keyed lineup SVG fixture — two teams of two seats plus four reserve slots — to `tests/unit/test_image_lineup_fill.py`, following the inline-SVG pattern used by `tests/unit/test_image_calendar_fill.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Teach the catalogue to express a keyed, nested, singleton collection with a data-fixed
capacity. Nothing in US1, US3, US4 or US5 can begin until this is done.

**⚠️ CRITICAL**: Every task here lands in `src/models/image_catalogues.py` or threads one argument
through two call sites. `RowSpec` must be left untouched and the calendar must behave identically —
that is the acceptance condition for the whole phase.

- [X] T003 Add `NestedSpec` frozen dataclass (prefix, capacity, fields, mandatory_fields, `first_member_mandatory_only`, assets) to `src/models/image_catalogues.py` per data-model.md §1
- [X] T004 Add `KeyedSpec` frozen dataclass (prefix, fields, mandatory_fields, assets, nested, `capacity_from_data`) to `src/models/image_catalogues.py`
- [X] T005 Add `SingletonSpec` frozen dataclass (name, fields, mandatory_fields, assets, nested) to `src/models/image_catalogues.py`, supporting a **mandatory group** per Constitution XIV.2
- [X] T006 Add `LineupBinding` frozen dataclass (`team_keys`, `seats`) with its three invariants asserted, to `src/models/image_catalogues.py`
- [X] T007 Extend `FieldCatalogue` with `keyed` and `singleton` fields and update `is_empty` so a type declaring only a keyed collection is not read as unspecified, in `src/models/image_catalogues.py`
- [X] T008 Add the optional `binding` parameter to `FieldCatalogue.all_mandatory_ids` and `all_known_ids` in `src/models/image_catalogues.py`; with `binding=None` the lineup yields only `division_name`, `reserve_group`, `reserve_driver_1_name` (research R2, contract §5)
- [X] T009 Add `FieldCatalogue.divergent_members(root, binding)` to `src/models/image_catalogues.py`, naming teams and seats declared-but-unbound and bound-but-undeclared (research R3, Constitution XIV.12 data-fixed)
- [X] T010 Declare `LINEUP_CATALOGUE` and register it under `lineup_template` in `src/models/image_catalogues.py`, exactly per `contracts/lineup-catalogue.md` §§1–3
- [X] T011 Add `binding: object | None = None` to `FillSpec` in `src/utils/svg_fill.py` and pass `spec.binding` through `_verify_against_data` in `src/services/image_render_service.py`
- [X] T012 [P] Unit-test the catalogue shapes in `tests/unit/test_image_lineup_catalogue.py`: binding-free vs bound enumeration, `divergent_members` in both directions, reserve capacity counting, and per-member classification of `reserve_driver_<y>_name`
- [X] T013 Confirm `declared_capacities()` in `src/models/image_catalogues.py` still returns nothing for `lineup_template`, so `placement_service._guard_image_capacity` is not engaged by team or seat collections (research R8)

**Checkpoint**: `pytest tests/unit/test_image_calendar_*.py -q` passes unchanged. The calendar is the
regression canary for this phase.

---

## Phase 3: User Story 1 — Preview the lineup graphic (Priority: P1) 🎯 MVP

**Goal**: `/images test lineup` returns a PNG built from fabricated data, with no season, division or
driver in existence.

**Independent Test**: Enable the module, name a template drawn against the server's team list, run
`/images test lineup`, confirm the PNG matches the wip-spec's § "Test data".

### Tests for User Story 1

- [X] T014 [P] [US1] Resolution tests in `tests/unit/test_image_lineup_service.py`: the five-link driver-name chain, seat ordering by number, unoccupied-seat emptying, duplicate-key fatality
- [X] T015 [P] [US1] Fill-spec tests in `tests/unit/test_image_lineup_fill.py`: keyed ids built via `normalise`, nested seat ids, `reserve_group` removal when no reserve driver, asset data for team/flag/portrait

### Implementation for User Story 1

- [X] T016 [US1] Create `src/services/image_lineup_service.py` with `LineupSeat`, `LineupTeam`, `LineupDrawing` dataclasses per data-model.md §1, modelled on `src/services/image_calendar_service.py`
- [X] T017 [US1] Implement `resolve_drawing()` in `src/services/image_lineup_service.py`, taking a pre-resolved display-name mapping rather than a `discord.Guild` (research R9), and raising `LineupDataError` for a duplicate normalised key
- [X] T018 [US1] Implement the driver-name chain in `src/services/image_lineup_service.py` — server display name, signup server display name, signup username, test display name, user id — emitting no Discord mention (FR-005)
- [X] T019 [US1] Resolve flag, portrait and team-image data in `src/services/image_lineup_service.py`: flag by normalised nationality, portrait by Discord user id, team image by normalised team name including the reserve team (FR-006)
- [X] T020 [US1] Add `nationality_collected` to the drawing and suppress the notice for a configured absence in `src/services/image_lineup_service.py` (FR-009, research R11)
- [X] T021 [US1] Implement `build_fill_spec()` in `src/services/image_lineup_service.py`: fill declared fields, empty unoccupied seat names, remove their flag and image fields, remove `reserve_group` where the division fields no reserve driver, attach the `LineupBinding`
- [X] T022 [US1] Implement `build_lineup_drawing(root)` in `src/services/image_sample_data.py` per research R10 — server's teams, one team wholly empty, reserve drivers to slots − 1, nationalities including `"Other"`, fabricated ids that resolve to no portrait
- [X] T023 [US1] Wire `lineup_template` into `build_spec()` in `src/services/image_sample_data.py` beside the calendar branch
- [X] T024 [US1] Reject `/images test lineup` with a clear error where the server holds no team beyond the reserve team, in `src/cogs/image_cog.py` (FR-030)
- [X] T025 [US1] Verify the rendered output as a **rasterised PNG**, never as SVG in a browser (Constitution XIV.14), per `quickstart.md` Scenario 1

**Checkpoint**: A manager can author and correct a lineup template with nothing else built.

---

## Phase 4: User Story 2 — Team names a template can address (Priority: P2)

**Goal**: A name that cannot become a template identifier is refused at the moment it is set,
module enabled or not.

**Independent Test**: With the images module **disabled**, run `/team add` and `/team rename` with
each invalid shape and confirm rejection; run `/season review` with an offending team and confirm it
is named and validation fails.

> **Independent of Phase 2.** This story needs only `utils.asset_resolver.normalise`, which already
> exists. It can be built in parallel with the foundational phase.

### Tests for User Story 2

- [X] T026 [P] [US2] Extend `tests/unit/test_team_service.py` with the four rejection rules and the two deliberate exemptions (current name of a rename, name of a remove) per data-model.md §3
- [X] T027 [P] [US2] Extend `tests/unit/test_team_cog.py` to assert rejection messages surface while the images module is disabled (FR-012)

### Implementation for User Story 2

- [X] T028 [US2] Add pure `validate_team_name(name, existing_keys)` to `src/services/team_service.py` returning an error string or `None`, per data-model.md §3
- [X] T029 [US2] Call it from `add_default_team` and `rename_default_team` in `src/services/team_service.py`, raising `ValueError` as the protected-name check already does
- [X] T030 [US2] Call it from `season_team_add` and `season_team_rename` in `src/services/team_service.py`, scoping uniqueness to the division rather than the server (research R7)
- [X] T031 [US2] Ensure a reserve team is created in the server team configuration whenever that configuration is read or written and none is present, in `src/services/team_service.py` (FR-014)
- [X] T032 [US2] Add team-name validation to `/season review` in `src/cogs/season_cog.py`, naming **every** offending team of every division and of the server list, and failing validation (FR-013)
- [X] T033 [US2] Confirm an already-approved season is not re-validated and that no team is renamed or removed by the rule's introduction, in `src/cogs/season_cog.py` (FR-013)

**Checkpoint**: No league can reach a state where two teams claim one template block.

---

## Phase 5: User Story 3 — The three verification moments (Priority: P3)

**Goal**: A manager is told what a lineup template cannot draw, at the earliest moment the data
exists, with the right severity at each.

**Independent Test**: Name templates with and without the team-independent mandatory fields; run
`/season review` against divergent divisions. Confirm rejection, warning and validation failure land
where `quickstart.md` Scenario 3 says.

### Tests for User Story 3

- [X] T034 [P] [US3] Layer tests in `tests/unit/test_lineup_validity.py`: `CatalogueLayer` evaluates the lineup binding-free, rejects a missing `reserve_group`, and reports genuine depth 2 rather than a skip
- [X] T035 [P] [US3] Integration test in `tests/integration/test_image_module_flow.py` covering all three moments and the gated uniformity check

### Implementation for User Story 3

- [X] T036 [US3] Confirm `CatalogueLayer.check` in `src/services/image_validity_service.py` calls `all_mandatory_ids(root)` with **no** binding for the lineup, so a stand-in finding can never make a template invalid everywhere (research R4)
- [X] T037 [US3] Verify reserve slot contiguity from 1 as a rejection at template-naming time, in `src/services/image_validity_service.py` (Constitution XIV.11)
- [X] T038 [US3] Implement `stand_in_binding()` in `src/services/image_lineup_service.py` — teams of the season under setup, else the server's team configuration, else `None` (research R5)
- [X] T039 [US3] Add the accept-with-warning outcome to `_set_template_filename` in `src/cogs/image_cog.py`: write the filename, report the stand-in divergence below the success line in the shape `format_notices` produces
- [X] T040 [US3] Compare the lineup template against **every** division at `/season review` in `src/cogs/season_cog.py`, a divergence failing validation and naming the division and the team or seat (FR-017)
- [X] T041 [US3] Add the division-uniformity check to `/season review` in `src/cogs/season_cog.py`, **gated** on the images module being enabled and the `lineup` toggle being on, naming the divisions that differ (FR-018)
- [X] T042 [US3] Confirm `/season approve` refuses while any of the above stands, reusing the single evaluation `/season review` reports, in `src/cogs/season_cog.py`

**Checkpoint**: A season cannot be approved with a lineup template that will fall back to text.

---

## Phase 6: User Story 4 — The lineup of record (Priority: P4)

**Goal**: Each division's lineup channel carries a drawn graphic, redrawn on every occasion the
textual lineup is refreshed today, with the textual path untouched.

**Independent Test**: With the module enabled and the toggle on, approve a season, then assign,
unassign and sack drivers; confirm the channel holds exactly one message throughout. Force a fatal
error in one division and confirm it alone falls back.

### Tests for User Story 4

- [X] T043 [P] [US4] Post-service tests in `tests/unit/test_lineup_post_service.py`: replacement ordering, the previous message surviving a failed rebuild, per-division fallback isolation
- [X] T044 [P] [US4] Regression test in `tests/unit/test_placement_service_team_role.py` asserting the textual path keeps its present delete-then-build order when the toggle is off (FR-025a, SC-007)

### Implementation for User Story 4

- [X] T045 [US4] Create `src/services/image_lineup_post.py` with `try_post(guild, division_id, origin)` returning `NOT_APPLICABLE` where the module is off, the toggle is off, or no template is configured (research R6)
- [X] T046 [US4] Build the PNG **before** deleting the previous message, deleting only on success and persisting the new id, in `src/services/image_lineup_post.py` (FR-025)
- [X] T047 [US4] Route failures through `ImageRenderService.render_for_posting` with the correct `PostingOrigin`, so the commanded/uncommanded split is not re-derived, in `src/services/image_lineup_post.py` (Constitution XIV.7)
- [X] T048 [US4] Add the guard clause calling `try_post` at the head of `_refresh_lineup_post` in `src/services/placement_service.py`, leaving the existing textual body **entirely unmodified** below it (FR-025a)
- [X] T049 [US4] Confirm the three callers need no change and are left unmodified: `src/services/placement_service.py` (assign, unassign), `src/services/attendance_service.py` line ~1109 (autoreserve, autosack), `src/cogs/season_cog.py` line ~3354 (approval)
- [X] T050 [US4] Confirm the attendance module's RSVP-deadline reserve distribution does **not** trigger a redraw, in `src/services/attendance_service.py` (FR-024)
- [X] T051 [US4] Report non-fatal errors to the logging channel naming the division, never in the lineup channel, in `src/services/image_lineup_post.py` (FR-020)
- [X] T052 [US4] Ensure the failure of one division does not prevent others being drawn, in `src/services/image_lineup_post.py` (FR-021, SC-003)
- [X] T053 [US4] Add the reserve-overflow guard to driver assignment in `src/services/placement_service.py`, modelled on `season_cog._calendar_round_overflow`, refusing the placement with its change unapplied and naming count, capacity and template (research R8)
- [X] T054 [US4] Preserve the existing `SIGNUP_LINEUP_POSTED` audit entry on both paths, in `src/services/image_lineup_post.py` and `src/services/placement_service.py` (Constitution V)

**Checkpoint**: The league sees its lineup as a graphic, and the textual path is provably unchanged.

---

## Phase 7: User Story 5 — Command surfaces (Priority: P5)

**Goal**: `/team lineup` answers with the graphic; `/season review` posts it alongside the textual
lineup. Neither disturbs the lineup of record.

**Independent Test**: Run `/team lineup` for one division and for all, `public` both ways; run
`/season review`. Confirm `divisions.lineup_message_id` is untouched and the lineup channel is not
written to.

### Tests for User Story 5

- [X] T055 [P] [US5] Command-surface tests in `tests/unit/test_team_cog.py` and `tests/unit/test_lineup_post_service.py`: `public` honoured, one image per division, lineup-of-record untouched

### Implementation for User Story 5

- [X] T056 [US5] Post the graphic in place of the textual output from `/team lineup` in `src/cogs/team_cog.py`, honouring `public` and producing one image per division when invoked for more than one (FR-026)
- [X] T057 [US5] Post the graphic **in addition to** the existing textual lineup at `/season review` in `src/cogs/season_cog.py` (FR-027)
- [X] T058 [US5] Ensure neither command records the image as the division's lineup message nor deletes the message in the lineup channel, in `src/cogs/team_cog.py` and `src/cogs/season_cog.py` (FR-028)
- [X] T059 [US5] Reject either command on a fatal error, naming what is at fault and posting nothing, in `src/cogs/team_cog.py` and `src/cogs/season_cog.py` — a commanded posting never falls back (FR-021)

**Checkpoint**: All five stories independently functional.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T060 [P] Confirm test mode behaves identically to live mode in generation, posting and replacement — no branch on the test-mode flag in `src/services/image_lineup_service.py`, `src/services/image_lineup_post.py` or `src/services/placement_service.py` — and add the assertion to `tests/integration/test_image_module_flow.py` (FR-035, FR-036)
- [X] T061 Walk every scenario in `specs/038-lineup-image-generation/quickstart.md`, verifying each graphic as a rasterised PNG
- [X] T062 Run `pytest tests/ -q` and compare against the 1135 passed / 1 skipped / 0 failed baseline; any failure is a real one and must not be written off as pre-existing
- [X] T063 [P] Update `README.md` for the lineup image behaviour, the new team-name rules, and a note that `resources/templates/lineup_template.svg` is an example to author against rather than a servable default
- [X] T064 Invoke the `close-out` skill before reporting the feature complete, per CLAUDE.md

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: blocks US1, US3, US4, US5 — but **not US2**
- **US1 (Phase 3)**: after Phase 2
- **US2 (Phase 4)**: after Phase 1 only — the one story independent of the catalogue work
- **US3 (Phase 5)**: after Phase 2; shares `image_lineup_service.py` with US1 (T038)
- **US4 (Phase 6)**: after Phase 2 and US1 (needs a working render)
- **US5 (Phase 7)**: after US4 (reuses `image_lineup_post.py`)
- **Polish (Phase 8)**: after all desired stories

### Within Each Story

Tests before implementation. Dataclasses before the functions that build them. Services before cogs.

### Parallel Opportunities

- **T002 with all of Phase 2** — the fixture depends on nothing
- **The whole of US2 (T026–T033) with the whole of Phase 2** — different files, no shared symbol.
  This is the single largest parallelisation available and the reason US2 is not P1 despite being
  buildable first.
- T012 with T013; T014 with T015; T026 with T027; T034 with T035; T043 with T044
- T063 with T060

### Parallel Example: Foundational plus US2

```bash
# Developer A — Phase 2, all in src/models/image_catalogues.py
Task: "T003 NestedSpec"  →  "T010 LINEUP_CATALOGUE"  →  "T011 thread binding"

# Developer B — Phase 4, all in src/services/team_service.py and src/cogs/
Task: "T028 validate_team_name"  →  "T032 season review naming"
```

---

## Implementation Strategy

### MVP (User Story 1 only)

1. Phase 1 → Phase 2 → Phase 3.
2. **STOP and VALIDATE**: `/images test lineup` returns a correct PNG with no season data.
3. That alone is shippable value: the lineup is the one template a league must author itself, and
   this is the tool for authoring it.

### Incremental Delivery

MVP → **US2** (names constrained, a safety rail with no visible output) → **US3** (a template is
trusted before a season depends on it) → **US4** (the league sees the graphic) → **US5** (the two
convenience surfaces).

US4 is deliberately late: it is the visible purpose but carries the highest risk to an existing flow
touched by three modules, and is worth nothing until US1 and US3 make the template trustworthy.

### The two things most likely to go wrong

1. **A refactor that unifies the image and textual refresh paths.** T048 exists to prevent it. The
   textual body is to be left alone, not tidied. SC-007 is the check.
2. **Widening `RowSpec` instead of adding the three new spec types.** T003–T005 add types beside it;
   the calendar tests at the Phase 2 checkpoint are the canary.

---

## Notes

- `[P]` = different files, no dependency on incomplete work
- No database migration is written: `divisions.lineup_message_id` (v2.8.0) already carries the state
- Verify every graphic as a rasterised PNG, never as SVG in a browser (Constitution XIV.14)
- Commit after each task or logical group
