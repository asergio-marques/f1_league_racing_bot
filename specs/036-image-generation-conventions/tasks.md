---

description: "Task list for Template Verification & Graphic Conventions"
---

# Tasks: Template Verification & Graphic Conventions

**Input**: Design documents from `/specs/036-image-generation-conventions/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: Included. Not because TDD was requested, but because [plan.md](./plan.md)'s source layout
names four new test modules and an extension to two existing ones — the engine here is pure
(no database, no Discord) and is meant to be tested without a bot.

**Organization**: Grouped by user story so each can be implemented and validated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1–US5, mapping to the user stories in [spec.md](./spec.md)
- Exact file paths in every task

## Path Conventions

Single project: `src/` and `tests/` at the repository root, per [plan.md](./plan.md).

---

## Phase 1: Setup

**Purpose**: Establish a known-good baseline before touching delivered 035 code

- [X] T001 Run `pytest tests/ -q` and record the baseline pass count, so any regression introduced by the 035 changes in [research.md](./research.md) R9 is attributable
- [X] T002 [P] Confirm the rasteriser resolves by calling `find_converter()` from `src/services/image_render_service.py`; if it does not, set the `INKSCAPE` environment variable before proceeding (its PATH entry is known-broken on the development box)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The resolution, catalogue and error primitives every user story depends on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T003 Add the Inkscape namespace (`http://www.inkscape.org/namespaces/inkscape`) to `NSMAP` in `src/utils/svg_document.py`
- [X] T004 Implement `FieldIndex` in `src/utils/svg_document.py` with `by_id`, `by_label` (restricted to `<g inkscape:groupmode="layer">` only), `resolve(name)` consulting id then label, and `group_for(name)` finding `<name>_group`; supersedes `index_by_id` per [contracts/field-resolution.md](./contracts/field-resolution.md)
- [X] T005 [P] Write unit tests for resolution precedence in `tests/unit/test_svg_field_resolution.py`: id-only, label-only, both-present-id-wins, neither, and a non-layer `inkscape:label` that must NOT resolve
- [X] T006 Implement named parse faults in `src/utils/svg_document.py`: classify `etree.XMLSyntaxError` into double-hyphen-in-comment, unclosed/mismatched tag, undefined entity, stray `&`, bad encoding, with a fallback of "not well-formed XML at line N"; send the parser's own string to the application log only, never into the raised message
- [X] T007 [P] Write unit tests for parse faults in `tests/unit/test_svg_parse_faults.py`, asserting for each fixture that the message names the fault and that the raw lxml text appears nowhere in it
- [X] T008 [P] Create `src/models/image_catalogues.py` with `FieldCatalogue` (mandatory, optional, assets, rows), `RowSpec` (prefix, capacity, fields), and fifteen entries keyed by `TEMPLATE_COLUMNS`, every one declared empty per [data-model.md](./data-model.md)
- [X] T009 [P] Add `ASSET_CLASS_DIRECTORIES` (asset class → `ImageConfig` column) and the notice kinds `ASSET_FALLBACK_USED` and `OPTIONAL_FIELD_EMPTIED` to `src/models/image_constants.py`
- [X] T010 [P] Add the `Problem` dataclass (kind, template_key, field_id, detail) and its **ten** kinds to `src/models/image_module.py`, and change `RenderOutcome.problem` from `str` to `Problem` preserving the invariant that `png_paths` is empty whenever it is set
- [X] T010a Convert all six `RenderOutcome(problem=…)` constructions in `src/services/image_render_service.py` to `Problem` instances: `RASTERISER` for converter-absent, converter-failure, timeout and oversize output; `UNKNOWN_IMAGE_TYPE` for the unknown-template lookup; the failing layer's own kind for an invalid `ValidityReport`; `NOT_SVG` for the `load_svg` failure; `UNRESOLVED_VALUE` for `result.unresolved`. Without this the type change in T010 does not compile (depends on T010)
- [X] T011 Migrate every `index_by_id` call site in `src/utils/svg_fill.py` to `FieldIndex`, so all six operations inherit the label fallback at once (depends on T004)
- [X] T011a [P] Migrate the fastest-lap contrast check in `src/cogs/image_cog.py` (lines 550, 568) from `index_by_id` to `FieldIndex.resolve`, so `fastest_lap_background` honours the layer-label fallback like any other field (depends on T004)
- [X] T011b [P] Migrate `src/services/image_sample_data.py` (lines 12, 54) from `index_by_id` to `FieldIndex`. **Not mechanical**: line 54's `declared` set must become the union of ids *and* layer labels, otherwise a template addressed entirely by labels reads as declaring nothing and `/images test` reports every field unknown (depends on T004)
- [X] T012 Add a per-call parse cache keyed by resolved path to `TemplateContext` in `src/services/image_validity_service.py`, so Layer 1 and Layer 2 share one parse and a fifteen-template review does not read each file twice (research R5)
- [X] T013 Implement `CatalogueLayer` (`number = LAYER_CATALOGUE = 2`) in `src/services/image_validity_service.py`, checking every mandatory field resolves via `FieldIndex`, with `applies_to()` returning **False** for an empty catalogue; register it in `LAYERS` (depends on T004, T008, T012)
- [X] T014 [P] Extend `tests/unit/test_image_validity_layers.py`: an empty catalogue leaves `depth_checked` at 1 and `depth_summary` still lists catalogue conformance as not applied; a populated catalogue with a missing field fails at layer 2 naming that field

**Checkpoint**: resolution, catalogues, Layer 2 and the error types exist. User stories can begin.

---

## Phase 3: User Story 1 - A template that cannot serve is refused at the moment it is named (Priority: P1) 🎯 MVP

**Goal**: `/images template <kind>` validates before it writes, and a rejection leaves the stored configuration exactly as it stood.

**Independent Test**: Configure a template with each defect class — wrong extension, absent file, malformed SVG, missing mandatory field — and confirm the command is refused each time and `/images config view` still shows the previous value.

### Tests for User Story 1

- [X] T015 [P] [US1] Add integration cases to `tests/integration/test_image_module_flow.py`: for each of the four rejection classes, assert the command is refused AND that reading the config back returns the pre-command value
- [X] T016 [P] [US1] Add unit cases to `tests/unit/test_image_config_service.py` for the extension check: `.svg`, `.SVG`, `.Svg` accepted; `.txt`, no extension, and a name ending `.svg` that is a directory rejected

### Implementation for User Story 1

- [X] T017 [US1] Add candidate-config construction to `src/services/image_config_service.py`: return an `ImageConfig` with one template column overridden via `dataclasses.replace`, without persisting (research R2)
- [X] T018 [US1] Implement the ordered check sequence as a shared function in `src/services/image_validity_service.py` — extension, then existence, then parse, then mandatory fields — returning a `Problem` or None, per [contracts/verification.md](./contracts/verification.md); this same function serves US2 (depends on T013)
- [X] T019 [US1] Rewrite `_set_template_filename` in `src/cogs/image_cog.py` to build the candidate, run T018 against it, and call `set_field` **only** when it returns None (depends on T017, T018)
- [X] T020 [US1] Render a `Problem` into a rejection message in `src/cogs/image_cog.py` naming the file and the fault, and for `NOT_FOUND` the full path searched (FR-006)
- [X] T021 [US1] Log rejections to the calculation log through the existing `_log` in `src/cogs/image_cog.py`, so a refused configuration is as auditable as an accepted one (Principle V)

**Checkpoint**: US1 is independently testable. No configuration can be stored that the module cannot use.

---

## Phase 4: User Story 2 - Season review reports; approval blocks (Priority: P2)

**Goal**: With the module enabled, all fifteen templates are verified from one evaluation. `/season review` names every failure; `/season approve` refuses while any remains.

**Independent Test**: Break two of the fifteen in different ways, run `/season review` and confirm both are named separately with distinct reasons, then run `/season approve` and confirm it is refused.

### Tests for User Story 2

- [X] T022 [P] [US2] Add integration cases to `tests/integration/test_image_module_flow.py`: two differently-broken templates are both named individually by review **and** by the approval refusal; a sound set contributes no finding and no failure; with the module disabled neither command produces an image finding (FR-009)

### Implementation for User Story 2

- [X] T023 [US2] Add an image-template gate to `season_approve` in `src/cogs/season_cog.py`, alongside the existing R&S, points and signup gates, reusing the T018 sequence over all fifteen templates (depends on T018)
- [X] T024 [US2] Ensure the refusal message names the individual template and its own reason for each failure in `src/cogs/season_cog.py` — a message naming a count or a group does not satisfy FR-008
- [X] T025 [US2] Confirm the existing directory-level shared-reason behaviour in `evaluate_all_templates` in `src/services/image_validity_service.py` survives: an unresolvable template directory yields one reason across fifteen reports, not fifteen near-identical lines
- [X] T026 [US2] Extend `_build_image_review_section` in `src/cogs/season_cog.py` so `/season review` names each failing template and its reason (FR-008), and reports the new Layer 2 depth honestly via `depth_summary`
- [X] T026a [US2] Confirm review and approval read the **same** evaluation in `src/cogs/season_cog.py`, so the two surfaces cannot disagree about whether a template is usable (FR-007). `/season review` refuses nothing (FR-008a)

**Checkpoint**: US1 and US2 both work independently.

---

## Phase 5: User Story 5 - A failure reaches the right audience (Priority: P2)

**Goal**: The same fault behaves oppositely depending on who asked, and no error text ever reaches a driver-read channel.

**Independent Test**: Trigger one fatal condition twice — once by command, once at a scheduled horizon — and confirm the first is refused with an explanation and posts nothing, while the second posts the text output.

### Tests for User Story 5

- [X] T027 [P] [US5] Add integration cases to `tests/integration/test_image_module_flow.py`: identical fault under `COMMANDED` posts nothing and returns a fault statement; under `SCHEDULED` posts the text fallback

### Implementation for User Story 5

- [X] T028 [P] [US5] Add the `PostingOrigin` enum (`COMMANDED`, `SCHEDULED`) to `src/models/image_module.py`
- [X] T029 [US5] Make `posting_origin` a **required** argument of the render-and-post entry point in `src/services/image_render_service.py` — never inferred and never defaulted, per research R6 (depends on T028)
- [X] T030 [US5] Implement the `COMMANDED` branch in `src/services/image_render_service.py`: on a `Problem`, post nothing to any channel and return the fault for the caller to surface (FR-030)
- [X] T031 [US5] Implement the `SCHEDULED` branch in `src/services/image_render_service.py`: on a `Problem`, fall back to the traditional text output (FR-029)
- [X] T032 [US5] Route notices in `src/services/image_render_service.py` to the calculation log channel always, and additionally alongside a command's own output when the origin is `COMMANDED` (FR-031)
- [X] T033 [US5] Audit every error path in `src/services/image_render_service.py` and `src/cogs/image_cog.py` and confirm by enumeration that each terminates at the log channel, an ephemeral reply, or a command followup — never a driver-read channel (FR-032)

**Checkpoint**: US1, US2 and US5 all work independently.

---

## Phase 6: User Story 3 - A generation re-checks the template against the data it is about to draw (Priority: P3)

**Goal**: Immediately before drawing, the module verifies the template against the concrete values, and classifies every miss against the catalogue.

**Independent Test**: Configure a sound template, change the data so a mandatory value can no longer be determined, trigger a generation, and confirm no image is produced and the failure is reported.

### Tests for User Story 3

- [X] T034 [P] [US3] Add integration cases to `tests/integration/test_image_module_flow.py`: an undeterminable mandatory value produces no image; an undeterminable optional value produces the image with that field emptied and a notice
- [X] T035 [P] [US3] Write unit tests in `tests/unit/test_asset_resolver.py` for normalisation (`Red Bull Racing`, `São Paulo`, `Emilia-Romagna`, whitespace, a datum normalising to empty) and for the four fallback outcomes in [contracts/asset-resolution.md](./contracts/asset-resolution.md)

### Implementation for User Story 3

- [X] T036 [P] [US3] Create `src/utils/asset_resolver.py` with `normalise(text)` — trim, lowercase, NFKD, drop combining marks, non-alphanumeric runs to a single underscore, strip leading/trailing underscores — and `resolve_asset(directory, datum)` returning the path, the fallback, or nothing (research R7). The name differs from the POC's `normalize()` by house style only; the repo is British-English throughout, cf. `src/utils/colour.py`. Behaviour is identical, and T035 asserts it against the POC's documented cases
- [X] T037 [US3] Wire asset resolution into `src/utils/svg_fill.py` so image fills resolve through the catalogue's asset class rather than a caller-supplied path (depends on T036, T008)
- [X] T038 [US3] Classify an unresolved asset in `src/utils/svg_fill.py`: fallback present → use it and raise `ASSET_FALLBACK_USED` naming the field and the datum; absent and mandatory → `Problem`; absent and optional → empty the field or remove its `_group` and raise `OPTIONAL_FIELD_EMPTIED`
- [X] T039 [US3] Implement the pre-generation mandatory-field check against concrete data in `src/services/image_render_service.py`, distinguishing "absent from the template" (FR-012) from "value undeterminable" (FR-011) in the reported `Problem`
- [X] T040 [US3] Implement the capacity check at generation in `src/services/image_render_service.py`: rows of data exceeding the catalogue's `RowSpec.capacity` is a `CAPACITY_EXCEEDED` problem reporting the count, the capacity and the template
- [X] T041 [US3] Add the division-capacity guard to `assign_driver` in `src/services/placement_service.py`, reading capacities from the catalogue module and refusing the change when it would exceed one; inert while every catalogue is empty (see Complexity Tracking in [plan.md](./plan.md))
- [X] T042 [P] [US3] Add unit cases to `tests/unit/test_image_validity_layers.py` — beside T014's, which already exercise catalogue-driven behaviour — confirming the capacity guard passes for every division while catalogues are empty, and refuses once a `RowSpec` declares a capacity smaller than the roster

**Checkpoint**: generation-time verification is complete and correctly classified.

---

## Phase 7: User Story 4 - A manager authors a template in an SVG editor and it works (Priority: P3)

**Goal**: Layer labels, `_group` wrappers and declared text room all behave as an author drawing in Inkscape would expect.

**Independent Test**: Author one template using a layer label instead of an id, one using a `_group` wrapper, and one declaring `inline-size`, and confirm each behaves as specified.

### Tests for User Story 4

- [X] T043 [P] [US4] Add unit cases to `tests/unit/test_svg_field_resolution.py` for group semantics: group declared and value absent → whole group gone, field untouched; no group → field alone emptied; **a `_group` wrapping a field the catalogue does not name is honoured identically (FR-025)**; nested groups → inner goes with outer; canvas height unchanged either way

### Implementation for User Story 4

- [X] T044 [US4] Implement `_group`-based removal in `src/utils/svg_fill.py` using `FieldIndex.group_for`, removing the group entire and leaving the field itself untouched (FR-023, depends on T004)
- [X] T045 [US4] Implement the empty-versus-remove distinction for optional fields in `src/utils/svg_fill.py`, per the catalogue's classification and spec A-003
- [X] T046 [US4] Assert in `src/utils/svg_fill.py` that group removal never rewrites the root `width`, `height` or `viewBox` (FR-026) — the vertical crop remains the only thing that may, and it belongs to the calendar type
- [X] T047 [US4] Add a row-id helper to `src/models/image_catalogues.py` constructing `row_<x>_<field>` from a `RowSpec`, so no utility concatenates row ids itself (Constitution XIV.11)
- [X] T048 [US4] Confirm and, if needed, correct `src/utils/svg_fill.py` so a field declaring `shape-inside` without `inline-size` is treated as a wrapping field (spec A-002), and one declaring `inline-size` alone is truncated (FR-036)

**Checkpoint**: all five user stories are independently functional.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T049 [P] Remove the superseded `index_by_id` from `src/utils/svg_document.py` once every caller has moved to `FieldIndex` (depends on T011, T011a, T011b — all four call sites across three files)
- [X] T050 [P] Confirm `README.md`'s "Templates: what the bot expects" covers FR-035 (font substitution is host-dependent), FR-041 (asset aspect and letterboxing) and FR-047 (`text-transform` is ignored); fill any gap
- [X] T051 [P] Confirm `README.md` documents the `fallback.svg` convention wherever asset directories are described (FR-043)
- [X] T052 Run `pytest tests/ -q` and compare against the T001 baseline; no 035 test may regress
- [X] T052a [P] Confirm the 035-inherited requirements listed in spec A-009 (FR-016, FR-017, FR-033, FR-034, FR-037, FR-039, FR-040) still hold, by checking that the existing tests covering recolour merging, canvas honouring, font substitution and single-line text bounds are present and passing — these have no implementation task, so T052's run is all that stands behind them
- [ ] T053 ⚠️ NEEDS A LIVE SERVER — Walk [quickstart.md](./quickstart.md) §2 and confirm every rejection leaves the stored configuration unchanged
- [ ] T054 ⚠️ NEEDS A LIVE SERVER — Walk [quickstart.md](./quickstart.md) §3–§5 in a live test server: season gating, authoring conventions, asset fallback
- [ ] T055 ⚠️ NEEDS A LIVE SERVER — Walk [quickstart.md](./quickstart.md) §6 watching every channel the bot posts to, confirming commanded and scheduled postings differ and nothing reaches a driver-read channel
- [X] T056 Inspect the PNGs returned by `/images test`, not the SVGs in a browser (Constitution XIV.14, [quickstart.md](./quickstart.md) §7)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup — **blocks every user story**
- **US1 (Phase 3)**: depends on Foundational
- **US2 (Phase 4)**: depends on Foundational **and on T018 from US1** — the shared check sequence
- **US5 (Phase 5)**: depends on Foundational only
- **US3 (Phase 6)**: depends on Foundational only
- **US4 (Phase 7)**: depends on Foundational only
- **Polish (Phase 8)**: depends on all desired stories

### The one cross-story dependency

US2 reuses the ordered check sequence built in T018. This is deliberate — two verification paths
that could disagree is exactly what [contracts/verification.md](./contracts/verification.md)
forbids. US2 is therefore not startable before T018 lands, though everything else in its phase is
independent.

US3, US4 and US5 have no cross-story dependencies and can proceed in any order after Phase 2.

### Within each story

- Tests before implementation
- Models before services, services before cogs
- `FieldIndex` (T004) gates T011, T011a, T011b, T013 and T044
- The `Problem` type change (T010) gates T010a, and **breaks the build until T010a lands** — keep
  them adjacent
- `index_by_id` may not be deleted (T049) until all three migrations are done

### Parallel Opportunities

- Phase 2: T005, T007, T008, T009, T010, T011a, T011b and T014 are `[P]` — seven different files
- Phase 3: T015 and T016 in parallel; then T017 and T018 in parallel; T019 joins them
- Phase 6: T035 and T036 in parallel with T034
- Phase 8: T049, T050, T051 and T052a in parallel
- After Phase 2, US3, US4 and US5 can be worked by three people at once

---

## Parallel Example: Phase 2 Foundational

```bash
# Seven independent files, no ordering between them:
Task: "Unit tests for resolution precedence in tests/unit/test_svg_field_resolution.py"   # T005
Task: "Unit tests for parse faults in tests/unit/test_svg_parse_faults.py"                # T007
Task: "Create src/models/image_catalogues.py with fifteen empty catalogues"               # T008
Task: "Add ASSET_CLASS_DIRECTORIES and two notice kinds to src/models/image_constants.py" # T009
Task: "Add Problem dataclass to src/models/image_module.py"                               # T010
Task: "Migrate the fastest-lap check in src/cogs/image_cog.py to FieldIndex"               # T011a
Task: "Migrate src/services/image_sample_data.py to FieldIndex"                            # T011b
```

## Parallel Example: after Phase 2, three developers

```bash
Developer A: Phase 3 (US1) → then Phase 4 (US2), which needs A's own T018
Developer B: Phase 5 (US5) — independent
Developer C: Phase 6 (US3) → then Phase 7 (US4)
```

---

## Implementation Strategy

### MVP (User Story 1 only)

1. Phase 1 Setup
2. Phase 2 Foundational — **blocks everything**
3. Phase 3 US1
4. **STOP and VALIDATE**: [quickstart.md](./quickstart.md) §2 — every rejection leaves the stored configuration untouched

That alone closes the defect that matters most: today a league manager can store a filename the
module cannot use, and only discover it on a race weekend.

### Incremental delivery

1. Setup + Foundational → resolution, catalogues, Layer 2 and error types exist
2. + US1 → configuration cannot be poisoned (**MVP**)
3. + US2 → a season cannot be approved on broken templates
4. + US5 → failures reach the right audience, and never the drivers
5. + US3 → generation-time verification and asset fallback
6. + US4 → authoring conventions honoured end to end

Each increment stands alone. None breaks a previous one.

---

## Notes

- Four files delivered in 035 change here — see [research.md](./research.md) R9. T001's baseline is
  what makes a regression in them visible.
- Every catalogue ships **empty**. Populating one is what a later image-type session does, and it
  switches on the mandatory-field check at all three verification moments, Layer 2 and the capacity
  guard at once, with no other edit (Constitution XIV.10).
- No command is added, removed or renamed. If a task appears to need one, the plan has drifted.
- Commit after each task or logical group; stop at any checkpoint to validate a story on its own.
