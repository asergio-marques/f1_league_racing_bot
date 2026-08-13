---

description: "Task list for attendance image generation (041)"
---

# Tasks: Attendance Image Generation

**Input**: Design documents from `/specs/041-attendance-image-generation/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: included. Not because the spec asks for them but because `CLAUDE.md` makes the suite a
standing obligation — `pytest tests/ -q` is run before and after a change and compared, and the suite
is expected to pass in full. Each contract in [contracts/](./contracts/) closes with its own test
obligations, and those are what the test tasks below discharge.

**Organization**: grouped by user story. Phase 2 is genuinely blocking for the two catalogues and the
sibling widening; it also carries three changes to the **attendance module's own flows** that are
independent of every story and ship value on their own.

**A note on Phase 2's last two tracks.** The replacement reordering (T012–T014) and the failed-call
report (T015–T017) are not prerequisites of any story — they are corrections to the textual flows this
feature is about to attach itself to. They are placed first because both edit functions that later
phases also edit, and because the second closes a hole in which a failed check-in post is permanently
recorded as flawless attendance. Either can be cherry-picked and shipped alone.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: the user story the task serves (US1–US5); Setup, Foundational and Polish carry none

---

## Phase 1: Setup

**Purpose**: establish the baseline the change is measured against, and confirm what the render needs
already exists.

- [ ] T001 Run `pytest tests/ -q` from the repo root and record the counts; the baseline as of 2026-08-13 is 1498 passed, 1 skipped, 0 failed. Any pre-existing failure is investigated on a clean tree before proceeding, never written off
- [ ] T002 [P] Confirm `resources/flags/fallback.svg`, `resources/teams/fallback.svg` and `resources/tracks/fallback.svg` are present, and that `resources/tracks/mystery.svg` ships — the mystery round of both graphics resolves its image from the datum "Mystery" and must not fall back
- [ ] T003 [P] Confirm `resources/templates/attendance_template.svg` and `resources/templates/rsvp_template.svg` resolve, parse and declare a root canvas, and note how many rows, rounds and sessions each declares. **No asset class and no template slot is added by this feature** — both were delivered at 035/036

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the two catalogues, the widened sibling relation, and three corrections to the attendance
module's own flows.

**⚠️ Order matters within `image_catalogues.py`, within `attendance_service.py` and within
`rsvp_service.py`** — tasks touching one file are sequential and carry no `[P]`.

### The two catalogues (R1, [contracts/attendance-catalogues.md](./contracts/attendance-catalogues.md))

- [ ] T004 Declare `ATTENDANCE_CATALOGUE` in `src/models/image_catalogues.py` — top-level fields, `rows` (prefix `row`, capacity `None`, `row_<x>_group` mandatory and valueless, flag and team assets), `rows.nested` (prefix `round`, `optional_unit=True`, field `points`), and `columns` (prefix `round`, `optional_unit=True`, `number` mandatory, `image` asset class `track`). **No new dataclass field is needed** — 040 built every form this uses
- [ ] T005 Declare `RSVP_CATALOGUE` in `src/models/image_catalogues.py` — the thirteen top-level fields with `race_name`, `round_format`, `round_date` and `round_time` mandatory, plus `rows` (prefix `session`, `optional_unit=True`, `group` and `name` both mandatory, `group` valueless); and register both catalogues in `CATALOGUES` under `attendance_template` and `rsvp_template`
- [ ] T006 [P] Add `tests/unit/test_image_attendance_catalogue.py` discharging the four test obligations of [contracts/attendance-catalogues.md](./contracts/attendance-catalogues.md): every constructed id matches the wip-spec's field list in both directions; `all_mandatory_ids()` with no template returns the top-level set alone; a template declaring `round_1_number` and no `round_1_group` is valid; and `RSVP_CATALOGUE` declares no id matching `driver`, `team`, `rsvp`, `status` or `points` (FR-009, the negative half of XIV.17)

### The widened sibling relation (R2, [contracts/sibling-and-floor.md](./contracts/sibling-and-floor.md))

- [ ] T007 Widen the sibling **relation** in `sibling_row_fields` in `src/models/image_catalogues.py` to the **union** of the `ASPECT_TEMPLATES` relation it uses today and a new `ASPECT_SOURCE_MODULE` relation, so the two graphics of one source module are siblings whatever they draw (XIV.3, v4.6.0). The union is required: replacing the aspect relation would break the results and standings pairs
- [ ] T008 Widen the sibling **surface** in `sibling_fields_declared` in `src/models/image_catalogues.py` past `rows` alone to the full addressable id set of each sibling catalogue — top-level `mandatory` ∪ `optional`, plus every collection's constructed ids. The sheet and the call overlap in their **top-level** fields, so the current `^<rows.prefix>_\d+_` match would miss `round_format` on a sheet template entirely
- [ ] T009 [P] Add sibling coverage to `tests/unit/test_image_attendance_catalogue.py` for the seven obligations of [contracts/sibling-and-floor.md](./contracts/sibling-and-floor.md) Part 1 — including that `calendar_template` gains **no** sibling, and that every template fixture passing today still passes (same file as T006 — sequential)

### The check-in deadline gets one derivation (R7, FR-027)

- [ ] T010 Add `derive_checkin_deadline(scheduled_at, deadline_hours) -> datetime` to `src/services/attendance_service.py`, returning the round's scheduled time less the configured hours, with `0` yielding the round's own start. It lives here and not in the image utility so the textual path can adopt the value without a second implementation (XIV.7's derived-presentation condition)
- [ ] T011 [P] Extend `tests/unit/test_attendance_service.py` covering `derive_checkin_deadline`: a positive configuration, a configuration of `0` landing on the round's start, and the returned value being timezone-aware

### The sheet's replacement ordering (R5, FR-045, [contracts/attendance-posting.md](./contracts/attendance-posting.md))

- [ ] T012 Reorder `post_attendance_sheet` in `src/services/attendance_service.py` to **produce before it destroys**: build the replacement, send it, and only then delete the prior message and persist the new id. The function today deletes at its top and sends ~90 lines later, so a failed send leaves the division with no sheet at all
- [ ] T013 Ensure `post_attendance_sheet` has exactly **one** send site and **one** delete site in `src/services/attendance_service.py`, so the image branch added in Phase 4 inherits the ordering rather than reimplementing it — the author's ruling of 2026-08-13, "the image path should inherit this"
- [ ] T014 [P] Extend `tests/unit/test_attendance_service.py` asserting the composed textual sheet is **byte-identical** to its previous output, the reorder having changed when the message is sent and not what it says; and that a failed send leaves the prior message undeleted

### The failed check-in call becomes visible (R6, FR-062)

- [ ] T015 Report a failed check-in post to the server's logging channel from the existing `except discord.HTTPException` branch of `run_rsvp_notice` in `src/services/rsvp_service.py`, via `output_router.post_log`, naming the season, the division and the round. Today the branch reaches `log.error` alone, which no league can see
- [ ] T016 Confirm the report of T015 fires **independently of the `rsvp` toggle** in `src/services/rsvp_service.py` — the fault is in the call, not the picture, and a league that never enables images must still learn its calls are failing. This is the one task in the feature that changes behaviour with the image module switched off
- [ ] T017 [P] Extend `tests/unit/test_rsvp_service.py`: force the send to fail with the toggle **off** and assert the log-channel report; assert nothing is enqueued for retry, the queue carrying text alone and a call replayed as text having no buttons (FR-061)

**Checkpoint**: both catalogues are registered, a sheet template holding a check-in field is detectable, the deadline has exactly one derivation, the sheet is never deleted before its replacement exists, and a failed call is visible to staff. User stories may now begin.

---

## Phase 3: User Story 1 — Preview both graphics (Priority: P1) 🎯 MVP

**Goal**: `/images test attendance` returns two PNGs and `/images test rsvp` returns five, all built from fabricated data, with no season, division or round in existence.

**Independent Test**: enable the module, name both templates, run both commands, and confirm the PNGs show every enumerated case of [quickstart.md](./quickstart.md) § 3 in the rasterised output.

### Tests for User Story 1

- [ ] T018 [P] [US1] Add `tests/unit/test_image_attendance_service.py` covering `resolve_drawing` for the sheet: composition as the textual sheet composes it (FR-010), ordering by total descending with ties alphabetical on the **resolved** name (FR-011), the total read from `total_points_after` and the cell from `points_awarded` (both already net of pardons and per-division), and the team of a row being the driver's seat **at generation** rather than any round's team (FR-020)
- [ ] T019 [US1] Add floor coverage to `tests/unit/test_image_attendance_service.py` for the five obligations of [contracts/sibling-and-floor.md](./contracts/sibling-and-floor.md) Part 2 — a division holding no driver raising the data error naming the **division** and not the template, and one driver against a ten-row template drawing with nine rows removed and no error (same file as T018 — sequential)
- [ ] T020 [P] [US1] Add `tests/unit/test_image_attendance_fill.py` covering the sheet's `build_fill_spec`: rows filled to the driver count, unused `row_<x>_group` removed with its fields marked off-canvas, an empty round cell emptied rather than drawn `0` or `—` (FR-015, FR-030), the autoreserve and autosack groups removed whole when disabled, and **no position drawn on any row** (FR-007)
- [ ] T021 [P] [US1] Add `tests/unit/test_image_rsvp_service.py` covering the call's `resolve_drawing`: four session names for a sprint round and two for any other (FR-024), the format label matching the embed's, the deadline through `derive_checkin_deadline`, a mystery round filling `race_name`/`track_name`/`country_name` with the convention's values and emptying no mandatory field (FR-029), and **no per-driver read reaching the utility at all** (FR-009)
- [ ] T022 [P] [US1] Add sample-data coverage to `tests/unit/test_image_sample_data.py` asserting the fabricated sheets exhibit every enumerated case of the wip-spec's § "Test data" where the row count allows, and drop the excess cases where it does not

### Implementation for User Story 1

- [ ] T023 [US1] Add `src/services/image_attendance_service.py` with the drawing dataclasses of [data-model.md](./data-model.md) and an `AttendanceDataError` raised for a fatal disagreement before anything is drawn
- [ ] T024 [US1] Implement `resolve_drawing` for the sheet in `src/services/image_attendance_service.py`: compose and order the rows as the textual sheet does, read `points_awarded` and `total_points_after` as persisted (FR-012, FR-014), resolve names through the person and team conventions (FR-019), place "Reached point limit" on a driver sanctioned upon this posting and empty it for every other (FR-017), and read the two limits from `AttendanceConfig` (FR-018)
- [ ] T025 [US1] Call the textual sheet's own formatting code for every value the graphic and the textual sheet both draw, in `src/services/image_attendance_service.py`, restating none of it — a change to how the textual sheet renders such a value is a change to the graphic by the same stroke (FR-013, XIV.7's shared-rendering clause). Assert in `tests/unit/test_image_attendance_service.py` that both paths produce the identical string for each shared value
- [ ] T026 [US1] Raise the floor in `resolve_drawing` in `src/services/image_attendance_service.py` **before any template measurement**, so a division holding no driver reports "holds no driver at all" rather than a capacity divergence against a template that is not at fault (R3, FR-041)
- [ ] T027 [US1] Implement `build_fill_spec` for the sheet in `src/services/image_attendance_service.py`: count the row and round capacities from the template, fill text through a `put` that empties rather than dashes, place `driver_flag`, `team_image` and `round_<z>_image` into `image_data`, remove unused rows' groups and mark their fields off-canvas, and remove a round's **three id families** together — `round_<z>_*` and `row_<x>_round_<z>_points` on every row (FR-039, XIV.12)
- [ ] T028 [P] [US1] Add `src/services/image_rsvp_service.py` with its drawing dataclass, `resolve_drawing` and `build_fill_spec` for the call: the heading fields, the session list counted from the template, the date, time and deadline rendered in the configured zone with its abbreviation (FR-025, XIV.15), and the track image resolved as the calendar's is
- [ ] T029 [US1] Add `build_attendance_drawing` to `src/services/image_sample_data.py` fabricating two sheets for "Test Division" of tier 1 and season 1, holding five rounds and standing after the third — one with both limits configured and one with both disabled (FR-063) — with round 2 of the mystery format and one round whose track is of the server's list but has no image file, so the fallback and its notice are both evaluated (FR-064)
- [ ] T030 [US1] Fabricate one driver fewer than the rows the template declares in `src/services/image_sample_data.py`, or exactly one where it declares a single row, drawn from the server's team configuration (FR-065), exhibiting the seven enumerated driver cases of the wip-spec's § "Test data" in its order (FR-066), and giving nationalities the signup wizard accepts, at least one being that recorded for a driver who stated none (FR-067)
- [ ] T031 [P] [US1] Add `build_rsvp_drawing` to `src/services/image_sample_data.py` fabricating the five cases of FR-069 — a sprint round, a normal round, a mystery round, a round whose track has no image file, and a round with the deadline configured to `0` — at dates and times spanning more than one month and more than one half of the day (FR-070)
- [ ] T032 [US1] Extend `build_spec` in `src/services/image_sample_data.py` to dispatch both template keys through their new services, resolving the packaged `resources/flags`, `resources/teams` and `resources/tracks` directories as the existing branches resolve theirs
- [ ] T033 [US1] Add the guards to `/images test` in `src/cogs/image_cog.py`: reject `attendance` where the server holds no team beyond the reserve team **or** its track list is empty, and reject `rsvp` where the track list is empty (FR-068, FR-071); report a fatal error from either to the invoking manager and fall back to **no** textual output, these two commands having none (FR-072)
- [ ] T034 [US1] Verify all seven PNGs against [quickstart.md](./quickstart.md) § 3 — opened as rasterised images, never as SVG in a browser (Constitution XIV.14). Confirm empty round cells are **empty**, that the sheet draws no position number, and that the disabled-limits sheet shows the two blocks *gone* rather than blank

**Checkpoint**: both graphics can be produced and inspected without any league data. Every later story reuses this path.

---

## Phase 4: User Story 2 — The sheet posted through its lifecycle (Priority: P2)

**Goal**: with the `attendance` toggle on, a division's sheet posts as a graphic under the textual sheet's heading, replaced on each occasion the text was reposted before.

**Independent Test**: enable the toggle for a division with an attendance channel, approve a round's post-race penalties, confirm one image message where a text message was; amend the round and confirm the message is replaced without the channel ever being empty.

### Tests for User Story 2

- [ ] T035 [P] [US2] Add `tests/unit/test_image_attendance_post.py` covering the toggle branch taken when on and skipped when off; nothing generated and nothing posted where no attendance channel is configured or the channel is inaccessible (FR-046); and nothing for a round recorded as cancelled, the toggle notwithstanding (FR-047)
- [ ] T036 [US2] Add the sanction-gate test to `tests/unit/test_image_attendance_post.py`: with the render forced to fail, assert the autoreserve and autosack effects are still applied and their verdicts still announced (FR-048, XIV.7's precondition clause) (same file as T035 — sequential)
- [ ] T037 [US2] Add the fallback matrix to `tests/unit/test_image_attendance_post.py`: `SCHEDULED` origin falling back to the textual sheet with the fault reported to the log channel, `COMMANDED` origin rejecting the command and posting nothing, and the textual sheet — never the image — enqueued on a transport failure (FR-057, FR-058, FR-060) (same file — sequential)

### Implementation for User Story 2

- [ ] T038 [US2] Add `src/services/image_attendance_post.py` with `attendance_enabled`, `build_drawing`, `render_png` and `try_post`, following the shape of `image_results_post.py`, and reusing its `report` and `report_notices` rather than restating them
- [ ] T039 [US2] Wire the image branch into `post_attendance_sheet` in `src/services/attendance_service.py` **inside** the produce-before-destroy ordering established at T012, so the graphic and the textual fallback take the same send site and the same delete site
- [ ] T040 [US2] Skip generation entirely — never attempt and discard — where the source module would post nothing: no attendance channel, an inaccessible channel, or a cancelled round (FR-046, FR-047, XIV.8's "no posting, no graphic")
- [ ] T041 [US2] Confirm in `src/services/attendance_service.py` that `enforce_attendance_sanctions` and the verdicts it announces run to completion regardless of what the sheet posting does, the graphic being downstream of every state change it depicts and never a precondition of one (FR-048)
- [ ] T042 [US2] Ensure the failure of one division prevents no other in `src/services/attendance_service.py`, each division rendering and posting on its own (FR-059)
- [ ] T043 [US2] Verify [quickstart.md](./quickstart.md) § 5 as rasterised PNGs, including step 3's ordering check — at no instant is the channel without a sheet — and step 5's sanction gate

**Checkpoint**: a league's attendance sheets post and replace themselves as pictures, and no sanction depends on one.

---

## Phase 5: User Story 3 — A check-in graphic that never goes stale (Priority: P3)

**Goal**: with the `rsvp` toggle on, a graphic is attached to the check-in call once, and survives every edit of the embed beneath it untouched.

**Independent Test**: enable the toggle, let the notice horizon fire, confirm the call carries mention, embed, three buttons and a graphic; press each button and confirm the embed changes while the attachment and the message id do not.

### Tests for User Story 3

- [ ] T044 [P] [US3] Add `tests/unit/test_image_rsvp_post.py` asserting the call is posted with role mention, embed, three buttons **and** the attachment when the toggle is on, and that with the toggle off the message is identical in every respect except the attachment (FR-052)
- [ ] T045 [US3] Add the **static call-graph test** to `tests/unit/test_image_rsvp_post.py`: assert directly that no module reachable from a button press — `RsvpView` and its callbacks, `run_reserve_distribution`, `run_rsvp_deadline`, `_rebuild_embed_for_round` — imports `image_rsvp_post` or `image_rsvp_service`. This is the strongest available guard on XIV.17, which the module cannot otherwise detect (same file as T044 — sequential)
- [ ] T046 [US3] Add the no-attachment fallback test to `tests/unit/test_image_rsvp_post.py`: with the render forced to fail, the call is posted with mention, embed and buttons entire, and `bulk_insert_attendance_rows` is still called — the graphic gating neither the call nor the rows (FR-055) (same file — sequential)

### Implementation for User Story 3

- [ ] T047 [US3] Add `src/services/image_rsvp_post.py` with `rsvp_enabled` and a single `try_attach` returning a `discord.File` or `None`, and reusing `image_results_post.report_notices`
- [ ] T048 [US3] Call `try_attach` from exactly **one** place — the initial post in `run_rsvp_notice` in `src/services/rsvp_service.py` — passing the file to the existing `channel.send` and changing nothing else about how the message is composed (FR-049, FR-051)
- [ ] T049 [US3] Confirm the graphic is never regenerated in `src/services/rsvp_service.py`: no image import in the button handler, the reserve distribution, the deadline handler or `_rebuild_embed_for_round`, each of which edits the embed in place while the attachment rides through (FR-051, XIV.17)
- [ ] T050 [US3] Leave the last notice, the reserve-distribution announcement and the no-reserve-available notice as message text carrying no graphic, the toggle notwithstanding (FR-053), and leave the previous round's call deletion unchanged (FR-054)
- [ ] T051 [US3] Confirm `rsvp_embed_messages.message_id` is written exactly as it is today and never rewritten by the image path — the call is not deleted and reposted while it stands, and reposting would orphan the view re-armed against that id at `src/bot.py:430`
- [ ] T052 [US3] Verify [quickstart.md](./quickstart.md) § 6 as rasterised PNGs, including step 2's three checks after each button press: the roster changes, the attachment is byte-identical and not re-uploaded, and the message id is unchanged

**Checkpoint**: a league's check-in calls carry a picture that is still true after every driver has answered.

---

## Phase 6: User Story 4 — A template's faults, before a season depends on it (Priority: P4)

**Goal**: `/images template attendance` and `/images template rsvp` refuse a file that cannot draw what it will be asked to draw, and `/season review` warns where only a stand-in can be compared.

**Independent Test**: set a sheet template with a gap in its row numbering and confirm the command is refused with that reason and the previous filename left in force.

### Tests for User Story 4

- [ ] T053 [P] [US4] Extend `tests/unit/test_image_validity_service.py` for the structural checks of FR-035 at all three moments: a sheet template declaring at least one row numbered continuously from 1 holding every mandatory row field; its rounds, if any, numbered continuously from 1 and each holding `round_<z>_number`; and a check-in template's sessions, if any, numbered continuously from 1 holding both mandatory session fields
- [ ] T054 [US4] Add the gap and missing-field cases to `tests/unit/test_image_validity_service.py`: a gap in rows, in rounds or in sessions each refused naming **which** of the three numberings is at fault, and a sheet template declaring no row at all refused (FR-041) (same file as T053 — sequential)
- [ ] T055 [P] [US4] Extend `tests/unit/test_season_review.py` asserting a sheet template declaring fewer rounds than the season's most demanding division, and a check-in template declaring fewer sessions than the season's largest round, are each a **warning** that still permits approval (FR-036)

### Implementation for User Story 4

- [ ] T056 [US4] Confirm `CatalogueLayer` in `src/services/image_validity_service.py` reads both new catalogues unchanged — the layer is catalogue-driven and this feature adds no declaration form, so the structural checks of FR-035 should follow from T004 and T005 alone. Add only what does not
- [ ] T057 [US4] Add the season-review stand-in comparisons in `src/cogs/season_cog.py`: a sheet template's rounds against the greatest number any division of the season holds, and a check-in template's sessions against the largest number any round holds, each a warning only (FR-036, XIV.9's "stand-ins warn")
- [ ] T058 [US4] Refuse a driver assignment that would carry a division past the rows its configured sheet template declares, with the change unapplied, in `src/services/placement_service.py` — naming the driver count, the declared capacity and the template (FR-042, XIV.12)
- [ ] T059 [US4] Confirm the generation-time checks of FR-037 to FR-040 report the right thing: drivers in excess of rows naming the drivers that would have been dropped, rounds in excess naming them, and sessions in excess naming the sessions dropped
- [ ] T060 [US4] Verify [quickstart.md](./quickstart.md) §§ 2, 4, 8 and 9 — including that a template declaring `round_1_number` and no `round_1_group` is **accepted**, and one declaring no round or no session at all is accepted, the grid and the session list being optional as a unit

**Checkpoint**: a faulty template is named at the moment it is configured, not at the moment a round is posted.

---

## Phase 7: User Story 5 — Degradations to staff, never to drivers (Priority: P5)

**Goal**: every non-fatal degradation reaches the logging channel naming season, division and round, and no channel a driver reads ever shows one.

**Independent Test**: post a sheet for a division holding a driver whose nationality has no flag file; the fallback is drawn, the notice appears in the logging channel, and the attendance channel carries only the sheet.

### Tests for User Story 5

- [ ] T061 [P] [US5] Add notice coverage to `tests/unit/test_image_attendance_post.py` and `tests/unit/test_image_rsvp_post.py`: a flag or track image falling back raises a notice naming the field and the datum; a substituted font and a truncated name each raise theirs; and every one reaches the log channel and no division channel (FR-056)
- [ ] T062 [P] [US5] Assert that where nationality collection is switched off at its source, **no** flag is drawn anywhere and **no** notice is raised — a configured absence has not degraded (FR-031, XIV.4)

### Implementation for User Story 5

- [ ] T063 [US5] Report non-fatal errors from both hooks through `image_results_post.report_notices`, naming the season, the division and the round, and additionally alongside the output of a command that triggered the generation (FR-056)
- [ ] T064 [US5] Confirm no notice, problem or fallback text can reach a division's attendance or RSVP channel from either hook — both are read by the drivers of the league and not by its staff
- [ ] T065 [US5] Suppress the notice for a configured absence in `src/services/image_attendance_service.py`, distinguishing nationality switched off at its source from a nationality the league collects and merely does not hold for one driver, which remains an ordinary emptied optional field and raises its notice (FR-031)
- [ ] T066 [US5] Verify [quickstart.md](./quickstart.md) § 7 — the failed check-in post reported with the toggle off as well as on, and nothing enqueued for retry

**Checkpoint**: safe to run unattended.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T067 Run `pytest tests/ -q` and compare against the T001 baseline; every delta is accounted for, and the textual sheet output is unchanged (T014)
- [ ] T068 [P] Confirm no time arithmetic exists anywhere in `src/services/image_rsvp_service.py` — the utility receives the finished deadline from `derive_checkin_deadline` and does not compute it (Constitution XIV.7, R7)
- [ ] T069 [P] Confirm `RSVP_CATALOGUE` still declares no field whose value can change while a call stands, and that `try_attach` still has exactly one caller. **This is the one obligation in the feature that no test can fully discharge** — XIV.17 places it on review, and a stale picture under a current message reports nothing
- [ ] T070 [P] Update `README.md` for what a league can now see: the `attendance` toggle drawing the sheet, the `rsvp` toggle adding a picture to the check-in call without changing it, the two template commands, the optional round grid and session list, and the row ceiling refusing an overflowing assignment
- [ ] T071 Correct the README's "remaining five toggles change what `/images config view` and `/season review` report, and nothing else" — `attendance` and `rsvp` are now wired, leaving three (shares `README.md` with T070 — sequential)
- [ ] T072 Verify every graphic of [quickstart.md](./quickstart.md) §§ 3–9 as a rasterised PNG, including the overflow cases naming what would have been dropped
- [ ] T073 Invoke the `close-out` skill, which is **mandatory before reporting this work complete**: it reconciles `docs/wip-specs/image_module_specification.md` and `README.md` against what was built and against every decision taken in conversation

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)** — no dependencies
- **Foundational (Phase 2)** — depends on Setup; the catalogue and sibling tracks **block every user story**. The reordering (T012–T014) and the failed-call report (T015–T017) block nothing and may ship alone
- **US1 (Phase 3)** — depends on Phase 2's catalogue track and on T010
- **US2 (Phase 4)** — depends on US1's sheet utility and on T012–T013
- **US3 (Phase 5)** — depends on US1's call utility; **independent of US2**
- **US4 (Phase 6)** — depends on Phase 2's catalogue track alone; independent of US1, US2 and US3
- **US5 (Phase 7)** — depends on US2 and US3, being the reporting around both posting paths
- **Polish (Phase 8)** — depends on every story to be delivered

### Within each story

- Tests are written first and confirmed failing before the implementation they cover
- Dataclasses before the resolution that fills them; resolution before the projection onto a template; projection before the posting

### Parallel opportunities

- T002 and T003 together
- T006, T009, T011, T014 and T017 are test files running alongside the implementation tracks they follow
- **The five Phase 2 tracks are independent**: the catalogues (T004–T006), the sibling widening (T007–T009), the deadline (T010–T011), the ordering (T012–T014) and the report (T015–T017) touch three files between them — `image_catalogues.py`, `attendance_service.py` and `rsvp_service.py`
- T018, T020, T021 and T022 are four different test files and run together
- T028 and T031 run alongside the sheet tasks — the call utility shares no file with the sheet utility
- **US2 and US3 can be built in parallel by two people** once US1 is done: US2 touches `attendance_service.py` and `image_attendance_post.py`, US3 touches `rsvp_service.py` and `image_rsvp_post.py`
- **US4 can be built in parallel with all of US1–US3** once Phase 2's catalogue track is done
- T068, T069 and T070 together (T071 also edits `README.md` and follows T070)

### Parallel example: after Phase 2

```bash
# Two independent tracks:
Track A (US1 → US2 → US3): T018–T022 → T023 → … → T034 → T035 → … → T043 → T044 → … → T052
Track B (US4):             T053, T055 → T054 → T056 → T057 → T058 → T059 → T060
```

---

## Implementation Strategy

### MVP (User Story 1 only)

1. Phase 1 — baseline recorded, assets and templates confirmed
2. Phase 2 — both catalogues, the widened sibling check, and the three flow corrections
3. Phase 3 — both test commands returning seven inspectable PNGs
4. **Stop and validate** against quickstart § 3, as PNGs
5. A league manager can author and correct both templates from here, with no season in existence

### Incremental delivery

1. Setup + Foundational → the catalogues exist, a swapped template is detectable, and two long-standing flow faults are corrected
2. + US1 → both graphics previewable (MVP)
3. + US2 → the sheet posted and replaced, with no sanction depending on it
4. + US3 → the check-in call carrying a picture that never goes stale
5. + US4 → faults caught at configuration rather than at posting
6. + US5 → safe unattended
7. + Polish → docs reconciled, suite compared, close-out run

### The five things most likely to go wrong

1. **A mutable field reaching the check-in catalogue.** The moment `RSVP_CATALOGUE` declares a driver, a status or a count, the static declaration is false and **nothing will report it** — the picture simply goes stale under a live message. T045 guards the call graph and T069 guards the catalogue; neither can guard the judgement, which is why XIV.17 puts it on review.
2. **Reimplementing produce-before-destroy in the image branch.** T012 puts the ordering in the textual flow and T039 puts the image branch inside it. Two orderings in one function will drift, and the fallback path — the one reached *because* something already failed — is the half that would be left deleting first.
3. **Drawing `0` in an empty round cell.** An empty cell means zero (author's ruling, 2026-08-13), and it is drawn **empty**. `points_awarded` is `None` before finalisation and `0` after a fully pardoned round; both are the same picture.
4. **Removing a round's cells by containment.** A cell belongs to its row and its round both, and a node has one parent — so a round's cells leave through T027's family removal, not through the heading group. Getting this wrong draws a grid with orphaned cells that no test of the heading will catch.
5. **Verifying in a browser.** The rasteriser exposes flowed text, substituted fonts and unresolvable hrefs that a browser hides. T034, T043, T052, T060, T066 and T072 all say PNG for that reason.

---

## Notes

- `[P]` means a different file and no dependency on an incomplete task
- Tasks touching `image_catalogues.py` (T004–T008), `attendance_service.py` (T010, T012–T013) and `rsvp_service.py` (T015–T016) are sequential within their file and carry no `[P]`
- **No migration is written.** Both message-id columns already exist, and the two lifecycles use them differently — the sheet replaces through `attendance_message_id`, the call leaves `rsvp_embed_messages.message_id` alone entirely
- **No asset class is added.** Flags, teams and tracks are all configured and shipped; the mystery round resolves `mystery.svg` from the track class as every other type does
- T015–T017 close a hole that predates this feature: a failed check-in post left no attendance rows, so the penalty pass found nothing and the round was permanently recorded as flawless attendance. It ships independently of the graphics
- Commit after each task or logical group; stop at any checkpoint to validate a story on its own

## Found during implementation

Work done outside the numbered tasks, recorded so it is not re-derived:

_(to be filled during implementation)_
