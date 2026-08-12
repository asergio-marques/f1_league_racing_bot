---
description: "Task list for calendar image generation"
---

# Tasks: Calendar Image Generation

**Input**: Design documents from `/specs/037-calendar-image-generation/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: Included. The plan's source layout names five test modules, and `CLAUDE.md` requires the
suite be run and compared before and after any change.

**Organization**: Grouped by user story so each is independently implementable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: The user story the task serves (US1–US4)
- Exact file paths are given in every task

## Path Conventions

Single project: `src/` and `tests/` at the repository root, matching the existing layout.

---

## Phase 1: Setup

**Purpose**: establish the baseline this work is measured against.

- [ ] T001 Run `pytest tests/ -q` from the repo root and record the failure count and the failing
      module names in this file under Notes. The suite has pre-existing failures (22 as of
      2026-08-12, in `test_attendance_tracking.py`, `test_rsvp_service.py`,
      `test_season_end_service.py`). Every later comparison is against this number, not against zero.
- [ ] T002 [P] Confirm the rasteriser resolves by running
      `python -c "from src.services.image_render_service import find_converter; print(find_converter())"`.
      A `None` result means every render will be refused before it starts and every visual check below
      will fail for the wrong reason; set the `INKSCAPE` environment variable if the probe misses it.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the field catalogue and the capacity model. Every user story reads these.

**⚠️ CRITICAL**: no user story work can begin until this phase is complete.

- [ ] T003 Extend `RowSpec` in `src/models/image_catalogues.py`: widen `capacity` to `int | None`
      where `None` means "count what the template declares", and add
      `assets: dict[str, str]` mapping a per-member field suffix to its asset class (so `image` →
      `track`). Existing integer capacities must keep behaving exactly as today.
- [ ] T004 Add `declared_capacity(root)` and `capacity_for(root)` to `RowSpec` in
      `src/models/image_catalogues.py`. `declared_capacity` scans ids matching `<prefix>_<n>_` through
      `FieldIndex` — so a member addressed by a **layer label** counts as one addressed by an `@id`
      (XIV.2) — requires contiguity from 1, and raises a named error on a gap or on no member at all.
      `capacity_for` returns the fixed integer where one is set, else delegates.
- [ ] T005 Give `FieldCatalogue.all_mandatory_ids()`, `all_known_ids()` and `capacity()` an optional
      `root` parameter in `src/models/image_catalogues.py`, used only when the capacity is derived.
      Callers passing nothing must continue to work against fixed capacities — that is every other
      image type.
- [ ] T006 Populate `CATALOGUES["calendar_template"]` in `src/models/image_catalogues.py` exactly as
      [contracts/calendar-catalogue.md](./contracts/calendar-catalogue.md) specifies: mandatory
      `division_name`; optional `season_number`, `division_tier`; a `RowSpec` with prefix `round`,
      `capacity=None`, the ten field suffixes, the five mandatory ones, and `assets={"image": "track"}`.
- [ ] T007 Update `declared_capacities()` in `src/models/image_catalogues.py` so a template-derived
      capacity is not reported as a fixed one. `placement_service._guard_image_capacity` reads this to
      guard **driver seats** and must not begin refusing placements because a calendar template is
      small — see [research.md § R3](./research.md).
- [ ] T008 Update `_verify_against_data` in `src/services/image_render_service.py` to obtain the
      capacity via `catalogue.capacity(root)` rather than `catalogue.capacity()`, leaving the three
      checks and their order untouched.
- [ ] T009 [P] Write `tests/test_image_calendar_catalogue.py`: the catalogue's shape; capacity derived
      from a template declaring N rounds; a gap in the numbering rejected; no round at all rejected; a
      round addressed by layer label counted; and `declared_capacities()` unchanged for the fourteen
      empty types.

**Checkpoint**: the catalogue is live. Layer 2 now applies to the calendar, and the pre-render checks
activate for it — by data, with no cog or command signature touched.

---

## Phase 3: User Story 1 — Preview the calendar before a season depends on it (Priority: P1) 🎯 MVP

**Goal**: `/images test calendar` returns a PNG built from fabricated data, so a manager can author
and correct a template with no season, division or round in existence.

**Independent Test**: enable the images module, name a calendar template, run
`/images test calendar`, and confirm the crop, the fields and the fallback behaviour match the
wip-spec's § "Test data".

### Tests for User Story 1

- [ ] T010 [P] [US1] Write `tests/test_image_calendar_fill.py`: a `CalendarDrawing` projects to the
      expected `FillSpec`; a mystery round carries "Mystery GP", "Mystery", "Mystery" and the datum
      "Mystery"; a normal round's format field is emptied; rounds beside the final one land in
      `remove`; a round whose track name matches no track record is fatal.
- [ ] T011 [P] [US1] Write `tests/test_image_calendar_crop.py`: for a template declaring M rounds, a
      division of N rounds for every N in 1..M cuts at round N's crop point. Assert on the
      **rasterised PNG's pixel height**, not the SVG `height` attribute — a crop that rewrites the
      attribute but not the `viewBox` passes the attribute check and fails the pixel one (XIV.14).

### Implementation for User Story 1

- [ ] T012 [P] [US1] Author `resources/tracks/mystery.svg` — generic, not league-specific, at the same
      aspect as the directory's ordinary assets, plain SVG with no `clipPath`, gradient or filter
      (XIV.6). This is what a mystery round's image field resolves to (FR-027).
- [ ] T013 [US1] Create `src/services/image_calendar_service.py` with the `CalendarRound` and
      `CalendarDrawing` shapes from [data-model.md](./data-model.md).
- [ ] T014 [US1] Implement `resolve_drawing(...)` in `src/services/image_calendar_service.py`: order
      rounds by number; read the track name and image datum straight off `Round.track_name`; join the
      tracks registry **by name** for country and grand prix name; substitute the mystery literals for
      a round of the mystery format; compute `final_round_index`, `rounds_beside_final` and
      `overflow`. Resolve fully before drawing anything, so the fatal checks precede the expensive work.
- [ ] T015 [US1] Implement `build_fill_spec(drawing, root, config)` in
      `src/services/image_calendar_service.py`: project a `CalendarDrawing` onto a `FillSpec` —
      text, `image_data` for the track class, `remove` for rounds beside the final one, `empty` for
      undeterminable optional values, `crop` naming the final round's crop point, and `row_count` for
      the capacity check. Dates and times go through the configured format and zone with the zone
      abbreviation appended (XIV.15).
- [ ] T016 [US1] Add the FR-026 notice to the crop step in `src/utils/svg_fill.py`: where the final
      **declared** round's crop point does not stand at the template's declared height, cut at that
      point anyway and raise a non-fatal notice naming the template. Do not reject such a template —
      it draws correctly for every division smaller than its capacity.
- [ ] T017 [US1] Replace the generic sample for the calendar kind in
      `src/services/image_sample_data.py`: fabricate "Test Division", tier 1, season 1, holding one
      round fewer than the template declares (or one round where the template declares a single one).
      Cover, as the round count allows, one round of each format including mystery; one whose track
      has no image file, to exercise the fallback and its notice; and dates spanning more than one
      month. Do **not** fabricate a round with no time — a round records date and time as one moment
      by design (see [research.md § R5](./research.md)). The other ten test kinds keep the generic
      sample unchanged.
- [ ] T018 [US1] Wire the calendar kind through `/images test` in `src/cogs/image_cog.py`, and reject
      with a clear error where the fabricated division would hold no round or the server's track list
      is empty. A fatal error here is reported to the caller and **never** falls back to text: this
      command has no textual counterpart (FR-022).

**Checkpoint**: a manager can author a template and see the result. US1 is shippable alone.

---

## Phase 4: User Story 2 — Learn a template is unusable before a season is approved (Priority: P2)

**Goal**: a faulty calendar template is caught when it is named, reported at season review with its
own reason, and blocks approval while it stands.

**Independent Test**: name templates with and without the mandatory fields and confirm acceptance or
rejection; run `/season review` with a faulty template and confirm it is named and approval refused.

**Depends on**: Phase 2 only. Does **not** depend on US1.

### Tests for User Story 2

- [ ] T019 [P] [US2] Write `tests/test_calendar_validity.py`: a template missing a mandatory field is
      rejected at naming with the configuration untouched; a gap in the round numbering is rejected; a
      structurally valid template is accepted without judging its round count; season review reports a
      capacity divergence as a **warning**; a faulty template refuses **approval**; and the report
      states the depth actually checked.

### Implementation for User Story 2

- [ ] T020 [US2] Extend `CatalogueLayer.check` in `src/services/image_validity_service.py` to derive
      the round count from the template before enumerating mandatory ids, then verify contiguity from
      1 and that each declared round carries all five mandatory round fields. Ratify no new layer —
      this is Layer 2's stated purpose (see [research.md § R2](./research.md)).
- [ ] T021 [US2] Ensure the report from `evaluate_template` records the depth actually applied so the
      calendar shows as checked to Layer 2 while the other fourteen types still show Layer 1. A type
      whose catalogue is empty must continue to be **skipped**, never passed (XIV.9, invariant 4).
- [ ] T022 [US2] Extend the season review's images section in `src/cogs/season_cog.py` to report the
      calendar at its checked depth, and to compare the configured template against the **greatest**
      round count of any division of the season — a divergence being a warning that does not refuse
      approval.
- [ ] T023 [US2] Ensure a faulty template refuses **approval** while leaving the review itself
      refusing nothing, per [contracts/commands.md](./contracts/commands.md).
- [ ] T024 [US2] Add a round-capacity guard to the command that adds a round to a division: where the
      images module is enabled, the `calendar` toggle is on, and the configured template declares
      fewer rounds than the division would then hold, refuse the command **with its change unapplied**
      and report the count, the capacity and the template (XIV.12). This is separate from
      `placement_service._guard_image_capacity`, which counts driver seats and stays as it is.

**Checkpoint**: a template cannot reach a season unnoticed. US1 and US2 both stand alone.

---

## Phase 5: User Story 3 — The league sees its calendar as a graphic at season approval (Priority: P3)

**Goal**: each division's calendar is drawn and posted at approval, with per-division isolation and a
textual fallback.

**Independent Test**: with the module enabled and the toggle on, approve a multi-division season and
confirm each calendar channel receives a PNG; force one division to fail and confirm it alone falls
back to text.

**Depends on**: Phase 2, and US1's drawing service (T013–T015).

### Tests for User Story 3

- [ ] T025 [P] [US3] Write `tests/test_calendar_post_service.py`: the message text is exactly
      `📅 **{division} — Race Calendar**`; one division's fatal error leaves the others posted as
      images; a fatal error falls back to text without refusing the approval; notices reach the log
      channel and never the calendar channel; the message id is persisted for both forms.

### Implementation for User Story 3

- [ ] T026 [P] [US3] Add `src/db/migrations/040_calendar_message_id.sql` adding
      `calendar_message_id TEXT` to `divisions`, nullable, defaulting to `NULL`.
- [ ] T027 [US3] Add `calendar_message_id: str | None = None` to `Division` in
      `src/models/division.py`, and read and write it in `src/services/season_service.py` — including
      `_row_to_division`, which must guard the column with the same `in keys` check the other optional
      columns use, so a database that has not run migration 040 still loads.
- [ ] T028 [US3] Create `src/services/calendar_post_service.py`: build a division's calendar in
      whichever form the configuration calls for, post it, and persist the resulting message id. The
      heading is the literal string the text path already emits — take it from one place so the two
      forms cannot drift.
- [ ] T029 [US3] Replace the inline calendar block in `src/cogs/season_cog.py` (commented
      `T017: Post calendar per division`) with a call into the new service. Preserve the existing
      textual output byte-for-byte when the module is disabled or the toggle is off (SC-006), and keep
      the loop going after a division fails — one failure must not abandon the rest.
- [ ] T030 [US3] Report a fatal error to the log channel and to the approving manager, and fall back
      to the textual calendar for that division. Approval itself is not refused: the posting is not
      the thing commanded (XIV.7).
- [ ] T031 [US3] Enqueue the **textual** calendar for retry where the posting fails for a Discord
      service reason rather than a generation reason (FR-020).

**Checkpoint**: the feature's visible purpose is delivered.

---

## Phase 6: User Story 4 — Redraw a calendar after the schedule changes (Priority: P4)

**Goal**: `/division calendar sync` replaces a division's calendar message with a freshly drawn one.

**Independent Test**: approve a season, amend a round, run the command, and confirm the old message is
gone, one new message stands, and the new id is persisted.

**Depends on**: US3's message id and posting service.

### Tests for User Story 4

- [ ] T032 [P] [US4] Extend `tests/test_calendar_post_service.py`: a successful sync deletes the old
      message only **after** the replacement is produced; a failed render deletes **nothing** and
      rejects the command; a division with no calendar channel is rejected; and two syncs under test
      mode leave exactly one calendar message.

### Implementation for User Story 4

- [ ] T033 [US4] Implement the replacement sequence in `src/services/calendar_post_service.py`:
      produce the replacement first, post it, then delete the old message named by
      `calendar_message_id`, then persist the new id. The ordering is the contract — a failure must
      never leave the channel with no calendar.
- [ ] T034 [US4] Add `/division calendar sync` per [contracts/commands.md](./contracts/commands.md):
      a division name parameter with the group's usual autocomplete, league-manager access, gated on
      **no** module, rejected with a clear error where the division has no calendar channel. Model it
      on `results standings sync` in `src/cogs/results_cog.py`.
- [ ] T035 [US4] Confirm the deletion is **not** routed through the forecast flow's test-mode
      suppression. `forecast_cleanup_service.delete_forecast_message` stays exactly as it is; the
      calendar's replacement deletion behaves identically in test mode and live (FR-017).

**Checkpoint**: all four stories are functional.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T036 [P] Update the README's template-authoring section: `round_<x>_*` in the naming paragraph is
      already covered, but add the calendar to whatever the README says about what each image type
      draws, and record that the last round's crop point should sit at the declared canvas height.
- [ ] T037 [P] Update `resources/README.md` and the README's **Reserved filenames** paragraph now that
      `resources/tracks/mystery.svg` actually exists — this was deliberately deferred at spec time
      rather than documenting a file that had not been created.
- [ ] T038 Run `pytest tests/ -q` and compare against the T001 baseline. Any new failure is this
      work's; the pre-existing ones are not.
- [ ] T039 Walk [quickstart.md](./quickstart.md) Scenario 1 and Scenario 4's ordering check by hand.
      These two are what the suite cannot prove: the first is a visual judgement on a PNG, the second
      depends on Discord's actual delete-then-post sequencing.
- [ ] T040 Invoke the `close-out` skill. This feature changed rules in
      `docs/wip-specs/image_module_specification.md` and behaviour a league sees, so both documents are
      in scope.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: blocks all four stories. The catalogue is what activates every
  downstream check.
- **US1 (Phase 3)**: needs Phase 2. Independently shippable.
- **US2 (Phase 4)**: needs Phase 2 only — **not** US1. Can run in parallel with US1.
- **US3 (Phase 5)**: needs Phase 2 and US1's drawing service (T013–T015).
- **US4 (Phase 6)**: needs US3's message id (T026–T027) and posting service (T028).
- **Polish (Phase 7)**: after the stories being delivered are complete.

### Story Dependency Graph

```text
Phase 2 (catalogue)
   ├──► US1 (preview) ──────► US3 (approval posting) ──► US4 (sync)
   └──► US2 (validity)
```

US2 is the only story with no dependency on another. US3 and US4 are genuinely sequential — the spec
ordered them that way, and pretending otherwise would produce tasks that cannot be executed.

### Within Each Story

Tests are written first and must fail before the implementation lands. Models before services,
services before commands, resolution before drawing.

### Parallel Opportunities

- T002 alongside T001.
- T009 alongside T003–T008 once the shapes exist.
- **US1 and US2 in parallel** once Phase 2 is done — different files throughout, and US2 touches
  neither the drawing service nor the sample data.
- T010 and T011 together; T012 alongside either.
- T026 alongside T025.
- T036 and T037 together.

---

## Parallel Example: after Phase 2

```bash
# Two developers, no file contention:
Developer A → US1: T010, T011, T012 in parallel, then T013 → T018
Developer B → US2: T019, then T020 → T024
```

```bash
# Within US1, launch the tests and the asset together:
Task: "Write tests/test_image_calendar_fill.py"
Task: "Write tests/test_image_calendar_crop.py"
Task: "Author resources/tracks/mystery.svg"
```

---

## Implementation Strategy

### MVP (US1 only)

1. Phase 1 → Phase 2 → Phase 3.
2. **Stop and validate**: run `/images test calendar` and read the PNG.
3. A manager can now author and correct a calendar template. That is real value with no season, no
   schema change and no risk to an existing posting path.

### Incremental delivery

1. Setup + Foundational → the catalogue is live and Layer 2 applies.
2. + US1 → previewable (**MVP**).
3. + US2 → trustworthy before a season depends on it.
4. + US3 → the league sees graphics; this is the first phase touching an existing posting path.
5. + US4 → the calendar can be moved on after approval.

Stopping after US2 leaves a coherent product: templates can be authored, previewed and validated,
and nothing a league currently sees has changed.

---

## Notes

- **Baseline (record at T001)**: _pending — fill in the failure count and module names._
- `[P]` means a different file with no dependency on incomplete work.
- Verify tests fail before implementing.
- Every visual check is made against the rasterised PNG, never the SVG in a browser (XIV.14).
- Commit after each task or logical group.
- The riskiest task is **T029**: it edits a posting path a league already depends on. SC-006 requires
  byte-identical textual output when the module is off, which is the check that keeps it honest.
